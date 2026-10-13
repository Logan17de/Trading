"""Fixed fault cadence; separate from trading authority and ordinary report mail."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .contracts import stamp
from .mail_window import regular_day

JST = ZoneInfo("Asia/Tokyo")
ACTIVE_REPEAT_SECONDS = 20 * 60
IDLE_REPEAT_SECONDS = 2 * 60 * 60
AUTH_CHECKPOINTS = frozenset((12, 15))


def auth_slot(now):
    """Only the 12:00 and 15:00 JST checkpoint's first five minutes."""
    if not regular_day(now):
        return None
    local = now.astimezone(JST)
    if local.hour not in AUTH_CHECKPOINTS or local.minute >= 5:
        return None
    return local.date().isoformat() + (":1200" if local.hour == 12 else ":1500")


def current(value, now, seconds=45):
    try:
        delta = (now - stamp(value)).total_seconds()
        return 0 <= delta <= seconds
    except (KeyError, ValueError, TypeError):
        return False


def position_risk(view, now):
    """ACTIVE, FLAT or UNKNOWN. A missing/stale read never certifies flat."""
    if not isinstance(view, dict):
        return "UNKNOWN"
    controller = view.get("execution_controller") or {}
    if isinstance(controller, dict) and controller.get("phase") not in (None, "IDLE", "CLOSED"):
        return "ACTIVE"
    test = (view.get("live_setup") or {}).get("owner_test") or {}
    if isinstance(test, dict) and (test.get("state") == "RUNNING" or test.get("attention") or
                                   test.get("state") == "ERROR"):
        return "ACTIVE"
    markets = view.get("markets")
    if isinstance(markets, list):
        for market in markets:
            if not isinstance(market, dict):
                continue
            for option in market.get("options", []):
                if isinstance(option, dict):
                    q = option.get("quantity")
                    if type(q) in (int, float) and q != 0:
                        return "ACTIVE"
    if (view.get("positions_status") == "AVAILABLE"
            and view.get("orders_status") == "AVAILABLE"
            and current(view.get("as_of"), now)
            and isinstance(markets, list)):
        return "FLAT"
    return "UNKNOWN"


def category(code, exposure):
    if code == "API_AUTH_FAILED":
        return "API"
    if code in ("ORDER_RECONCILIATION_REQUIRED", "EXECUTOR_WORKER_FAILED",
                "OWNER_TEST_FAILED", "TRADE_SUBMISSION_FAILED",
                "TRADE_EXIT_FAILED", "STOP_PROTECTION_FAILED",
                "OBSERVER_UNREACHABLE", "WORKER_STUCK", "INVALID_HEARTBEAT"):
        return "ACTIVE" if exposure != "FLAT" else "IDLE"
    return "ROUTINE"


def interval_seconds(kind):
    return ACTIVE_REPEAT_SECONDS if kind == "ACTIVE" else IDLE_REPEAT_SECONDS if kind == "IDLE" else None


def may_send(code, kind, now, last_accepted_at=None, *, initial=False, detected_at=None):
    """Critical: around the clock. Idle: regular mail window. API: slot only."""
    from .mail_window import status
    if kind == "API":
        return bool(auth_slot(now))
    if kind == "ROUTINE":
        return status(now) == "OPEN" and initial
    if kind == "IDLE" and status(now) != "OPEN":
        return False
    if initial or not last_accepted_at:
        return True
    try:
        age = (now - stamp(last_accepted_at)).total_seconds()
    except (TypeError, ValueError):
        return False
    return age >= interval_seconds(kind)


def expected_auth_failure(probe, now):
    """An actual authenticated 401/403 probe for the exact slot, never a guess."""
    slot = auth_slot(now)
    return (slot is not None and isinstance(probe, dict)
            and probe.get("slot") == slot and probe.get("status") == "INVALID"
            and probe.get("provider_code") in ("401", "403")
            and current(probe.get("checked_at"), now, 300))
