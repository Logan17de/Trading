"""Explicit one-shot integration email; no trading journal or scheduled mail changes.

Uses a dedicated diagnostic outbox and NO_DATA report, never a REPLAY profit report.
Invoke --send only for the owner's authorized diagnostic delivery verification.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path

from nifty_engine.agent_engine.contracts import IST, dumps
from nifty_engine.agent_engine.reporting import build_report, save_preview, queue_report, deliver_once, reconcile_receipt
from nifty_engine.agent_engine.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/etc/growing-trader/call-seller.env"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.env_file.exists():
        for line in args.env_file.read_text().splitlines():
            for token in shlex.split(line, comments=True):
                key, sep, value = token.partition("=")
                if sep and key in {"RESEND_API_KEY", "TRADING_REPORT_FROM", "TRADING_REPORT_TO"}:
                    os.environ[key] = value
    args.output.mkdir(parents=True, exist_ok=True)
    store = Store(args.output / "diagnostic.sqlite")
    store.set_meta("source", ["headless-integration-email", "OBSERVE"])
    now = datetime.now(timezone.utc)
    day = store.meta("test_day") or now.astimezone(IST).date().isoformat()
    store.set_meta("test_day", day)
    bundle = build_report(store, day, now)
    bundle["mail"]["subject"] = "Growing Trader [INTEGRATION TEST] visual headless email " + day
    bundle["mail"]["text"] = "Authorized diagnostic only. Trading and scheduled email remain paused.\n" + bundle["mail"]["text"]
    bundle["mail"]["html"] = bundle["mail"]["html"].replace("Your market day, explained.", "Headless email integration test.")
    save_preview(bundle, args.output)
    report_id = queue_report(store, bundle)
    result = {"report_id": report_id, "sent": False, "status": "PREVIEW_ONLY", "inbox_verified": False}
    if args.send:
        # Owner-approved recipient retained from the existing configuration; never reroute it.
        if os.getenv("TRADING_REPORT_TO") != "loganlogesh17@gmail.com":
            raise ValueError("diagnostic recipient differs from approved workflow recipient")
        status = deliver_once(store, now)
        result.update(status=status, sent=status == "ACCEPTED")
        accepted = store.read("SELECT status FROM reports WHERE id=?", (report_id,))[0]["status"] == "ACCEPTED"
        result["provider_accepted"] = accepted
        if accepted:
            result["receipt"] = reconcile_receipt(store, report_id, datetime.now(timezone.utc))
    (args.output / "delivery.json").write_text(dumps(result), encoding="utf-8")
    print(dumps(result))
    if args.send and not result["provider_accepted"]:
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
