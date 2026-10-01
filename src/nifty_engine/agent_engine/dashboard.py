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

ASSETS = Path(__file__).with_name("dashboard_assets")
STRATEGIES = (
    ("everyday", "Everyday hedged call", "13:15 JST · NIFTY +400 / SENSEX +800"),
    ("late_session", "Late-session decay", "17:45–18:45 JST · Hedged calls"),
    ("swing", "Swing call spread", "Overnight · 10 / 20 strike study"),
    ("expiry_reversal", "Expiry reversal", "SENSEX · 18:50 JST onward"),
)
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
        # Only real broker order/position contracts enter buy/sell charts.
        ordered = snapshot.get("ordered_options", [])
        for item in (ordered if isinstance(ordered, list) else [])[:40]:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            if item.get("index") != index or item.get("side") not in ("BUY","SELL"):
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
                "series_status": "AVAILABLE" if series else "LOADING_OPTION_HISTORY" if snapshot.get("history_refreshing") is True
                    and symbol not in charts else "NO_OPTION_HISTORY",
                "book_time_verified": False, "order_status":item.get("order_status") if item.get("order_status") in ("OPEN","TRIGGER_PENDING","EXECUTED","DELIVERY_AWAITED","POSITION","PARTIAL_FILL") else "RECORDED",
                "quantity":finite(item.get("quantity"))})
        markets.append({"index": index, "price": last, "change": change,
            "change_pct": change / previous * 100 if change is not None else None,
            "high": finite(ohlc.get("high")), "low": finite(ohlc.get("low")), "previous": previous,
            "received_at": clean_time(probe.get("received_at")),
            "series": clean_series(charts.get(index, {}).get("candles", [])), "options": options})
    acct = account_summary(account, now)
    strategies = [{"id": key, "name": name, "description": description,
        **acct["strategy_results"].get(key, {"closed_trades": 0, "non_loss_pct": None,
            "loss_pct": None, "net_pnl_inr": None, "return_pct": None})}
        for key, name, description in STRATEGIES]
    return {"format": "trading-dashboard-v1", "demo": False, "as_of": received,
        "freshness": "NO_DATA" if age is None else "FUTURE" if age < 0 else "RECENT" if age <= 15 else "STALE",
        "source": "Groww · read-only", "market_day": stamp(received).astimezone(IST).date().isoformat() if received else None,
        "markets": markets, "account": acct, "strategies": strategies,
        "orders_status": snapshot.get("orders_status") if snapshot.get("orders_status") in ("AVAILABLE","INCOMPLETE","UNAVAILABLE") else "UNAVAILABLE",
        "market_status": snapshot.get("status") if snapshot.get("status") in ("READ_ONLY_DATA_AVAILABLE","PARTIAL_MARKET_DATA","BLOCKED") else "UNKNOWN",
        "poll_interval_seconds":POLL_SECONDS, "candle_interval_minutes":5,
        "sequence": snapshot.get("sequence") if type(snapshot.get("sequence")) is int else None,
        "execution_enabled": False, "order_capability": False}


def option_identity(row):
    symbol = row.get("trading_symbol")
    if row.get("segment") != "FNO" or not isinstance(symbol, str) or not OPTION_SYMBOL.fullmatch(symbol):
        return None
    index = next((key for key in ("BANKNIFTY", "NIFTY", "SENSEX")
                  if re.match(key + r"\d", symbol)), None)
    if index is None or row.get("exchange") != EXCHANGES[index]:
        return None
    return {"symbol": symbol, "index": index, "exchange": EXCHANGES[index]}


def ordered_contracts(orders, positions):
    """Contract-level chart eligibility; pending orders are never labelled fills."""
    result = {}
    for row in orders:
        item = option_identity(row) if isinstance(row, dict) else None
        if item is None or row.get("transaction_type") not in ("BUY", "SELL"):
            continue
        status = row.get("order_status")
        filled = finite(row.get("filled_quantity")) or 0
        if status not in ("OPEN", "TRIGGER_PENDING", "EXECUTED", "DELIVERY_AWAITED") and filled <= 0:
            continue
        quantity = finite(row.get("quantity"))
        if quantity is None or quantity <= 0:
            continue
        if status not in ("OPEN", "TRIGGER_PENDING", "EXECUTED", "DELIVERY_AWAITED"):
            status = "PARTIAL_FILL"
        item.update(side=row["transaction_type"], order_status=status, quantity=quantity)
        result[item["symbol"], item["side"]] = item
    for row in positions:
        item = option_identity(row) if isinstance(row, dict) else None
        quantity = finite(row.get("quantity")) if isinstance(row, dict) else None
        if item is None or quantity is None or quantity == 0:
            continue
        item.update(side="BUY" if quantity > 0 else "SELL", order_status="POSITION", quantity=abs(quantity))
        result[item["symbol"], item["side"]] = item
    return list(result.values())


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


def download_metadata(requested):
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
    return instrument_metadata(b"".join(data).decode("utf-8-sig"), requested)


def five_minute_candles(payload, end):
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
        if at.date() == end.date() and at + timedelta(minutes=5) <= end:
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
        return charts

    def candles(key, groww_symbol, exchange, segment):
        if not refresh_all and previous.get(key, {}).get("groww_symbol") == groww_symbol:
            charts[key] = previous[key]
            return
        try:
            market.limiter.wait()
            raw = market.groww.get_historical_candles(exchange=exchange, segment=segment,
                groww_symbol=groww_symbol, start_time=start, end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                candle_interval="5minute", timeout=5)
            charts[key] = {"candles": five_minute_candles(raw, end), "groww_symbol": groww_symbol,
                "received_at": datetime.now(timezone.utc).isoformat(), "source": "GROWW_HISTORICAL_CANDLES",
                "interval_minutes": 5, "timestamp_semantics_verified": False}
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
                 background_history=True):
        self.market, self.clock, self.metadata_loader = market, clock, metadata_loader
        self.metadata, self.charts, self.chart_bucket, self.metadata_attempt = {}, {}, None, None
        self.sequence = 0
        self.history_pool = ThreadPoolExecutor(max_workers=1) if background_history else None
        self.history_future = None

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
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(index_quote, INDICES))
        orders, positions, complete, succeeded = [], [], True, 0
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

        def position_summary(raw):
            if not isinstance(raw.get("positions"), list):
                raise ValueError("positions unavailable")
            return {"record_count": len(raw["positions"])}
        raw = read("positions", self.market.groww.get_positions_for_user, position_summary, segment="FNO")
        if raw is None:
            complete = False
        else:
            succeeded += 1
            positions = raw["positions"][:1000]
            complete &= len(raw["positions"]) <= 1000
        ordered = ordered_contracts(orders, positions)
        complete &= len(ordered) <= 40
        ordered = ordered[:40]
        result["orders_status"] = "AVAILABLE" if complete else "INCOMPLETE" if succeeded else "UNAVAILABLE"
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
            list(pool.map(option_quote, unique.values()))
        for option in ordered:
            option.update(self.metadata.get(option["symbol"], {}))
            option["quote"] = probes[option["symbol"] + "_quote"].get("value", {})
            probe = probes[option["symbol"] + "_quote"]
            option["received_at"] = probe["received_at"] if probe["ok"] else None
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
                      cycle_duration_seconds=(finished-started).total_seconds(),
                      status="READ_ONLY_DATA_AVAILABLE" if all(probes[key + "_quote"]["ok"] for key in INDICES)
                      else "PARTIAL_MARKET_DATA")
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


def run_reader(output, *, lease_file=None):
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
                collector = DashboardCollector(GrowwMarketData(token), background_history=lease_file is not None)
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
    def __init__(self, root, *, offline=False):
        self.root, self.offline = Path(root).resolve(), offline
        self.directory = self.root / ".agent-state" / "dashboard-captures"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lease_file = self.directory / f"viewer-{os.getpid()}.lease"
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.refreshing = False
        self.last_refresh = -1000.0
        self.last_error = None
        self.cache, self.signature = None, None

    def read(self):
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
                try:
                    account = load_json(account_path) if account_path.exists() else None
                    self.cache = view_model(snapshot, protocol, account)
                except ValueError:
                    self.cache = view_model(snapshot, protocol)
                    self.cache["account"]["status"] = "INVALID_LEDGER"
                self.signature = signature
            result = json.loads(dumps(self.cache))
            at = result["as_of"]
            age = (datetime.now(timezone.utc) - stamp(at)).total_seconds() if at else None
            result["freshness"] = "NO_DATA" if age is None else "FUTURE" if age < 0 else "RECENT" if age <= 15 else "STALE"
            result.update(refreshing=self.refreshing, refresh_error=self.last_error, offline=self.offline)
            return result

    def refresh(self):
        with self.lock:
            if self.offline:
                return "OFFLINE"
            self.lease_file.touch(mode=0o600)
            if self.refreshing:
                return "IN_PROGRESS"
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
        except Exception:
            self.last_error = "LOCAL_READER_UNAVAILABLE"
        finally:
            with self.lock:
                self.refreshing = False


def handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, value, kind="application/json; charset=utf-8"):
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
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
                "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8")}
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
            return self.respond(404, {"status": "NOT_FOUND"})

        def do_POST(self):
            if not self.local() or not secrets.compare_digest(self.headers.get("X-Local-Token", ""), state.token):
                return self.respond(403, {"status": "LOCAL_ACCESS_ONLY"})
            if self.path != "/api/refresh" or self.headers.get("Content-Length", "0") != "0":
                return self.respond(400, {"status": "UNSUPPORTED_REQUEST"})
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
    sub = subs.add_parser("serve")
    sub.add_argument("--root", type=Path, default=Path.cwd())
    sub.add_argument("--port", type=int, default=8765)
    sub.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.command == "capture":
        return capture(args.output)
    if args.command == "watch":
        directory, lease = args.directory.resolve(), args.lease_file.resolve()
        if not directory.is_dir() or lease.parent != directory or not viewer_active(lease):
            raise ValueError("active local viewer lease required")
        return run_reader(directory / f"market-check-live-{lease.stem}.json", lease_file=lease)
    if not 1024 <= args.port <= 65535:
        raise ValueError("local port outside range")
    state = DashboardState(args.root, offline=args.offline)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(state))
    print(dumps({"status": "LOCAL_DASHBOARD_READY", "url": f"http://127.0.0.1:{args.port}", "order_capability": False}), flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(dumps({"status": "BLOCKED", "failure": safe_error(exc)}))
        raise SystemExit(2) from None
