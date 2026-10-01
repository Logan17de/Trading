"""Reviewed calendar evidence and private atomic validated-snapshot writes."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .contracts import IST, dumps, snapshot, stamp, text




def calendar_evidence(config, now):
    """Allowlist only the reviewed session/event data; missing is not no events."""
    try:
        if stamp(config["reviewed_at"]) > now:
            return None
        session = config["sessions"][now.astimezone(IST).date().isoformat()]
        if stamp(session["open"]) >= stamp(session["close"]):
            return None
        events = config["events"]
        if not isinstance(events, list) or len(events) > 32:
            return None
        result = []
        for event in events:
            if stamp(event["start"]) >= stamp(event["end"]):
                return None
            result.append({"name": text(event.get("name") or event.get("title") or "Scheduled event", 160),
                           "start": event["start"], "end": event["end"]})
        return {"reviewed_at": config["reviewed_at"], "session_open": session["open"],
                "session_close": session["close"], "events": result}
    except (ValueError, KeyError, TypeError):
        return None


def write_snapshot(path, body, *, group_read=False):
    snapshot(body)
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=destination.parent, prefix=".snapshot-")
    try:
        if group_read:
            os.chmod(name, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(dumps(body))
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return body
