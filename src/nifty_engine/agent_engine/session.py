"""Durable real-data observations and an OBSERVE publisher. No broker writes.

Reception, last-trade and bar times are separate. Missing accounting and source
book/Greek timestamps remain unknown. Research samples never become positions.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta

from .contracts import INDICES, IST, dumps, identity, snapshot, stamp
from .market_check import finite
from .pc_control import JST, SYMBOL


QUOTE_FIELDS = ("last_price", "bid_price", "offer_price", "bid_quantity", "offer_quantity",
                "volume", "open_interest", "last_trade_age_seconds")


def safe_quote(value):
    value = value if isinstance(value, dict) else {}
    result = {key: finite(value.get(key)) for key in QUOTE_FIELDS}
    for key in ("traded_at", "book_at", "greeks_at"):
        try:
            result[key] = stamp(value[key]).isoformat() if value.get(key) else None
        except (ValueError, TypeError):
            result[key] = None
    ohlc = value.get("ohlc") if isinstance(value.get("ohlc"), dict) else {}
    depth = value.get("depth") if isinstance(value.get("depth"), dict) else {}
    result["ohlc"] = {k: finite(ohlc.get(k)) for k in ("open", "high", "low", "close")}
    result["depth"] = {}
    for side in ("buy", "sell"):
        rows = depth.get(side, [])
        rows = rows if isinstance(rows, list) else []
        result["depth"][side] = [{"price": r["price"], "quantity": r["quantity"]}
            for r in rows[:5] if isinstance(r, dict) and finite(r.get("price")) is not None
            and r["price"] > 0 and finite(r.get("quantity")) is not None and r["quantity"] > 0]
    result["timestamp_semantics"] = "TRADE_TIME_ONLY_BOOK_AND_GREEKS_UNVERIFIED"
    return result


def observation(raw, now):
    if (raw.get("execution_mode") != "OBSERVE" or raw.get("order_capability") is not False
            or raw.get("status") not in ("READ_ONLY_DATA_AVAILABLE", "PARTIAL_MARKET_DATA")):
        raise ValueError("actual read-only collector data required")
    at, began = stamp(raw["finished_at"]), stamp(raw["checked_at"])
    if not began <= at <= now or (at-began).total_seconds() > 300:
        raise ValueError("invalid observation interval")
    result = {"format": "groww-session-observation-v1", "observed_at": at.isoformat(),
              "request_started_at": began.isoformat(), "status": raw["status"],
              "orders_complete": raw.get("orders_status") == "AVAILABLE", "markets": {}, "options": []}
    for index in INDICES:
        probe = raw.get("probes", {}).get(index+"_quote", {})
        received = probe.get("received_at")
        valid = probe.get("ok") is True and received is not None and began <= stamp(received) <= at
        result["markets"][index] = {"ok": valid, "received_at": received if valid else None,
                                   "quote": safe_quote(probe.get("value", {})) if valid else None}
    for row in raw.get("ordered_options", [])[:40]:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if (not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or row.get("index") not in INDICES
                or row.get("side") not in ("BUY", "SELL") or row.get("order_status") != "POSITION"
                or finite(row.get("quantity")) is None or row["quantity"] <= 0):
            continue
        try:
            expiry = date.fromisoformat(row["expiry"]).isoformat()
            received = stamp(row["received_at"]).isoformat()
        except (ValueError, TypeError, KeyError):
            expiry, received = None, None
        result["options"].append({"symbol": symbol, "index": row.get("index"),
            "side": row.get("side"), "expiry": expiry, "strike": finite(row.get("strike")),
            "lot_size":row.get("lot_size") if type(row.get("lot_size")) is int and 0 < row["lot_size"] <= 100000 else None,
            "tick_size": finite(row.get("tick_size")),
            "received_at": received, "quote": safe_quote(row.get("quote", {})),
            "ownership": "ENGINE_VERIFIED" if row.get("ownership") == "ENGINE_VERIFIED" else "MANUAL_OR_UNKNOWN_PROTECTED"})
    if len(dumps(result)) > 128000:
        raise ValueError("observation exceeds journal bound")
    result["id"] = identity(result)
    return result


class SessionJournal:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pc_observations(id TEXT PRIMARY KEY, at REAL NOT NULL,
                    day TEXT NOT NULL, complete INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS pc_observation_time ON pc_observations(day,at);
                CREATE TABLE IF NOT EXISTS pc_bars(symbol TEXT NOT NULL, at REAL NOT NULL,
                    day TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(symbol,at));
            """)

    def record(self, raw, now):
        value = observation(raw, now)
        at = stamp(value["observed_at"])
        day = at.astimezone(JST).date().isoformat()
        complete = all(r["ok"] and r["quote"]["last_price"] is not None and r["quote"]["last_price"] > 0
            for r in value["markets"].values())
        with self.store.transaction() as db:
            inserted = db.execute("INSERT OR IGNORE INTO pc_observations VALUES(?,?,?,?,?)",
                (value["id"], at.timestamp(), day, int(complete), dumps(value))).rowcount
            # Keep completed historical bars independently, without duplicating seven
            # days of history into every five-second observation. No silent revision.
            for symbol, chart in raw.get("charts", {}).items():
                if symbol not in INDICES and not SYMBOL.fullmatch(symbol):
                    continue
                for bar in chart.get("context_candles", chart.get("candles", []))[:2000]:
                    start = stamp(bar["at"])
                    if start+timedelta(minutes=5) > at:
                        continue
                    prices = {k: finite(bar.get(k)) for k in ("open", "high", "low", "close")}
                    if any(v is None or v <= 0 for v in prices.values()):
                        continue
                    body = dumps(dict(prices, at=start.isoformat(), interval_minutes=5,
                        timestamp_semantics="IST_BAR_START_ASSUMPTION_UNVERIFIED"))
                    old = db.execute("SELECT body FROM pc_bars WHERE symbol=? AND at=?", (symbol,start.timestamp())).fetchone()
                    if old and old[0] != body:
                        self._revision(db, symbol, start, now)
                    else:
                        db.execute("INSERT OR IGNORE INTO pc_bars VALUES(?,?,?,?)",
                            (symbol,start.timestamp(),start.astimezone(JST).date().isoformat(),body))
        return bool(inserted)

    @staticmethod
    def _revision(db, symbol, at, now):
        key = "bar-revision-"+identity([symbol,at.isoformat()])
        db.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", (key,dumps({"status":"BAR_REVISION_REVIEW_REQUIRED",
                   "symbol":symbol,"bar_at":at.isoformat(),"seen_at":now.isoformat()})))

    def coverage(self, day):
        date = datetime.strptime(day,"%Y-%m-%d").date()
        start,end = (datetime.combine(date,time(h,m),JST).timestamp() for h,m in ((12,45),(19,0)))
        rows = self.store.read("SELECT at,complete FROM pc_observations WHERE day=? AND at>=? AND at<=? ORDER BY at",(day,start,end))
        complete = [r["at"] for r in rows if r["complete"]]
        gaps = [b-a for a,b in zip(complete,complete[1:])]
        max_gap = max([complete[0]-start,end-complete[-1],*gaps]) if complete else end-start
        return {"status":"COMPLETE_OBSERVED_SESSION" if complete and max_gap <= 15 else "INCOMPLETE_SESSION",
                "observations":len(rows),"complete_observations":len(complete),
                "max_gap_seconds":round(max_gap,3),"expected_interval_seconds":5,
                "source_timestamp_semantics_verified":False,
                "first_at":datetime.fromtimestamp(complete[0],JST).isoformat() if complete else None,
                "last_at":datetime.fromtimestamp(complete[-1],JST).isoformat() if complete else None}

    def publish(self, raw, now, *, account=None, news=None):
        """Publisher feeds the retained contracts/report outbox; no ledger invention."""
        from .dashboard import account_summary
        observed = observation(raw,now)
        at = stamp(observed["observed_at"])
        day = at.astimezone(IST).date().isoformat()
        ledger = account_summary(account,at)
        reviewed = ledger["status"] == "OWNER_REVIEWED"
        realized = ledger.get("realized_today_inr") if reviewed else None
        unrealized = ledger.get("unrealized_inr") if reviewed else None
        evidence = []
        for row in (news or {}).get("articles",[])[:30]:
            if not stamp(row["published_at"]) <= at:
                continue
            evidence.append({k:row[k] for k in ("id","title","source","published_at")})
        reasons = []
        if not reviewed: reasons.append("NET_ACCOUNTING_UNCONNECTED")
        low = False
        try:
            from .news import validate_assessment
            validate_assessment({k:news[k] for k in ("risk", "summary", "evidence_ids")}, evidence)
            sources = {r["source"] for r in evidence if r["id"] in news["evidence_ids"]
                and 0 <= (at-stamp(r["published_at"])).total_seconds() <= 86400}
            low = (news["risk"] == "LOW" and 0 <= (at-stamp(news["assessed_at"])).total_seconds() <= 180
                and {"rbi.org.in", "economictimes.indiatimes.com"} <= sources)
        except (ValueError, TypeError, KeyError):
            pass
        if not low: reasons.append("NEWS_HIGH_UNKNOWN_OR_STALE")
        reasons.append("BOOK_GREEK_AND_HISTORY_TIMESTAMP_SEMANTICS_UNVERIFIED")
        markets = {index:{"at":row["received_at"],"spot":row["quote"]["last_price"],
                    "iv_pct":None,"short_delta":None,"distance_bps":None,"move_1m_bps":None}
                   for index,row in observed["markets"].items() if row["ok"]}
        body = {"mode":"OBSERVE", "source":"groww-local-session-v1",
            "observed_at":observed["observed_at"],"status":observed["status"],"markets":markets,
            "portfolio":{"accounting_day":day,"realized_pnl":realized,"unrealized_pnl":unrealized,
                "open_positions":None,"trades":ledger.get("trade_count_today") if reviewed else None,"loss_used":None},
            "news":evidence,"reasons":reasons}
        # Accounting/news can change without a new market observation. Bind the
        # published identity to all normalized content, retaining immutable rows.
        body["id"] = "pc-published-"+identity(body)
        snapshot(body)
        self.store.ingest(body,now)
        self.store.set_meta("session_coverage_"+day,self.coverage(day))
        return body


class ObservedLines:
    """Display actual journalled LTP observations, independently of research bars.

    A price is plotted at its reception time, not claimed to be an exchange tick.
    Reload the day after restart; bounded overlapping reads catch delayed commits.
    """
    def __init__(self, store):
        self.store, self.day, self.cursor, self.points = store, None, None, {}

    def read(self, now):
        day = now.astimezone(JST).date().isoformat()
        start = datetime.combine(now.astimezone(IST).date(), time(9,15), IST).timestamp()
        if self.day != day:
            self.day, self.cursor, self.points = day, start, {}
        rows = self.store.read("SELECT at,body FROM pc_observations WHERE day=? AND at>=? AND at<=? ORDER BY at LIMIT 10000",
            (day, max(start, self.cursor-300), now.timestamp()))
        for stored in rows:
            try:
                value = json.loads(stored["body"])
                if value.get("format") != "groww-session-observation-v1":
                    continue
                began, finished = stamp(value["request_started_at"]), stamp(value["observed_at"])
                records = [(index,row) for index,row in value["markets"].items()
                    if index in INDICES and row.get("ok") is True]
                records += [(r["symbol"],r) for r in value["options"]
                    if isinstance(r.get("symbol"),str) and SYMBOL.fullmatch(r["symbol"])]
                for symbol, row in records:
                    try:
                        at = stamp(row["received_at"])
                        price = finite(row["quote"].get("last_price"))
                        if price is None or price <= 0 or not began <= at <= finished <= now or at.timestamp() < start:
                            continue
                        points = self.points.setdefault(symbol,{})
                        # Equal reception identity never invents a revised price.
                        points.setdefault(at.isoformat(), {"at":at.isoformat(),"value":price})
                    except (ValueError,TypeError,KeyError):
                        continue
                self.cursor = max(self.cursor,stored["at"])
            except (ValueError,TypeError,KeyError):
                continue
        result = {}
        for symbol, points in self.points.items():
            times = sorted(at for at in points if stamp(at) <= now)[-6000:]
            self.points[symbol] = {at:points[at] for at in times}
            result[symbol] = [points[at] for at in times]
        return result
