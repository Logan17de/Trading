"""Bounded live display buffers; only operational trade/accounting state is durable."""
import copy
import threading
from collections import deque

from .contracts import INDICES, stamp
from .pc_control import JST


class OperationalStore:
    """Current strategy input cache is RAM only, never research/archive data.

    Order reservations, receipts, ownership, controls, capital and email outbox
    still use the original durable store. A restart must fetch new market inputs.
    """
    PREFIXES = ('normal-theta-evidence-', 'normal-theta-contracts-',
        'report-daily-features-', 'report-history-status-', 'report-expiries-',
        'report-execution-preparation')

    def __init__(self, store):
        self.durable, self.values, self.lock = store, {}, threading.RLock()

    def __getattr__(self, name): return getattr(self.durable, name)

    def meta(self, key, default=None):
        if key.startswith(self.PREFIXES):
            with self.lock: return copy.deepcopy(self.values.get(key, default))
        return self.durable.meta(key, default)

    def set_meta(self, key, value):
        if key.startswith(self.PREFIXES):
            with self.lock: self.values[key] = copy.deepcopy(value)
        else: self.durable.set_meta(key, value)


class LiveLines:
    """One session, active contracts only, at most 6,000 real prices per line."""
    def __init__(self):
        self.day, self.points, self.lock = None, {}, threading.RLock()

    def record(self, raw, now):
        from .session import observation
        value = observation(raw, now)
        records = [(i,r) for i,r in value['markets'].items() if r['ok']]
        records += [(r['symbol'], r) for r in value['options']]
        day = now.astimezone(JST).date()
        with self.lock:
            if self.day != day: self.day, self.points = day, {}
            active = set(INDICES) | {r['symbol'] for r in value['options']}
            self.points = {s:p for s,p in self.points.items() if s in active}
            for symbol,row in records:
                price = row['quote']['last_price']
                if price is None or price <= 0 or not row['received_at']: continue
                at = stamp(row['received_at'])
                if at > now or at.astimezone(JST).date() != day: continue
                points = self.points.setdefault(symbol, deque(maxlen=6000))
                if not points or at > stamp(points[-1]['at']):
                    points.append(dict(at=at.isoformat(), value=price))

    def read(self, now):
        with self.lock:
            if self.day != now.astimezone(JST).date(): return {}
            return {s:[dict(p) for p in points if stamp(p['at'])<=now] for s,points in self.points.items()}


class LivePnl:
    def __init__(self, store):
        self.store, self.day = store, None
        self.points, self.lock = deque(maxlen=6000), threading.RLock()

    def record(self, value, now):
        from .broker_pnl import public, BUCKETS
        value = public(value, now)
        if not value.get('received_at'): return
        at = stamp(value['observed_at']); day = at.astimezone(JST).date()
        with self.lock:
            if self.day != day: self.day = day; self.points.clear()
            if not self.points or at > stamp(self.points[-1]['at']):
                self.points.append(dict(at=at.isoformat(), **{b:value['buckets'][b]['total_inr'] for b in BUCKETS}))

    def series(self, now):
        with self.lock:
            if self.day != now.astimezone(JST).date(): return []
            return [dict(p) for p in self.points if stamp(p['at'])<=now]
