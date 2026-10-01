"""Format owner-specified order details for manual review. Offline; no execution."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .contracts import dumps, identity, integer, keys


def prepare(value, now):
    keys(value, {"exchange", "segment", "symbol", "side", "quantity", "limit_price", "product"})
    if value["exchange"] not in ("NSE", "BSE") or value["segment"] not in ("CASH", "FNO"):
        raise ValueError("invalid market")
    if value["side"] not in ("BUY", "SELL") or value["product"] not in ("CNC", "MIS", "NRML"):
        raise ValueError("invalid owner-specified side or product")
    if (value["segment"] == "FNO" and value["product"] == "CNC") or (value["segment"] == "CASH" and value["product"] == "NRML"):
        raise ValueError("product incompatible with segment")
    if not isinstance(value["symbol"], str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9&._-]{0,79}", value["symbol"]):
        raise ValueError("invalid symbol")
    integer(value["quantity"], 1, 1000000)
    try:
        if not isinstance(value["limit_price"], str) or len(value["limit_price"]) > 20:
            raise ValueError("limit_price must be a decimal string")
        price = Decimal(value["limit_price"])
        if not price.is_finite() or not 0 < price <= 10000000 or price.as_tuple().exponent < -4:
            raise ValueError("invalid limit price")
    except InvalidOperation:
        raise ValueError("invalid limit price") from None
    fields = {key: value[key] for key in ("exchange", "segment", "symbol", "side", "quantity", "product")}
    fields.update(order_type="LIMIT", limit_price=format(price, "f"), validity="DAY")
    return {"id": identity(fields), "created_at": now.isoformat(), "status": "MANUAL_REVIEW_REQUIRED",
            "submitted": False, "execution_capability": False, "fields": fields,
            "market_data_verified": False, "lot_tick_expiry_verified": False,
            "risk_margin_verified": False}


def render(ticket):
    fields = ticket["fields"]
    rows = ["# Groww manual review ticket", "", "**Draft only. Nothing has been submitted.**", "",
            "These are owner-specified details. Prices, lot size, tick size, expiry, margin and risk are unverified.",
            "", "| Field | Value |", "| --- | --- |"]
    rows += [f"| {key.replace('_', ' ').title()} | {value} |" for key, value in fields.items()]
    rows += ["", "1. Sign in to Groww yourself and select the matching instrument and exchange.",
             "2. Check the current contract, quote, depth, lot size and tick size in Groww.",
             "3. Review these fields, available funds/margin and your existing positions.",
             "4. If you decide to proceed, enter and submit the order yourself.",
             "5. Check order status and filled quantity before taking another action.",
             "", "For multiple legs, this ticket does not verify combined risk, fills or a protected spread.",
             "", f"Prepared: {ticket['created_at']}", f"Ticket: {ticket['id']}", ""]
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.input.stat().st_size > 4096:
        raise ValueError("ticket input too large")
    ticket = prepare(json.loads(args.input.read_text(encoding="utf-8")), datetime.now(timezone.utc))
    with args.output.open("x", encoding="utf-8") as output:
        output.write(render(ticket))
    print(dumps({"status": ticket["status"], "submitted": False, "output": str(args.output)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(dumps({"status": "FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
