"""Local, synthetic audit tests. Never contacts Groww or the Oracle executor."""
from datetime import datetime, timezone
import sqlite3

import pytest

from nifty_engine.agent_engine.store import Store
from nifty_engine.agent_engine.trade_audit import change, recent, projection


def test_atomic_audit_dedup_and_privacy(tmp_path):
    store=Store(tmp_path/'audit.sqlite3')
    order=dict(status='SUBMITTING',reference='GT-secret-ref',broker_id='SECRET_ID',
        order=dict(trading_symbol='SENSEX12345CE',transaction_type='BUY',
                   quantity=20,price=19.5,account_id='PRIVATE_ACCOUNT'))
    store.set_meta('prepared-order-opaque',order)
    store.set_meta('prepared-order-opaque',dict(order))
    assert len(recent(store)['events'])==1
    filled=dict(order,status='FULLY_FILLED',filled_quantity=20,average_fill_price=19.45,
                checked_at=datetime.now(timezone.utc).isoformat(),broker_status='EXECUTED')
    store.set_meta('prepared-order-opaque',filled)
    rows=recent(store)['events']
    assert len(rows)==2
    assert [x['status'] for x in rows]==['FULLY_FILLED','SUBMITTING']
    assert rows[0]['average_fill_inr']==19.45 and rows[0]['filled_quantity']==20
    assert rows[0]['evidence']=='BROKER_READBACK'
    assert 'SECRET_ID' not in str(rows)
    assert 'PRIVATE_ACCOUNT' not in str(rows)
    assert 'GT-secret-ref' not in str(rows)


def test_engine_phase_stop_and_owner_decisions(tmp_path):
    store=Store(tmp_path/'audit.sqlite3')
    store.set_meta('premium-algo-intent',dict(enabled=False,changed_at='2026-10-14T00:00:00Z'))
    store.set_meta('premium-algo-intent',dict(enabled=True,changed_at='2026-10-14T00:00:01Z'))
    store.set_meta('premium-executor-status',dict(reason='NO_CONFIRMED_DIRECTIONAL_TREND',at='t1'))
    store.set_meta('premium-executor-status',dict(reason='NO_CONFIRMED_DIRECTIONAL_TREND',at='t2'))
    state=dict(phase='REPORT_ENTRY_HEDGES',strategy='normal_theta',
               candidate=dict(index='SENSEX',spread_type='bear_call',quantity=20,lots=1,expiry='2026-11-10'))
    store.set_meta('premium-executor-v1',state)
    store.set_meta('premium-executor-v1',dict(state,phase='REPORT_PROTECTION'))
    store.set_meta('premium-gtt-opaque',dict(status='VERIFIED_ACTIVE',smart_id='PRIVATE_ID',
        request=dict(trading_symbol='SENSEX12345CE',quantity=20,trigger_price='20.50',
        order=dict(price='20.55')),checked_at=datetime.now(timezone.utc).isoformat()))
    events=recent(store)['events']
    assert len(events)==6
    assert events[0]['kind']=='PROTECTION'
    assert events[0]['trigger_price_inr']==20.5
    assert events[1]['status']=='REPORT_PROTECTION'
    assert any(e['kind']=='DECISION' for e in events)
    assert [e['status'] for e in events if e['kind']=='CONTROL']==['ON','OFF']
    assert 'PRIVATE_ID' not in str(events)


def test_commit_and_rollback_keep_audit_consistent(tmp_path):
    store=Store(tmp_path/'audit.sqlite3')
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            db.execute("INSERT INTO meta VALUES(?,?)",('prepared-order-rollback','{}'))
            change(db,'prepared-order-rollback',None,dict(status='SUBMITTING',order=dict(
                trading_symbol='NIFTY12345CE',transaction_type='SELL',quantity=25,price=12)))
            raise RuntimeError('failure before commit')
    assert store.meta('prepared-order-rollback') is None
    assert recent(store)['events']==[]
    store.set_meta('unrelated-key',dict(token='DO_NOT_DISPLAY',status='READY'))
    assert recent(store)['events']==[]


def test_no_broker_pnl_or_manual_positions_mislabeled_as_algo(tmp_path):
    store=Store(tmp_path/'audit.sqlite3')
    store.set_meta('normal-theta-evaluations',dict(indices={
        'NIFTY':dict(status='WAIT',regime='UNKNOWN',reasons=['OUTSIDE_1400_1830_JST']),
        'SENSEX':dict(status='WAIT',regime='UNKNOWN',reasons=['NO_CONFIRMED_DIRECTIONAL_TREND'])}))
    assert recent(store)['events'][0]['kind']=='EVALUATION'
    assert not any(k in recent(store)['events'][0] for k in ('realized_pnl','account_id','broker_id','portfolio_balance'))


def test_bounded_api_projection(tmp_path):
    store=Store(tmp_path/'audit.sqlite3')
    for i in range(225):
        store.set_meta('premium-executor-status',dict(reason='STATUS_'+str(i)))
    result=recent(store,limit=10000)
    assert len(result['events'])==200
    assert result['events'][0]['id']>result['events'][-1]['id']
    assert result['read_only'] and result['broker_writes'] is False
