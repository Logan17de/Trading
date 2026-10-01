"""Offline maintenance. No service installation, VM lifecycle or broker actions."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from .contracts import stamp


def backup_database(source, destination):
    """SQLite online backup, including committed WAL; never overwrite a journal."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or source == destination:
        raise ValueError("an existing source and a new destination are required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst, pages=128, sleep=0.05)
                if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("backup integrity check failed")
                version = dst.execute("PRAGMA user_version").fetchone()[0]
                tables = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {"status": "VERIFIED_BACKUP", "sha256": digest, "bytes": destination.stat().st_size,
                "schema_version": version, "tables": tables}
    except BaseException:
        destination.unlink(missing_ok=True)  # Only the file exclusively created above.
        raise


def restore_database(source, destination):
    # Restore to a NEW path. Operator reconciles outstanding jobs/mail before use.
    result = backup_database(source, destination)
    if not {"snapshots", "jobs", "reports", "meta"} <= set(result["tables"]) or result["schema_version"] != 1:
        Path(destination).unlink()
        raise ValueError("not an agent-engine backup")
    return {**result, "status": "RESTORED_TO_NEW_PATH", "activation_required": True}


def health(store, now, *, heartbeat_ttl=300, min_free_bytes=256 * 1024 * 1024):
    problems = []
    for name in ("scheduler_heartbeat", "worker_heartbeat"):
        value = store.meta(name)
        if value is None or not 0 <= (now - stamp(value)).total_seconds() <= heartbeat_ttl:
            problems.append(name.upper() + "_MISSING_OR_STALE")
    latest = store.latest()
    if not latest or not 0 <= (now - stamp(latest["observed_at"])).total_seconds() <= 60:
        problems.append("PUBLISHER_MISSING_OR_STALE")
    space = shutil.disk_usage(store.path.parent)
    if space.free < min_free_bytes:
        problems.append("LOW_DISK_SPACE")
    for row in store.read("SELECT status, COUNT(*) AS count FROM reports WHERE status IN ('REVIEW_REQUIRED','SENDING') GROUP BY status"):
        if row["status"] == "REVIEW_REQUIRED":
            problems.append("EMAIL_REVIEW_REQUIRED")
    return {"status": "HEALTHY" if not problems else "ATTENTION", "checked_at": now.isoformat(),
            "problems": problems, "free_bytes": space.free, "database_bytes": store.path.stat().st_size,
            "external_host_watchdog_verified": False}
