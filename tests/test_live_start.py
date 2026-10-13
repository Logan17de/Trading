"""Synthetic start/stop safety: neither preflight nor tests send broker orders."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import live_start, premium_strategy
from nifty_engine.agent_engine.execution_gate import ExecutionGate
from nifty_engine.agent_engine.pc_control import PcJournal
from nifty_engine.agent_engine.oracle_runtime import Runtime
from nifty_engine.agent_engine.oracle_link import RemoteViewer

RELEASE="a"*40
NOW=datetime(2026,10,13,5,30,tzinfo=timezone.utc)


def ready_alert_evidence(store, *, at=NOW, release=RELEASE):
    store.set_meta("safety-heartbeat-status",
        {"status":"UPLOADED","checked_at":at.isoformat(),"broker_writes":False})
    store.set_meta(live_start.KEY, {
        "format":live_start.KEY,"release":release,"owner_verified":True,
        "critical_transport_tested":True,"independent_cloud_outage_tested":True,
        "api_checkpoint_code_tested":True,"provider_acceptance_at":at.isoformat(),
        "reviewed_at":at.isoformat(),
    })


def test_missing_real_email_proof_never_counts_as_ready(tmp_path):
    store=PcJournal(tmp_path/"audit.sqlite3").store
    assert set(live_start.blockers(store,RELEASE,NOW))=={
        "INDEPENDENT_VM_HEARTBEAT_NOT_CURRENT",
        "SAFETY_MAIL_END_TO_END_COMMISSIONING_REQUIRED"}
    ready_alert_evidence(store)
    assert live_start.blockers(store,RELEASE,NOW)==[]
    assert "INDEPENDENT_VM_HEARTBEAT_NOT_CURRENT" in live_start.blockers(
        store,RELEASE,NOW+timedelta(seconds=91))
    assert "SAFETY_MAIL_END_TO_END_COMMISSIONING_REQUIRED" in live_start.blockers(
        store,RELEASE,NOW+timedelta(days=8))
    assert "SAFETY_MAIL_END_TO_END_COMMISSIONING_REQUIRED" in live_start.blockers(
        store,"b"*40,NOW)


def test_entry_requires_alerts_but_existing_exit_not_blocked(tmp_path):
    journal=PcJournal(tmp_path/"j.sqlite3")
    premium_strategy.set_intent(journal.store,True,NOW)
    gate=ExecutionGate(
        journal,tmp_path/".trader-paused",mode="live",release=RELEASE,
        host="ORACLE",clock=lambda:NOW,policy_hash="b"*64,
        safety_alerts_required=True)
    observed={"complete":True,"received_at":NOW.isoformat()}
    assert "SAFETY_MAIL_END_TO_END_COMMISSIONING_REQUIRED" in gate.blockers(
        observed,purpose="ENTRY")
    assert "SAFETY_MAIL_END_TO_END_COMMISSIONING_REQUIRED" not in gate.blockers(
        observed,purpose="EXIT")
    ready_alert_evidence(journal.store)
    assert "SAFETY_MAIL_END_TO_END_COMMISSIONING_REQUIRED" not in gate.blockers(
        observed,purpose="ENTRY")


def runtime(tmp_path, *, gate_blockers):
    store=PcJournal(tmp_path/"test.sqlite3").store
    state=SimpleNamespace(pnl_lines=SimpleNamespace(store=store),
        algo_set=lambda enabled: (_ for _ in ()).throw(
            AssertionError("START_MUST_NOT_PERSIST_OWNER_ON")))
    r=Runtime.__new__(Runtime)
    r.state=state
    r.gate=SimpleNamespace(account_fingerprint=None,blockers=lambda *a,**k:gate_blockers)
    r.executor=None;r.error=None;r.execution_error=None
    r.output=tmp_path/"missing-snapshot.json"
    return r,store


def test_oracle_rejects_paper_start_without_setting_owner_intent(tmp_path):
    r,store=runtime(tmp_path,gate_blockers=["LIVE_ENVIRONMENT_NOT_ACTIVATED",
                                           "REPOSITORY_PAUSED","OWNER_ALGO_OFF"])
    r.normal_catalog={}
    result=r.command({"action":"intent","enabled":True})
    assert result["status"]=="START_REFUSED" and result["desired_enabled"] is False
    assert "LIVE_ENVIRONMENT_NOT_ACTIVATED" in result["blockers"]
    assert result["broker_writes"] is False
    assert premium_strategy.intent(store)["enabled"] is False


def test_remote_viewer_recognizes_confirmed_refusal_without_claiming_on(monkeypatch):
    import nifty_engine.agent_engine.oracle_link as link
    from nifty_engine.agent_engine.oracle_link import RemoteViewer
    viewer=RemoteViewer.__new__(RemoteViewer)
    viewer.config={}
    viewer.poll=lambda:pytest.fail("NO_START_NO_RECONCILIATION_NEEDED")
    reply={"status":"START_REFUSED","desired_enabled":False,
           "owner_intent":{"enabled":False},"execution_enabled":False,
           "broker_writes":False,"blockers":["REPOSITORY_PAUSED"]}
    monkeypatch.setattr(link,"request",lambda *args:reply)
    assert viewer.set_intent(True)==reply


def test_refused_remote_ack_must_not_accept_a_rogue_on_intent(monkeypatch):
    import nifty_engine.agent_engine.oracle_link as link
    viewer=RemoteViewer.__new__(RemoteViewer)
    viewer.config={}
    monkeypatch.setattr(link,"request",lambda *a:{
        "status":"START_REFUSED","desired_enabled":False,
        "owner_intent":{"enabled":True},"execution_enabled":False,
        "broker_writes":False})
    with pytest.raises(ConnectionError, match="did not confirm"):
        viewer.set_intent(True)


def test_preflight_checks_selected_executable_strategy_and_ignores_only_clock_off(tmp_path):
    r,store=runtime(tmp_path,gate_blockers=[
        "OWNER_ALGO_OFF","OUTSIDE_1400_1900_JST"])
    r.executor=object()
    r.gate.account_fingerprint="f"*64
    assert "NORMAL_THETA_STRATEGY_NOT_SELECTED" in live_start.preflight(
        r,{"complete":True,"received_at":NOW.isoformat()},NOW)
    from nifty_engine.agent_engine.strategy_groups import set_switch
    set_switch(store,{"id":"normal_theta","enabled":True,"revision":0},NOW)
    assert live_start.preflight(r,{"complete":True,"received_at":NOW.isoformat()},NOW)==[]


def test_existing_owner_on_is_never_secretly_turned_off_by_start_refusal(tmp_path):
    r,store=runtime(tmp_path,gate_blockers=['LIVE_ENVIRONMENT_NOT_ACTIVATED'])
    premium_strategy.set_intent(store,True,NOW)
    result=live_start.refusal(r,['LIVE_ENVIRONMENT_NOT_ACTIVATED'])
    assert result['status']=='ON_BLOCKED'
    assert result['desired_enabled'] is True
    assert result['execution_enabled'] is False
    assert premium_strategy.intent(store)['enabled'] is True
