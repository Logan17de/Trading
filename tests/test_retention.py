"""Synthetic-only retention checks. No Groww connection or production DB."""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from nifty_engine.agent_engine.store import Store
from nifty_engine.agent_engine.trade_audit import put
from nifty_engine.agent_engine.retention import DAYS, configure_audit_expiry, run_once, quiet_hours


def seed(tmp_path):
    store=Store(tmp_path/'retention.sqlite3')
    now=datetime.now(timezone.utc)
    old=(now-timedelta(days=75)).timestamp()
    fresh=(now-timedelta(days=3)).timestamp()
    day_old=(now-timedelta(days=75)).date().isoformat()
    day_new=(now-timedelta(days=3)).date().isoformat()
    with store.transaction() as db:
        db.executescript("""
            CREATE TABLE pc_pnl_observations(at REAL PRIMARY KEY, day TEXT, body TEXT);
            CREATE TABLE premium_impulse_ticks(id INTEGER PRIMARY KEY, trade_date TEXT, body TEXT);
            CREATE TABLE premium_impulse_events(event_id TEXT PRIMARY KEY, trade_date TEXT, body TEXT);
            CREATE TABLE premium_impulse_outcomes(event_id TEXT,horizon INTEGER,body TEXT);
            CREATE TABLE pc_observations(id TEXT PRIMARY KEY,day TEXT,at REAL,body TEXT);
            CREATE TABLE pc_bars(symbol TEXT,at REAL,body TEXT);
            CREATE TABLE pc_requests(id TEXT PRIMARY KEY,created REAL,status TEXT);
            CREATE TABLE report_iv30_days(index_name TEXT,day TEXT,body TEXT);
            CREATE TABLE daily_research_summary(trade_date TEXT PRIMARY KEY,body TEXT);
        """)
        db.execute("INSERT INTO pc_pnl_observations VALUES(?,?,?)",(old,day_old,'{}'))
        db.execute("INSERT INTO pc_pnl_observations VALUES(?,?,?)",(fresh,day_new,'{}'))
        db.execute("INSERT INTO premium_impulse_ticks VALUES(?,?,?)",(1,day_old,'{}'))
        db.execute("INSERT INTO premium_impulse_ticks VALUES(?,?,?)",(2,day_old,'{}'))
        db.execute("INSERT INTO premium_impulse_ticks VALUES(?,?,?)",(3,day_new,'{}'))
        db.execute("INSERT INTO premium_impulse_events VALUES(?,?,?)",('old',day_old,'{}'))
        db.execute("INSERT INTO premium_impulse_outcomes VALUES(?,?,?)",('old',5,'{}'))
        db.execute("INSERT INTO pc_observations VALUES(?,?,?,?)",('a',day_old,old,'{}'))
        db.execute("INSERT INTO pc_bars VALUES(?,?,?)",('NIFTY',old,'{}'))
        db.execute("INSERT INTO pc_requests VALUES(?,?,?)",('old',old,'ANALYZED'))
        db.execute("INSERT INTO pc_requests VALUES(?,?,?)",('pending',old,'QUEUED'))
        db.execute("INSERT INTO report_iv30_days VALUES(?,?,?)",('NIFTY',day_old,'{}'))
        db.execute("INSERT INTO daily_research_summary VALUES(?,?)",(day_old,'{}'))
        db.execute("INSERT INTO snapshots VALUES(?,?,?,?)",('old',old,day_old,'{}'))
        db.execute("INSERT INTO snapshots VALUES(?,?,?,?)",('linked',old,day_old,'{}'))
        db.execute("INSERT INTO jobs(id,snapshot_id,root,depth,reason,created,expires,status) VALUES(?,?,?,?,?,?,?,?)",
                   ('open','linked','run',0,'test',old,old,'QUEUED'))
        db.execute("INSERT INTO reports(id,day,payload,status) VALUES(?,?,?,?)",('delivered',day_old,'{}','ACCEPTED'))
        db.execute("INSERT INTO reports(id,day,payload,status) VALUES(?,?,?,?)",('queued',day_old,'{}','QUEUED'))
        put(db,'ORDER',dict(status='FULLY_FILLED'),at=old)
        put(db,'ORDER',dict(status='FULLY_FILLED'),at=fresh)
    store.set_meta('research-sync-tick-cursor',1)
    store.set_meta('research-sync-pnl-cursor',fresh)
    store.set_meta('oracle-backup-2026-01-01',dict(status='old_telemetry'))
    return store,old,fresh


def test_retention_removes_expired_and_preserves_unsettled(tmp_path):
    store,old,fresh=seed(tmp_path)
    configure_audit_expiry(store)
    result=run_once(store,require_quiet=False)
    assert result['days']==60 and result['total_deleted']>=10
    assert store.read('SELECT id FROM premium_impulse_ticks')==[{'id':2},{'id':3}]
    assert store.read('SELECT event_id FROM premium_impulse_events')==[]
    assert store.read('SELECT event_id FROM premium_impulse_outcomes')==[]
    assert store.read('SELECT at FROM pc_pnl_observations')==[{'at':fresh}]
    assert [r['id'] for r in store.read('SELECT id FROM pc_trade_audit')]==[2]
    assert store.read('SELECT id FROM snapshots')==[{'id':'linked'}]
    assert [r['id'] for r in store.read('SELECT id FROM pc_requests ORDER BY id')]==['pending']
    assert [r['id'] for r in store.read('SELECT id FROM reports ORDER BY id')]==['queued']
    assert store.meta('oracle-backup-2026-01-01') is None
    again=run_once(store,require_quiet=False)
    assert again['total_deleted']==0


def test_append_only_60_day_guard_is_active(tmp_path):
    store,old,fresh=seed(tmp_path)
    configure_audit_expiry(store)
    with pytest.raises(sqlite3.IntegrityError,match='AUDIT_NOT_YET_EXPIRED'):
        with store.transaction() as db:
            db.execute('DELETE FROM pc_trade_audit WHERE id=2')
    with pytest.raises(sqlite3.IntegrityError,match='AUDIT_APPEND_ONLY'):
        with store.transaction() as db:
            db.execute("UPDATE pc_trade_audit SET kind='CONTROL' WHERE id=1")
    with store.transaction() as db:
        db.execute('DELETE FROM pc_trade_audit WHERE id=1')
    assert [r['id'] for r in store.read('SELECT id FROM pc_trade_audit')]==[2]


def test_quiet_hours_and_clock_validation(tmp_path):
    store,old,fresh=seed(tmp_path)
    from zoneinfo import ZoneInfo
    jst=ZoneInfo('Asia/Tokyo')
    blocked=datetime(2026,10,14,15,tzinfo=jst)
    allowed=datetime(2026,10,14,21,tzinfo=jst)
    assert not quiet_hours(blocked) and quiet_hours(allowed)
    assert run_once(store,now=blocked)['status']=='WAITING_FOR_QUIET_HOURS'
    assert len(store.read('SELECT id FROM pc_trade_audit'))==2
    with pytest.raises(ValueError,match='CURRENT_AWARE_CLOCK'):
        run_once(store,now=datetime(2026,1,1),require_quiet=False)
