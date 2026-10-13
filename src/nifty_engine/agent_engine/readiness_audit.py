"""Read-only, evidence-bounded trading readiness audit.

Reads the existing private Oracle dashboard transport or a local snapshot.
Never changes owner intent, touches broker order routes, or creates activation proof.
An OBSERVED check is a reported observation, not independent broker certification.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .contracts import stamp


def _obj(value):
    return value if isinstance(value, dict) else {}


def _current(value, now, maximum_age):
    try:
        age = (now - stamp(value)).total_seconds()
        return 0 <= age <= maximum_age
    except (ValueError, TypeError, KeyError):
        return False


def assess(view, *, now=None, monitor=None, expected_release=None):
    """Project whitelisted public evidence into a non-authorizing audit result."""
    if not isinstance(view, dict) or view.get("format") != "trading-dashboard-v1" or view.get("order_capability") is not False:
        raise ValueError("READ_ONLY_DASHBOARD_EVIDENCE_REQUIRED")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("AWARE_AUDIT_CLOCK_REQUIRED")
    if expected_release is not None and not re.fullmatch(r"[a-f0-9]{40}", expected_release):
        raise ValueError("EXACT_RELEASE_SHA_REQUIRED")

    runtime = _obj(view.get("runtime"))
    execution = _obj(view.get("execution_controller"))
    setup = _obj(view.get("live_setup"))
    readiness = _obj(view.get("readiness_evidence"))
    protection = _obj(readiness.get("broker_protection"))
    matched = _obj(readiness.get("matched_iv"))
    normal = _obj(view.get("normal_theta"))
    account = _obj(view.get("account"))
    archive = _obj(view.get("research_archive"))
    checks = []

    def add(name, status, evidence, *, blocks_live=False):
        assert status in ("OBSERVED", "BLOCKED", "UNKNOWN")
        checks.append(dict(id=name, status=status, evidence=evidence, blocks_live=blocks_live))

    heartbeat = (runtime.get("host") == "ORACLE"
                 and type(runtime.get("heartbeat_sequence")) is int
                 and runtime["heartbeat_sequence"] > 0
                 and _current(runtime.get("heartbeat_at"), now, 45))
    add("oracle_heartbeat", "OBSERVED" if heartbeat else "UNKNOWN",
        "FRESH_ORACLE_SEQUENCE" if heartbeat else "FRESH_ADVANCING_SEQUENCE_NOT_PROVEN",
        blocks_live=True)

    market_recent = _current(view.get("as_of"), now, 15)
    connected = (heartbeat and market_recent and execution.get("connection_status") == "CONNECTED")
    add("current_broker_reads", "OBSERVED" if connected else "UNKNOWN",
        "CURRENT_CONNECTED_SNAPSHOT" if connected else "MARKET_OR_PROVIDER_READS_NOT_CURRENT",
        blocks_live=True)

    release = runtime.get("release")
    verified_release = (expected_release is not None and release == expected_release)
    add("deployed_source_identity", "OBSERVED" if verified_release else "UNKNOWN",
        "MATCHED_RUNTIME_SHA" if verified_release else "RUNTIME_RELEASE_ID_NOT_VERIFIED",
        blocks_live=True)

    activated = (setup.get("provider_verified") is True and setup.get("activation_installed") is True)
    add("reviewed_broker_commissioning", "OBSERVED" if activated else "BLOCKED",
        "RELEASE_BOUND_PROVIDER_RECEIPT_REPORTED" if activated else "COMPLETED_PROVIDER_TEST_REQUIRED",
        blocks_live=True)

    protected = (activated and protection.get("persistent_owned_gtt_verified") is True
                 and protection.get("generated_child_verified") is True)
    add("persistent_stop_and_child_link", "OBSERVED" if protected else "BLOCKED",
        "PROVIDER_PROTECTION_EVIDENCE_REPORTED" if protected else "REAL_STOP_CHILD_LINK_VALIDATION_REQUIRED",
        blocks_live=True)

    live_gate = (setup.get("mode") == "live" and setup.get("paused") is False
                 and execution.get("execution_enabled") is True)
    add("live_execution_gate", "OBSERVED" if live_gate else "BLOCKED",
        "RUNTIME_REPORTS_LIVE_GATE_OPEN" if live_gate else "PAPER_PAUSE_OR_EXECUTION_GATE_BLOCKED",
        blocks_live=True)

    strategies = view.get("strategies")
    normal_row = next((row for row in strategies if isinstance(row, dict)
                       and row.get("id") == "normal_theta"), {}) if isinstance(strategies, list) else {}
    normal_ready = (_obj(normal_row.get("readiness")).get("live_ready") is True
                    and normal_row.get("execution_route_implemented") is True)
    add("normal_theta_entry_evidence", "OBSERVED" if normal_ready else "BLOCKED",
        "CURRENT_CANDIDATE_AND_RUNTIME_GATES_REPORTED" if normal_ready
        else "NO_CURRENT_ELIGIBLE_EXECUTION_CANDIDATE",
        blocks_live=True)

    perf = [item.get("performance_status") for item in _obj(normal.get("indices")).values()
            if isinstance(item, dict)]
    validated = bool(perf) and all(p == "OUT_OF_SAMPLE_VALIDATED" for p in perf)
    add("strategy_profitability_validation", "OBSERVED" if validated else "BLOCKED",
        "OUT_OF_SAMPLE_EVIDENCE_REPORTED_REVIEW_REQUIRED" if validated
        else "COSTED_OUT_OF_SAMPLE_OPTION_BACKTEST_REQUIRED",
        blocks_live=True)

    iv_complete = all((_obj(matched.get(index)).get("status") == "MATCHED_CURRENT"
                       and _obj(matched.get(index)).get("reviewed_sessions", 0) >= 252
                       and _obj(matched.get(index)).get("current_available") is True)
                      for index in ("NIFTY", "SENSEX"))
    add("research_matched_iv", "OBSERVED" if iv_complete else "BLOCKED",
        "BOTH_INDICES_HAVE_CURRENT_MATCHED_HISTORY" if iv_complete
        else "REVIEWED_252_SESSION_IV_AND_CURRENT_PROVIDER_DATA_REQUIRED")

    campaign = _obj(readiness.get("research_campaign"))
    add("research_campaign", "OBSERVED" if campaign.get("status") == "COLLECTING" else "UNKNOWN",
        "CAMPAIGN_COLLECTING_NOT_PROFITABILITY_PROOF" if campaign.get("status") == "COLLECTING"
        else "CAMPAIGN_COLLECTION_NOT_VERIFIED")

    net_verified = account.get("status") == "OWNER_REVIEWED" and bool(_obj(account.get("strategy_results")))
    add("reviewed_strategy_net_pnl", "OBSERVED" if net_verified else "UNKNOWN",
        "REVIEWED_RESULTS_REPORTED" if net_verified else "NET_LEDGER_AND_BROKER_CHARGES_NOT_VERIFIED")

    add("archive_sync", "OBSERVED" if archive.get("status") == "SYNCED" else "UNKNOWN",
        "ARCHIVE_SYNC_REPORTED" if archive.get("status") == "SYNCED"
        else "CURRENT_ARCHIVE_RECEIPTS_NOT_VERIFIED")

    monitor = _obj(monitor)
    monitor_ok = monitor.get("format") == "trading-incident-watch-v1" and monitor.get("broker_writes") is False and monitor.get("monitor_status") == "CURRENT"
    add("oracle_failure_watchdog", "OBSERVED" if monitor_ok else "UNKNOWN",
        "CURRENT_MONITOR_STATUS_REPORTED" if monitor_ok else "CURRENT_MONITOR_STATUS_NOT_SUPPLIED")
    add("independent_full_vm_outage_alert", "UNKNOWN",
        "NO_INDEPENDENT_ALWAYS_ON_WATCHDOG_PROOF")
    add("unattended_full_session_recovery", "UNKNOWN",
        "FULL_SESSION_AND_REBOOT_EVIDENCE_NOT_IN_DASHBOARD")

    blocking = [c["id"] for c in checks if c["blocks_live"] and c["status"] != "OBSERVED"]
    return dict(format="trading-readiness-audit-v1", checked_at=now.isoformat(),
                source="EXISTING_READ_ONLY_DASHBOARD_EVIDENCE", broker_writes=False,
                changes_owner_intent=False, authorizes_execution=False,
                overall="BLOCKED" if blocking else "REVIEW_REQUIRED",
                live_blockers=blocking, checks=checks)


def _read_file(path):
    if path.stat().st_size > 8_000_000:
        raise ValueError("BOUNDED_AUDIT_INPUT_REQUIRED")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="PC repository checkout with existing private SSH viewer settings")
    parser.add_argument("--snapshot", type=Path,
                        help="Previously captured dashboard JSON; no network access")
    parser.add_argument("--monitor", type=Path,
                        help="Optional saved read-only incident-watch JSON")
    parser.add_argument("--expected-release", help="Expected 40-character lowercase release SHA")
    args = parser.parse_args()
    try:
        expected_release = args.expected_release
        if args.snapshot:
            view = _read_file(args.snapshot)
        else:
            from .oracle_link import request, settings
            config = settings(_read_file(args.root / ".agent-state" / "oracle-viewer.json"))
            # The pinned client release and the running Oracle release must agree.
            # The client path alone cannot certify the currently running process.
            if expected_release is None:
                pinned = re.fullmatch(
                    r"/opt/growing-trader/releases/([a-f0-9]{40})/venv/bin/python",
                    config["python"])
                expected_release = pinned.group(1) if pinned else None
            view = request(config, {"action": "read"})
        monitor = _read_file(args.monitor) if args.monitor else None
        result = assess(view, monitor=monitor, expected_release=expected_release)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError, ConnectionError) as exc:
        # Never print exception bodies: SSH/provider exceptions may contain identities.
        print(json.dumps({"format": "trading-readiness-audit-v1", "overall": "UNAVAILABLE",
                          "error": type(exc).__name__, "broker_writes": False,
                          "authorizes_execution": False}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
