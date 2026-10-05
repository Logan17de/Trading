"""Explicit one-shot Groww access diagnostic; no journal, orders or publication."""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import logging
import math
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..brokers.groww_data import GrowwMarketData
from .contracts import EXCHANGES, INDICES, IST, dumps

SDK_VERSION = "1.5.0"


def read_credentials(stream):
    raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("credential input too large")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"api_key", "api_secret"}:
        raise ValueError("expected API key and secret on stdin")
    for item in value.values():
        if not isinstance(item, str) or not 8 <= len(item) <= 16384 or any(c.isspace() for c in item):
            raise ValueError("invalid credential format")
    return value


def safe_error(exc):
    """Never include exception messages, URLs, response bodies or request headers."""
    code = str(getattr(exc, "code", ""))
    return {"ok": False, "error_type": type(exc).__name__,
            "code": code if re.fullmatch(r"(?:GA[0-9]{3}|[0-9]{3})", code) else None}


def allowed_request(method, url, *, history=False, dashboard=False):
    parsed = urlsplit(url)
    if dashboard and method.upper() == "GET" and url == "https://growwapi-assets.groww.in/instruments/instrument.csv":
        return True
    if (parsed.scheme, parsed.netloc) != ("https", "api.groww.in") or parsed.fragment:
        return False
    read_paths = {"/v1/live-data/quote", "/v1/live-data/ltp", "/v1/historical/expiries", "/v1/user/detail"}
    if history:
        read_paths.update({"/v1/historical/candles", "/v1/historical/contracts"})
    if dashboard:
        read_paths.update({"/v1/order/list", "/v1/positions/user"})
        read_paths.add("/v1/order-advance/list")
        if method.upper() == "GET" and re.fullmatch(r"/v1/order-advance/status/FNO/(?:OCO|GTT)/internal/[A-Za-z0-9_-]{1,128}",parsed.path):
            return True
    read_paths.update(f"/v1/option-chain/exchange/{EXCHANGES[index]}/underlying/{index}" for index in INDICES)
    return ((method.upper() == "POST" and parsed.path == "/v1/token/api/access" and not parsed.query)
            or (method.upper() == "GET" and parsed.path in read_paths))


@contextlib.contextmanager
def readonly_transport(audit, *, history=False, dashboard=False, deadline=None, timeout_seconds=15):
    """Guard this single-purpose process against order writes and auth redirects."""
    import requests
    original = requests.sessions.Session.request

    def guarded(session, method, url, **kwargs):
        if not allowed_request(method, url, history=history, dashboard=dashboard):
            raise PermissionError("request outside read-only diagnostic scope")
        kwargs["allow_redirects"] = False
        kwargs["timeout"] = timeout_seconds
        if deadline is not None:
            remaining = (deadline-datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise TimeoutError("read-only recording deadline reached")
            kwargs["timeout"] = min(timeout_seconds, remaining)
        response = original(session, method, url, **kwargs)
        item = {"method": method.upper(), "path": urlsplit(url).path,
                "http_status": response.status_code, "json_response": False, "provider_code": None}
        try:
            if url == "https://growwapi-assets.groww.in/instruments/instrument.csv":
                raise ValueError("CSV response, no JSON audit")
            body = response.json()
            item["json_response"] = True
            code = body.get("error", {}).get("code") if isinstance(body, dict) else None
            if isinstance(code, str) and re.fullmatch(r"GA[0-9]{3}", code):
                item["provider_code"] = code
        except (ValueError, AttributeError, TypeError):
            pass
        audit.append(item)
        if 300 <= response.status_code < 400:
            raise PermissionError("redirect refused")
        return response

    requests.sessions.Session.request = guarded
    try:
        yield
    finally:
        requests.sessions.Session.request = original


def finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def quote_summary(raw, received_at):
    result = {key: finite(raw.get(key)) for key in (
        "last_price", "bid_price", "offer_price", "bid_quantity", "offer_quantity", "volume", "open_interest")}
    ohlc = raw.get("ohlc") if isinstance(raw.get("ohlc"), dict) else {}
    result["ohlc"] = {key: finite(ohlc.get(key)) for key in ("open", "high", "low", "close")}
    # Actual SDK 1.5.0 option responses on 2026-09-30 used epoch seconds,
    # while the documented examples use milliseconds. Expose the unit explicitly.
    value = finite(raw.get("last_trade_time"))
    unit = ("SECONDS" if value is not None and 1e9 <= value < 1e11 else
            "MILLISECONDS" if value is not None and 1e12 <= value < 1e14 else None)
    try:
        traded = datetime.fromtimestamp(value/(1000 if unit == "MILLISECONDS" else 1), timezone.utc) if unit else None
    except (ValueError, OverflowError, OSError):
        traded, unit = None, None
    depth = raw.get("depth") or {}
    levels = {}
    for side in ("buy", "sell"):
        rows = depth.get(side, []) if isinstance(depth, dict) else []
        levels[side] = [{"price": row["price"], "quantity": row["quantity"]}
            for row in (rows[:5] if isinstance(rows, list) else [])
            if isinstance(row, dict) and finite(row.get("price")) is not None and row["price"] > 0
            and finite(row.get("quantity")) is not None and row["quantity"] > 0]
    result["depth"] = levels
    sources = {}
    for side, price_key, qty_key in (("buy", "bid_price", "bid_quantity"), ("sell", "offer_price", "offer_quantity")):
        if result[price_key] is not None and result[price_key] > 0 and result[qty_key] is not None and result[qty_key] > 0:
            sources[side] = "TOP_LEVEL"
        elif levels[side]:
            best = (max if side == "buy" else min)(levels[side], key=lambda row: row["price"])
            result[price_key], result[qty_key] = best["price"], best["quantity"]
            sources[side] = "DEPTH"
        else:
            sources[side] = "MISSING"
    result["book_sources"] = sources
    result["crossed_book"] = (result["bid_price"] > result["offer_price"]
        if result["bid_price"] is not None and result["offer_price"] is not None else None)
    age = (received_at - traded).total_seconds() if traded else None
    result.update(traded_at=traded.isoformat() if traded else None, book_at=None, greeks_at=None,
                  trade_timestamp_unit=unit,
                  last_trade_age_seconds=age,
                  timestamp_status="MISSING" if age is None else "FUTURE" if age < 0 else "STALE" if age > 30 else "RECENT_TRADE_ONLY")
    return result


def call_samples(raw):
    """Fixed research sample: first OTM call and calls 10/20 listed strikes above.

    This is sampling, not a recommendation or a liquidity/lot-size approval.
    """
    spot = finite(raw.get("underlying_ltp"))
    if spot is None or spot <= 0:
        return []
    calls = []
    for key, sides in raw.get("strikes", {}).items():
        try:
            strike = float(key)
        except (ValueError, TypeError):
            continue
        call = sides.get("CE") if isinstance(sides, dict) else None
        if not math.isfinite(strike) or strike < spot or not isinstance(call, dict):
            continue
        symbol = call.get("trading_symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9&._-]{0,79}CE", symbol):
            continue
        greeks = call.get("greeks") or {}
        calls.append({"symbol": symbol, "strike": strike,
                      "greeks": {k: finite(greeks.get(k)) for k in ("delta", "gamma", "theta", "vega", "iv")},
                      "greeks_at": None})
    calls.sort(key=lambda row: row["strike"])
    return [{**calls[offset], "listed_strike_offset": offset} for offset in (0, 10, 20) if offset < len(calls)]


def run_checks(market, *, indices=INDICES, clock=lambda: datetime.now(timezone.utc)):
    if not indices or len(set(indices)) != len(indices) or not set(indices) <= set(INDICES):
        raise ValueError("unsupported or duplicate index")
    groww, probes = market.groww, {}

    def probe(name, call, summarize):
        started = clock()
        try:
            market.limiter.wait()
            raw = call()
            probes[name] = {"ok": True, "value": summarize(raw, clock())}
        except Exception as exc:
            probes[name] = safe_error(exc)
        probes[name].update(request_started_at=started.isoformat(), received_at=clock().isoformat())
        return probes[name].get("value")

    probe("profile", lambda: groww.get_user_profile(timeout=15), lambda raw, at: {
        "nse_enabled": raw.get("nse_enabled") is True, "bse_enabled": raw.get("bse_enabled") is True,
        "active_segments": [v for v in raw.get("active_segments", []) if v in ("CASH", "FNO", "COMMODITY")]})
    today = clock().astimezone(IST).date()
    for index in indices:
        exchange = EXCHANGES[index]
        probe(index + "_quote", lambda: groww.get_quote(trading_symbol=index, exchange=exchange,
              segment="CASH", timeout=15), quote_summary)
        probe(index + "_ltp", lambda: groww.get_ltp(exchange_trading_symbols=exchange + "_" + index,
              segment="CASH", timeout=15), lambda raw, at: {"price": finite(raw.get(exchange + "_" + index)), "at": None})
        def expiry_summary(raw, at):
            values = sorted({v for v in raw.get("expiries", []) if isinstance(v, str)
                             and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v)
                             and date.fromisoformat(v) >= today})
            return {"expiries": values[:12]}
        expiry = probe(index + "_expiries", lambda: groww.get_expiries(exchange=exchange,
                       underlying_symbol=index, year=today.year, timeout=15), expiry_summary)
        if expiry and not expiry["expiries"] and today.month == 12:
            expiry = probe(index + "_next_year_expiries", lambda: groww.get_expiries(exchange=exchange,
                           underlying_symbol=index, year=today.year + 1, timeout=15), expiry_summary)
        if expiry and expiry["expiries"]:
            selected = expiry["expiries"][0]
            def summarize_chain(raw, at):
                return {"expiry": selected, "underlying_ltp": finite(raw.get("underlying_ltp")),
                        "strike_count": len(raw.get("strikes", {})), "book_at": None, "greeks_at": None,
                        "call_samples": call_samples(raw)}
            chain = probe(index + "_chain", lambda: groww.get_option_chain(exchange=exchange, underlying=index,
                  expiry_date=selected, timeout=15), summarize_chain)
            for call in (chain or {}).get("call_samples", []):
                name = index + "_call_" + str(call["listed_strike_offset"])
                quote = probe(name, lambda: groww.get_quote(trading_symbol=call["symbol"],
                    exchange=exchange, segment="FNO", timeout=15), quote_summary)
                call["quote"] = quote
                call["lot_size"] = None
                call["value_kind"] = "OPTION_QUOTE_WITH_UNVERIFIED_BOOK_TIME"
        else:
            probes[index + "_chain"] = {"ok": False, "skipped": "NO_VERIFIED_CURRENT_EXPIRY"}
    quotes = [probes[index + "_quote"] for index in indices]
    if all(row.get("code") in ("403", "GA005") for row in quotes):
        status = "MARKET_DATA_FORBIDDEN"
    elif all(row.get("ok") and (row["value"]["last_price"] or 0) > 0 for row in quotes) and all(
        probes[index + "_chain"].get("value", {}).get("strike_count", 0) > 0 for index in indices
    ):
        status = "READ_ONLY_DATA_AVAILABLE"
    else:
        status = "MARKET_DATA_INCOMPLETE"
    return {"status": status, "probes": probes}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-stdin", required=True, action="store_true")
    parser.add_argument("--indices", nargs="+", choices=INDICES, default=list(INDICES))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if len(set(args.indices)) != len(args.indices):
        raise ValueError("duplicate index")
    # Fail before consuming credentials or contacting Groww if output is unsafe.
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError("use a new output file in an existing private directory")
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "execution_mode": "OBSERVE",
              "order_capability": False, "sdk_version": importlib.metadata.version("growwapi"),
              "authentication": {"ok": False}, "http_audit": []}
    previous_logging = logging.root.manager.disable
    with open(os.devnull, "w") as muted, contextlib.redirect_stdout(muted), contextlib.redirect_stderr(muted):
        logging.disable(logging.CRITICAL)
        try:
            if result["sdk_version"] != SDK_VERSION:
                result["status"] = "SDK_VERSION_MISMATCH"
            else:
                credentials = read_credentials(sys.stdin)
                from growwapi import GrowwAPI
                with readonly_transport(result["http_audit"]):
                    try:
                        token = GrowwAPI.get_access_token(api_key=credentials["api_key"], secret=credentials["api_secret"])
                    finally:
                        credentials.clear()
                    if not isinstance(token, str) or not token:
                        raise ValueError("empty access token")
                    result["authentication"] = {"ok": True}
                    market = GrowwMarketData(token)
                    del token
                    result.update(run_checks(market, indices=args.indices))
        except Exception as exc:
            result.update(status="BLOCKED", failure=safe_error(exc))
        finally:
            logging.disable(previous_logging)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    with args.output.open("x", encoding="utf-8") as output:
        output.write(dumps(result))
    print(dumps(result))
    return 0 if result["status"] == "READ_ONLY_DATA_AVAILABLE" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(dumps({"status": "BLOCKED", "failure": safe_error(exc)}))
        raise SystemExit(2) from None
