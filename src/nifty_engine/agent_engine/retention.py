"""Bounded, quiet-hours retention for disposable trading observations.

Never prunes order ownership, unsettled broker receipts, protective GTT state,
owner consent, active work, credentials or unarchived raw research.  It contains
no broker API access and cannot enable orders. Records expire after 60 days;
historical backups are managed independently.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .contracts import dumps

DAYS = 60
BATCH = 1000
JST = ZoneInfo("Asia/Tokyo")
STATUS_KEY = "journal-retention-status"

# All SQL is static/allowlisted. Small transactions run off-market; there is
# no VACUUM (which could lock the multi-gigabyte production journal).
_RULES = (
    ("premium_impulse_outcomes",
     """DELETE FROM premium_impulse_outcomes WHERE rowid IN (
           SELECT o.rowid FROM premium_impulse_outcomes o
           JOIN premium_impulse_events e ON e.event_id=o.event_id
           WHERE e.trade_date < ? LIMIT ?)""", "day"),
    ("premium_impulse_events",
     """DELETE FROM premium_impulse_events WHERE event_id IN (
           SELECT e.event_id FROM premium_impulse_events e WHERE e.trade_date < ?
           AND NOT EXISTS(SELECT 1 FROM premium_impulse_outcomes o WHERE o.event_id=e.event_id)
           LIMIT ?)""", "day"),
    ("premium_impulse_ticks",
     """DELETE FROM premium_impulse_ticks WHERE id IN (
           SELECT id FROM premium_impulse_ticks WHERE trade_date < ?
           AND id <= ? ORDER BY trade_date LIMIT ?)""", "tick_cursor"),
    ("pc_pnl_observations",
     """DELETE FROM pc_pnl_observations WHERE at IN (
           SELECT at FROM pc_pnl_observations WHERE at < ?
           AND at <= ? ORDER BY at LIMIT ?)""", "pnl_cursor"),
    ("pc_trade_audit",
     """DELETE FROM pc_trade_audit WHERE id IN (
           SELECT id FROM pc_trade_audit WHERE at < ? ORDER BY at LIMIT ?)""", "seconds"),
    ("pc_observations",
     """DELETE FROM pc_observations WHERE id IN (
           SELECT id FROM pc_observations WHERE day < ? LIMIT ?)""", "day"),
    ("pc_bars",
     """DELETE FROM pc_bars WHERE rowid IN (
           SELECT rowid FROM pc_bars WHERE at < ? LIMIT ?)""", "seconds"),
    ("snapshots",
     """DELETE FROM snapshots WHERE id IN (
           SELECT s.id FROM snapshots s WHERE s.at < ?
           AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.snapshot_id=s.id
                           AND j.status IN ('QUEUED','RUNNING')) LIMIT ?)""", "seconds"),
    ("events",
     """DELETE FROM events WHERE id IN (
           SELECT id FROM events WHERE at < ? LIMIT ?)""", "seconds"),
    ("calls",
     """DELETE FROM calls WHERE id IN (
           SELECT id FROM calls WHERE at < ? LIMIT ?)""", "seconds"),
    ("report_iv30_days",
     """DELETE FROM report_iv30_days WHERE rowid IN (
           SELECT rowid FROM report_iv30_days WHERE day < ? LIMIT ?)""", "day"),
    ("daily_research_summary",
     """DELETE FROM daily_research_summary WHERE trade_date IN (
           SELECT trade_date FROM daily_research_summary WHERE trade_date < ? LIMIT ?)""", "day"),
    ("jobs",
     """DELETE FROM jobs WHERE id IN (
           SELECT id FROM jobs WHERE created < ?
           AND status NOT IN ('QUEUED','RUNNING') LIMIT ?)""", "seconds"),
    ("pc_requests",
     """DELETE FROM pc_requests WHERE id IN (
           SELECT id FROM pc_requests WHERE created < ?
           AND status NOT IN ('QUEUED','RUNNING','CLAIMED','PROCESSING') LIMIT ?)""", "seconds"),
    ("reports",
     """DELETE FROM reports WHERE id IN (
           SELECT id FROM reports WHERE day < ?
           AND status IN ('ACCEPTED','SENT','FAILED','CANCELLED','EXPIRED')
           LIMIT ?)""", "day"),
    ("old_backup_status",
     """DELETE FROM meta WHERE key IN (
           SELECT key FROM meta
           WHERE key >= 'oracle-backup-0000-00-00' AND key < 'oracle-backup-9999-99-99'
           AND substr(key,15,10) < ? LIMIT ?)""", "day"),
)


def quiet_hours(now: datetime) -> bool:
    hour=now.astimezone(JST).hour
    return hour >= 20 or hour < 11


def configure_audit_expiry(store):
    """Migrate the append-only guard: rows younger than 60 days never delete."""
    with store.transaction() as db:
        db.execute("DROP TRIGGER IF EXISTS pc_trade_audit_no_delete")
        db.execute("""CREATE TRIGGER pc_trade_audit_no_delete
            BEFORE DELETE ON pc_trade_audit
            WHEN OLD.at IS NULL OR OLD.at >= CAST(strftime('%s','now','-60 days') AS REAL)
            BEGIN SELECT RAISE(ABORT,'AUDIT_NOT_YET_EXPIRED'); END""")


def run_once(store, *, now=None, batch=BATCH, require_quiet=True):
    if now is None:
        now=datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.year < 2025:
        raise ValueError("CURRENT_AWARE_CLOCK_REQUIRED")
    if type(batch) is not int or not 1<=batch<=5000:
        raise ValueError("INVALID_RETENTION_BATCH")
    if require_quiet and not quiet_hours(now):
        return dict(status="WAITING_FOR_QUIET_HOURS",days=DAYS,broker_writes=False)
    # Leave an extra minute for the SQLite audit guard's independent clock.
    cutoff=now.astimezone(timezone.utc)-timedelta(days=DAYS,seconds=65)
    second_cutoff=cutoff.timestamp()
    day_cutoff=cutoff.date().isoformat()
    counts={}
    # Guard research captured into durable local outbox before removing raw data.
    # Outstanding uploads are retained in that outbox and must not be removed.
    with store.transaction() as db:
        tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cursors={}
        for name,key in (("tick_cursor","research-sync-tick-cursor"),
                         ("pnl_cursor","research-sync-pnl-cursor")):
            row=db.execute("SELECT body FROM meta WHERE key=?",(key,)).fetchone()
            import json
            value=json.loads(row[0]) if row else 0
            cursors[name]=value if type(value) in (int,float) and value>=0 else 0
        for name,query,kind in _RULES:
            if name != "old_backup_status" and name not in tables:
                continue
            args=((day_cutoff,cursors['tick_cursor'],batch) if kind=='tick_cursor' else
                  (second_cutoff,cursors['pnl_cursor'],batch) if kind=='pnl_cursor' else
                  (day_cutoff,batch) if kind=='day' else (second_cutoff,batch))
            db.execute(query,args)
            counts[name]=db.execute("SELECT changes()").fetchone()[0]
        status=dict(status="COMPLETE",days=DAYS,cutoff_utc=cutoff.isoformat(),
                    last_run_utc=now.astimezone(timezone.utc).isoformat(),deleted=counts,
                    total_deleted=sum(counts.values()),batch_per_table=batch,
                    protected="ACTIVE_ORDERS_OWNER_RECEIPTS_UNSYNCED_TICKS_AND_PNL",
                    broker_writes=False)
        db.execute("INSERT OR REPLACE INTO meta(key,body) VALUES(?,?)",
                   (STATUS_KEY,dumps(status)))
    return status
