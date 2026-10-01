"""Bounded, data-only contracts. No orders, credentials, or executable conditions."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
EXCHANGES = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "BSE"}
METRICS = ("spot", "iv_pct", "short_delta", "distance_bps", "move_1m_bps")
OPS = ("above", "below", "cross_above", "cross_below", "interval")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":"))


def identity(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def stamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def number(value: Any, *, nullable: bool = False) -> float | None:
    if nullable and value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("finite number required")
    return value


def integer(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("integer outside allowed range")
    return value


def text(value: Any, limit: int = 500) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or "\x00" in value:
        raise ValueError("invalid text")
    return value


def keys(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(value, dict) or not required <= value.keys():
        raise ValueError("missing required fields")
    if value.keys() - required - (optional or set()):
        raise ValueError("unknown fields")


def watch(value: dict, now: datetime) -> dict:
    required = {"name", "instrument", "metric", "op", "threshold", "cooldown_seconds", "max_fires", "expires_at"}
    keys(value, required)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", value["name"]):
        raise ValueError("invalid watch name")
    if value["instrument"] not in INDICES or value["metric"] not in METRICS or value["op"] not in OPS:
        raise ValueError("unsupported condition")
    number(value["threshold"])
    integer(value["cooldown_seconds"], 300, 86400)
    integer(value["max_fires"], 1, 12)
    if not 0 < (stamp(value["expires_at"]) - now).total_seconds() <= 86400:
        raise ValueError("watch expiry must be within 24 hours; explicit renewal required")
    return value


def snapshot(value: dict) -> dict:
    keys(value, {"id", "mode", "observed_at", "source", "markets", "portfolio", "status", "reasons", "news"}, {"context"})
    text(value["id"], 128)
    if value["mode"] not in ("PAPER", "REPLAY", "OBSERVE"):
        raise ValueError("live accounts are outside this research engine")
    observed = stamp(value["observed_at"])
    text(value["source"], 80)
    text(value["status"], 80)
    if not isinstance(value["markets"], dict) or not value["markets"].keys() <= set(INDICES):
        raise ValueError("unsupported instrument")
    for row in value["markets"].values():
        keys(row, {"at", *METRICS})
        if stamp(row["at"]) > observed:
            raise ValueError("market time is in the future")
        for metric in METRICS:
            number(row[metric], nullable=True)
        if row["spot"] is not None and row["spot"] <= 0:
            raise ValueError("spot must be positive")
    p = value["portfolio"]
    keys(p, {"accounting_day", "realized_pnl", "unrealized_pnl", "open_positions", "trades", "loss_used"})
    datetime.strptime(p["accounting_day"], "%Y-%m-%d")
    for name in ("realized_pnl", "unrealized_pnl", "loss_used"):
        number(p[name], nullable=True)
    for name in ("open_positions", "trades"):
        if p[name] is not None:
            integer(p[name], 0, 1000000)
    if not isinstance(value["reasons"], list) or len(value["reasons"]) > 40:
        raise ValueError("too many reasons")
    for reason in value["reasons"]:
        text(reason, 300)
    if not isinstance(value["news"], list) or len(value["news"]) > 30:
        raise ValueError("too many news items")
    for row in value["news"]:
        keys(row, {"id", "title", "source", "published_at"})
        for key in ("id", "title", "source"):
            text(row[key], 600)
        if stamp(row["published_at"]) > observed:
            raise ValueError("future news")
    if "context" in value:
        ctx = value["context"]
        keys(ctx, {"positions", "option_quotes"}, {"event_calendar"})
        calendar = ctx.get("event_calendar")
        if calendar is not None:
            keys(calendar, {"reviewed_at", "session_open", "session_close", "events"})
            if stamp(calendar["reviewed_at"]) > observed:
                raise ValueError("future calendar review")
            if stamp(calendar["session_open"]) >= stamp(calendar["session_close"]):
                raise ValueError("invalid session window")
            if not isinstance(calendar["events"], list) or len(calendar["events"]) > 32:
                raise ValueError("too many calendar events")
            for event in calendar["events"]:
                keys(event, {"name", "start", "end"})
                text(event["name"], 160)
                if stamp(event["start"]) >= stamp(event["end"]):
                    raise ValueError("invalid calendar event window")
        if not isinstance(ctx["positions"], list) or len(ctx["positions"]) > 4:
            raise ValueError("too many positions")
        for row in ctx["positions"]:
            keys(row, {"short_symbol", "hedge_symbol", "expiry", "short_strike", "hedge_strike", "units", "modeled_max_loss"})
            for name in ("short_symbol", "hedge_symbol", "expiry"):
                text(row[name], 100)
            for name in ("short_strike", "hedge_strike", "modeled_max_loss"):
                number(row[name])
            integer(row["units"], 1, 1000000)
            if row["hedge_strike"] <= row["short_strike"]:
                raise ValueError("invalid protective call")
        if not isinstance(ctx["option_quotes"], list) or len(ctx["option_quotes"]) > 64:
            raise ValueError("too many quotes")
        for row in ctx["option_quotes"]:
            keys(row, {"symbol", "expiry", "strike", "bid", "ask", "delta", "iv_pct", "received_at", "traded_at"},
                 {"instrument", "exchange", "lot_size", "bid_qty", "ask_qty", "book_at", "greeks_at"})
            text(row["symbol"], 100)
            text(row["expiry"], 30)
            for name in ("strike", "bid", "ask", "delta", "iv_pct"):
                number(row[name], nullable=True)
            for name in ("received_at", "traded_at"):
                if row[name] is not None and stamp(row[name]) > observed:
                    raise ValueError("future option observation")
            if "instrument" in row and row["instrument"] not in INDICES:
                raise ValueError("unsupported option instrument")
            if "exchange" in row and row["exchange"] not in ("NSE", "BSE"):
                raise ValueError("unsupported exchange")
            for name in ("lot_size", "bid_qty", "ask_qty"):
                if name in row:
                    integer(row[name], 0 if name != "lot_size" else 1, 1000000000)
            for name in ("book_at", "greeks_at"):
                if row.get(name) is not None and stamp(row[name]) > observed:
                    raise ValueError("future book/Greek observation")
    if len(dumps(value)) > 60000:
        raise ValueError("snapshot too large")
    return value


def decision(value: dict, expected_snapshot: str, evidence: set[str], now: datetime) -> dict:
    keys(value, {"snapshot_id", "action", "summary", "evidence_ids", "watches", "research_proposal"})
    if value["snapshot_id"] != expected_snapshot:
        raise ValueError("decision belongs to another snapshot")
    if value["action"] not in ("WAIT", "REVIEW", "RESEARCH"):
        raise ValueError("execution actions are not permitted")
    text(value["summary"], 2500)
    if not isinstance(value["evidence_ids"], list) or not set(value["evidence_ids"]) <= evidence:
        raise ValueError("unsupported evidence")
    if not isinstance(value["watches"], list) or len(value["watches"]) > 3:
        raise ValueError("too many follow-up watches")
    for item in value["watches"]:
        watch(item, now)
    if value["research_proposal"] is not None:
        text(value["research_proposal"], 4000)
    return value


WATCH_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "name": {"type": "string"}, "instrument": {"type": "string", "enum": list(INDICES)},
    "metric": {"type": "string", "enum": list(METRICS)},
    "op": {"type": "string", "enum": list(OPS)}, "threshold": {"type": "number"},
    "cooldown_seconds": {"type": "integer"}, "max_fires": {"type": "integer"},
    "expires_at": {"type": "string"}},
    "required": ["name", "instrument", "metric", "op", "threshold", "cooldown_seconds", "max_fires", "expires_at"]}
DECISION_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "snapshot_id": {"type": "string"}, "action": {"type": "string", "enum": ["WAIT", "REVIEW", "RESEARCH"]},
    "summary": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}},
    "watches": {"type": "array", "items": WATCH_SCHEMA},
    "research_proposal": {"type": ["string", "null"]}},
    "required": ["snapshot_id", "action", "summary", "evidence_ids", "watches", "research_proposal"]}
