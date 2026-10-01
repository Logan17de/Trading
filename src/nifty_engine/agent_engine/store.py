"""Durable at-least-once analysis jobs. No broker-side effects exist in this package."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from .contracts import IST, dumps, identity, snapshot, stamp, watch


class Store:
    def __init__(self, path: str | Path, *, shared_group=False):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("unsupported database version")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, at REAL NOT NULL, day TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS snapshot_time ON snapshots(at);
                CREATE TABLE IF NOT EXISTS watches(name TEXT PRIMARY KEY, body TEXT NOT NULL, root TEXT NOT NULL,
                    depth INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, fires INTEGER NOT NULL DEFAULT 0,
                    last_at REAL, last_value REAL, last_fire REAL, expiry REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, root TEXT NOT NULL,
                    depth INTEGER NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'QUEUED', attempts INTEGER NOT NULL DEFAULT 0,
                    lease REAL, token TEXT, result TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, at REAL NOT NULL, day TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'QUEUED', attempts INTEGER NOT NULL DEFAULT 0,
                    first_attempt REAL, next_attempt REAL NOT NULL DEFAULT 0, lease REAL, token TEXT, provider_id TEXT, error TEXT);
                PRAGMA user_version=1;
            """)
        mode = 0o660 if shared_group else 0o600
        # Another service UID may already own the shared DB. Group write permits
        # SQLite access, not chmod; avoid an unnecessary failing ownership operation.
        if self.path.stat().st_mode & 0o777 != mode:
            os.chmod(self.path, mode)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read(self, sql: str, args=()):
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(sql, args)]

    def meta(self, key: str, default=None):
        rows = self.read("SELECT body FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["body"]) if rows else default

    def set_meta(self, key: str, body):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, dumps(body)))

    def event(self, kind: str, body: dict, now: datetime):
        with self.transaction() as db:
            db.execute("INSERT INTO events(at,kind,body) VALUES(?,?,?)", (now.timestamp(), kind, dumps(body)))

    def ingest(self, value: dict, now: datetime) -> bool:
        value = snapshot(value)
        if stamp(value["observed_at"]) > now:
            raise ValueError("future snapshot")
        source = [value["source"], value["mode"]]
        with self.transaction() as db:
            old = db.execute("SELECT body FROM meta WHERE key='source'").fetchone()
            if old and json.loads(old[0]) != source:
                raise ValueError("source or mode changed; use a separate database")
            db.execute("INSERT OR IGNORE INTO meta VALUES('source',?)", (dumps(source),))
            previous = db.execute("SELECT body FROM snapshots WHERE id=?", (value["id"],)).fetchone()
            if previous:
                if previous[0] != dumps(value):
                    raise ValueError("snapshot identity collision")
                return False
            at = stamp(value["observed_at"])
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?)", (
                value["id"], at.timestamp(), at.astimezone(IST).date().isoformat(), dumps(value)))
        return True

    def latest(self):
        rows = self.read("SELECT body FROM snapshots ORDER BY at DESC, rowid DESC LIMIT 1")
        return json.loads(rows[0]["body"]) if rows else None

    @staticmethod
    def _add_watch(db, spec, now, root="operator", depth=0):
        watch(spec, now)
        if depth > 2:
            raise ValueError("follow-up depth exceeded")
        name = spec["name"] if depth == 0 else f"agent-{root[:12]}-{spec['name']}"
        existing = db.execute("SELECT body FROM watches WHERE name=?", (name,)).fetchone()
        if existing:
            if existing[0] != dumps(spec):
                raise ValueError("existing watch cannot be silently reset or changed")
            return name
        count = db.execute("SELECT COUNT(*) FROM watches WHERE enabled=1 AND expiry>?", (now.timestamp(),)).fetchone()[0]
        if count >= 24:
            raise ValueError("active watch limit reached")
        db.execute("INSERT INTO watches(name,body,root,depth,expiry) VALUES(?,?,?,?,?)", (
            name, dumps(spec), root, depth, stamp(spec["expires_at"]).timestamp()))
        return name

    def add_watch(self, spec, now):
        with self.transaction() as db:
            return self._add_watch(db, spec, now)

    def evaluate(self, now: datetime, *, freshness: int = 60, job_ttl: int = 300):
        s = self.latest()
        if s is None or not 0 <= (now - stamp(s["observed_at"])).total_seconds() <= freshness:
            return 0
        created = 0
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM watches WHERE enabled=1 AND expiry>?", (now.timestamp(),)).fetchall():
                w = json.loads(row["body"])
                m = s["markets"].get(w["instrument"])
                if m is None or m[w["metric"]] is None:
                    continue
                at, value = stamp(m["at"]).timestamp(), m[w["metric"]]
                if not 0 <= now.timestamp() - at <= freshness:
                    continue
                old_at, old_value = row["last_at"], row["last_value"]
                new_sample = old_at is None or at > old_at
                if old_at is not None and at < old_at:
                    continue
                coherent = old_at is not None and 0 < at - old_at <= freshness
                threshold = w["threshold"]
                matched = {
                    "above": value > threshold and new_sample,
                    "below": value < threshold and new_sample,
                    "cross_above": coherent and old_value <= threshold < value,
                    "cross_below": coherent and old_value >= threshold > value,
                    "interval": True,
                }[w["op"]]
                if new_sample:
                    db.execute("UPDATE watches SET last_at=?,last_value=? WHERE name=?", (at, value, row["name"]))
                cooled = row["last_fire"] is None or now.timestamp() - row["last_fire"] >= w["cooldown_seconds"]
                if not matched or not cooled or row["fires"] >= w["max_fires"]:
                    continue
                if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0] >= 24:
                    continue
                job_id = identity([row["name"], row["fires"] + 1])
                root = job_id if row["depth"] == 0 else row["root"]
                db.execute("INSERT OR IGNORE INTO jobs(id,snapshot_id,root,depth,reason,created,expires) VALUES(?,?,?,?,?,?,?)", (
                    job_id, s["id"], root, row["depth"], row["name"], now.timestamp(),
                    min(now.timestamp() + job_ttl, stamp(s["observed_at"]).timestamp() + job_ttl)))
                db.execute("UPDATE watches SET fires=fires+1,last_fire=? WHERE name=?", (now.timestamp(), row["name"]))
                created += 1
        return created

    def claim(self, now: datetime, *, daily_cap=24, timeout=120):
        with self.transaction() as db:
            db.execute("UPDATE jobs SET status='EXPIRED' WHERE status='QUEUED' AND expires<=?", (now.timestamp(),))
            db.execute("UPDATE jobs SET status=CASE WHEN attempts>=2 OR expires<=? THEN 'FAILED' ELSE 'QUEUED' END, token=NULL, error='LEASE_EXPIRED' WHERE status='RUNNING' AND lease<=?", (now.timestamp(), now.timestamp()))
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status='RUNNING'").fetchone()[0]:
                return None
            day = now.astimezone(IST).date().isoformat()
            if db.execute("SELECT COUNT(*) FROM calls WHERE day=?", (day,)).fetchone()[0] >= daily_cap:
                return None
            row = db.execute("SELECT * FROM jobs WHERE status='QUEUED' AND expires>? ORDER BY created,id LIMIT 1", (now.timestamp(),)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='RUNNING',attempts=attempts+1,token=?,lease=? WHERE id=?", (token, now.timestamp() + timeout + 30, row["id"]))
            db.execute("INSERT INTO calls(at,day) VALUES(?,?)", (now.timestamp(), day))
            result = dict(row)
            result["token"] = token
            result["snapshot"] = json.loads(db.execute("SELECT body FROM snapshots WHERE id=?", (row["snapshot_id"],)).fetchone()[0])
            return result

    def finish(self, job, result, now, *, error=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND token=? AND status='RUNNING'", (job["id"], job["token"])).fetchone()
            if not row or row["lease"] <= now.timestamp():
                return False
            status = "FAILED" if error else ("STALE" if row["expires"] <= now.timestamp() else "SUCCEEDED")
            rejected = []
            if status == "SUCCEEDED":
                for spec in result["watches"]:
                    try:
                        self._add_watch(db, spec, now, row["root"], row["depth"] + 1)
                    except ValueError as exc:
                        rejected.append(type(exc).__name__)
                if rejected:
                    db.execute("INSERT INTO events(at,kind,body) VALUES(?,?,?)", (now.timestamp(), "WATCH_REJECTED", dumps({"job": row["id"], "count": len(rejected)})))
            db.execute("UPDATE jobs SET status=?,result=?,error=?,token=NULL WHERE id=?", (status, dumps(result) if result else None, error, row["id"]))
            return True

    def status(self):
        return {"source": self.meta("source"), "goal": self.meta("goal"),
                "jobs": self.read("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status"),
                "watches": self.read("SELECT name,enabled,fires,expiry FROM watches ORDER BY name"),
                "reports": self.read("SELECT id,day,status,attempts,provider_id,error FROM reports ORDER BY day DESC LIMIT 10"),
                "latest_observed_at": (self.latest() or {}).get("observed_at"), "execution": "NOT_IMPLEMENTED_NO_ORDER_API"}
