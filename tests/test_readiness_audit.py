"""Tests for the strictly non-authorizing readiness projection."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from nifty_engine.agent_engine import readiness_audit


NOW = datetime(2026, 10, 13, 4, 30, tzinfo=timezone.utc)
SHA = "a" * 40


def snapshot():
    return {
        "format": "trading-dashboard-v1",
        "order_capability": False,
        "as_of": NOW.isoformat(),
        "runtime": {
            "host": "ORACLE",
            "heartbeat_at": NOW.isoformat(),
            "heartbeat_sequence": 5,
        },
        "execution_controller": {
            "connection_status": "CONNECTED",
            "execution_enabled": False,
        },
        "live_setup": {
            "mode": "paper",
            "paused": True,
            "provider_verified": False,
            "activation_installed": False,
        },
        "readiness_evidence": {
            "broker_protection": {
                "read_api": "AVAILABLE",
                "persistent_owned_gtt_verified": False,
                "generated_child_verified": False,
            },
            "matched_iv": {
                i: {
                    "status": "MATCHED_IV_INPUTS_REQUIRED",
                    "reviewed_sessions": 0,
                    "current_available": False,
                }
                for i in ("NIFTY", "SENSEX")
            },
            "research_campaign": {"status": "COLLECTING"},
        },
        "normal_theta": {
            "indices": {
                i: {"status": "WAIT", "performance_status": "NOT_BACKTESTED"}
                for i in ("NIFTY", "SENSEX")
            }
        },
        "strategies": [{
            "id": "normal_theta",
            "execution_route_implemented": True,
            "readiness": {"live_ready": False},
        }],
        "account": {"status": "NOT_CONNECTED"},
        "research_archive": {"status": "SYNCED"},
    }


def by_id(report):
    return {item["id"]: item for item in report["checks"]}


def test_paused_state_is_blocked_even_when_reads_work():
    result = readiness_audit.assess(snapshot(), now=NOW)
    checks = by_id(result)
    assert result["overall"] == "BLOCKED"
    assert result["broker_writes"] is False
    assert result["authorizes_execution"] is False
    assert checks["oracle_heartbeat"]["status"] == "OBSERVED"
    assert checks["current_broker_reads"]["status"] == "OBSERVED"
    assert checks["reviewed_broker_commissioning"]["status"] == "BLOCKED"
    assert checks["persistent_stop_and_child_link"]["status"] == "BLOCKED"
    assert checks["live_execution_gate"]["status"] == "BLOCKED"
    assert checks["strategy_profitability_validation"]["status"] == "BLOCKED"
    assert checks["research_matched_iv"]["status"] == "BLOCKED"
    assert checks["archive_sync"]["status"] == "OBSERVED"
    assert checks["deployed_source_identity"]["status"] == "UNKNOWN"
    assert "live_execution_gate" in result["live_blockers"]


def test_gtt_list_read_access_does_not_prove_protection():
    view = snapshot()
    view["readiness_evidence"]["broker_protection"]["counts"] = {"ACTIVE": 100}
    result = readiness_audit.assess(view, now=NOW)
    assert by_id(result)["persistent_stop_and_child_link"]["status"] == "BLOCKED"


def test_stale_dashboard_and_heartbeat_cannot_be_marked_current():
    view = snapshot()
    view["as_of"] = (NOW - timedelta(hours=1)).isoformat()
    view["runtime"]["heartbeat_at"] = (NOW - timedelta(hours=1)).isoformat()
    checks = by_id(readiness_audit.assess(view, now=NOW))
    assert checks["oracle_heartbeat"]["status"] == "UNKNOWN"
    assert checks["current_broker_reads"]["status"] == "UNKNOWN"


def test_missing_receipt_fails_closed_even_if_flags_claim_live():
    view = snapshot()
    view["live_setup"].update(mode="live", paused=False)
    view["execution_controller"]["execution_enabled"] = True
    view["readiness_evidence"]["broker_protection"].update(
        persistent_owned_gtt_verified=True, generated_child_verified=True)
    checks = by_id(readiness_audit.assess(view, now=NOW))
    assert checks["persistent_stop_and_child_link"]["status"] == "BLOCKED"
    assert checks["reviewed_broker_commissioning"]["status"] == "BLOCKED"


def test_runtime_release_requires_exact_expected_sha():
    view = snapshot()
    view["runtime"]["release"] = SHA
    assert by_id(readiness_audit.assess(view, now=NOW, expected_release=SHA))[
        "deployed_source_identity"]["status"] == "OBSERVED"
    assert by_id(readiness_audit.assess(view, now=NOW))[
        "deployed_source_identity"]["status"] == "UNKNOWN"
    with pytest.raises(ValueError, match="EXACT_RELEASE_SHA_REQUIRED"):
        readiness_audit.assess(view, now=NOW, expected_release="main")


def test_missing_monitor_is_not_reported_healthy():
    checks = by_id(readiness_audit.assess(snapshot(), now=NOW))
    assert checks["oracle_failure_watchdog"]["status"] == "UNKNOWN"
    view = snapshot()
    monitor = {"format": "trading-incident-watch-v1",
               "broker_writes": False, "monitor_status": "CURRENT",
               "oracle": {"checked_at": NOW.isoformat()}}
    checks = by_id(readiness_audit.assess(view, now=NOW, monitor=monitor))
    assert checks["oracle_failure_watchdog"]["status"] == "OBSERVED"
    assert checks["independent_full_vm_outage_alert"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("view", [{}, {"format": "trading-dashboard-v1"},
                                  {"format": "trading-dashboard-v1", "order_capability": True}])
def test_untrusted_or_non_read_only_input_rejected(view):
    with pytest.raises(ValueError, match="READ_ONLY_DASHBOARD_EVIDENCE_REQUIRED"):
        readiness_audit.assess(view, now=NOW)


def test_live_gate_does_not_auto_authorize_orders():
    view = snapshot()
    view["runtime"]["release"] = SHA
    view["live_setup"].update(mode="live", paused=False,
                              provider_verified=True, activation_installed=True)
    view["execution_controller"]["execution_enabled"] = True
    view["readiness_evidence"]["broker_protection"].update(
        persistent_owned_gtt_verified=True, generated_child_verified=True)
    view["strategies"][0]["readiness"]["live_ready"] = True
    view["normal_theta"]["indices"] = {
        i: {"performance_status": "OUT_OF_SAMPLE_VALIDATED"}
        for i in ("NIFTY", "SENSEX")
    }
    for i in ("NIFTY", "SENSEX"):
        view["readiness_evidence"]["matched_iv"][i] = {
            "status": "MATCHED_CURRENT",
            "reviewed_sessions": 252,
            "current_available": True,
        }
    result = readiness_audit.assess(view, now=NOW, expected_release=SHA)
    assert result["overall"] == "REVIEW_REQUIRED"
    assert result["live_blockers"] == []
    assert result["authorizes_execution"] is False


def test_snapshot_cli_emits_only_evidence_codes(tmp_path, capsys, monkeypatch):
    source = tmp_path / "private-snapshot.json"
    private = snapshot()
    private["some_private_account_id"] = "DO_NOT_LEAK_MY_ACCOUNT"
    source.write_text(json.dumps(private), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["readiness_audit", "--snapshot", str(source)])
    assert readiness_audit.main() == 0
    output = capsys.readouterr().out
    assert "DO_NOT_LEAK_MY_ACCOUNT" not in output
    assert json.loads(output)["overall"] == "BLOCKED"


def test_bad_snapshot_cli_fails_without_leaking_error(tmp_path, capsys, monkeypatch):
    source = tmp_path / "private-snapshot.json"
    source.write_text('{"error": "PRIVATE"}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["readiness_audit", "--snapshot", str(source)])
    assert readiness_audit.main() == 2
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    assert json.loads(output)["overall"] == "UNAVAILABLE"


def test_saved_monitor_current_label_expires():
    monitor = {"format": "trading-incident-watch-v1", "broker_writes": False,
               "monitor_status": "CURRENT",
               "oracle": {"checked_at": (NOW - timedelta(hours=1)).isoformat()}}
    result = readiness_audit.assess(snapshot(), now=NOW, monitor=monitor)
    assert by_id(result)["oracle_failure_watchdog"]["status"] == "UNKNOWN"


def test_pc_audit_only_uses_fixed_read_command(tmp_path, capsys, monkeypatch):
    from nifty_engine.agent_engine import oracle_link
    state = tmp_path / ".agent-state"
    state.mkdir()
    config = {
        "format": "trading-oracle-viewer-v1",
        "host": "example.invalid",
        "user": "ubuntu",
        "identity_file": str(tmp_path / "existing-key"),
        "python": "/opt/growing-trader/releases/" + SHA + "/venv/bin/python",
        "root": "/var/lib/trading-observer",
    }
    (state / "oracle-viewer.json").write_text(json.dumps(config), encoding="utf-8")
    view = snapshot()
    view["runtime"]["release"] = SHA
    commands = []

    def fake_request(settings, command):
        assert settings == config
        commands.append(command)
        return view

    monkeypatch.setattr(oracle_link, "request", fake_request)
    monkeypatch.setattr("sys.argv", ["readiness_audit", "--root", str(tmp_path)])
    assert readiness_audit.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert commands == [{"action": "read"}]
    assert by_id(result)["deployed_source_identity"]["status"] == "OBSERVED"
    assert result["authorizes_execution"] is False
