"""Append-only, private audit of engine decisions and exact broker evidence.

This module never talks to Groww and never grants execution permission.
It records only an allowlisted subset of durable engine state, atomically with
the underlying journal mutation. Raw broker IDs, references, keys and account
identities are deliberately excluded from the exported records.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from .contracts import dumps

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pc_trade_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pc_trade_audit_time ON pc_trade_audit(at,id);
"""

_MAX_EVENTS = 200
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_:.+ -]{1,110}$")
_SAFE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,78}$")


def safe(value, limit=100):
    return value[:limit] if isinstance(value, str) and len(value) <= 256 and _SAFE_TEXT.fullmatch(value) else None


def symbol(value):
    return value if isinstance(value, str) and _SAFE_SYMBOL.fullmatch(value) else None


def numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and -1e10 < value < 1e10 else None


def put(db, kind, data, *, at=None):
    if kind not in ('CONTROL','STRATEGY','EVALUATION','BASKET','DECISION','ORDER','PROTECTION'):
        raise ValueError("UNSUPPORTED_AUDIT_KIND")
    record = {k:v for k,v in data.items() if v is not None}
    if not record:
        return
    db.execute("INSERT INTO pc_trade_audit(at,kind,body) VALUES(?,?,?)",
               (float(time.time() if at is None else at), kind, dumps(record)))


def projection(key, body):
    if not isinstance(body, dict):
        return None
    if key == 'premium-algo-intent':
        if type(body.get('enabled')) is not bool:
            return None
        return 'CONTROL', dict(status='ON' if body['enabled'] else 'OFF',source='OWNER_INTENT')
    if key == 'strategy-groups-v1':
        enabled=body.get('enabled')
        if not isinstance(enabled, dict):
            return None
        return 'STRATEGY', dict(status='PREFERENCE',normal_theta=enabled.get('normal_theta') is True,
                                research=enabled.get('research') is True)
    if key == 'premium-executor-v1':
        candidate=body.get('candidate') or {}
        return 'BASKET', dict(status=safe(body.get('phase')),strategy=safe(body.get('strategy')),
                              index=safe(candidate.get('index')),spread=safe(candidate.get('spread_type')),
                              expiry=safe(candidate.get('expiry')),quantity=numeric(candidate.get('quantity')),
                              lots=numeric(candidate.get('lots')),
                              exit_reason=safe(body.get('exit_reason')))
    if key == 'premium-executor-status':
        return 'DECISION',dict(status=safe(body.get('reason')),stage=safe(body.get('stage')))
    if key.startswith('prepared-order-'):
        order=body.get('order') or {}
        return 'ORDER',dict(status=safe(body.get('status')),symbol=symbol(order.get('trading_symbol')),
                             side=safe(order.get('transaction_type')),quantity=numeric(order.get('quantity')),
                             price_inr=numeric(order.get('price')),filled_quantity=numeric(body.get('filled_quantity')),
                             average_fill_inr=numeric(body.get('average_fill_price')),
                             broker_status=safe(body.get('broker_status')),
                             cancel_requested=body.get('cancel_attempted') is True,
                             broker_acknowledged=bool(body.get('broker_id')),
                             evidence='BROKER_READBACK' if body.get('checked_at') and body.get('broker_id') else 'JOURNAL_STATE')
    if key.startswith('premium-gtt-'):
        request=body.get('request') or {}
        order=request.get('order') or {}
        return 'PROTECTION',dict(status=safe(body.get('status')),symbol=symbol(request.get('trading_symbol')),
                                  side='BUY',quantity=numeric(request.get('quantity')),
                                  trigger_price_inr=numeric(float(request['trigger_price'])) if isinstance(request.get('trigger_price'), str) and re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,8})?',request['trigger_price']) else numeric(request.get('trigger_price')),
                                  limit_price_inr=numeric(float(order['price'])) if isinstance(order.get('price'),str) and re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,8})?',order['price']) else numeric(order.get('price')),
                                  parent_acknowledged=bool(body.get('smart_id')),
                                  cancel_requested=body.get('cancel_attempted') is True,
                                  evidence='BROKER_READBACK' if body.get('checked_at') else 'JOURNAL_STATE')
    if key == 'normal-theta-evaluations':
        values=body.get('indices') or {}
        if not isinstance(values, dict):
            return None
        results={}
        for i in ('NIFTY','SENSEX'):
            item=values.get(i) or {}
            scan=item.get('basket_scan') or {}
            reasons=item.get('reasons') or []
            candidate=item.get('candidate') or {}
            results[i]=dict(status=safe(item.get('status')),regime=safe(item.get('regime')),
                           reasons=[safe(r) for r in reasons[:5] if safe(r)],
                           scan=safe(scan.get('status')),candidate_present=bool(candidate))
        return 'EVALUATION',results
    return None


def change(db, key, previous, current):
    projected=projection(key,current)
    if projected is None:
        return
    old=projection(key,previous)
    if projected == old:
        return
    kind,body=projected
    if kind in ('BASKET','DECISION') and not body.get('status'):
        return
    put(db,kind,body)


def recent(store,limit=120):
    limit=min(_MAX_EVENTS,max(1,int(limit)))
    rows=store.read("SELECT id,at,kind,body FROM pc_trade_audit ORDER BY id DESC LIMIT ?",(limit,))
    events=[]
    for row in rows:
        try:
            body=json.loads(row['body'])
            if not isinstance(body,dict):
                continue
            events.append(dict(id=row['id'],at=datetime.fromtimestamp(row['at'],timezone.utc).isoformat(),
                               kind=row['kind'],**body))
        except (ValueError,TypeError,OverflowError):
            continue
    return dict(format='trading-audit-v1',status='AVAILABLE',source='PRIVATE_ORACLE_JOURNAL',
                count=len(events),events=events,read_only=True,broker_writes=False)
