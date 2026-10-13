"""Fail-closed safety commissioning for Algo Start.

A real broker commissioning receipt and a live runtime are necessary, but not
sufficient, for unattended entry. Alert coverage must also have been verified
against real infrastructure. No public endpoint can install this evidence.

Existing owned-position protection/exit intentionally ignores this new-entry
safety gate: disabling alerts must never strand an already open basket.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .contracts import stamp

KEY = "safety-alert-activation-v1"
HEARTBEAT_KEY = "safety-heartbeat-status"
MAX_HEARTBEAT_AGE = 90
MAX_PROOF_AGE = 7 * 86400


def _fresh(value, now, max_age):
    try:
        delta = (now - stamp(value)).total_seconds()
        return 0 <= delta <= max_age
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def blockers(store, release, now):
    """Use private reviewed evidence, not UI checkboxes or mock email attempts."""
    result=[]
    heartbeat=store.meta(HEARTBEAT_KEY,{})
    if (not isinstance(heartbeat,dict)
            or heartbeat.get("status")!="UPLOADED"
            or not _fresh(heartbeat.get("checked_at"),now,MAX_HEARTBEAT_AGE)):
        result.append("INDEPENDENT_VM_HEARTBEAT_NOT_CURRENT")
    proof=store.meta(KEY,{})
    if (not isinstance(proof,dict)
            or proof.get("format")!=KEY
            or proof.get("release")!=release
            or proof.get("owner_verified") is not True
            or proof.get("critical_transport_tested") is not True
            or proof.get("independent_cloud_outage_tested") is not True
            or proof.get("api_checkpoint_code_tested") is not True
            or not _fresh(proof.get("provider_acceptance_at"),now,MAX_PROOF_AGE)
            or not _fresh(proof.get("reviewed_at"),now,MAX_PROOF_AGE)):
        result.append("SAFETY_MAIL_END_TO_END_COMMISSIONING_REQUIRED")
    return result


def preflight(runtime, observation, now):
    """Answer if a *new* LIVE intent can safely be recorded.

    Allowed to arm outside the entry window; the actual order gateway still
    enforces its exact time window, account, margin and fresh broker state.
    """
    store=runtime.state.pnl_lines.store
    from . import premium_strategy as policy
    # Existing On intent is not evidence that today's broker and alert state
    # remains safe. Always re-evaluate without changing that intent.
    policy.intent(store)
    reasons=[]
    if runtime.executor is None or runtime.error or runtime.execution_error:
        reasons.append("ORACLE_EXECUTOR_CONNECTION_REQUIRED")
    if runtime.gate.account_fingerprint is None:
        reasons.append("EXACT_BROKER_ACCOUNT_REQUIRED")
    reasons += [reason for reason in runtime.gate.blockers(
        observation,purpose="ENTRY",strategy="normal_theta")
        if reason not in ("OWNER_ALGO_OFF","OUTSIDE_1400_1900_JST")]
    try:
        from .strategy_groups import read
        switches=read(store)
        if switches["enabled"]["normal_theta"] is not True:
            reasons.append("NORMAL_THETA_STRATEGY_NOT_SELECTED")
    except (ValueError, TypeError, KeyError):
        reasons.append("STRATEGY_CONTROLS_CORRUPT")
    return list(dict.fromkeys(reasons))


def refusal(runtime, reasons):
    """The client can prove that Algo Start did not silently persist On."""
    from .premium_strategy import intent
    value=intent(runtime.state.pnl_lines.store)
    return dict(status="ON_BLOCKED" if value["enabled"] else "START_REFUSED",
                desired_enabled=value["enabled"],owner_intent=value,
                execution_enabled=False,broker_writes=False,
                blockers=list(dict.fromkeys(reasons)),live_orders_submitted=False)
