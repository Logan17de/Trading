from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.contracts import dumps
from nifty_engine.agent_engine.market_check import (
    allowed_request, quote_summary, read_credentials, readonly_transport, run_checks,
)
from nifty_engine.agent_engine.manual_ticket import prepare, render

NOW = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)


class Forbidden(Exception):
    code = "403"


class Broker:
    def __init__(self, forbidden=False):
        self.forbidden = forbidden

    def get_user_profile(self, **kwargs):
        return {"nse_enabled": True, "bse_enabled": True, "active_segments": ["FNO"], "ucc": "PRIVATE_ACCOUNT"}

    def get_quote(self, **kwargs):
        if self.forbidden:
            raise Forbidden("Authorization Bearer PRIVATE_TOKEN")
        return {"last_price": 25000, "last_trade_time": NOW.timestamp() * 1000,
                "bid_price": 3, "offer_price": 4, "debug_token": "PRIVATE_TOKEN"}

    def get_ltp(self, exchange_trading_symbols, **kwargs):
        if self.forbidden:
            raise Forbidden("PRIVATE_TOKEN")
        return {exchange_trading_symbols: 25000}

    def get_expiries(self, **kwargs):
        if self.forbidden:
            raise Forbidden("PRIVATE_TOKEN")
        return {"expiries": ["2026-10-06", "2026-09-29"]}

    def get_option_chain(self, **kwargs):
        return {"underlying_ltp": 25000, "strikes": {"25000": {}}, "secret": "PRIVATE_TOKEN"}


def test_forbidden_is_not_success_and_errors_do_not_leak():
    result = run_checks(SimpleNamespace(groww=Broker(True), limiter=SimpleNamespace(wait=lambda: None)), clock=lambda: NOW)
    assert result["status"] == "MARKET_DATA_FORBIDDEN"
    assert result["probes"]["profile"]["ok"]
    assert "PRIVATE" not in dumps(result)
    assert all(result["probes"][index + "_chain"]["skipped"] == "NO_VERIFIED_CURRENT_EXPIRY"
               for index in ("NIFTY", "BANKNIFTY", "SENSEX"))


def test_success_preserves_unknown_timestamps_and_filters_private_data():
    result = run_checks(SimpleNamespace(groww=Broker(), limiter=SimpleNamespace(wait=lambda: None)), clock=lambda: NOW)
    assert result["status"] == "READ_ONLY_DATA_AVAILABLE"
    quote = result["probes"]["NIFTY_quote"]["value"]
    assert quote["timestamp_status"] == "RECENT_TRADE_ONLY"
    assert quote["book_at"] is None and quote["greeks_at"] is None
    assert result["probes"]["NIFTY_ltp"]["value"]["at"] is None
    assert "PRIVATE" not in dumps(result)
    assert quote_summary({"last_trade_time": (NOW + timedelta(seconds=1)).timestamp() * 1000}, NOW)["timestamp_status"] == "FUTURE"
    seconds = quote_summary({"last_trade_time": NOW.timestamp()}, NOW)
    assert seconds["timestamp_status"] == "RECENT_TRADE_ONLY" and seconds["trade_timestamp_unit"] == "SECONDS"


def test_read_only_transport_rejects_orders_hosts_and_redirects(monkeypatch):
    import requests
    calls = []
    def fake(session, method, url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=302, json=lambda: {"token": "PRIVATE_TOKEN"})
    monkeypatch.setattr(requests.sessions.Session, "request", fake)
    audit = []
    with readonly_transport(audit):
        for method, url in [("POST", "https://api.groww.in/v1/order/create"),
                            ("GET", "https://other.invalid/v1/user/detail"),
                            ("GET", "https://api.groww.in/v1/order/create")]:
            with pytest.raises(PermissionError):
                requests.request(method, url)
        assert not calls
        with pytest.raises(PermissionError, match="redirect"):
            requests.get("https://api.groww.in/v1/user/detail", allow_redirects=True)
    assert requests.sessions.Session.request is fake
    assert len(calls) == 1 and calls[0]["allow_redirects"] is False and calls[0]["timeout"] == 15
    assert "PRIVATE" not in dumps(audit)
    assert allowed_request("POST", "https://api.groww.in/v1/token/api/access")
    assert not allowed_request("POST", "https://api.groww.in/v1/token/api/access?leak=true")


def test_secret_stdin_contract_rejects_extra_fields_and_oversize():
    with pytest.raises(ValueError):
        read_credentials(StringIO('{"api_key":"12345678","api_secret":"12345678","exec":"order"}'))
    with pytest.raises(ValueError):
        read_credentials(StringIO(" " * 65537))


def ticket_input():
    # Invented contract for an offline formatting test; not a trade recommendation.
    return {"exchange": "NSE", "segment": "FNO", "symbol": "FIXTURE26O0625000CE",
            "side": "BUY", "quantity": 10, "limit_price": "2.50", "product": "NRML"}


def test_manual_ticket_has_no_execution_and_never_infers_price_or_quantity():
    value = ticket_input()
    ticket = prepare(value, NOW)
    assert ticket["submitted"] is False and ticket["execution_capability"] is False
    assert ticket["fields"]["quantity"] == value["quantity"]
    assert ticket["fields"]["limit_price"] == "2.50"
    assert "Nothing has been submitted" in render(ticket)
    for key, bad in [("quantity", 0), ("quantity", True), ("limit_price", "NaN"),
                     ("limit_price", "0"), ("symbol", "ABC\n|injected|"), ("product", "CNC")]:
        with pytest.raises(ValueError):
            prepare({**value, key: bad}, NOW)
    with pytest.raises(ValueError):
        prepare({**value, "automatic_submit": True}, NOW)
