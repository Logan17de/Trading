"""Headless local/VM CLI. Scheduler and Codex worker are separate processes."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import IST, dumps, integer, number
from .reporting import build_report, deliver_once, queue_report, save_preview
from .runner import CodexRunner, work_once
from .store import Store

ROOT = Path(__file__).resolve().parents[3]
DEFAULT = {"enabled": False, "send_email": False, "repository_root": ".", "database": ".agent-state/engine.sqlite",
           "snapshot_file": ".agent-state/latest.json", "reports_dir": ".agent-state/reports", "report_time_ist": "16:15",
           "poll_seconds": 2, "analysis_interval_seconds": 900, "daily_codex_calls": 24, "codex_timeout_seconds": 120,
           "codex_executable": "/usr/local/bin/codex", "codex_home": ".agent-state/codex-home", "codex_model": None,
           "codex_expected_version": None, "codex_expected_sha256": None, "shared_state_group": False}


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError("input file too large")
    return json.loads(path.read_text())


def load_config(path):
    given = read_json(path)
    if not isinstance(given, dict) or given.keys() - DEFAULT.keys():
        raise ValueError("unknown configuration keys")
    cfg = {**DEFAULT, **given}
    if type(cfg["enabled"]) is not bool or type(cfg["send_email"]) is not bool:
        raise ValueError("enabled/send_email must be booleans")
    if type(cfg["shared_state_group"]) is not bool:
        raise ValueError("shared_state_group must be boolean")
    if cfg["codex_expected_sha256"] is not None and not re.fullmatch(r"[a-f0-9]{64}", cfg["codex_expected_sha256"]):
        raise ValueError("invalid Codex hash pin")
    integer(cfg["poll_seconds"], 1, 60)
    integer(cfg["daily_codex_calls"], 1, 200)
    integer(cfg["analysis_interval_seconds"], 300, 86400)
    integer(cfg["codex_timeout_seconds"], 5, 240)
    datetime.strptime(cfg["report_time_ist"], "%H:%M")
    root = Path(cfg["repository_root"]).resolve()
    if not root.is_dir():
        raise ValueError("repository_root does not exist")
    for key in ("database", "snapshot_file", "reports_dir", "codex_home"):
        value = Path(cfg[key])
        cfg[key] = str(value if value.is_absolute() else root / value)
    cfg["repository_root"] = str(root)
    return cfg


def paused(cfg):
    return (ROOT / ".trader-paused").exists() or (Path(cfg["repository_root"]) / ".trader-paused").exists()


def scheduler_tick(store, cfg, now):
    error = None
    try:
        store.ingest(read_json(cfg["snapshot_file"]), now)
    except Exception as exc:
        error = type(exc).__name__
    if error != store.meta("last_ingest_error"):
        store.event("INGEST_FAILED" if error else "INGEST_RECOVERED", {"error_type": error}, now)
        store.set_meta("last_ingest_error", error)
    local = now.astimezone(IST)
    expiry = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for index in (store.latest() or {}).get("markets", {}):
        spec = {"name": "review-" + local.date().isoformat() + "-" + index,
            "instrument": index, "metric": "spot", "op": "interval", "threshold": 0,
            "cooldown_seconds": cfg["analysis_interval_seconds"], "max_fires": 12,
            "expires_at": expiry.isoformat()}
        if not store.read("SELECT name FROM watches WHERE name=?", (spec["name"],)):
            try:
                store.add_watch(spec, now)
            except ValueError:
                pass  # An existing bounded watch population takes precedence.
    store.evaluate(now)
    started = store.meta("started_day")
    if started is None:
        started = local.date().isoformat()
        store.set_meta("started_day", started)
    # Bounded catch-up for days the scheduler was already enrolled, never old
    # imported history or fabricated reports from before the engine was started.
    for offset in range(6, -1, -1):
        day = local.date() - timedelta(days=offset)
        if day.isoformat() < started or (offset == 0 and local.strftime("%H:%M") < cfg["report_time_ist"]):
            continue
        if not store.read("SELECT id FROM reports WHERE day=?", (day.isoformat(),)):
            bundle = build_report(store, day.isoformat(), now)
            save_preview(bundle, Path(cfg["reports_dir"]) / day.isoformat())
            queue_report(store, bundle)
    store.set_meta("scheduler_heartbeat", now.isoformat())


def synthetic(at, spot, realized=0, unrealized=0, open_positions=0):
    return {"id": "demo-" + at.isoformat(), "source": "synthetic-demo", "mode": "REPLAY", "observed_at": at.isoformat(),
        "markets": {"NIFTY": {"at": at.isoformat(), "spot": spot, "iv_pct": 12.0,
            "short_delta": 0.12, "distance_bps": 180, "move_1m_bps": 0}},
        "portfolio": {"accounting_day": at.astimezone(IST).date().isoformat(), "realized_pnl": realized,
            "unrealized_pnl": unrealized, "open_positions": open_positions, "trades": 1 if open_positions or realized else 0,
            "loss_used": max(0, -realized)}, "status": "DEMO_ONLY", "reasons": ["Synthetic fixture. Not market data or trading performance."], "news": []}


def demo(directory):
    root = Path(directory)
    if (root / "demo.sqlite").exists():
        raise ValueError("use a new demo directory; never reset existing journals")
    store = Store(root / "demo.sqlite")
    start = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)
    watch = {"name": "demo-cross", "instrument": "NIFTY", "metric": "spot", "op": "cross_above",
             "threshold": 24600, "cooldown_seconds": 300, "max_fires": 1, "expires_at": (start + timedelta(hours=6)).isoformat()}
    store.add_watch(watch, start)
    for index, (spot, realized, pnl, count) in enumerate(((24500,0,0,0),(24610,0,-180,1),(24590,0,260,1),(24570,0,650,1),(24550,900,0,0))):
        now = start + timedelta(seconds=index * 20)
        store.ingest(synthetic(now, spot, realized, pnl, count), now)
        store.evaluate(now)
        if index == 1:
            def analyst(payload):
                return {"snapshot_id": payload["snapshot"]["id"], "action": "REVIEW",
                    "summary": "DEMO: price crossed the stored threshold. Review the call-spread hypothesis; no order was sent.",
                    "evidence_ids": [], "research_proposal": "Compare this hypothesis against the deterministic baseline before approval.",
                    "watches": [{**watch, "name": "demo-follow-up", "op": "below", "threshold": 24600}]}
            work_once(store, analyst, clock=lambda: now)
        elif index == 2:
            work_once(store, lambda payload: {"snapshot_id": payload["snapshot"]["id"], "action": "WAIT",
                "summary": "DEMO: the persisted follow-up condition fired without a manual prompt.",
                "evidence_ids": [], "watches": [], "research_proposal": None}, clock=lambda: now)
    store.set_meta("goal", {"month": start.strftime("%Y-%m"), "target_inr": 10000, "allocated_capital_inr": None})
    bundle = build_report(store, start.date().isoformat(), start.replace(hour=16, minute=15))
    save_preview(bundle, root / "email")
    queue_report(store, bundle)
    print(dumps({"status": "SYNTHETIC_DEMO_COMPLETE", "directory": str(root.resolve()), **store.status()}))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Headless Codex trigger engine; no broker orders")
    parser.add_argument("--config", type=Path, default=Path("config/agent_engine.example.json"))
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("doctor", "status", "snapshot", "tick", "run", "worker", "deliver", "health", "codex-check"):
        sub = subs.add_parser(command)
        if command == "worker":
            sub.add_argument("--once", action="store_true")
    sub = subs.add_parser("demo"); sub.add_argument("directory", type=Path)
    sub = subs.add_parser("ingest"); sub.add_argument("path", type=Path)
    sub = subs.add_parser("watch-add"); sub.add_argument("path", type=Path)
    sub = subs.add_parser("watch-pause"); sub.add_argument("name")
    sub = subs.add_parser("goal"); sub.add_argument("--month", required=True); sub.add_argument("--target-inr", required=True, type=float); sub.add_argument("--capital-inr", type=float)
    sub = subs.add_parser("report"); sub.add_argument("--date", required=True); sub.add_argument("--output", required=True, type=Path)
    sub = subs.add_parser("backup"); sub.add_argument("--output", required=True, type=Path)
    sub = subs.add_parser("restore"); sub.add_argument("--source", required=True, type=Path); sub.add_argument("--output", required=True, type=Path)
    sub = subs.add_parser("mail-status"); sub.add_argument("--report-id", required=True)
    args = parser.parse_args()
    if args.command == "demo":
        demo(args.directory)
        return
    cfg = load_config(args.config)
    if cfg["shared_state_group"]:
        os.umask(0o007)
    if args.command == "restore":
        from .operations import restore_database
        print(dumps(restore_database(args.source, args.output)))
        return
    if args.command == "codex-check":
        from .contracts import decision
        runner = CodexRunner(cfg["codex_executable"], cfg["codex_home"], timeout=cfg["codex_timeout_seconds"],
            model=cfg["codex_model"], expected_version=cfg["codex_expected_version"], expected_sha256=cfg["codex_expected_sha256"])
        verification = runner.preflight()
        now = datetime.now(timezone.utc)
        sample = synthetic(now, 24500)
        result = runner({"snapshot": sample, "now": now.isoformat(), "trigger": "SYNTHETIC_CLI_CHECK",
                         "execution": "NO_ORDER_CAPABILITY", "monthly_goal": None})
        decision(result, sample["id"], set(), datetime.now(timezone.utc))
        print(dumps({"status": "VALIDATED", "cli": verification, "run": runner.last_run, "decision": result}))
        return
    if args.command == "doctor":
        print(dumps({"enabled": cfg["enabled"], "paused": paused(cfg), "email_enabled": cfg["send_email"],
            "codex_executable_exists": Path(cfg["codex_executable"]).is_file(), "codex_home_exists": Path(cfg["codex_home"]).is_dir(),
            "source_file_exists": Path(cfg["snapshot_file"]).is_file(), "report_time_ist": cfg["report_time_ist"],
            "order_capability": False, "doctor_performs_network_checks": False}))
        return
    external = args.command in ("run", "worker", "deliver")
    if external and (not cfg["enabled"] or paused(cfg)):
        print(dumps({"status": "DISABLED_OR_REPOSITORY_PAUSED"}))
        return
    if args.command == "backup":
        from .operations import backup_database
        print(dumps(backup_database(cfg["database"], args.output)))
        return
    store = Store(cfg["database"], shared_group=cfg["shared_state_group"])
    now = datetime.now(timezone.utc)
    if args.command == "status": print(dumps(store.status()))
    elif args.command == "health":
        from .operations import health
        print(dumps(health(store, now)))
    elif args.command == "mail-status":
        from .reporting import reconcile_receipt
        print(dumps(reconcile_receipt(store, args.report_id, now)))
    elif args.command == "snapshot": print(dumps(store.latest()))
    elif args.command == "ingest": print(dumps({"inserted": store.ingest(read_json(args.path), now)}))
    elif args.command == "watch-add": print(dumps({"watch": store.add_watch(read_json(args.path), now)}))
    elif args.command == "watch-pause":
        with store.transaction() as db: db.execute("UPDATE watches SET enabled=0 WHERE name=?", (args.name,))
    elif args.command == "goal":
        if not re.fullmatch(r"\d{4}-\d{2}", args.month): raise ValueError("month must be YYYY-MM")
        datetime.strptime(args.month, "%Y-%m")
        if number(args.target_inr) <= 0 or (args.capital_inr is not None and number(args.capital_inr) <= 0): raise ValueError("goal/capital must be positive")
        store.set_meta("goal", {"month": args.month, "target_inr": args.target_inr,
            "monthly_profit_target_inr": args.target_inr, "allocated_capital_inr": args.capital_inr})
    elif args.command == "report": save_preview(build_report(store, args.date, now), args.output)
    elif args.command == "tick": print(dumps({"queued": store.evaluate(now)}))
    elif args.command == "deliver": print(dumps({"mail": deliver_once(store, now) if cfg["send_email"] else "EMAIL_DISABLED"}))
    elif args.command == "run":
        while True:
            if paused(cfg): return
            scheduler_tick(store, cfg, datetime.now(timezone.utc))
            if cfg["send_email"] and not paused(cfg): deliver_once(store, datetime.now(timezone.utc))
            time.sleep(cfg["poll_seconds"])
    elif args.command == "worker":
        if not cfg["codex_expected_version"] or not cfg["codex_expected_sha256"]:
            raise ValueError("review and pin the target Codex version and executable hash before worker activation")
        runner = CodexRunner(cfg["codex_executable"], cfg["codex_home"], timeout=cfg["codex_timeout_seconds"], model=cfg["codex_model"],
            expected_version=cfg["codex_expected_version"], expected_sha256=cfg["codex_expected_sha256"])
        while True:
            if paused(cfg): return
            work_once(store, runner, daily_cap=cfg["daily_codex_calls"], timeout=cfg["codex_timeout_seconds"])
            if args.once: return
            time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(dumps({"status": "FAILED", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
