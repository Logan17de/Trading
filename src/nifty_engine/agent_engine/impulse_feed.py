"""Pinned Groww price/depth callbacks, isolated from broker orders and REST polling."""
import asyncio
import csv
import io
import os
from pathlib import Path
import queue
import statistics
import threading
import copy
from datetime import datetime, timedelta, timezone

from .impulse import ImpulseDetector, finite
from .impulse_research import PremiumImpulseMonitor, config as research_config
from .pc_control import JST
from .contracts import IST, EXCHANGES
from .dashboard import clean_date, download_instrument_text, load_json

MAJOR=('RELIANCE','HDFCBANK','ICICIBANK','INFY','BHARTIARTL')


def universe(text,snapshot,now):
    """Exact current master tokens; sampled major-stock breadth, not full index weights."""
    day=now.astimezone(JST).date().isoformat();rows=[]
    for r in csv.DictReader(io.StringIO(text)):
        if r.get('underlying_symbol') in ('NIFTY','SENSEX') and r.get('segment')=='FNO' or r.get('trading_symbol') in MAJOR and r.get('exchange')=='NSE' and r.get('segment')=='CASH':
            r=dict(r);r['expiry']=clean_date(r.get('expiry_date'));rows.append(r)
    result={}
    for index in ('NIFTY','SENSEX'):
        result[index]=dict(index=index,role='spot',exchange=EXCHANGES[index],segment='CASH',exchange_token='NIFTY' if index=='NIFTY' else '1')
        fut=[r for r in rows if r.get('underlying_symbol')==index and r.get('exchange')==EXCHANGES[index] and r.get('instrument_type')=='FUT' and r['expiry'] and r['expiry']>=day]
        if fut:
            for n,r in enumerate(sorted(fut,key=lambda r:r['expiry'])[:3]):
                result[r['trading_symbol']]=dict(r,index=index,role='future' if n==0 else 'future_candidate')
        spot=(snapshot.get('probes',{}).get(index+'_quote',{}).get('value') or {}).get('last_price')
        opts=[r for r in rows if r.get('underlying_symbol')==index and r.get('exchange')==EXCHANGES[index] and r['trading_symbol'].endswith(('CE','PE')) and r['expiry'] and r['expiry']>=day]
        if opts and finite(spot) and spot>0:
            expiry=min(r['expiry'] for r in opts);opts=[r for r in opts if r['expiry']==expiry]
            strikes=sorted({float(r['strike_price']) for r in opts});atm=min(strikes,key=lambda s:abs(s-spot));at=strikes.index(atm)
            for r in opts:
                offset=strikes.index(float(r['strike_price']))-at
                if abs(offset)<=3:
                    result[r['trading_symbol']]=dict(r,index=index,role='option',offset=offset,strike=float(r['strike_price']),option_type=r['trading_symbol'][-2:])
        for r in rows:
            if r.get('trading_symbol') in MAJOR and r.get('segment')=='CASH':
                result[index+'@'+r['trading_symbol']]=dict(r,index=index,role='constituent')
    return {s:r for s,r in result.items() if r.get('exchange_token')}


def timestamp(raw):
    value=finite(raw)
    if value is None:return None
    return value/1000 if value>=1e12 else value if value>=1e9 else None


def packet(meta,data,registry):
    """Read just the changed topic, never re-ingest all cached SDK values."""
    physical=(meta.get('exchange'),meta.get('segment'),str(meta.get('feed_key')))
    row=data.get(physical[0],{}).get(physical[1],{}).get(physical[2])
    if not isinstance(row,dict):return []
    at=timestamp(row.get('tsInMillis'));kind='depth' if meta.get('feed_type')=='market_depth' else 'price'
    event=dict(kind=kind,provider_at=at,raw_exchange_timestamp=finite(row.get('tsInMillis')),
        source_timestamp_unit='milliseconds' if finite(row.get('tsInMillis')) and finite(row.get('tsInMillis'))>=1e12 else 'seconds' if at else 'UNKNOWN')
    if kind=='price':
        event.update(price=row.get('ltp',row.get('value')),volume=row.get('volume'),
            open_interest=row.get('openInterest'),iv=row.get('iv'),delta=row.get('delta'),
            gamma=row.get('gamma'),theta=row.get('theta'),vega=row.get('vega'))
    else:
        buy=row.get('buyBook') or {};sell=row.get('sellBook') or {}
        bids=[r for r in buy.values() if finite(r.get('price')) and finite(r.get('qty'))]
        asks=[r for r in sell.values() if finite(r.get('price')) and finite(r.get('qty'))]
        if not bids or not asks:return []
        b=max(bids,key=lambda r:float(r['price']));a=min(asks,key=lambda r:float(r['price']))
        event.update(bid=b['price'],ask=a['price'],bid_quantity=b['qty'],ask_quantity=a['qty'],
            top5_bids=[dict(price=finite(r['price']),quantity=finite(r['qty'])) for r in sorted(bids,key=lambda r:float(r['price']),reverse=True)[:5]],
            top5_asks=[dict(price=finite(r['price']),quantity=finite(r['qty'])) for r in sorted(asks,key=lambda r:float(r['price']))[:5]])
    return [dict(event,symbol=s) for s in registry.get(physical,())]


def baseline_from_candles(payload,now):
    """Only prior-session one-minute actual volumes; never synthesize a baseline."""
    buckets={};today=now.astimezone(JST).date()
    for row in payload.get('candles',[])[:15000]:
        if not isinstance(row,list) or len(row)<6:continue
        try:
            at=datetime.fromisoformat(row[0]) if isinstance(row[0],str) else datetime.fromtimestamp(timestamp(row[0]),timezone.utc)
            if at.tzinfo is None:at=at.replace(tzinfo=IST)
            at=at.astimezone(JST);volume=finite(row[5])
            if at.date()>=today or volume is None or volume<=0 or at.second!=0:continue
            buckets.setdefault(at.strftime('%H:%M'),{}).setdefault(at.date().isoformat(),volume)
        except (ValueError,TypeError,OverflowError):continue
    return {minute:dict(days=len(days),volume=statistics.median(days.values())) for minute,days in buckets.items()}


class StreamingImpulse:
    def __init__(self,root,store,*,clock=lambda:datetime.now(timezone.utc)):
        self.root=Path(root);self.store=store;self.clock=clock;self.market=None
        self.feed=None;self.detector=None;self.registry={};self.queue=queue.Queue(maxsize=4096)
        self.stop=threading.Event();self.lock=threading.RLock();self.generation=0
        self.changed=threading.Condition(self.lock)
        self.status='WAITING_FOR_MARKET_AUTH';self.failure=None;self.dropped=0;self.connected_at=None
        self.credentials=[];self.threads=[]
        self.confirmations=store.meta('premium-impulse-events',[])[:50];self.confirmed_keys=set()
        self.dirty=False;self.master_text=None;self.last_universe_refresh=0
        self.reconfigure_requested=False
        self.next_connect_at=0
        self.research=PremiumImpulseMonitor(store,cfg=research_config(self.root/'config/premium_impulse_monitor.json'),clock=clock)

    def start(self):
        for target in (self._process,self._supervise):
            t=threading.Thread(target=target,daemon=True);t.start();self.threads.append(t)

    def attach(self,market):
        with self.lock:self.market=market

    def detach(self):
        with self.lock:self.market=None
        self._disconnect()

    def public(self):
        with self.lock:
            value=self.detector.public() if self.detector else dict(indices={},event_driven=True,broker_writes=False)
            return dict(value,transport_status=self.status,failure=self.failure,queue_dropped=self.dropped,
                subscriptions=len(self.registry),connected_at=self.connected_at,
                format='trading-impulse-v1',recent_confirmations=self.confirmations[-5:],research=self.research.public())

    def next_view(self):
        with self.changed:
            self.changed.wait(timeout=3)
            return self.public()

    def _process(self):
        while not self.stop.is_set():
            try:generation,event=self.queue.get(timeout=.5)
            except queue.Empty:continue
            with self.lock:
                if generation!=self.generation or self.detector is None:continue
                self.detector.ingest(event)
                try:self.research.ingest(event)
                except Exception:
                    self.research.failure='RESEARCH_PACKET_PROCESSING_UNAVAILABLE'
                    self.research.reset('PROCESSING_FAULT')
                index=self.detector.universe[event['symbol']]['index']
                report=self.detector.reports.get(index,{})
                candidate=self.detector.candidates.get(index)
                if report.get('status')=='BREAKOUT_CONFIRMED' and candidate:
                    key=(index,candidate['symbol'],candidate['at'])
                    if key not in self.confirmed_keys:
                        self.confirmed_keys.add(key)
                        if len(self.confirmed_keys)>256:self.confirmed_keys.remove(min(self.confirmed_keys,key=lambda k:k[2]))
                        self.confirmations=(self.confirmations+[dict(copy.deepcopy(report),index=index,confirmed_at=self.clock().isoformat())])[-50:]
                        self.dirty=True
                self.changed.notify_all()

    def _callback(self,meta):
        try:
            with self.lock:
                if self.feed is None:return
                getter=self.feed.get_market_depth if meta.get('feed_type')=='market_depth' else self.feed.get_index_value if meta.get('feed_type')=='index_value' else self.feed.get_ltp
                events=packet(meta,getter(),self.registry);generation=self.generation
            received=self.clock().timestamp()
            for event in events:
                event['receive_at']=received
                self.queue.put_nowait((generation,event))
        except queue.Full:
            self.dropped+=1
            if self.detector:self.detector.reset()
            self.research.reset('QUEUE_OVERFLOW')
        except Exception:self.failure='STREAM_PACKET_REJECTED'

    def _private_credentials(self):
        """SDK otherwise writes socket credentials into immutable package files."""
        from growwapi.common import files
        import tempfile
        directory=self.root/'.agent-state/feed-auth';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(directory,0o700)
        def save(value):
            fd,name=tempfile.mkstemp(prefix='socket-',suffix='.creds',dir=directory)
            os.chmod(name,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(value)
            self.credentials.append(Path(name));return name
        # Retained for SDK reconnects; all credential writes stay in private state.
        files.generate_token_file=save;files.generate_seed_file=save

    def _connect(self,market):
        from growwapi import GrowwFeed
        text=download_instrument_text();self.master_text=text;snapshot=load_json(self.root/'.agent-state/dashboard-captures/market-check-oracle-live.json',8_000_000)
        members=universe(text,snapshot,self.clock())
        registry={}
        for symbol,r in members.items():registry.setdefault((r['exchange'],r['segment'],str(r['exchange_token'])),[]).append(symbol)
        # October 6 provider 307 points from the SDK's trailing slash to this
        # exact same-origin path. Request it directly; never follow auth redirects.
        market.groww._GROWW_GENERATE_SOCKET_TOKEN_URL='https://api.groww.in/v1/api/apex/v1/socket/token/create'
        self._private_credentials();feed=GrowwFeed(market.groww)
        with self.lock:
            self.generation+=1;self.registry=registry;self.detector=ImpulseDetector(members,clock=self.clock);self.feed=feed
            self.research.configure(members,replace=True)
        spots=[];prices=[];depth=[]
        for key,symbols in registry.items():
            r=members[symbols[0]];subscription=dict(exchange=key[0],segment=key[1],exchange_token=key[2])
            if r['role']=='spot':spots.append(subscription)
            else:prices.append(subscription)
            if r['role'] in ('future','future_candidate','option'):depth.append(subscription)
        feed.subscribe_index_value(spots,on_data_received=self._callback)
        if prices:feed.subscribe_ltp(prices,on_data_received=self._callback)
        if depth:feed.subscribe_market_depth(depth,on_data_received=self._callback)
        self.connected_at=self.clock().isoformat();self.status='SUBSCRIBED_WAITING_FOR_EVENTS';self.failure=None
        self.last_universe_refresh=self.clock().timestamp()
        threading.Thread(target=self._baseline,args=(market,members,self.detector),daemon=True).start()

    def _baseline(self,market,members,detector):
        for symbol,r in members.items():
            if r['role']!='future':continue
            day=self.clock().astimezone(IST).date();key='impulse-volume-'+symbol+'-'+day.isoformat()
            rows=self.store.meta(key,None)
            if rows is None:
                try:
                    market.limiter.wait()
                    raw=market.groww.get_historical_candles(exchange=r['exchange'],segment='FNO',groww_symbol=r['groww_symbol'],
                        start_time=f'{day-timedelta(days=14)} 09:15:00',end_time=f'{day-timedelta(days=1)} 15:30:00',candle_interval='1minute',timeout=5)
                    rows=baseline_from_candles(raw,self.clock());self.store.set_meta(key,rows)
                except Exception:continue
            with detector.lock:
                detector.baseline.update({(r['index'],minute):value for minute,value in rows.items()})

    def _disconnect(self):
        with self.lock:
            feed=self.feed;self.feed=None;self.registry={};self.generation+=1
            if self.detector:self.detector.reset()
            self.research.reset()
        if feed:
            # Pinned SDK has no public close. Close its own NATS loop/client, never shared services.
            try:
                client=feed._nats_client
                asyncio.run_coroutine_threadsafe(client._socket.close(),client._loop).result(timeout=3)
                client._loop.call_soon_threadsafe(client._loop.stop)
                GrowwFeed=type(feed);GrowwFeed._nats_clients.pop(feed._client_key,None)
            except Exception:pass
        for p in self.credentials:
            try:p.unlink(missing_ok=True)
            except OSError:pass
        self.credentials=[]

    def _refresh_options(self):
        snapshot=load_json(self.root/'.agent-state/dashboard-captures/market-check-oracle-live.json',8_000_000)
        members=universe(self.master_text,snapshot,self.clock());added=[]
        with self.lock:
            if not self.feed or not self.detector:return
            with self.detector.lock:
                for symbol,r in members.items():
                    if r['role']!='option' or symbol in self.detector.universe:continue
                    if len(self.registry)>=100:
                        self.reconfigure_requested=True
                        return
                    physical=(r['exchange'],r['segment'],str(r['exchange_token']))
                    self.detector.universe[symbol]=r;self.registry[physical]=[symbol]
                    added.append(dict(exchange=physical[0],segment=physical[1],exchange_token=physical[2]))
            if added:
                self.research.configure(members)
                self.feed.subscribe_ltp(added,on_data_received=self._callback)
                self.feed.subscribe_market_depth(added,on_data_received=self._callback)

    def _supervise(self):
        attached=None
        while not self.stop.is_set():
            market=self.market
            if market is None:
                if attached:self._disconnect();attached=None
                self.status='WAITING_FOR_MARKET_AUTH'
            elif market is not attached or self.reconfigure_requested:
                if self.clock().timestamp()>=self.next_connect_at:
                    self.reconfigure_requested=False
                    self._disconnect()
                    try:self._connect(market);attached=market;self.next_connect_at=0
                    except Exception:
                        self._disconnect();self.status='STREAM_CONNECTION_UNAVAILABLE';self.failure='STREAM_AUTH_OR_CONNECTION_FAILED'
                        self.next_connect_at=self.clock().timestamp()+30
            elif self.detector and self.detector.public().get('status')=='RECEIVING':self.status='RECEIVING'
            if self.feed and self.clock().timestamp()-self.last_universe_refresh>=30:
                try:self._refresh_options()
                except Exception:self.failure='ATM_REFRESH_UNAVAILABLE'
                self.last_universe_refresh=self.clock().timestamp()
            if self.dirty:
                try:
                    self.store.set_meta('premium-impulse-events',self.confirmations)
                    self.dirty=False
                except Exception:self.failure='CONFIRMATION_STORAGE_UNAVAILABLE'
            self.research.maintenance()
            self.stop.wait(1)

    def close(self):
        self.stop.set();self.detach()
        self.research.maintenance()
