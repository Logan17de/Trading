"""Viewer regression: funds and market/P&L clocks are independent."""
import threading
from datetime import datetime, timedelta, timezone

from nifty_engine.agent_engine.oracle_link import RemoteViewer


def test_healthy_oracle_allows_recent_cash_when_market_snapshot_is_old():
    now = datetime.now(timezone.utc)
    remote = RemoteViewer.__new__(RemoteViewer)
    remote.lock = threading.Lock()
    remote.status = "HEALTHY"
    remote.alert_status = None
    remote.incident = None
    remote.cache = {
        "format": "trading-dashboard-v1",
        "as_of": (now - timedelta(seconds=23)).isoformat(),
        "markets": [],
        "funds": {
            "status": "AVAILABLE",
            "received_at": (now - timedelta(seconds=30)).isoformat(),
            "clear_cash_inr": 12345.67,
            "option_sell_available_inr": 5603.73,
        },
        "broker_pnl": {"status": "UNAVAILABLE", "series": []},
        "capital_summary": None,
        "control": {"algo": {"desired_enabled": False, "execution_enabled": False, "blockers": []}},
    }
    result = remote.read()
    assert result["freshness"] == "STALE"
    assert result["funds"]["status"] == "AVAILABLE"
    assert result["funds"]["clear_cash_inr"] == 12345.67
    assert result["funds"]["option_sell_available_inr"] == 5603.73
    assert result["broker_pnl"]["status"] != "AVAILABLE"
    assert remote.cache["funds"]["clear_cash_inr"] == 12345.67


def test_unhealthy_oracle_hides_cash_even_if_independently_recent():
    now = datetime.now(timezone.utc)
    remote = RemoteViewer.__new__(RemoteViewer)
    remote.lock = threading.Lock()
    remote.status = "WORKER_STUCK"
    remote.alert_status = None
    remote.incident = None
    remote.cache = {
        "format": "trading-dashboard-v1",
        "as_of": (now - timedelta(seconds=23)).isoformat(),
        "markets": [],
        "funds": {"status": "AVAILABLE", "received_at": now.isoformat(), "clear_cash_inr": 5000.0},
        "broker_pnl": {"status": "UNAVAILABLE", "series": []},
        "capital_summary": None,
        "control": {"algo": {"desired_enabled": False, "execution_enabled": False, "blockers": []}},
    }
    result = remote.read()
    assert result["funds"]["status"] == "STALE"
    assert result["funds"]["clear_cash_inr"] is None
    assert result["freshness"] == "STALE"
