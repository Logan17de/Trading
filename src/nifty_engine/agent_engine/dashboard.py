"""Loopback-only, read-only dashboard over the existing Groww research engine."""
from __future__ import annotations

import argparse
import contextlib
import csv
import importlib.metadata
import io
import json
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .contracts import EXCHANGES, INDICES, IST, dumps, keys, number, stamp
from .market_check import SDK_VERSION, finite, quote_summary, read_credentials, readonly_transport, safe_error
from .owner_study import validate_protocol
from .pc_control import JST, PcJournal, PcMonitor, collection_window, in_window
from . import broker_pnl
from . import premium_strategy

ASSETS = Path(__file__).with_name("dashboard_assets")
STRATEGIES = (
    ("everyday", "Everyday hedged call", "13:15 JST · NIFTY +500 / SENSEX +1,000"),
    ("late_session", "Late-session decay", "17:45–18:45 JST · Hedged calls"),
    ("swing", "Swing call spread", "Overnight · 10 / 20 strike study"),
    ("expiry_reversal", "Expiry reversal", "SENSEX · 18:50 JST onward"),
)
ACTIVE_STRATEGIES = {"everyday", "late_session"}
ACTIVE_DESCRIPTIONS = {"everyday":"14:00–19:00 JST · NIFTY ₹20 / SENSEX ₹80 call",
    "late_session":"Actual expiry · 18:00 JST · 3 strike intervals from ATM"}
POLL_SECONDS = 5
LEASE_SECONDS = 20
INSTRUMENT_CSV = "https://growwapi-assets.groww.in/instruments/instrument.csv"
OPTION_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9&._-]{0,79}(CE|PE)")


def load_json(path, limit=5_000_000):
    if path.stat().st_size > limit:
        raise ValueError("input too large")
    return json.loads(path.read_text(encoding="utf-8"))


def clean_time(value):
    try:
        return stamp(value).isoformat()
    except (ValueError, TypeError):
        return None


def clean_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        return None


def clean_series(rows):
    """Whitelisted timestamp/price pairs; no raw SDK fields reach the browser."""
    result = {}
    if not isinstance(rows, list):
        return []
    for row in rows[:2000]:
        if not isinstance(row, dict):
            continue
        at, price = clean_time(row.get("at")), finite(row.get("close"))
        if at and price is not None and price >= 0:
            result[at] = {"at": at, "value": price, **{k:finite(row.get(k)) for k in ("open","high","low")}}
    return [result[at] for at in sorted(result)]


def account_summary(account, now):
    """Import an owner-reviewed net-cost ledger; zero trades never imply success."""
    empty = {"status": "NOT_CONNECTED", "as_of": None, "capital_inr": None,
        "portfolio_value_inr": None, "used_margin_inr": None, "available_margin_inr": None,
        "realized_today_inr": None, "unrealized_inr": None, "today_pnl_inr": None,
        "today_return_pct": None, "trade_count_today": None, "margin_utilization_pct": None,
        "pnl_series": [], "portfolio_series": [], "strategy_results": {}}
    if account is None:
        return empty
    keys(account, {"format", "as_of", "capital_inr", "portfolio_value_inr", "used_margin_inr",
        "available_margin_inr", "unrealized_inr", "strategy_capital_inr", "closed_trades",
        "pnl_series", "portfolio_series"})
    if account["format"] != "dashboard-account-v1":
        raise ValueError("unsupported account ledger")
    as_of = stamp(account["as_of"])
    if as_of > now:
        raise ValueError("future account timestamp")
    result = dict(empty, status="OWNER_REVIEWED", as_of=as_of.isoformat())
    for field in ("capital_inr", "portfolio_value_inr", "used_margin_inr", "available_margin_inr", "unrealized_inr"):
        value = number(account[field], nullable=True)
        if field != "unrealized_inr" and value is not None and value < 0:
            raise ValueError("negative account amount")
        result[field] = value
    if not isinstance(account["closed_trades"], list) or len(account["closed_trades"]) > 100000:
        raise ValueError("bounded closed-trade ledger required")
    strategy_ids = {r[0] for r in STRATEGIES}
    keys(account["strategy_capital_inr"], set(), strategy_ids)
    trades, seen = [], set()
    for row in account["closed_trades"]:
        keys(row, {"id", "strategy", "closed_at", "net_pnl_inr"})
        if not isinstance(row["id"], str) or not 1 <= len(row["id"]) <= 128 or row["id"] in seen:
            raise ValueError("unique closed-trade id required")
        if row["strategy"] not in strategy_ids:
            raise ValueError("unknown strategy")
        closed = stamp(row["closed_at"])
        if closed > as_of:
            raise ValueError("trade closed after ledger timestamp")
        seen.add(row["id"])
        trades.append((row["strategy"], closed, number(row["net_pnl_inr"])))
    for strategy in strategy_ids:
        pnls = [pnl for name, _, pnl in trades if name == strategy]
        capital = number(account["strategy_capital_inr"].get(strategy), nullable=True)
        if capital is not None and capital <= 0:
            raise ValueError("positive strategy capital required")
        count = len(pnls)
        non_loss = sum(pnl >= 0 for pnl in pnls)
        total = number(math.fsum(pnls)) if count else None
        result["strategy_results"][strategy] = {"closed_trades": count,
            "non_loss_pct": 100 * non_loss / count if count else None,
            "loss_pct": 100 * (count - non_loss) / count if count else None,
            "net_pnl_inr": total, "return_pct": total / capital * 100 if capital and count else None}
    today = now.astimezone(IST).date()
    if as_of.astimezone(IST).date() == today:
        daily = [pnl for _, closed, pnl in trades if closed.astimezone(IST).date() == today]
        realized = number(math.fsum(daily))
        result.update(realized_today_inr=realized, trade_count_today=len(daily))
        if result["unrealized_inr"] is not None:
            result["today_pnl_inr"] = number(realized + result["unrealized_inr"])
            if result["capital_inr"]:
                result["today_return_pct"] = result["today_pnl_inr"] / result["capital_inr"] * 100
    else:
        result["status"] = "STALE_LEDGER"
        result["unrealized_inr"] = None
    used, available = result["used_margin_inr"], result["available_margin_inr"]
    if used is not None and available is not None and used + available > 0:
        result["margin_utilization_pct"] = used / (used + available) * 100
    for field in ("pnl_series", "portfolio_series"):
        if not isinstance(account[field], list) or len(account[field]) > 2000:
            raise ValueError("bounded account series required")
        pairs = []
        for row in account[field]:
            keys(row, {"at", "value"})
            at, value = stamp(row["at"]), number(row["value"])
            if at > as_of:
                raise ValueError("future account series")
            if field == "pnl_series" and at.astimezone(IST).date() != today:
                continue
            pairs.append({"at": at.isoformat(), "value": value})
        if [r["at"] for r in pairs] != sorted({r["at"] for r in pairs}):
            raise ValueError("unique chronological account series required")
        result[field] = pairs
    return result


def funds_summary(raw):
    """Provider money fields only; cash is not total investment value or P&L."""
    if not isinstance(raw, dict) or not isinstance(raw.get("fno_margin_details"), dict):
        raise ValueError("Groww money details unavailable")
    fno = raw["fno_margin_details"]
    result = {"clear_cash_inr":finite(raw.get("clear_cash")),
        "total_margin_used_inr":finite(raw.get("net_margin_used")),
        "fno_margin_used_inr":finite(fno.get("net_fno_margin_used")),
        "option_buy_available_inr":finite(fno.get("option_buy_balance_available")),
        "option_sell_available_inr":finite(fno.get("option_sell_balance_available")),
        "collateral_available_inr":finite(raw.get("collateral_available"))}
    if result["clear_cash_inr"] is None:
        raise ValueError("clear cash unavailable")
    return result


def public_funds(snapshot, now):
    raw = snapshot.get("funds", {})
    result = {k:None for k in ("clear_cash_inr", "total_margin_used_inr", "fno_margin_used_inr",
        "option_buy_available_inr", "option_sell_available_inr", "collateral_available_inr")}
    at = clean_time(raw.get("received_at"))
    age = (now-stamp(at)).total_seconds() if at else None
    status = "AVAILABLE" if raw.get("status") == "AVAILABLE" and age is not None and 0 <= age <= 45 else "STALE" if at else "UNAVAILABLE"
    if status == "AVAILABLE":
        result.update({k:finite(raw.get(k)) for k in result})
    return dict(result, status=status, received_at=at, refresh_interval_seconds=30,
                source="GROWW_AVAILABLE_MARGIN_DETAILS")


def view_model(snapshot, protocol, account=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    validate_protocol(protocol)
    snapshot = snapshot or {}
    received = clean_time(snapshot.get("finished_at") or snapshot.get("checked_at"))
    age = (now - stamp(received)).total_seconds() if received else None
    markets = []
    probes = snapshot.get("probes", {})
    if not isinstance(probes, dict):
        probes = {}
    charts = snapshot.get("charts", {})
    if not isinstance(charts, dict):
        charts = {}
    for index in INDICES:
        probe = probes.get(index + "_quote", {})
        quote = probe.get("value", {}) if probe.get("ok") is True else {}
        ohlc = quote.get("ohlc", {})
        if not isinstance(ohlc, dict):
            ohlc = {}
        last, previous = finite(quote.get("last_price")), finite(ohlc.get("close"))
        change = last - previous if last is not None and previous and previous > 0 else None
        options = []
        # Latest owner direction: only confirmed nonzero open positions qualify.
        ordered = snapshot.get("ordered_options", [])
        for item in (ordered if isinstance(ordered, list) else [])[:40]:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            if item.get("index") != index or item.get("side") not in ("BUY","SELL"):
                continue
            quantity = finite(item.get("quantity"))
            if item.get("order_status") != "POSITION" or quantity is None or quantity <= 0:
                continue
            if not isinstance(symbol, str) or not OPTION_SYMBOL.fullmatch(symbol):
                continue
            strike = finite(item.get("strike"))
            q = item.get("quote") or {}
            series = clean_series(charts.get(symbol, {}).get("candles", []))
            options.append({"symbol": symbol, "index": index, "exchange": EXCHANGES[index],
                "expiry": clean_date(item.get("expiry")),
                "strike": strike, "type": symbol[-2:], "side":item["side"], "last_price": finite(q.get("last_price")),
                "bid": finite(q.get("bid_price")), "ask": finite(q.get("offer_price")),
                "received_at": clean_time(item.get("received_at")),
                "last_trade_at": clean_time(q.get("traded_at")), "series": series,
                "series_status": "AVAILABLE" if series else "WAITING_FOR_SESSION" if charts.get(symbol,{}).get("status") == "WAITING_FOR_SESSION"
                    else "LOADING_OPTION_HISTORY" if snapshot.get("history_refreshing") is True
                    and symbol not in charts else "NO_OPTION_HISTORY",
                "book_time_verified": False, "order_status":item.get("order_status") if item.get("order_status") in ("OPEN","TRIGGER_PENDING","EXECUTED","DELIVERY_AWAITED","POSITION","PARTIAL_FILL") else "RECORDED",
                "quantity":quantity, "price_levels":public_price_levels(item),
                "protective_levels_status":item.get("protective_levels_status") if item.get("protective_levels_status")
                    in ("AVAILABLE", "UNAVAILABLE") else "UNAVAILABLE",
                "open_pnl_inr":finite(item.get("open_pnl_inr")) if age is not None and 0 <= age <= 15 else None,
                "pnl_bucket":item.get("pnl_bucket") if item.get("pnl_bucket") in broker_pnl.BUCKETS else "unassigned",
                "ownership":item.get("ownership") if item.get("ownership") in
                    ("ENGINE_VERIFIED","ENGINE_PENDING_VERIFIED") else "MANUAL_OR_UNKNOWN_PROTECTED"})
        markets.append({"index": index, "price": last, "change": change,
            "change_pct": change / previous * 100 if change is not None else None,
            "high": finite(ohlc.get("high")), "low": finite(ohlc.get("low")), "previous": previous,
            "received_at": clean_time(probe.get("received_at")),
            "series": clean_series(charts.get(index, {}).get("candles", [])), "options": options})
    acct = account_summary(account, now)
    strategies = [{"id": key, "name": name, "description": ACTIVE_DESCRIPTIONS.get(key,description),
        **acct["strategy_results"].get(key, {"closed_trades": 0, "non_loss_pct": None,
            "loss_pct": None, "net_pnl_inr": None, "return_pct": None})}
        for key, name, description in STRATEGIES if key in ACTIVE_STRATEGIES]
    return {"format": "trading-dashboard-v1", "demo": False, "as_of": received,
        "freshness": "NO_DATA" if age is None else "FUTURE" if age < 0 else "RECENT" if age <= 15 else "STALE",
        "source": "Groww · read-only", "market_day": stamp(received).astimezone(IST).date().isoformat() if received else None,
        "markets": markets, "account": acct, "funds":public_funds(snapshot,now),
        "broker_pnl":broker_pnl.public(snapshot.get("broker_pnl"),now), "strategies": strategies,
        "orders_status": snapshot.get("orders_status") if snapshot.get("orders_status") in ("AVAILABLE","INCOMPLETE","UNAVAILABLE") else "UNAVAILABLE",
        "positions_status": snapshot.get("positions_status") if snapshot.get("positions_status") in
            ("AVAILABLE", "UNAVAILABLE") else "UNAVAILABLE",
        "market_status": snapshot.get("status") if snapshot.get("status") in ("READ_ONLY_DATA_AVAILABLE","PARTIAL_MARKET_DATA","BLOCKED") else "UNKNOWN",
        "poll_interval_seconds":POLL_SECONDS, "candle_interval_minutes":5,
        "sequence": snapshot.get("sequence") if type(snapshot.get("sequence")) is int else None,
        "chart_style":"LINE", "execution_enabled": False, "order_capability": False}


def option_identity(row):
    symbol = row.get("trading_symbol")
    if row.get("segment") != "FNO" or not isinstance(symbol, str) or not OPTION_SYMBOL.fullmatch(symbol):
        return None
    index = next((key for key in ("BANKNIFTY", "NIFTY", "SENSEX")
                  if re.match(key + r"\d", symbol)), None)
    if index is None or row.get("exchange") != EXCHANGES[index]:
        return None
    return {"symbol": symbol, "index": index, "exchange": EXCHANGES[index]}


def public_price_levels(item):
    """Only prices and their provenance reach the browser; never broker identities."""
    result = []
    levels = item.get("price_levels", [])
    for level in levels[:40] if isinstance(levels, list) else []:
        if not isinstance(level, dict):
            continue
        price = finite(level.get("price"))
        if (level.get("kind") not in ("ENTRY", "SL", "TARGET") or price is None
                or not 0 < price < 1e7 or level.get("source") not in
                ("GROWW_POSITION_NET_PRICE", "GROWW_CARRY_FORWARD_PRICE", "GROWW_PENDING_SL", "GROWW_ACTIVE_OCO", "GROWW_ACTIVE_GTT_EXIT")):
            continue
        result.append({"kind":level["kind"], "price":price, "source":level["source"],
            "quantity":finite(level.get("quantity")),
            "product":level.get("product") if level.get("product") in ("NRML", "MIS") else None})
    return result


def ordered_contracts(orders, positions, smart_orders=()):
    """Open position charts only; read-only order records supply verified levels."""
    result = {}
    for row in positions:
        item = option_identity(row) if isinstance(row, dict) else None
        quantity = finite(row.get("quantity")) if isinstance(row, dict) else None
        if item is None or quantity is None or quantity == 0:
            continue
        side = "BUY" if quantity > 0 else "SELL"
        entry = finite(row.get("net_price"))
        source = "GROWW_POSITION_NET_PRICE"
        if (entry is None or entry <= 0) and finite(row.get("net_carry_forward_quantity")) == quantity:
            entry = finite(row.get("net_carry_forward_price"))
            source = "GROWW_CARRY_FORWARD_PRICE"
        product = row.get("product")
        item.update(side=side, order_status="POSITION", quantity=abs(quantity), price_levels=[])
        if entry is not None and 0 < entry < 1e7:
            item["price_levels"].append({"kind":"ENTRY","price":entry,"source":source,
                "quantity":abs(quantity), "product":product})
        for order in orders:
            if (not isinstance(order, dict) or option_identity(order) != option_identity(row)
                    or product not in ("NRML", "MIS") or order.get("product") != product
                    or order.get("transaction_type") != ("SELL" if side == "BUY" else "BUY")
                    or order.get("order_status") not in ("OPEN","TRIGGER_PENDING")
                    or order.get("order_type") not in ("SL","SL_M")
                    or finite(order.get("filled_quantity")) != 0
                    or finite(order.get("quantity")) is None or not 0 < order["quantity"] <= abs(quantity)):
                continue
            price = finite(order.get("trigger_price"))
            if price is not None and 0 < price < 1e7:
                item["price_levels"].append({"kind":"SL","price":price,"source":"GROWW_PENDING_SL",
                    "quantity":finite(order["quantity"]), "product":product})
        for order in smart_orders:
            if (not isinstance(order, dict) or order.get("trading_symbol") != item["symbol"] or order.get("exchange") != item["exchange"]
                    or order.get("segment") != "FNO" or order.get("status") != "ACTIVE"
                    or product not in ("NRML", "MIS") or order.get("smart_order_type") not in ("OCO", "GTT")
                    or order.get("product_type") != product
                    or finite(order.get("quantity")) is None or not 0 < order["quantity"] <= abs(quantity)):
                continue
            prices = {}
            if order["smart_order_type"] == "GTT":
                exit_order = order.get("order")
                direction = order.get("trigger_direction")
                if (not isinstance(exit_order, dict) or exit_order.get("transaction_type") !=
                        ("SELL" if side == "BUY" else "BUY") or direction not in ("UP", "DOWN")):
                    continue
                price = smart_price(order.get("trigger_price"))
                if price is not None:
                    kind = "SL" if direction == ("DOWN" if side == "BUY" else "UP") else "TARGET"
                    item["price_levels"].append({"kind":kind, "price":price, "source":"GROWW_ACTIVE_GTT_EXIT",
                        "quantity":finite(order["quantity"]), "product":product})
                continue
            for field,kind in (("stop_loss","SL"),("target","TARGET")):
                leg = order.get(field)
                value = leg.get("trigger_price") if isinstance(leg, dict) else None
                price = smart_price(value)
                if price is not None:
                    prices[kind] = price
            if len(prices) == 2 and not (prices["SL"] < prices["TARGET"] if side == "BUY" else prices["TARGET"] < prices["SL"]):
                continue
            for kind, price in prices.items():
                item["price_levels"].append({"kind":kind,"price":price,"source":"GROWW_ACTIVE_OCO",
                    "quantity":finite(order["quantity"]), "product":product})
        key = item["symbol"], side
        if key in result:
            prior = result[key]
            total = item["quantity"] + prior["quantity"]
            old_entry = next((r for r in prior["price_levels"] if r["kind"] == "ENTRY"), None)
            new_entry = next((r for r in item["price_levels"] if r["kind"] == "ENTRY"), None)
            levels = [r for r in prior["price_levels"]+item["price_levels"] if r["kind"] != "ENTRY"]
            # Same-contract, same-side rows have a quantity-weighted position average.
            # Missing averages remain unknown rather than using a previous fill.
            if old_entry and new_entry:
                levels.insert(0, {"kind":"ENTRY", "price":(old_entry["price"]*prior["quantity"]+
                    new_entry["price"]*item["quantity"])/total, "source":"GROWW_POSITION_NET_PRICE",
                    "quantity":total, "product":None})
            item.update(quantity=total, price_levels=levels)
        result[key] = item
    return list(result.values())


def smart_price(value):
    if type(value) not in (str, int, float) or isinstance(value, str) and len(value) > 32:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and 0 < price < 1e7 else None


def instrument_metadata(text, requested):
    """Match exact broker trading symbols; retain only public contract metadata."""
    result = {}
    for row in csv.DictReader(io.StringIO(text)):
        symbol = row.get("trading_symbol")
        identity = requested.get(symbol)
        if not identity or row.get("exchange") != identity["exchange"] or row.get("segment") != "FNO":
            continue
        if row.get("underlying_symbol") != identity["index"]:
            continue
        groww_symbol = row.get("groww_symbol", "")
        expiry = clean_date(row.get("expiry_date"))
        try:
            strike = float(row.get("strike_price", ""))
        except ValueError:
            continue
        if (not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", groww_symbol)
                or not groww_symbol.startswith(identity["exchange"] + "-" + identity["index"] + "-")
                or not groww_symbol.endswith("-" + symbol[-2:])
                or not expiry or not math.isfinite(strike) or strike <= 0):
            continue
        result[symbol] = {"groww_symbol": groww_symbol, "expiry": expiry, "strike": strike}
    return result


def download_instrument_text():
    import requests
    # The SDK instrument loader writes a CSV in the working directory; avoid it.
    data, count, started = [], 0, time.monotonic()
    with requests.get(INSTRUMENT_CSV, stream=True, timeout=5, allow_redirects=False) as response:
        response.raise_for_status()
        for chunk in response.iter_content(1_048_576):
            count += len(chunk)
            if count > 64_000_000 or time.monotonic() - started > 15:
                raise ValueError("bounded instrument master required")
            data.append(chunk)
    return b"".join(data).decode("utf-8-sig")


def download_metadata(requested):
    return instrument_metadata(download_instrument_text(), requested)


def download_calendar():
    dates = {index:set() for index in ("NIFTY","SENSEX")}
    for row in csv.DictReader(io.StringIO(download_instrument_text())):
        index = row.get("underlying_symbol")
        if index in dates and row.get("exchange") == EXCHANGES[index] and row.get("segment") == "FNO":
            expiry = clean_date(row.get("expiry_date"))
            if expiry and str(row.get("trading_symbol","")).endswith(("CE","PE")):
                dates[index].add(expiry)
    return {index:sorted(values) for index,values in dates.items()}


def five_minute_candles(payload, end, *, lookback_days=0):
    if not isinstance(payload, dict) or payload.get("interval_in_minutes") != 5:
        raise ValueError("five-minute candles required")
    rows = payload.get("candles")
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("bounded candles required")
    result = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            raise ValueError("invalid candle")
        if isinstance(row[0], str):
            at = datetime.fromisoformat(row[0])
            at = at.replace(tzinfo=IST) if at.tzinfo is None else at.astimezone(IST)
        elif finite(row[0]) is not None:
            at = datetime.fromtimestamp(row[0], IST)  # Documented epoch seconds.
        else:
            raise ValueError("invalid candle timestamp")
        values = row[1:5]
        if (at.second or at.microsecond or at.minute % 5
                or not all(finite(v) is not None and v >= 0 for v in values)):
            raise ValueError("invalid five-minute candle")
        opened, high, low, closed = values
        if not low <= min(opened, closed) <= max(opened, closed) <= high:
            raise ValueError("inconsistent OHLC")
        # Naive timestamps are assumed IST/bar-start, with unverified provenance.
        if end.date()-timedelta(days=lookback_days) <= at.date() <= end.date() and at + timedelta(minutes=5) <= end:
            result[at.isoformat()] = dict(at=at.isoformat(), open=opened, high=high, low=low, close=closed)
    return [result[at] for at in sorted(result)]


def collect_charts(market, snapshot, now, *, previous=None, refresh_all=True):
    """Five-minute history for indices and confirmed broker-order contracts only."""
    previous = previous or {}
    charts = {}
    day = now.astimezone(IST).date()
    start = f"{day} 09:15:00"
    end = min(now.astimezone(IST), datetime.combine(day, datetime.min.time(), IST) + timedelta(hours=16))
    if end.strftime("%H:%M:%S") <= "09:15:00":
        return {key:{"candles":[],"status":"WAITING_FOR_SESSION"} for key in
                list(INDICES)+[o["symbol"] for o in snapshot.get("ordered_options",[])]}

    def candles(key, groww_symbol, exchange, segment):
        if not refresh_all and previous.get(key, {}).get("groww_symbol") == groww_symbol:
            charts[key] = previous[key]
            return
        try:
            market.limiter.wait()
            raw = market.groww.get_historical_candles(exchange=exchange, segment=segment,
                groww_symbol=groww_symbol, start_time=f"{day-timedelta(days=7)} 09:15:00" if segment == "CASH" else start,
                end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                candle_interval="5minute", timeout=5)
            charts[key] = {"candles": five_minute_candles(raw, end), "groww_symbol": groww_symbol,
                "received_at": datetime.now(timezone.utc).isoformat(), "source": "GROWW_HISTORICAL_CANDLES",
                "interval_minutes": 5, "timestamp_semantics_verified": False}
            if segment == "CASH":
                charts[key]["context_candles"] = five_minute_candles(raw,end,lookback_days=7)
        except Exception as exc:
            charts[key] = {"candles": [], "failure": safe_error(exc)}
    for index in INDICES:
        exchange = EXCHANGES[index]
        candles(index, exchange + "-" + index, exchange, "CASH")
    for option in snapshot.get("ordered_options", []):
        if option.get("groww_symbol"):
            candles(option["symbol"], option["groww_symbol"], option["exchange"], "FNO")
        else:
            charts[option["symbol"]] = {"candles": [], "status": "CONTRACT_NOT_CONFIRMED"}
    return charts


class DashboardCollector:
    def __init__(self, market, *, clock=lambda: datetime.now(timezone.utc), metadata_loader=download_metadata,
                 background_history=True, journal=None, calendar_loader=download_calendar, smart_loader=None,
                 before_ownership=None):
        self.market, self.clock, self.metadata_loader = market, clock, metadata_loader
        self.metadata, self.charts, self.chart_bucket, self.metadata_attempt = {}, {}, None, None
        self.sequence = 0
        self.history_pool = ThreadPoolExecutor(max_workers=1) if background_history else None
        self.history_future = None
        self.journal, self.calendar_loader = journal, calendar_loader
        self.expiry_evidence, self.expiry_day, self.expiry_next_at = {}, None, 0
        self.smart_loader = smart_loader
        self.before_ownership = before_ownership
        from .session import SessionJournal
        self.sessions = SessionJournal(journal.store) if journal else None
        self.pnl_journal = broker_pnl.PnlJournal(journal.store) if journal else None
        self.account_loader = None
        self.news_loader = None
        self.funds, self.funds_next_at = {}, 0

    def close(self):
        if self.history_pool:
            self.history_pool.shutdown(wait=True, cancel_futures=True)

    def sample(self):
        started = self.clock()
        result = {"checked_at": started.isoformat(), "execution_mode": "OBSERVE", "order_capability": False,
                  "probes": {}, "ordered_options": [], "poll_interval_seconds": POLL_SECONDS}
        probes = result["probes"]

        def read(name, method, summarize, **kwargs):
            at = self.clock()
            try:
                self.market.limiter.wait()
                raw = method(timeout=5, **kwargs)
                probes[name] = {"ok": True, "value": summarize(raw)}
                return raw
            except Exception as exc:
                probes[name] = safe_error(exc)
                return None
            finally:
                probes[name].update(request_started_at=at.isoformat(), received_at=self.clock().isoformat())

        def index_quote(index):
            read(index + "_quote", self.market.groww.get_quote, lambda raw: quote_summary(raw, self.clock()),
                 exchange=EXCHANGES[index], segment="CASH", trading_symbol=index)
        def money():
            self.funds_next_at = started.timestamp() + 5
            method = getattr(self.market.groww,"get_available_margin_details",None)
            raw = read("available_money",method,funds_summary) if method else None
            self.funds = dict(probes["available_money"]["value"],status="AVAILABLE",
                received_at=probes["available_money"]["received_at"]) if raw is not None else {"status":"UNAVAILABLE"}
        def order_pages():
            orders, complete, succeeded = [], True, 0
            for page in range(4):
                def order_summary(raw):
                    if not isinstance(raw.get("order_list"), list):
                        raise ValueError("order list unavailable")
                    return {"record_count": len(raw["order_list"])}
                raw = read("orders_page_" + str(page), self.market.groww.get_order_list, order_summary,
                           page=page, segment="FNO")
                if raw is None:
                    complete = False
                    break
                succeeded += 1
                rows = raw["order_list"]
                orders.extend(rows[:100])
                complete &= len(rows) <= 100
                # SDK 1.5.0 ignores page_size; an empty page proves pagination is complete.
                if not rows:
                    break
            else:
                complete = False
            return orders, complete, succeeded

        def position_summary(raw):
            if not isinstance(raw.get("positions"), list):
                raise ValueError("positions unavailable")
            return {"record_count": len(raw["positions"])}
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = [pool.submit(index_quote,index) for index in INDICES]
            if started.timestamp() >= self.funds_next_at:
                jobs.append(pool.submit(money))
            orders_job = pool.submit(order_pages)
            positions_job = pool.submit(read,"positions",self.market.groww.get_positions_for_user,position_summary,segment="FNO")
            for job in jobs:
                job.result()
            orders, complete, succeeded = orders_job.result()
            raw = positions_job.result()
        result["funds"] = self.funds
        positions, orders_complete = [], complete
        if raw is None:
            complete = False
        else:
            succeeded += 1
            positions = raw["positions"][:1000]
            complete &= len(raw["positions"]) <= 1000
        result["positions_status"] = "AVAILABLE" if raw is not None and len(raw["positions"]) <= 1000 else "UNAVAILABLE"
        # Protection and exact-position quote reads overlap below; ownership
        # reconciliation still uses this cycle's complete positions/order reads.
        smart = []
        # Match existing standard SLs first; append smart levels after readback.
        ordered = ordered_contracts(orders, positions, smart)
        for option in ordered:
            option["protective_levels_status"] = "AVAILABLE" if orders_complete and probes.get("smart_orders", {}).get("ok") else "UNAVAILABLE"
        complete &= len(ordered) <= 40
        ordered = ordered[:40]
        result["orders_status"] = "AVAILABLE" if complete else "INCOMPLETE" if succeeded else "UNAVAILABLE"
        if self.journal:
            if self.before_ownership:
                self.before_ownership()
            ownership = self.journal.ownership(orders,positions,complete=complete)
            for option in ordered:
                option["ownership"] = ownership.get(option["symbol"],"MANUAL_OR_UNKNOWN_PROTECTED")
            if self.expiry_day != started.astimezone(IST).date() or started.timestamp() >= self.expiry_next_at:
                self.expiry_day = started.astimezone(IST).date()
                self.expiry_evidence = {}
                try:
                    master = self.calendar_loader()
                    for index in ("NIFTY","SENSEX"):
                        raw = read(index+"_expiries",self.market.groww.get_expiries,
                            lambda r:{"expiries":sorted({clean_date(d) for d in r["expiries"] if clean_date(d)})},
                            exchange=EXCHANGES[index],underlying_symbol=index,year=started.astimezone(IST).year)
                        today = started.astimezone(IST).date()
                        upcoming = sorted(d for d in master[index] if d >= str(today))
                        api_dates = sorted(d for d in raw["expiries"] if d >= str(today)) if raw else []
                        if raw is not None and not api_dates and upcoming and upcoming[0][:4] == str(today.year+1):
                            raw = read(index+"_next_year_expiries",self.market.groww.get_expiries,
                                lambda r:{"expiries":sorted({clean_date(d) for d in r["expiries"] if clean_date(d)})},
                                exchange=EXCHANGES[index],underlying_symbol=index,year=today.year+1)
                            api_dates = sorted(d for d in raw["expiries"] if d >= str(today)) if raw else []
                        # Feeds have different distant-contract horizons. Verify this
                        # month and the nearest actual expiry; never certify later dates.
                        current = sorted({d for d in upcoming if d[:7] == str(today)[:7]} | set(upcoming[:1]))
                        api_relevant = sorted({d for d in api_dates if d[:7] == str(today)[:7]} | set(api_dates[:1]))
                        confirmed = bool(upcoming and api_dates and current == api_relevant)
                        self.expiry_evidence[index] = {"status":"CONFIRMED_CURRENT_MASTER" if confirmed else "UNKNOWN_BLOCKED",
                            "expiries":current if confirmed else [], "day_jst":started.astimezone(JST).date().isoformat(),
                            "comparison_scope":"CURRENT_MONTH_AND_NEAREST_LISTED_EXPIRY",
                            "received_at":self.clock().isoformat()}
                except Exception as exc:
                    self.expiry_evidence = {}
                    probes["expiry_calendar"] = safe_error(exc)
                calendar_complete = all(self.expiry_evidence.get(i,{}).get("status") == "CONFIRMED_CURRENT_MASTER"
                                        for i in ("NIFTY","SENSEX"))
                self.expiry_next_at = started.timestamp() + (3600 if calendar_complete else 300)
            result["expiry_evidence"] = self.expiry_evidence
        bucket = int(started.timestamp()) // 300
        missing = {o["symbol"]: o for o in ordered if o["symbol"] not in self.metadata}
        metadata_attempt = (bucket, tuple(sorted(missing)))
        if missing and self.metadata_attempt != metadata_attempt:
            self.metadata_attempt = metadata_attempt
            try:
                self.metadata.update(self.metadata_loader(missing))
            except Exception as exc:
                probes["instrument_master"] = safe_error(exc)
        def option_quote(option):
            option.update(self.metadata.get(option["symbol"], {}))
            read(option["symbol"] + "_quote", self.market.groww.get_quote,
                       lambda value: quote_summary(value, self.clock()), trading_symbol=option["symbol"],
                       exchange=option["exchange"], segment="FNO")
        unique = {option["symbol"]: option for option in ordered}
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(option_quote,option) for option in unique.values()]
            smart_future = pool.submit(self.smart_loader,positions,started) if self.smart_loader and positions else None
            for job in jobs:
                job.result()
            if smart_future:
                try:
                    smart = smart_future.result()
                    probes["smart_orders"] = {"ok":True,"record_count":len(smart)}
                except Exception as exc:
                    probes["smart_orders"] = safe_error(exc)
        if smart_future:
            enriched = {(o["symbol"],o["side"]):o for o in ordered_contracts(orders,positions,smart)}
            for option in ordered:
                option["price_levels"] = enriched.get((option["symbol"],option["side"]),{}).get("price_levels",option["price_levels"])
                option["protective_levels_status"] = "AVAILABLE" if orders_complete and probes["smart_orders"]["ok"] else "UNAVAILABLE"
        for option in ordered:
            option.update(self.metadata.get(option["symbol"], {}))
            option["quote"] = probes[option["symbol"] + "_quote"].get("value", {})
            probe = probes[option["symbol"] + "_quote"]
            option["received_at"] = probe["received_at"] if probe["ok"] else None
        if self.journal:
            # Private execution evidence never grants a desktop order capability
            # and is omitted by public_response. Preserve product and signed net
            # quantities; an average/side alone cannot prove an owned position.
            result["execution_observation"] = dict(complete=complete,
                received_at=probes["positions"]["received_at"],
                orders_complete=orders_complete,
                orders_received_at=min(p['received_at'] for k,p in probes.items() if k.startswith('orders_page_')),
                orders=[{k:r.get(k) for k in ('groww_order_id','order_reference_id','trading_symbol',
                    'segment','exchange','product','transaction_type','quantity','filled_quantity','order_status')}
                    for r in orders if isinstance(r,dict)],
                expiry_evidence=self.expiry_evidence,
                positions=[dict(symbol=r.get("trading_symbol"),quantity=r.get("quantity"),
                    product=r.get("product"),ownership=ownership.get(r.get("trading_symbol"),"MANUAL_OR_UNKNOWN_PROTECTED"))
                    for r in positions if option_identity(r) and r.get("quantity")],
                funds=self.funds,
                books={o["symbol"]:dict(bid=o["quote"].get("bid_price"),ask=o["quote"].get("offer_price"),
                    bid_quantity=o["quote"].get("bid_quantity"),ask_quantity=o["quote"].get("offer_quantity"),
                    received_at=o["received_at"]) for o in ordered if o.get("received_at")})
        result["ordered_options"] = ordered
        if self.history_future and self.history_future.done():
            self.charts = self.history_future.result()
            self.history_future = None
        chart_keys = set(INDICES) | {o["symbol"] for o in ordered}
        newly_confirmed = any(o.get("groww_symbol") and self.charts.get(o["symbol"], {}).get("groww_symbol")
                              != o["groww_symbol"] for o in ordered)
        needs_history = bucket != self.chart_bucket or not chart_keys <= self.charts.keys() or newly_confirmed
        if needs_history and self.history_future is None:
            if self.history_pool:
                # Historical reads run independently so a five-minute boundary does not stop quotes.
                self.history_future = self.history_pool.submit(collect_charts, self.market, result, self.clock(),
                    previous=self.charts, refresh_all=bucket != self.chart_bucket)
            else:
                self.charts = collect_charts(self.market, result, self.clock(), previous=self.charts,
                                            refresh_all=bucket != self.chart_bucket)
            self.chart_bucket = bucket
        result["charts"] = {key: self.charts[key] for key in chart_keys if key in self.charts}
        result["history_refreshing"] = self.history_future is not None
        self.sequence += 1
        finished = self.clock()
        result.update(sequence=self.sequence, finished_at=finished.isoformat(),
                      collection_duration_seconds=(finished-started).total_seconds(),
                      status="READ_ONLY_DATA_AVAILABLE" if all(probes[key + "_quote"]["ok"] for key in INDICES)
                      else "PARTIAL_MARKET_DATA")
        owners = self.journal.pnl_owners(orders,positions,complete=complete) if self.journal else {}
        result["broker_pnl"], legs = broker_pnl.summarize(positions,ordered,owners,now=finished,
            received_at=probes["positions"]["received_at"], complete=complete and result["positions_status"] == "AVAILABLE")
        for option in ordered:
            option["open_pnl_inr"] = legs.get((option["symbol"],option["side"]))
            option["pnl_bucket"] = owners.get(option["symbol"],"unassigned")
        if self.pnl_journal:
            self.pnl_journal.record(result["broker_pnl"],finished)
        if self.sessions:
            try:
                self.sessions.record(result,finished)
                self.sessions.publish(result,finished,
                    account=self.account_loader() if self.account_loader else None,
                    news=self.news_loader() if self.news_loader else None)
                result["journal_status"] = "RECORDED_AND_PUBLISHED"
            except Exception as exc:
                result["journal_status"] = "JOURNAL_"+type(exc).__name__
        completed = self.clock()
        result["sample_completed_at"] = completed.isoformat()
        result["journal_duration_seconds"] = (completed-finished).total_seconds()
        result["cycle_duration_seconds"] = (completed-started).total_seconds()
        return result


def write_snapshot(output, result):
    temporary = output.with_suffix(".tmp")
    temporary.write_text(dumps(result) + "\n", encoding="utf-8")
    os.replace(temporary, output)


def viewer_active(lease_file, *, now=None):
    try:
        age = (time.time() if now is None else now) - lease_file.stat().st_mtime
        return 0 <= age <= LEASE_SECONDS
    except FileNotFoundError:
        return False


def polling_loop(collector, output, active, *, monotonic=time.monotonic, sleep=time.sleep):
    """One authenticated, non-overlapping worker; slow reads delay the next cycle."""
    while active():
        started = monotonic()
        write_snapshot(output, collector.sample())
        sleep(max(0, POLL_SECONDS - (monotonic()-started)))


def run_reader(output, *, lease_file=None, root=None):
    prior = logging.root.manager.disable
    with open(os.devnull, "w") as muted, contextlib.redirect_stdout(muted), contextlib.redirect_stderr(muted):
        logging.disable(logging.CRITICAL)
        try:
            if importlib.metadata.version("growwapi") != SDK_VERSION:
                raise ValueError("SDK version mismatch")
            credentials = read_credentials(sys.stdin)
            from growwapi import GrowwAPI
            from ..brokers.groww_data import GrowwMarketData
            deadline = datetime.now(timezone.utc) + (timedelta(hours=8) if lease_file else timedelta(seconds=180))
            with readonly_transport([], history=True, dashboard=True, deadline=deadline, timeout_seconds=5):
                try:
                    token = GrowwAPI.get_access_token(api_key=credentials["api_key"], secret=credentials["api_secret"])
                finally:
                    credentials.clear()
                journal = PcJournal(Path(root)/".agent-state"/"pc-monitor.sqlite3") if root else None
                market = GrowwMarketData(token)
                from .smart_read import SmartOrderReader
                collector = DashboardCollector(market, background_history=lease_file is not None, journal=journal,
                    smart_loader=SmartOrderReader(market.groww,market.limiter))
                if root:
                    def private_json(name):
                        p=Path(root)/".agent-state"/name
                        return load_json(p) if p.is_file() else None
                    collector.account_loader=lambda:private_json("dashboard-account.json")
                    collector.news_loader=lambda:private_json("pc-news.json")
                del token
                try:
                    if lease_file is None:
                        write_snapshot(output, collector.sample())
                    else:
                        polling_loop(collector, output, lambda: viewer_active(lease_file)
                                     and datetime.now(timezone.utc) < deadline)
                finally:
                    collector.close()
        except Exception as exc:
            write_snapshot(output, {"finished_at": datetime.now(timezone.utc).isoformat(), "status": "BLOCKED",
                           "failure": safe_error(exc), "orders_status": "UNAVAILABLE", "order_capability": False})
            return 2
        finally:
            logging.disable(prior)
    return 0


def capture(output):
    if output.exists() or not output.parent.is_dir():
        raise ValueError("new private output required")
    code = run_reader(output)
    result = load_json(output)
    print(dumps({"status": result["status"], "output": str(output), "order_capability": False}))
    return code if code else 0 if result["status"] == "READ_ONLY_DATA_AVAILABLE" else 2


class DashboardState:
    def __init__(self, root, *, offline=False, background=False):
        self.root, self.offline = Path(root).resolve(), offline
        self.directory = self.root / ".agent-state" / "dashboard-captures"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lease_file = self.directory / f"viewer-{os.getpid()}.lease"
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.refreshing = False
        self.last_refresh = -1000.0
        self.retry_after = 0.0
        self.last_error = None
        self.cache, self.signature = None, None
        self.background = background and not offline
        self.monitor = PcMonitor(self.root) if background else None
        from .session import ObservedLines, SessionJournal
        line_store = self.monitor.journal.store if self.monitor else PcJournal(self.root/".agent-state/pc-monitor.sqlite3").store
        SessionJournal(line_store)
        self.observed_lines = ObservedLines(line_store)
        self.pnl_lines = broker_pnl.PnlJournal(line_store)
        from .capital import CapitalLedger
        self.capital_ledger = CapitalLedger(line_store)
        self.snapshot = None
        self.stop_event = threading.Event()
        self.background_thread = None
        self.analysis_thread = None
        self.news_thread = None
        self.analysis_error = None
        from .oracle_link import RemoteViewer
        self.remote = RemoteViewer(self.root, self.pnl_lines.store) if (self.root/".agent-state/oracle-viewer.json").is_file() and not offline else None

    def read(self):
        if self.remote:
            return self.remote.read()
        files = list(self.directory.glob("market-check-*.json"))
        files += list((self.root / ".agent-state" / "market-checks").glob("market-check-*.json"))
        latest = max(files, key=lambda p: p.stat().st_mtime_ns, default=None)
        account_path = self.root / ".agent-state" / "dashboard-account.json"
        protocol_path = self.root / "config" / "owner_strategies.json"
        signature = (str(latest), latest.stat().st_mtime_ns if latest else None,
            account_path.stat().st_mtime_ns if account_path.exists() else None,
            protocol_path.stat().st_mtime_ns, datetime.now(IST).date())
        with self.lock:
            if self.signature != signature or self.cache is None:
                protocol = load_json(protocol_path, 16384)
                snapshot = load_json(latest) if latest else None
                self.snapshot = snapshot
                try:
                    account = load_json(account_path) if account_path.exists() else None
                    self.cache = view_model(snapshot, protocol, account)
                except ValueError:
                    self.cache = view_model(snapshot, protocol)
                    self.cache["account"]["status"] = "INVALID_LEDGER"
                self.signature = signature
            result = json.loads(dumps(self.cache))
            live_lines = self.observed_lines.read(stamp(result["as_of"])) if result["as_of"] else {}
            for market in result["markets"]:
                market["bar_series"] = market["series"]
                market["series_source"] = "GROWW_HISTORICAL_5_MIN_CLOSE"
                if len(live_lines.get(market["index"],[])) >= 2:
                    market["series"] = live_lines[market["index"]]
                    market["series_source"] = "GROWW_OBSERVED_LTP"
                for option in market["options"]:
                    option["bar_series"] = option["series"]
                    option["series_source"] = "GROWW_HISTORICAL_5_MIN_CLOSE"
                    if len(live_lines.get(option["symbol"],[])) >= 2:
                        option["series"] = live_lines[option["symbol"]]
                        option["series_source"] = "GROWW_OBSERVED_LTP"
                        option["series_status"] = "AVAILABLE"
                    option["series_updated_at"] = option["series"][-1]["at"] if option["series"] else None
            result["chart_points"] = "OBSERVED_LTP_WITH_5_MIN_GRID"
            at = result["as_of"]
            age = (datetime.now(timezone.utc) - stamp(at)).total_seconds() if at else None
            result["freshness"] = "NO_DATA" if age is None else "FUTURE" if age < 0 else "RECENT" if age <= 15 else "STALE"
            result.update(refreshing=self.refreshing, refresh_error=self.last_error, offline=self.offline)
            result["funds"] = public_funds(self.snapshot or {}, datetime.now(timezone.utc))
            current = datetime.now(timezone.utc)
            result["broker_pnl"] = broker_pnl.public((self.snapshot or {}).get("broker_pnl"),current)
            result["broker_pnl"]["series"] = self.pnl_lines.series(current)
            result["capital_summary"] = self.capital_ledger.summary(current)
            if result["broker_pnl"]["status"] != "AVAILABLE":
                for market in result["markets"]:
                    for option in market["options"]:
                        option["open_pnl_inr"] = None
            result["background_monitor"] = self.background
            if self.monitor:
                result["control"] = self.monitor.tick(self.snapshot,load_json(protocol_path,16384),datetime.now(timezone.utc))
                result["control"]["analysis_error"] = self.analysis_error
                result["control"]["reader_active"] = self.refreshing
            return result

    def record_withdrawal(self, body):
        if self.remote:
            return self.remote.record_withdrawal(body)
        return self.capital_ledger.record_withdrawal(body,datetime.now(timezone.utc))

    def record_investment(self, body):
        if self.remote:
            return self.remote.record_investment(body)
        return self.capital_ledger.record_investment(body,datetime.now(timezone.utc))

    def start_background(self):
        if self.background and self.background_thread is None:
            if self.remote:
                self.background_thread = threading.Thread(target=self.remote.run,args=(self.stop_event,),daemon=True)
                self.background_thread.start()
                return
            if premium_strategy.load(self.root) is None:
                self.news_thread = threading.Thread(target=self.monitor.news.run,args=(self.stop_event,),daemon=True,
                    name="trading-pc-news")
                self.news_thread.start()
            self.background_thread = threading.Thread(target=self._monitor_loop,daemon=True)
            self.background_thread.start()

    def algo_set(self, enabled):
        """Persist intent without enabling a blocked broker executor."""
        if self.remote:
            return self.remote.set_intent(enabled)
        now = datetime.now(timezone.utc)
        policy = premium_strategy.load(self.root)
        if policy is None:
            return {"status":"BLOCKED","execution_enabled":False,"broker_writes":False,
                "blockers":["PREMIUM_POLICY_REQUIRED"]}
        view = self.read()
        owner_intent = premium_strategy.set_intent(self.pnl_lines.store,enabled,now)
        risk = view.get("control",{}).get("news",{}).get("risk","UNKNOWN")
        result = premium_strategy.readiness(policy,now,paused=(self.root/".trader-paused").exists(),
            offline=self.offline,fresh=view["freshness"] == "RECENT",news_risk=risk,desired_enabled=enabled)
        result["owner_intent"] = owner_intent
        result["requested_at"] = now.isoformat()
        self.pnl_lines.store.set_meta("premium-algo-start",result)
        return result

    def algo_start(self):
        return self.algo_set(True)

    def strategy_switch(self,body):
        from .strategy_controls import validate_command,set_switch
        validate_command(body)
        if self.remote: return self.remote.strategy_switch(body)
        return set_switch(self.pnl_lines.store,body,datetime.now(timezone.utc))

    def close(self):
        self.stop_event.set()
        self.lease_file.unlink(missing_ok=True)

    def _monitor_loop(self):
        runner, active_pin, failed_pin = None, None, None
        while not self.stop_event.is_set():
            cfg = None
            try:
                now = datetime.now(timezone.utc)
                if collection_window(now):
                    self.refresh()
                self.read()
                cfg = self.monitor.read_settings()["codex"]
                if cfg != active_pin:
                    runner, active_pin = None, cfg
                if cfg is None or cfg != failed_pin:
                    self.analysis_error = None
                if cfg and cfg != failed_pin and in_window(now,research=True):
                    if runner is None:
                        from .runner import CodexRunner
                        from .pc_control import ANALYSIS_PROMPT, ANALYSIS_SCHEMA
                        runner = CodexRunner(cfg["executable"],cfg["home"],expected_version=cfg["version"],
                            expected_sha256=cfg["sha256"],schema=ANALYSIS_SCHEMA,prompt=ANALYSIS_PROMPT)
                        if not runner.preflight()["authenticated"]:
                            raise ValueError("analyst authentication required")
                        self.analysis_error = None
                    if self.analysis_thread is None or not self.analysis_thread.is_alive():
                        self.analysis_thread = threading.Thread(target=self.monitor.analyze_once,args=(runner,now),daemon=True)
                        self.analysis_thread.start()
            except Exception as exc:
                self.analysis_error = "MONITOR_"+type(exc).__name__
                # A malformed local file must not kill the resident monitor.
                runner, failed_pin = None, cfg
            self.stop_event.wait(POLL_SECONDS)

    def refresh(self):
        if self.remote:
            return "REMOTE_OBSERVER_RUNNING"
        with self.lock:
            if self.offline:
                return "OFFLINE"
            self.lease_file.touch(mode=0o600)
            if self.refreshing:
                return "IN_PROGRESS"
            if time.monotonic() < self.retry_after:
                return "RETRY_BACKOFF"
            if time.monotonic() - self.last_refresh < POLL_SECONDS:
                return "RECENT_REQUEST"
            self.refreshing, self.last_refresh, self.last_error = True, time.monotonic(), None
        threading.Thread(target=self._capture, daemon=True).start()
        return "STARTED"

    def _capture(self):
        try:
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if os.name != "nt" or not shell:
                raise RuntimeError("Windows DPAPI reader unavailable")
            run = subprocess.run([shell, "-NoProfile", "-File", str(self.root / "scripts" / "Read-GrowwMarket.ps1"),
                "-Dashboard", "-Watch", "-LeaseFile", str(self.lease_file), "-OutputDirectory", str(self.directory)], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8 * 3600 + 180,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if run.returncode:
                self.last_error = "BROKER_READ_FAILED"
                self.retry_after = time.monotonic() + 60
        except Exception:
            self.last_error = "LOCAL_READER_UNAVAILABLE"
            self.retry_after = time.monotonic() + 60
        finally:
            with self.lock:
                self.refreshing = False


def handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, value, kind="application/json; charset=utf-8"):
            # Windows may reset a connection closed with an unread POST body,
            # hiding an otherwise valid 400/403 response. Discard only a small,
            # declared body after rejection; never parse or act on it.
            if self.command == "POST" and not getattr(self, "body_consumed", False):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if 0 < length <= 4096 and not self.headers.get("Transfer-Encoding"):
                        previous = self.connection.gettimeout()
                        try:
                            self.connection.settimeout(1)
                            self.rfile.read(length)
                        finally:
                            self.connection.settimeout(previous)
                except (ValueError, OSError):
                    self.close_connection = True
            body = dumps(value).encode() if isinstance(value, (dict, list)) else value
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def local(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            return (self.headers.get("Host") == expected and self.client_address[0] == "127.0.0.1"
                    and (not origin or origin == "http://" + expected)
                    and self.headers.get("Sec-Fetch-Site", "same-origin") not in ("cross-site", "same-site"))

        def do_GET(self):
            if not self.local():
                return self.respond(403, {"status": "LOCAL_ACCESS_ONLY"})
            if self.path == "/report":
                import base64
                from .visual_report import build
                bundle=build(state.read(),datetime.now(JST).date().isoformat())
                content=bundle["mail"]["html"].replace("19:30 JST report","Visual email preview · current observations")
                content=content.replace('<html><body','<html><head><title>Options Trader · Email preview</title></head><body',1)
                end=content.index('>',content.index('<body'))+1
                content=content[:end]+'<div style="padding:12px;text-align:center"><a href="/">← Dashboard</a></div>'+content[end:]
                for attachment in bundle["mail"]["attachments"]:
                    content=content.replace("cid:"+attachment["content_id"],"data:image/png;base64,"+attachment["content"])
                return self.respond(200,content.encode(),"text/html; charset=utf-8")
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                "/live-setup": ("live-setup.html", "text/html; charset=utf-8"),
                "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
                "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
                "/app-icon.svg":("app-icon.svg","image/svg+xml")}
            if self.path in assets:
                name, kind = assets[self.path]
                body = (ASSETS / name).read_bytes()
                if name == "index.html":
                    body = body.replace(b"__LOCAL_TOKEN__", state.token.encode())
                return self.respond(200, body, kind)
            if self.path == "/api/dashboard":
                try:
                    return self.respond(200, state.read())
                except Exception:
                    return self.respond(503, {"status": "LOCAL_DATA_UNAVAILABLE"})
            if self.path == '/api/impulse/events':
                self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Cache-Control','no-store');self.end_headers()
                previous=None
                while not state.stop_event.is_set():
                    value=state.remote.impulse_read() if state.remote else {'format':'trading-impulse-v1','transport_status':'ORACLE_CONNECTION_REQUIRED','indices':{},'broker_writes':False}
                    body=json.dumps(value,separators=(',',':'))
                    if body!=previous:
                        try:self.wfile.write(('data: '+body+'\n\n').encode());self.wfile.flush()
                        except (BrokenPipeError,ConnectionResetError):break
                        previous=body
                    state.stop_event.wait(.1)
                return
            return self.respond(404, {"status": "NOT_FOUND"})

        def do_POST(self):
            if not self.local() or not secrets.compare_digest(self.headers.get("X-Local-Token", ""), state.token):
                return self.respond(403, {"status": "LOCAL_ACCESS_ONLY"})
            if self.path == '/api/strategies/switch':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if (not 0<length<=256 or self.headers.get('Transfer-Encoding')
                            or self.headers.get('Content-Type','').split(';')[0]!='application/json'):
                        raise ValueError('INVALID_STRATEGY_SWITCH')
                    raw=self.rfile.read(length);self.body_consumed=True
                    result=state.strategy_switch(json.loads(raw))
                    return self.respond(409 if result['status']=='REVISION_CONFLICT' else 200,result)
                except (ValueError,TypeError,KeyError):
                    return self.respond(400,{'status':'INVALID_STRATEGY_SWITCH','broker_writes':False})
                except Exception:
                    return self.respond(503,{'status':'STRATEGY_CONTROL_UNAVAILABLE','broker_writes':False})
            if self.path in ("/api/capital/withdrawals", "/api/capital/investments"):
                kind = "investment" if self.path.endswith("investments") else "withdrawal"
                try:
                    length=int(self.headers.get("Content-Length","0"))
                    if not 0 < length <= 1024 or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type","").split(";")[0] != "application/json":
                        raise ValueError("INVALID_REQUEST")
                    raw=self.rfile.read(length)
                    self.body_consumed=True
                    body=json.loads(raw)
                    return self.respond(200,getattr(state, "record_" + kind)(body))
                except (ValueError,TypeError) as exc:
                    reasons={"INVALID_AMOUNT","POSITIVE_AMOUNT_REQUIRED","INVALID_RECORD_ID","INVALID_WITHDRAWAL_DATE","FUTURE_WITHDRAWAL_DATE","CAPITAL_NOT_CONFIGURED","WITHDRAWAL_ID_CONFLICT","WITHDRAWAL_LIMIT","INVALID_INVESTMENT_DATE","FUTURE_INVESTMENT_DATE","INVESTMENT_ID_CONFLICT","INVESTMENT_LIMIT","CAPITAL_RECORD_ID_CONFLICT"}
                    reason=str(exc) if str(exc) in reasons else "INVALID_REQUEST"
                    return self.respond(409 if reason.endswith("_ID_CONFLICT") else 400,{"status":"INVALID_"+kind.upper(),"reason":reason,"money_moved":False,"broker_writes":False})
                except Exception:
                    return self.respond(503,{"status":"ACCOUNTING_UNAVAILABLE","money_moved":False})
            if self.path not in ("/api/refresh","/api/algo/start","/api/algo/stop") or self.headers.get("Content-Length", "0") != "0":
                return self.respond(400, {"status": "UNSUPPORTED_REQUEST"})
            if self.path in ("/api/algo/start", "/api/algo/stop"):
                try:
                    return self.respond(200, state.algo_set(self.path == "/api/algo/start"))
                except Exception:
                    return self.respond(503, {"status":"TRANSPORT_FAILED","broker_writes":False})
            self.respond(202, {"status": state.refresh(), "order_capability": False})
    return Handler


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    sub = subs.add_parser("capture")
    sub.add_argument("--credentials-stdin", action="store_true", required=True)
    sub.add_argument("--output", type=Path, required=True)
    sub = subs.add_parser("watch")
    sub.add_argument("--credentials-stdin", action="store_true", required=True)
    sub.add_argument("--directory", type=Path, required=True)
    sub.add_argument("--lease-file", type=Path, required=True)
    sub.add_argument("--root", type=Path)
    sub = subs.add_parser("serve")
    sub.add_argument("--root", type=Path, default=Path.cwd())
    sub.add_argument("--port", type=int, default=8765)
    sub.add_argument("--offline", action="store_true")
    sub.add_argument("--background", action="store_true")
    args = parser.parse_args()
    if args.command == "capture":
        return capture(args.output)
    if args.command == "watch":
        directory, lease = args.directory.resolve(), args.lease_file.resolve()
        if not directory.is_dir() or lease.parent != directory or not viewer_active(lease):
            raise ValueError("active local viewer lease required")
        return run_reader(directory / f"market-check-live-{lease.stem}.json", lease_file=lease,root=args.root)
    if not 1024 <= args.port <= 65535:
        raise ValueError("local port outside range")
    state = DashboardState(args.root, offline=args.offline,background=args.background)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(state))
    print(dumps({"status": "LOCAL_DASHBOARD_READY", "url": f"http://127.0.0.1:{args.port}", "order_capability": False}), flush=True)
    state.start_background()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        state.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(dumps({"status": "BLOCKED", "failure": safe_error(exc)}))
        raise SystemExit(2) from None
