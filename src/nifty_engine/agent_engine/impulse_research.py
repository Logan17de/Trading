"""Private, reproducible tick research. No broker/client/controller is accepted here."""
from collections import defaultdict, deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import copy
import json
import math
from pathlib import Path
import statistics
import threading
import uuid
import hashlib

from .impulse import finite

MONITOR_ONLY = True
IST = ZoneInfo('Asia/Kolkata')
JST = ZoneInfo('Asia/Tokyo')
HORIZONS = (.25, .5, 1, 2, 3, 5, 10, 15, 30, 60, 120)
POINTS = (5, 10, 15, 20, 30, 50)
DEFAULT = dict(enabled=True, trading_enabled=False, mode='MONITOR_ONLY',
    nifty=dict(window_seconds=5, thresholds=[5, 7.5, 10, 12.5, 15]),
    sensex=dict(window_seconds=5, thresholds=[15, 20, 25, 30, 40]),
    fresh_seconds=3, lock_seconds=30, idle_seconds=10, minimum_report_samples=30,
    flat_underlying_points=2, spread_widen_multiple=2, report_after_jst='20:00')
FIELDS = ('volume', 'open_interest', 'iv', 'delta', 'gamma', 'theta', 'vega')


def config(path=None, *, overrides=None):
    value = copy.deepcopy(DEFAULT)
    if path and Path(path).exists():
        raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        value.update(raw)
    if overrides is not None:value.update(copy.deepcopy(overrides))
    if value['trading_enabled'] is not False or value['mode'] != 'MONITOR_ONLY':
        raise ValueError('MONITOR_ONLY_REQUIRED')
    for index in ('nifty', 'sensex'):
        r = value[index]
        if r['window_seconds'] != 5 or not r['thresholds'] or any(
            finite(t) is None or t <= 0 for t in r['thresholds']):
            raise ValueError('INVALID_RESEARCH_THRESHOLDS')
        r['thresholds'] = sorted(set(r['thresholds']))
    if value['lock_seconds'] < 30 or value['fresh_seconds'] <= 0 or value['idle_seconds'] <= 0:
        raise ValueError('INVALID_RESEARCH_TIMING')
    if value['minimum_report_samples'] < 1:
        raise ValueError('INVALID_RESEARCH_SAMPLE_COUNT')
    datetime.strptime(value['report_after_jst'], '%H:%M')
    return value


def time_bucket(at):
    minute = datetime.fromtimestamp(at, IST).strftime('%H:%M')
    for low, high in (('09:15','09:30'), ('09:30','11:30'), ('11:30','13:30'),
                      ('13:30','14:30'), ('14:30','15:15'), ('15:15','15:30')):
        if low <= minute < high:
            return low+'–'+high
    return 'OUTSIDE_RESEARCH_BUCKETS'


class PremiumImpulseMonitor:
    """Events lock contracts; samples use observed timestamps, never interpolation."""
    def __init__(self, store, *, cfg=None, clock=lambda: datetime.now(timezone.utc)):
        self.store = store
        self.cfg = config(overrides=cfg)
        self.config_sha256=hashlib.sha256(json.dumps(self.cfg,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        store.set_meta('premium-research-config-'+self.config_sha256,self.cfg)
        self.clock = clock
        self.lock = threading.RLock()
        self.flush_lock = threading.Lock()
        self.members = {}
        self.history = defaultdict(lambda: deque(maxlen=20000))
        self.books = {}
        self.last = {}
        self.active = {}
        self.follow = {}
        self.dirty = {}
        self.pending_ticks = []
        self.pending_outcomes = {}
        self.rejected = 0
        self.day = None
        self.snapshot_second = None
        self.outcome_second = None
        self.generation = 0
        self.failure = None
        self.gap_count = 0
        self.latest_metrics = {}
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS premium_impulse_events(
                    event_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, index_name TEXT NOT NULL,
                    start REAL NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS impulse_event_day ON premium_impulse_events(trade_date);
                CREATE TABLE IF NOT EXISTS premium_impulse_ticks(
                    id INTEGER PRIMARY KEY, trade_date TEXT NOT NULL, exchange_timestamp REAL,
                    receive_timestamp REAL NOT NULL, processing_timestamp REAL NOT NULL,
                    generation INTEGER NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS impulse_tick_day ON premium_impulse_ticks(trade_date);
                CREATE TABLE IF NOT EXISTS premium_impulse_outcomes(
                    event_id TEXT NOT NULL, horizon REAL NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(event_id,horizon));
                CREATE TABLE IF NOT EXISTS daily_research_summary(
                    trade_date TEXT PRIMARY KEY, generated_at REAL NOT NULL, body TEXT NOT NULL);
            ''')
            # An interrupted observation must never be resumed across an unknown gap.
            for row in db.execute("SELECT event_id,body FROM premium_impulse_events WHERE json_extract(body,'$.followup_status')='PENDING'").fetchall():
                e = json.loads(row['body'])
                if e.get('followup_status') == 'PENDING':
                    e.update(followup_status='INTERRUPTED_RESTART', status='CLOSED', data_gap=True)
                    db.execute('UPDATE premium_impulse_events SET body=? WHERE event_id=?',
                               (json.dumps(e, allow_nan=False), row['event_id']))

    def submit_order(self, *args, **kwargs):
        raise PermissionError('MONITOR_ONLY_NO_BROKER_WRITES')

    modify_order = submit_order
    cancel_order = submit_order

    def configure(self, members):
        with self.lock:
            self.members.update(copy.deepcopy(members))

    def reset(self, reason='RECONNECT'):
        with self.lock:
            for e in self.follow.values():
                e.update(data_gap=True, status='CLOSED', followup_status='INTERRUPTED_'+reason)
                self.dirty[e['event_id']] = e
            self.active.clear()
            self.follow.clear()
            self.history.clear()
            self.books.clear()
            self.last.clear()
            self.latest_metrics.clear()
            if hasattr(self,'flow_history'):self.flow_history.clear()
            self.generation += 1
            self.gap_count += 1

    def _sample(self, symbol, at):
        rows = self.history.get(symbol, ())
        row = next((r for r in reversed(rows) if r['exchange_timestamp'] <= at), None)
        return row if row and at-row['exchange_timestamp'] <= self.cfg['fresh_seconds'] else None

    def _change(self, symbol, at, seconds):
        a, b = self._sample(symbol, at-seconds), self._sample(symbol, at)
        # History is cleared on every per-symbol gap/reconnect, so an anchor in
        # this deque proves contiguous coverage without rescanning five minutes.
        if not a or not b:
            return None
        return b['price']-a['price']

    def metrics(self, symbol, at):
        moves = {str(n): self._change(symbol, at, n) for n in (1,2,3,5,10)}
        velocity = {str(n): moves[str(n)]/n if moves[str(n)] is not None else None for n in (1,3,5)}
        old = self._change(symbol, at-1, 1)
        return dict(premium_change=moves, velocity=velocity,
                    acceleration_1s=moves['1']-old if moves['1'] is not None and old is not None else None)

    def _contracts(self, index, at):
        spot = self._sample(index, at)
        rows = [(s,r) for s,r in self.members.items() if r['index']==index and r['role']=='option'
                and r.get('expiry') and r['expiry']>=self.day]
        if not spot or not rows:
            return None
        expiry = min(r['expiry'] for s,r in rows)
        rows = [(s,r) for s,r in rows if r['expiry']==expiry]
        strikes = sorted({r['strike'] for s,r in rows})
        atm = min(strikes, key=lambda n: (abs(n-spot['price']),n))
        offset = strikes.index(atm)
        locked = {s: dict(r,offset=strikes.index(r['strike'])-offset) for s,r in rows
                  if abs(strikes.index(r['strike'])-offset)<=2}
        return expiry, atm, locked

    def _context(self, index, at):
        result = {}
        for seconds in (30,60,300):
            rows = [r for r in self.history.get(index,()) if at-seconds <= r['exchange_timestamp'] < at]
            complete = self._sample(index,at-seconds) is not None and self._change(index,at,seconds) is not None
            result[str(seconds)] = dict(high=max(r['price'] for r in rows) if rows and complete else None,
                low=min(r['price'] for r in rows) if rows and complete else None)
        # Collection can start late: observed extrema are explicitly not full-session extrema.
        rows = self.history.get(index,())
        result['session'] = dict(high=None,low=None,status='FULL_SESSION_COVERAGE_UNKNOWN')
        result['observed_history'] = dict(high=max(r['price'] for r in rows) if rows else None,
                                         low=min(r['price'] for r in rows) if rows else None)
        spot = self._sample(index,at)
        for r in result.values():
            r['distance_to_high'] = r['high']-spot['price'] if r['high'] is not None and spot else None
            r['distance_to_low'] = spot['price']-r['low'] if r['low'] is not None and spot else None
        return result

    def _relationships(self, e, at):
        neighbours = {}
        for symbol, member in e['locked_contracts'].items():
            row = self._sample(symbol, at)
            book = self.books.get(symbol)
            if book and at-book['exchange_timestamp'] > self.cfg['fresh_seconds']: book=None
            prev = self._sample(symbol,at-5)
            fields = {k: row.get(k) if row else None for k in FIELDS}
            for field in ('volume','open_interest'):
                a,b=(row.get(field) if row else None),(prev.get(field) if prev else None)
                fields[field+'_delta_5s'] = a-b if a is not None and b is not None and (field=='open_interest' or a>=b) else None
            neighbours[symbol] = dict(offset=member['offset'],option_type=member['option_type'],
                movement={str(n):self._change(symbol,at,n) for n in (1,3,5,10)}, **fields,
                book=copy.deepcopy(book),buy_aggressive_volume=None,sell_aggressive_volume=None,
                trade_flow_imbalance=None,cvd=None)
        lead = neighbours[e['symbol']]
        opp_symbol = next((s for s,r in e['locked_contracts'].items()
                           if r['offset']==0 and r['option_type']!=e['option_type']),None)
        opposite = neighbours.get(opp_symbol,{}).get('movement',{}).get('5')
        leading = lead['movement']['5']
        same = [v['movement']['5'] for v in neighbours.values() if v['option_type']==e['option_type']]
        count = sum(v>0 for v in same if v is not None) if len(same)==5 and all(v is not None for v in same) else None
        delta,gamma=lead['delta'],lead['gamma']
        underlying=self._change(e['index'],at,5)
        expected=delta*underlying+.5*gamma*underlying**2 if None not in (delta,gamma,underlying) else None
        fut=e.get('future_symbol');future=self._sample(fut,at) if fut else None
        return dict(neighbours=neighbours,leading_change=leading,opposite_change=opposite,
            directional_option_spread=leading-opposite if None not in (leading,opposite) else None,
            number_of_strikes_confirming_direction=count,expected_option_move=expected,
            excess_premium_move=leading-expected if None not in (leading,expected) else None,
            futures=dict(symbol=fut,liquidity_status=e.get('future_liquidity_status','NOT_VERIFIED'),
                volume=future.get('volume') if future else None,
                open_interest=future.get('open_interest') if future else None,
                change_5s=self._change(fut,at,5) if fut else None),
            iv_regime='UNKNOWN')

    def _begin(self, symbol, at, contracts):
        member=self.members[symbol];index=member['index'];expiry,atm,locked=contracts
        start=self._sample(symbol,at-5);row=self._sample(symbol,at)
        futures=[(s,r) for s,r in self.members.items() if r['index']==index and r['role'] in ('future','future_candidate')]
        liquid=[]
        for s,r in futures:
            a,b=self._sample(s,at-5),self._sample(s,at);book=self.books.get(s)
            if a and b and book and at-book['exchange_timestamp']<=self.cfg['fresh_seconds'] and a.get('volume') is not None and b.get('volume') is not None and b['volume']>a['volume']:
                liquid.append((s,r,b['volume']-a['volume']))
        future=min(liquid,key=lambda v:(v[1].get('expiry','9999'),-v[2]))[0] if liquid else next((s for s,r in futures if r['role']=='future'),None)
        spot=self._sample(index,at);fut=self._sample(future,at) if future else None
        e=dict(event_id=uuid.uuid4().hex,index=index,direction='UP' if member['option_type']=='CE' else 'DOWN',
            option_type=member['option_type'],symbol=symbol,trade_date=self.day,event_start_timestamp=at,
            receive_timestamp=row['receive_timestamp'],processing_timestamp=row['processing_timestamp'],
            premium_window_start_timestamp=start['exchange_timestamp'],premium_start=start['price'],
            premium_at_trigger=row['price'],locked_expiry=expiry,locked_atm_strike=atm,
            locked_contracts=locked,lock_until=at+self.cfg['lock_seconds'],thresholds_crossed={},
            last_impulse=at,spot_start=spot['price'] if spot else None,future_start=fut['price'] if fut else None,
            spot_start_timestamp=spot['exchange_timestamp'] if spot else None,
            future_start_timestamp=fut['exchange_timestamp'] if fut else None,
            future_symbol=future,context=self._context(index,at),breaks={},continuation={},
            future_liquidity_status='OBSERVED_RECENT_VOLUME_AND_TWO_SIDED_BOOK' if liquid else 'NOT_VERIFIED',
            status='ACTIVE',followup_status='PENDING',data_gap=False,mode='MONITOR_ONLY',
            research_parameters=copy.deepcopy(self.cfg),research_config_sha256=self.config_sha256,
            time_bucket_ist=time_bucket(at),dte=(datetime.fromisoformat(expiry).date()-datetime.fromtimestamp(at,IST).date()).days,
            labels=['UNKNOWN'],false_flags={},signal_combinations={})
        self.active[index]=e;self.follow[e['event_id']]=e
        return e

    def _enrich(self, e, at):
        row=self._sample(e['symbol'],at)
        if row:
            change=self._change(e['symbol'],at,5)
            for threshold in self.cfg[e['index'].lower()]['thresholds']:
                key=str(threshold)
                if change is not None and change>=threshold and key not in e['thresholds_crossed']:
                    anchor=self._sample(e['symbol'],at-5)
                    if anchor:
                        e['thresholds_crossed'][key]=dict(exchange_timestamp=at,
                            seconds_from_event_start=at-e['event_start_timestamp'],
                            seconds_from_window_start=at-e['premium_window_start_timestamp'],
                            rolling_window_start_timestamp=anchor['exchange_timestamp'],
                            rolling_premium_change=change)
            current=self._change(e['symbol'],at,5)
            if current is not None and current>=self.cfg[e['index'].lower()]['thresholds'][0]:
                e['last_impulse']=at
        relationship=self._relationships(e,at);e['latest_relationships']=relationship
        if 'trigger_relationships' not in e:e['trigger_relationships']=copy.deepcopy(relationship)
        sign=1 if e['direction']=='UP' else -1
        spot=self._sample(e['index'],at);fut_change=relationship['futures']['change_5s']
        opposite=relationship['opposite_change'];count=relationship['number_of_strikes_confirming_direction']
        book=relationship['neighbours'][e['symbol']]['book']
        e['signal_combinations']=dict(futures=fut_change*sign>0 if fut_change is not None else None,
            opposite=opposite<0 if opposite is not None else None,neighbours=count>=3 if count is not None else None,
            order_flow=book['order_flow_imbalance_5s']>0 if book and book.get('order_flow_imbalance_5s') is not None else None)
        for period,levels in e['context'].items():
            if period not in ('30','60','300'):continue
            for side in ('high','low'):
                level=levels[side];key=period+'_'+side
                if spot and level is not None and key not in e['breaks'] and ((side=='high' and spot['price']>level) or (side=='low' and spot['price']<level)):
                    e['breaks'][key]=dict(exchange_timestamp=spot['exchange_timestamp'],seconds=spot['exchange_timestamp']-e['event_start_timestamp'])
        flags=e['false_flags']
        initial=e['trigger_relationships'];initial_book=initial['neighbours'][e['symbol']]['book']
        flags['both_ce_pe_rise']=opposite>0 if opposite is not None else None
        flags['single_strike_moves']=count==1 if count is not None else None
        flags['spread_widens']=book['spread']>initial_book['spread']*self.cfg['spread_widen_multiple'] if book and initial_book else None
        flags['premium_reverses']=row['price']<e['premium_at_trigger']-self.cfg[e['index'].lower()]['thresholds'][0] if row else None
        flags['underlying_flat']=abs(spot['price']-e['spot_start'])<self.cfg['flat_underlying_points'] if spot and e['spot_start'] is not None and at-e['event_start_timestamp']>=5 else None
        labels=[]
        if flags['both_ce_pe_rise'] is True:labels.append('IV_EXPANSION_CANDIDATE')
        if flags['single_strike_moves'] is True or flags['spread_widens'] is True:labels.append('LIQUIDITY_SPIKE_CANDIDATE')
        if e['signal_combinations']['futures'] is True and e['signal_combinations']['opposite'] is True:labels.append('DIRECTIONAL_CANDIDATE')
        desired='high' if sign>0 else 'low'
        if any(k.endswith(desired) for k in e['breaks']):
            labels.append('BREAKOUT_CANDIDATE')
            if spot and e['spot_start'] is not None and (spot['price']-e['spot_start'])*sign<0:labels.append('FAILED_BREAKOUT')
        e['labels']=labels or ['UNKNOWN']
        # Freeze the first ten-second false-signal observation separately from later context.
        if at-e['event_start_timestamp']<=10:
            prior=e.setdefault('early_false_flags',{})
            for key,value in flags.items():
                if value is True or key not in prior or prior[key] is None:prior[key]=value
        self.dirty[e['event_id']]=e

    def ingest(self, event):
        if not self.cfg['enabled']:return
        with self.lock:
            now=self.clock().timestamp();at=finite(event.get('provider_at'));symbol=event.get('symbol');kind=event.get('kind')
            if symbol not in self.members or kind not in ('price','depth'):
                self.rejected+=1;return
            if at is None or not -.5<=now-at<=self.cfg['fresh_seconds']:
                self._reject(event,'MISSING_OR_STALE_EXCHANGE_TIMESTAMP',now);return
            key=(symbol,kind)
            if at<=self.last.get(key,0):self._reject(event,'DUPLICATE_OR_OUT_OF_ORDER',now);return
            day=datetime.fromtimestamp(at,IST).date().isoformat()
            if self.day and self.day!=day:self.reset('DAY_ROLLOVER')
            self.day=day
            if key in self.last and at-self.last[key]>self.cfg['fresh_seconds']:
                self.history[symbol].clear();self.books.pop(symbol,None);self.gap_count+=1
                for e in self.follow.values():
                    if symbol in e['locked_contracts'] or symbol in (e['index'],e['future_symbol']):
                        e['data_gap']=True;self.dirty[e['event_id']]=e
            row=dict(symbol=symbol,kind=kind,exchange_timestamp=at,
                receive_timestamp=finite(event.get('receive_at')) or now,processing_timestamp=now,
                generation=self.generation,accepted=True,raw_exchange_timestamp=finite(event.get('raw_exchange_timestamp')),
                research_config_sha256=self.config_sha256,
                source_timestamp_unit=event.get('source_timestamp_unit','normalized_seconds'))
            if kind=='price':
                price=finite(event.get('price'))
                if price is None or price<=0:self._reject(event,'INVALID_PRICE',now);return
                row['price']=price
                for field in FIELDS:
                    value=finite(event.get(field))
                    row[field]=value if field not in ('volume','open_interest','iv') or value and value>0 else None
                row.update(buy_aggressive_volume=None,sell_aggressive_volume=None,trade_flow_imbalance=None,cvd=None)
                self.history[symbol].append(row)
                role=self.members[symbol]['role']
                keep=305 if role=='spot' else 125 if role in ('future','future_candidate') else 15
                while self.history[symbol] and self.history[symbol][0]['exchange_timestamp']<at-keep:self.history[symbol].popleft()
            else:
                bid,ask,bq,aq=(finite(event.get(k)) for k in ('bid','ask','bid_quantity','ask_quantity'))
                if None in (bid,ask,bq,aq) or not 0<bid<=ask or min(bq,aq)<=0:self._reject(event,'INVALID_DEPTH',now);return
                old=self.books.get(symbol);flow=None
                if old:
                    flow=(bq if bid>=old['bid'] else 0)-(old['bid_quantity'] if bid<=old['bid'] else 0)-(aq if ask<=old['ask'] else 0)+(old['ask_quantity'] if ask>=old['ask'] else 0)
                row.update(bid=bid,ask=ask,bid_quantity=bq,ask_quantity=aq,spread=ask-bid,
                    order_book_imbalance=(bq-aq)/(bq+aq),order_flow_event=flow,
                    order_flow_imbalance_5s=None,
                    top5_bids=event.get('top5_bids'),top5_asks=event.get('top5_asks'))
                # Quote-event OFI, not aggressive traded volume. Complete 5s coverage required.
                flow_history=getattr(self,'flow_history',None)
                if flow_history is None:self.flow_history=defaultdict(lambda:deque(maxlen=10000))
                h=self.flow_history[symbol]
                if old is None:h.clear()
                h.append((at,flow))
                while h and h[0][0]<at-6:h.popleft()
                if h and h[0][0]<=at-5 and all(v is not None for t,v in h if t>at-5):
                    row['order_flow_imbalance_5s']=sum(v for t,v in h if t>at-5)
                self.books[symbol]=row
            self.last[key]=at
            self.pending_ticks.append(dict(row,instrument=copy.deepcopy(self.members[symbol])))
            if kind=='price' and self.members[symbol]['role']=='spot':
                for f in self.follow.values():
                    if f['index']!=symbol or f['spot_start'] is None:continue
                    elapsed=at-f['event_start_timestamp']
                    if not 0<=elapsed<=120:continue
                    movement=(row['price']-f['spot_start'])*(1 if f['direction']=='UP' else -1)
                    for p in POINTS:
                        if movement>=p:f['continuation'].setdefault(str(p),dict(seconds=elapsed,exchange_timestamp=at))
            index=self.members[symbol]['index']
            e=self.active.get(index)
            if e and at>=e['lock_until'] and at-e['last_impulse']>=self.cfg['idle_seconds']:
                e['status']='CLOSED';e['closed_at']=at;self.dirty[e['event_id']]=e;self.active.pop(index);e=None
            if not e and kind=='price' and self.members[symbol]['role']=='option':
                contracts=self._contracts(index,at)
                if contracts and symbol in contracts[2] and contracts[2][symbol]['offset']==0:
                    change=self._change(symbol,at,5)
                    if change is not None and change>=self.cfg[index.lower()]['thresholds'][0]:e=self._begin(symbol,at,contracts)
            if e and (symbol in e['locked_contracts'] or symbol in (index,e['future_symbol'])):self._enrich(e,at)
            if kind=='price' and self.members[symbol]['role']=='option':self.latest_metrics[symbol]=self.metrics(symbol,at)
            self._outcomes(now)

    def _reject(self,event,reason,now):
        self.rejected+=1
        row=dict(symbol=event['symbol'],kind=event['kind'],exchange_timestamp=finite(event.get('provider_at')),
            receive_timestamp=finite(event.get('receive_at')) or now,processing_timestamp=now,generation=self.generation,
            accepted=False,rejection_reason=reason,instrument=copy.deepcopy(self.members[event['symbol']]),
            research_config_sha256=self.config_sha256,
            raw_exchange_timestamp=finite(event.get('raw_exchange_timestamp')),
            source_timestamp_unit=event.get('source_timestamp_unit','UNKNOWN'))
        for field in ('price','bid','ask','bid_quantity','ask_quantity',*FIELDS):row[field]=finite(event.get(field))
        self.pending_ticks.append(row)

    def _outcomes(self, now):
        if int(now)==self.outcome_second:return
        self.outcome_second=int(now)
        for event_id,e in list(self.follow.items()):
            sign=1 if e['direction']=='UP' else -1;start=e['event_start_timestamp']
            spot_rows=[r for r in self.history.get(e['index'],()) if start<=r['exchange_timestamp']<=start+120]
            done=e.setdefault('completed_horizons',[])
            for horizon in HORIZONS:
                # Wait one extra second for a genuine endpoint tick; never interpolate subsecond data.
                if horizon in done or now<start+horizon+1:continue
                rows=[r for r in spot_rows if r['exchange_timestamp']<=start+horizon]
                sample=next((r for r in spot_rows if start+horizon<=r['exchange_timestamp']<=start+horizon+1),None)
                path=[dict(exchange_timestamp=start),*rows]
                complete=bool(sample and e['spot_start'] is not None and
                    all(b['exchange_timestamp']-a['exchange_timestamp']<=self.cfg['fresh_seconds'] for a,b in zip(path,path[1:])) and
                    (rows[-1]['exchange_timestamp'] if rows else start)+self.cfg['fresh_seconds']>=start+horizon)
                moves=[(r['price']-e['spot_start'])*sign for r in rows] if e['spot_start'] is not None else []
                fut_rows=self.history.get(e['future_symbol'],())
                future=next((r for r in fut_rows if start+horizon<=r['exchange_timestamp']<=start+horizon+1),None)
                future_path=[dict(exchange_timestamp=start),*[r for r in fut_rows if start<=r['exchange_timestamp']<=start+horizon]]
                future_complete=bool(future and e['future_start'] is not None and
                    all(b['exchange_timestamp']-a['exchange_timestamp']<=self.cfg['fresh_seconds'] for a,b in zip(future_path,future_path[1:])) and
                    future_path[-1]['exchange_timestamp']+self.cfg['fresh_seconds']>=start+horizon)
                outcome=dict(horizon_seconds=horizon,status='OBSERVED' if complete else 'UNKNOWN',
                    endpoint_exchange_timestamp=sample['exchange_timestamp'] if sample else None,
                    endpoint_delay_seconds=sample['exchange_timestamp']-start-horizon if sample else None,
                    spot_change=sample['price']-e['spot_start'] if complete else None,
                    futures_change=future['price']-e['future_start'] if future_complete else None,
                    maximum_favorable_move=max([0,*moves]) if complete else None,
                    maximum_adverse_move=max([0,*[-v for v in moves]]) if complete else None,
                    continuation={str(p): (str(p) in e['continuation'] and e['continuation'][str(p)]['seconds']<=horizon) if complete else None for p in POINTS},
                    data_gap=e['data_gap'])
                self.pending_outcomes[(event_id,horizon)]=outcome;done.append(horizon)
            if 120 in done:
                e['followup_status']='COMPLETE' if not e['data_gap'] else 'DATA_GAP';self.follow.pop(event_id)
            self.dirty[event_id]=e

    def tick(self):
        """One-second observed-state snapshots, explicitly aged; no generated prices."""
        if not self.cfg['enabled']:return
        with self.lock:
            now=self.clock().timestamp();second=int(now);self._outcomes(now)
            for index,e in list(self.active.items()):
                if now>=e['lock_until'] and now-e['last_impulse']>=self.cfg['idle_seconds']:
                    e.update(status='CLOSED',closed_at=now);self.dirty[e['event_id']]=e;self.active.pop(index)
            if second!=self.snapshot_second:
                self.snapshot_second=second
                for index in ('NIFTY','SENSEX'):
                    at=self._sample(index,now)
                    if at and self.day:
                        e=self.active.get(index)
                        contracts=e['locked_contracts'] if e else (self._contracts(index,now) or (None,None,{}))[2]
                        symbols=[index,*contracts,*[s for s,r in self.members.items() if r['index']==index and r['role'] in ('future','future_candidate')]]
                        row=dict(symbol=index,kind='snapshot',exchange_timestamp=None,receive_timestamp=now,
                            processing_timestamp=now,generation=self.generation,observations={s:copy.deepcopy(self._sample(s,now)) for s in symbols},
                            research_config_sha256=self.config_sha256,
                            metrics={s:self.metrics(s,now) for s in contracts},snapshot_time=now,
                            semantics='LAST_OBSERVED_WITH_SOURCE_TIMESTAMPS_NOT_NEW_TICKS')
                        self.pending_ticks.append(row)

    def flush(self):
        """Only supervisor touches SQLite. Failure retains buffers for bounded retry."""
        with self.flush_lock:
            with self.lock:
                ticks=self.pending_ticks;events=copy.deepcopy(self.dirty);outcomes=copy.deepcopy(self.pending_outcomes)
                generation=self.generation
                if not ticks and not events and not outcomes:return
                self.pending_ticks=[];self.dirty={};self.pending_outcomes={}
            try:
                with self.store.transaction() as db:
                    self._write(db,ticks,events,outcomes,generation)
            except Exception:
                with self.lock:
                    self.pending_ticks=ticks+self.pending_ticks
                    self.dirty={**events,**self.dirty}
                    self.pending_outcomes={**outcomes,**self.pending_outcomes}
                raise
            self.failure=None

    def _write(self,db,ticks,events,outcomes,generation):
        for row in ticks:
            day=datetime.fromtimestamp(row['receive_timestamp'],IST).date().isoformat()
            db.execute('INSERT INTO premium_impulse_ticks(trade_date,exchange_timestamp,receive_timestamp,processing_timestamp,generation,symbol,kind,body) VALUES(?,?,?,?,?,?,?,?)',
                (day,row['exchange_timestamp'],row['receive_timestamp'],row['processing_timestamp'],row.get('generation',generation),row['symbol'],row['kind'],json.dumps(row,allow_nan=False)))
        for e in events.values():
            db.execute('INSERT OR REPLACE INTO premium_impulse_events VALUES(?,?,?,?,?)',
                (e['event_id'],e['trade_date'],e['index'],e['event_start_timestamp'],json.dumps(e,allow_nan=False)))
        for (event_id,horizon),body in outcomes.items():
            db.execute('INSERT OR REPLACE INTO premium_impulse_outcomes VALUES(?,?,?)',
                       (event_id,horizon,json.dumps(body,allow_nan=False)))

    def maintenance(self):
        try:
            self.tick();self.flush()
            now=self.clock().astimezone(JST)
            if now.strftime('%H:%M')>=self.cfg['report_after_jst']:
                day=now.astimezone(IST).date().isoformat()
                days=self.store.read('SELECT DISTINCT trade_date FROM premium_impulse_events WHERE trade_date<=? AND trade_date NOT IN (SELECT trade_date FROM daily_research_summary)',(day,))
                for row in days:self.report(row['trade_date'],persist=True)
                rows=self.store.read('SELECT trade_date FROM daily_research_summary WHERE trade_date=?',(day,))
                if not rows:self.report(day,persist=True)
        except Exception:
            with self.lock:
                self.failure='RESEARCH_STORAGE_OR_SUMMARY_UNAVAILABLE'
                if len(self.pending_ticks)>20000:
                    self.pending_ticks=self.pending_ticks[-20000:];self.reset('STORAGE_OVERFLOW')

    def public(self):
        with self.lock:return dict(mode='MONITOR_ONLY',enabled=self.cfg['enabled'],trading_enabled=False,
            broker_writes=False,active_events={i:dict(event_id=e['event_id'],locked_expiry=e['locked_expiry'],
            locked_atm_strike=e['locked_atm_strike'],thresholds_crossed=copy.deepcopy(e['thresholds_crossed'])) for i,e in self.active.items()},
            following_events=len(self.follow),rejected_ticks=self.rejected,gaps=self.gap_count,
            pending_ticks=len(self.pending_ticks),failure=self.failure)

    def report(self, day, *, persist=False):
        self.flush()
        events=[json.loads(r['body']) for r in self.store.read('SELECT body FROM premium_impulse_events WHERE trade_date=?',(day,))]
        outcomes={(r['event_id'],r['horizon']):json.loads(r['body']) for r in self.store.read(
            'SELECT o.* FROM premium_impulse_outcomes o JOIN premium_impulse_events e ON e.event_id=o.event_id WHERE e.trade_date=?',(day,))}
        groups=defaultdict(list)
        for index in ('NIFTY','SENSEX'):
            for kind in ('CE','PE'):
                for threshold in self.cfg[index.lower()]['thresholds']:
                    for combo in ('impulse','futures','opposite','neighbours','order_flow','all'):
                        groups[(index,kind,str(threshold),'ALL',combo)]=[]
        for e in events:
            dte=str(e['dte']) if e['dte']<3 else '3+'
            for threshold in e['thresholds_crossed']:
                for condition in ('ALL','IST:'+e['time_bucket_ist'],'DTE:'+dte,'IV:UNKNOWN'):
                    for combo in ('impulse','futures','opposite','neighbours','order_flow','all'):
                        # Signal state at trigger is frozen, not hindsight-selected confirmation.
                        rel=e['trigger_relationships'];sign=1 if e['direction']=='UP' else -1
                        f=rel['futures']['change_5s'];o=rel['opposite_change'];n=rel['number_of_strikes_confirming_direction']
                        b=rel['neighbours'][e['symbol']]['book'];ofi=b.get('order_flow_imbalance_5s') if b else None
                        signals=dict(futures=f*sign>0 if f is not None else None,opposite=o<0 if o is not None else None,
                            neighbours=n>=3 if n is not None else None,order_flow=ofi>0 if ofi is not None else None)
                        eligible=combo=='impulse' or (all(v is True for v in signals.values()) if combo=='all' else signals[combo] is True)
                        if eligible:groups[(e['index'],e['option_type'],threshold,condition,combo)].append(e)
        result=[]
        for key,rows in sorted(groups.items()):
            metrics={}
            for horizon in (5,10,30,60):
                observed=[outcomes[(e['event_id'],horizon)] for e in rows if (e['event_id'],horizon) in outcomes and outcomes[(e['event_id'],horizon)]['status']=='OBSERVED']
                points={}
                for p in (5,10,15,20):
                    wins=sum(o['continuation'][str(p)] is True for o in observed);n=len(observed)
                    rate=wins/n if n else None;ci=None
                    if n:
                        z=1.96;den=1+z*z/n;center=(rate+z*z/(2*n))/den;half=z*math.sqrt(rate*(1-rate)/n+z*z/(4*n*n))/den
                        ci=[100*(center-half),100*(center+half)]
                    points[str(p)]=dict(known=n,continued=wins,percent=100*rate if rate is not None else None,
                        wilson_95_percent=ci,status='EXPLORATORY' if n>=self.cfg['minimum_report_samples'] else 'INSUFFICIENT_SAMPLES')
                favored=[o['maximum_favorable_move'] for o in observed];adverse=[o['maximum_adverse_move'] for o in observed]
                metrics[str(horizon)]=dict(known=len(observed),unknown=len(rows)-len(observed),continuation=points,
                    average_favorable=statistics.mean(favored) if favored else None,median_favorable=statistics.median(favored) if favored else None,
                    average_adverse=statistics.mean(adverse) if adverse else None,median_adverse=statistics.median(adverse) if adverse else None)
            flags={}
            for flag in ('underlying_flat','single_strike_moves','both_ce_pe_rise','spread_widens','premium_reverses'):
                known=[e.get('early_false_flags',{}).get(flag) for e in rows if isinstance(e.get('early_false_flags',{}).get(flag),bool)]
                flags[flag]=dict(known=len(known),flagged=sum(known),percent=100*sum(known)/len(known) if known else None)
            union=[]
            for e in rows:
                f=e.get('early_false_flags',{})
                if any(v is True for v in f.values()):union.append(True)
                elif len(f)==5 and all(v is False for v in f.values()):union.append(False)
            false_rate=dict(known=len(union),flagged=sum(union),percent=100*sum(union)/len(union) if union else None)
            result.append(dict(index=key[0],option_type=key[1],threshold=key[2],condition=key[3],combination=key[4],events=len(rows),outcomes=metrics,possible_false_signals=flags))
            result[-1]['possible_false_signal_rate']=false_rate
        summary=dict(mode='MONITOR_ONLY',trade_date=day,events=len(events),groups=result,
            minimum_report_samples=self.cfg['minimum_report_samples'],thresholds=copy.deepcopy({i:self.cfg[i.lower()] for i in ('NIFTY','SENSEX')}),
            warning='Observed continuation is not trade profitability or an optimized threshold. Unknown outcomes excluded, denominators shown.',
            iv_regime='UNKNOWN_NO_CALIBRATED_BASELINE',time_zone='Asia/Kolkata',broker_writes=False)
        if persist:
            with self.store.transaction() as db:db.execute('INSERT OR REPLACE INTO daily_research_summary VALUES(?,?,?)',
                (day,self.clock().timestamp(),json.dumps(summary,allow_nan=False)))
        return summary


def main():
    import argparse
    from .store import Store
    parser=argparse.ArgumentParser(description='Private monitor-only research inspection; never orders')
    parser.add_argument('--database',required=True)
    parser.add_argument('--day',default=datetime.now(IST).date().isoformat())
    parser.add_argument('--events',action='store_true')
    parser.add_argument('--replay-to',help='New private SQLite path; replay stored ticks without network')
    args=parser.parse_args();store=Store(args.database)
    if args.replay_to:
        destination=Path(args.replay_to).resolve()
        if destination.exists() or destination==store.path:raise ValueError('NEW_REPLAY_DATABASE_REQUIRED')
        clock=[datetime.now(timezone.utc)];replay=PremiumImpulseMonitor(Store(destination),clock=lambda:clock[0])
        generation=None
        rows=store.read('SELECT * FROM premium_impulse_ticks WHERE trade_date=? ORDER BY id',(args.day,))
        for r in rows:
            raw=json.loads(r['body']);clock[0]=datetime.fromtimestamp(r['processing_timestamp'],timezone.utc)
            cfg_sha=raw.get('research_config_sha256')
            if cfg_sha and cfg_sha!=replay.config_sha256:
                saved=store.meta('premium-research-config-'+cfg_sha,None)
                if saved is None:raise ValueError('RECORDED_RESEARCH_CONFIG_MISSING')
                replay.reset('CONFIGURATION_CHANGE');replay.cfg=config(overrides=saved);replay.config_sha256=cfg_sha
                replay.store.set_meta('premium-research-config-'+cfg_sha,saved)
            if generation is not None and generation!=r['generation']:replay.reset('RECORDED_RECONNECT')
            generation=r['generation']
            if r['kind']=='snapshot':replay.tick();continue
            if 'instrument' not in raw:continue
            replay.configure({r['symbol']:raw['instrument']})
            replay.ingest(dict(raw,provider_at=r['exchange_timestamp'],receive_at=r['receive_timestamp']))
        replay.flush();print(json.dumps(replay.report(args.day,persist=True),indent=2))
    elif args.events:
        print(json.dumps([json.loads(r['body']) for r in store.read('SELECT body FROM premium_impulse_events WHERE trade_date=?',(args.day,))],indent=2))
    else:
        # Inspection must not mark the running monitor interrupted; skip its constructor.
        reader=object.__new__(PremiumImpulseMonitor);reader.store=store;reader.cfg=config();reader.flush=lambda:None
        print(json.dumps(reader.report(args.day),indent=2))


if __name__=='__main__':main()
