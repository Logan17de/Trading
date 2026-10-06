"""Event-driven, read-only premium impulse evidence. Scores are not probabilities."""
from collections import defaultdict, deque
from datetime import datetime, timezone
import math
import threading
from zoneinfo import ZoneInfo

WEIGHTS={'futures_direction':25,'breakout':20,'order_flow':20,'volume_acceleration':15,'neighbours':10,'breadth':10}
DEFAULT={'impulse_seconds':5,'confirmation_seconds':10,'breakout_seconds':60,'fresh_seconds':3,
         'confirm_score':80,'impulse_rupees':{'NIFTY':10,'SENSEX':30},
         'book_imbalance':.2,'volume_multiple':2,'breadth_fraction':.6,'baseline_days':5}


def finite(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):return None


class ImpulseDetector:
    def __init__(self,universe,*,config=None,baseline=None,clock=lambda:datetime.now(timezone.utc)):
        self.universe=universe;self.cfg={**DEFAULT,**(config or {})};self.clock=clock
        self.baseline=baseline or {};self.prices=defaultdict(lambda:deque(maxlen=4096))
        self.depth=defaultdict(lambda:deque(maxlen=4096));self.last={};self.reports={}
        self.lock=threading.RLock();self.events=0;self.rejected=0;self.sequence=0
        self.last_received=None;self.processing_ms=None
        self.provider_to_score_ms=None
        self.candidates={}

    def reset(self):
        with self.lock:
            self.prices.clear();self.depth.clear();self.last.clear();self.reports.clear();self.candidates.clear();self.last_received=None

    def ingest(self,event):
        """Called directly for each queued stream event; no timer, REST or orders."""
        import time
        start=time.monotonic();now=self.clock().timestamp()
        with self.lock:
            symbol=event.get('symbol');kind=event.get('kind');at=finite(event.get('provider_at'))
            if symbol not in self.universe or kind not in ('price','depth') or at is None or not -.5<=now-at<=self.cfg['fresh_seconds']:
                self.rejected+=1;return
            key=(symbol,kind)
            if at<=self.last.get(key,0):self.rejected+=1;return
            if at-self.last.get(key,at)>self.cfg['fresh_seconds']:
                (self.prices if kind=='price' else self.depth)[symbol].clear()
            if kind=='price':
                price=finite(event.get('price'));volume=finite(event.get('volume'))
                if volume is not None and volume<=0:volume=None
                if price is None or price<=0:self.rejected+=1;return
                self.prices[symbol].append((at,price,volume))
                if self.universe[symbol]['role']=='spot':
                    index=self.universe[symbol]['index']
                    options=[r for r in self.universe.values() if r['index']==index and r['role']=='option']
                    strikes=sorted({r['strike'] for r in options})
                    if strikes:
                        atm=min(strikes,key=lambda s:abs(s-price));i=strikes.index(atm)
                        for r in options:r['offset']=strikes.index(r['strike'])-i
                        candidate=self.candidates.get(index)
                        if candidate and candidate['option']['offset']!=0:self.candidates.pop(index,None)
            else:
                bid,ask,bq,aq=(finite(event.get(k)) for k in ('bid','ask','bid_quantity','ask_quantity'))
                if None in (bid,ask,bq,aq) or not 0<bid<=ask or min(bq,aq)<=0:self.rejected+=1;return
                history=self.depth[symbol];flow=0
                if history:
                    _,pb,pa,pbq,paq,_=history[-1]
                    # Cont et al. best-quote event OFI, distinct from static book imbalance.
                    flow=(bq if bid>=pb else 0)-(pbq if bid<=pb else 0)-(aq if ask<=pa else 0)+(paq if ask>=pa else 0)
                history.append((at,bid,ask,bq,aq,flow))
            history=(self.prices if kind=='price' else self.depth)[symbol]
            while history and history[0][0]<now-self.cfg['breakout_seconds']-self.cfg['fresh_seconds']:history.popleft()
            self.last[key]=at;self.events+=1;self.last_received=now
            self.provider_to_score_ms=round((now-at)*1000,3)
            index=self.universe[symbol]['index']
            self.reports[index]=self.evaluate(index,now);self.sequence+=1
            self.processing_ms=round((time.monotonic()-start)*1000,3)

    def _change(self,symbol,now,seconds):
        rows=self.prices.get(symbol,())
        if not rows or now-rows[-1][0]>self.cfg['fresh_seconds']:return None
        edge=now-seconds
        anchor=next((r for r in reversed(rows) if r[0]<=edge),None)
        if anchor is None or edge-anchor[0]>self.cfg['fresh_seconds']:return None
        return rows[-1][1]-anchor[1]

    def _breakout(self,symbol,now,direction):
        rows=self.prices.get(symbol,())
        if len(rows)<2 or now-rows[-1][0]>self.cfg['fresh_seconds']:return None
        edge=now-self.cfg['breakout_seconds']
        if not any(edge-self.cfg['fresh_seconds']<=r[0]<=edge for r in rows):return None
        previous=[r[1] for r in rows if edge<=r[0]<rows[-1][0]]
        if not previous:return None
        return rows[-1][1]>max(previous) if direction>0 else rows[-1][1]<min(previous)

    def _flow(self,symbol,now,direction):
        rows=self.depth.get(symbol,())
        if len(rows)<2 or now-rows[-1][0]>self.cfg['fresh_seconds']:return None
        active=[r for r in rows if r[0]>=now-self.cfg['impulse_seconds']]
        q=sum((r[3]+r[4])/2 for r in active[1:])
        if q<=0:return None
        ofi=sum(r[5] for r in active[1:])/q
        imbalance=(rows[-1][3]-rows[-1][4])/(rows[-1][3]+rows[-1][4])
        return ofi*direction>0 and imbalance*direction>=self.cfg['book_imbalance']

    def evaluate(self,index,now):
        members={s:r for s,r in self.universe.items() if r['index']==index}
        futures=[s for s,r in members.items() if r['role']=='future']
        spots=[s for s,r in members.items() if r['role']=='spot']
        impulses=[]
        for symbol,meta in members.items():
            if meta['role']=='option' and meta.get('offset')==0:
                change=self._change(symbol,now,self.cfg['impulse_seconds'])
                if change is not None and change>=self.cfg['impulse_rupees'][index]:impulses.append((change,symbol,meta))
        candidate=self.candidates.get(index)
        if candidate and now-candidate['at']>self.cfg['confirmation_seconds']:
            self.candidates.pop(index,None)
            return dict(status='CONFIRMATION_WINDOW_EXPIRED',score=None,known_points=0,signals={},broker_writes=False)
        if not candidate:
            if not impulses:return dict(status='WAITING_FOR_IMPULSE_OR_HISTORY',score=None,known_points=0,signals={},broker_writes=False)
            delta,symbol,option=max(impulses,key=lambda r:r[0])
            candidate=dict(at=self.prices[symbol][-1][0],delta=delta,symbol=symbol,option=option)
            self.candidates[index]=candidate
        symbol,option=candidate['symbol'],candidate['option'];direction=1 if option['option_type']=='CE' else -1
        signals={k:None for k in WEIGHTS}
        future=futures[0] if len(futures)==1 else None
        if future:
            change=self._change(future,now,self.cfg['impulse_seconds'])
            if change is not None:signals['futures_direction']=change*direction>0
        breakouts=[self._breakout(s,now,direction) for s in ([future] if future else [])+spots]
        if True in breakouts:signals['breakout']=True
        elif breakouts and all(r is not None for r in breakouts):signals['breakout']=False
        flows=[self._flow(symbol,now,1)]+([self._flow(future,now,direction)] if future else [])
        if True in flows:signals['order_flow']=True
        elif all(r is not None for r in flows):signals['order_flow']=False
        if future:
            rows=self.prices.get(future,());edge=now-60
            anchor=next((r for r in reversed(rows) if r[0]<=edge),None)
            minute=datetime.fromtimestamp(now,timezone.utc).astimezone(ZoneInfo('Asia/Tokyo')).strftime('%H:%M')
            baseline=self.baseline.get((index,minute))
            if rows and anchor and edge-anchor[0]<=self.cfg['fresh_seconds'] and now-rows[-1][0]<=self.cfg['fresh_seconds'] and baseline:
                delta=rows[-1][2]-anchor[2] if None not in (rows[-1][2],anchor[2]) else None
                if delta is not None and delta>=0 and baseline['days']>=self.cfg['baseline_days'] and baseline['volume']>0:
                    signals['volume_acceleration']=delta>=baseline['volume']*self.cfg['volume_multiple']
        neighbours=[]
        for offset in (-1,1):
            matches=[s for s,r in members.items() if r['role']=='option' and r.get('offset')==offset and r['option_type']==option['option_type'] and r['expiry']==option['expiry']]
            neighbours.append(self._change(matches[0],now,self.cfg['impulse_seconds']) if len(matches)==1 else None)
        if all(r is not None for r in neighbours):signals['neighbours']=all(r>0 for r in neighbours)
        constituents=[s for s,r in members.items() if r['role']=='constituent']
        changes=[self._change(s,now,self.cfg['impulse_seconds']) for s in constituents]
        if len(changes)>=5 and all(r is not None for r in changes):signals['breadth']=sum(r*direction>0 for r in changes)/len(changes)>=self.cfg['breadth_fraction']
        score=sum(WEIGHTS[k] for k,v in signals.items() if v is True)
        known=sum(WEIGHTS[k] for k,v in signals.items() if v is not None)
        # Mandatory futures + breakout + OFI + adjacent strikes; no confirmation with unknown evidence.
        confirmed=known==100 and score>=self.cfg['confirm_score'] and all(signals[k] is True for k in ('futures_direction','breakout','order_flow','neighbours'))
        status='BREAKOUT_CONFIRMED' if confirmed else 'EVIDENCE_UNKNOWN' if known<100 else 'NOT_CONFIRMED'
        if signals['futures_direction'] is False:status='FUTURES_DISAGREE_POSSIBLE_IV_OR_NOISE'
        return dict(status=status,score=score,known_points=known,maximum_points=100,
            direction='UP' if direction>0 else 'DOWN',option=symbol,impulse_rupees=round(candidate['delta'],2),
            confirmation_elapsed_ms=round((now-candidate['at'])*1000,1),confirmation_deadline_seconds=self.cfg['confirmation_seconds'],
            fast_confirmation_target_seconds=3,
            signals={k:dict(status='UNKNOWN' if v is None else 'PASS' if v else 'FAIL',points=WEIGHTS[k] if v is True else 0,weight=WEIGHTS[k]) for k,v in signals.items()},
            breadth_scope='MAJOR_CONSTITUENT_SAMPLE_EQUAL_VOTES',broker_writes=False,win_probability=None)

    def public(self):
        with self.lock:
            now=self.clock().timestamp();stale=self.last_received is None or now-self.last_received>self.cfg['fresh_seconds']
            if not stale:
                self.reports={index:self.evaluate(index,now) for index in {r['index'] for r in self.universe.values()}}
            return dict(status='STALE_OR_WAITING_FOR_STREAM' if stale else 'RECEIVING',event_driven=True,
                poll_interval_seconds=None,sequence=self.sequence,events=self.events,rejected=self.rejected,
                last_received_at=datetime.fromtimestamp(self.last_received,timezone.utc).isoformat() if self.last_received else None,
                processing_ms=self.processing_ms,confirmation_score=self.cfg['confirm_score'],
                provider_to_score_ms=self.provider_to_score_ms,
                indices={} if stale else self.reports,broker_writes=False,win_probability=None)
