"""Owner's premium-target policy and deterministic plans. No broker transport.

This replaces active strike-offset/barrier/swing entry rules. Historical studies
stay reproducible. A decision is not an order; Oracle's controller has separate
deployment, activation, ownership and broker-verification gates.
"""
import json
from datetime import time
from pathlib import Path

from .contracts import keys, number, stamp
from .pc_control import JST, SYMBOL


def validate(value):
    keys(value,{"format","active_strategies","action_window_jst","short_call_target_rupees",
        "everyday_skip_actual_expiry","roll_below_rupees","hold_entry_offset_rupees",
        "hedge_change_min_improvement_inr","loss_stop_inr","maximum_lots","late_session"})
    if (value["format"] != "trading-premium-policy-v3" or value["active_strategies"] != ["everyday","late_session"]
            or value["action_window_jst"] != ["14:00","19:00"]
            or value["short_call_target_rupees"] != {"NIFTY":20,"SENSEX":80}
            or value["everyday_skip_actual_expiry"] is not True):
        raise ValueError("fixed owner premium policy required")
    for field,expected in (("roll_below_rupees",8),("hold_entry_offset_rupees",5),
                           ("hedge_change_min_improvement_inr",100),("loss_stop_inr",1000)):
        if number(value[field]) != expected:
            raise ValueError("owner premium/stop thresholds required")
    lots = value["maximum_lots"]
    if lots is not None and (type(lots) is not int or not 1 <= lots <= 2):
        raise ValueError("explicit bounded lot cap required")
    late = value["late_session"]
    keys(late,{"entry_jst","actual_expiry_only","short_strike_distance","strike_step_points","hedge_required","occupied_policy","trend_mapping"})
    if (late["entry_jst"] != "18:00" or late["actual_expiry_only"] is not True or late["short_strike_distance"] != 3
            or late["occupied_policy"] != "SKIP_MATCHING_ELSE_REPLACE_OWNED"
            or late["trend_mapping"] != {"UP":"PE","DOWN":"CE","FLAT":"NO_TRADE"}
            or late["strike_step_points"] != {"NIFTY":50,"SENSEX":100}
            or late["hedge_required"] is not True):
        raise ValueError("three listed strike intervals and bought hedges required")
    return value


def load(root):
    path = Path(root)/"config/premium_strategy.json"
    if not path.exists():
        return None
    if path.stat().st_size > 8192:
        raise ValueError("premium policy too large")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def preferred_index(now):
    return "SENSEX" if now.astimezone(JST).weekday() in (2,3) else "NIFTY"


def entry_window(now):
    local = now.astimezone(JST)
    return local.weekday()<5 and time(14)<=local.time()<time(19)


def position_review_window(now):
    """Existing-position proposals start at market open, independently of entries."""
    local = now.astimezone(JST)
    return local.weekday()<5 and time(12,45)<=local.time()<time(19)


def set_intent(store, enabled, now):
    """Durable owner preference. Only explicit On/Off requests change it."""
    if type(enabled) is not bool:
        raise ValueError("explicit boolean intent required")
    value = {"enabled":enabled,"changed_at":now.isoformat(),"source":"OWNER_BUTTON"}
    store.set_meta("premium-algo-intent",value)
    return value


def intent(store):
    value = store.meta("premium-algo-intent",{"enabled":False,"changed_at":None,"source":"DEFAULT_OFF"})
    if not isinstance(value,dict) or type(value.get("enabled")) is not bool:
        raise ValueError("invalid stored owner intent")
    return value


def readiness(policy, now, *, paused=True, offline=False, fresh=False, news_risk="UNKNOWN", desired_enabled=False):
    validate(policy)
    blockers = ["ORACLE_EXECUTOR_CONNECTION_REQUIRED"]
    if not desired_enabled:blockers.append("OWNER_ALGO_OFF")
    if paused:blockers.append("REPOSITORY_PAUSED")
    if offline:blockers.append("OFFLINE_VIEW")
    if not fresh:blockers.append("FRESH_MARKET_DATA_REQUIRED")
    if not entry_window(now):blockers.append("OUTSIDE_1400_1900_JST")
    if policy["maximum_lots"] is None:blockers.append("MAXIMUM_LOTS_REQUIRED")
    return {"status":"ON_BLOCKED" if desired_enabled else "OFF","desired_enabled":desired_enabled,
        "execution_enabled":False,"broker_writes":False,
        "blockers":blockers,"index":preferred_index(now),"window_open":entry_window(now),
        "policy_version":policy["format"],"maximum_lots":policy["maximum_lots"],
        "stop_loss_inr":policy["loss_stop_inr"],"stop_status":"BROKER_PROTECTION_NOT_VERIFIED_TRIGGER_NOT_GUARANTEED_LOSS_CAP",
        "news_required":False}


def everyday_review(policy, position, now, *, end_of_day=False):
    """Generate only an owned-position proposal, with stop priority over rollover."""
    validate(policy)
    result = {"action":"WAIT","broker_writes":False,"reason":"OWNED_FRESH_POSITION_REQUIRED"}
    if position.get("ownership") != "ENGINE_VERIFIED":return result
    try:
        if not 0 <= (now-stamp(position["received_at"])).total_seconds() <= 15:return result
        entry, premium = number(position["short_entry"]), number(position["short_premium"])
        if entry<=0 or premium<0:return result
        pnl = number(position["net_pnl_inr"]) if position.get("net_pnl_inr") is not None else None
    except (ValueError,TypeError,KeyError):return result
    if pnl is not None and pnl <= -policy["loss_stop_inr"]:
        return dict(result,action="REVIEW_OWNED_EXIT",reason="BASKET_LOSS_STOP",exit_sequence="CLOSE_SHORT_THEN_HEDGE")
    if premium <= policy["roll_below_rupees"]:
        return dict(result,action="REVIEW_ROLL_SHORT" if position_review_window(now) else "QUEUE_NEXT_WINDOW_ROLL",
            reason="SHORT_AT_OR_BELOW_8",target="NEXT_LISTED_SHORT_WITH_PREMIUM_ABOVE_8",keep_hedge=True,
            hedge_change_min_improvement_inr=policy["hedge_change_min_improvement_inr"],
            exit_sequence="CONFIRM_OLD_SHORT_CLOSED_BEFORE_NEW_SHORT")
    if end_of_day:
        if premium > entry-policy["hold_entry_offset_rupees"]:
            return dict(result,action="HOLD",reason="SHORT_ABOVE_ENTRY_MINUS_5")
        return dict(result,action="REVIEW_ROLL_SHORT" if entry_window(now) else "QUEUE_NEXT_WINDOW_ROLL",
            reason="END_OF_DAY_PREMIUM_LOW",target="INDEX_TARGET_20_OR_80",keep_hedge=True)
    return dict(result,action="HOLD",reason="MONITOR_OWNED_SPREAD")


def hedge_change(current_profit, replacement_profit, incremental_cost):
    """₹100 is a strict net improvement for the whole same-quantity basket."""
    improvement = number(replacement_profit)-number(current_profit)-number(incremental_cost)
    if incremental_cost<0:raise ValueError("nonnegative replacement cost required")
    return {"action":"REVIEW_HEDGE_REPLACEMENT" if improvement>100 else "KEEP_HEDGE",
        "net_improvement_inr":round(improvement,2),"broker_writes":False}


def expiry_handoff_review(cfg, index, expiry, evidence, now):
    """19:00 expiry close/other-index review. Never submits or authorizes orders."""
    validate(cfg)
    day=now.astimezone(JST).date().isoformat()
    result=dict(action='WAIT',reason='CONFIRMED_EXPIRY_1900_REQUIRED',broker_writes=False)
    if index not in ('NIFTY','SENSEX') or expiry!=day or now.astimezone(JST).time()<time(19):return result
    current=evidence.get(index,{})
    if current.get('status')!='CONFIRMED_CURRENT_MASTER' or current.get('day_jst')!=day or day not in current.get('expiries',[]):return result
    other='SENSEX' if index=='NIFTY' else 'NIFTY'
    target=evidence.get(other,{})
    eligible=sorted(d for d in target.get('expiries',[]) if isinstance(d,str) and d>day)
    ready=target.get('status')=='CONFIRMED_CURRENT_MASTER' and target.get('day_jst')==day and day not in target.get('expiries',[]) and bool(eligible)
    return dict(result,action='REVIEW_OWNED_EXIT',reason='EXPIRY_1900_HANDOFF',
        exit_sequence='CLOSE_SHORT_THEN_HEDGE',successor=dict(index=other,strategy='EVERYDAY',
            short_call_target_rupees=cfg['short_call_target_rupees'][other],expiry=eligible[0] if ready else None,
            status='REVIEW_AFTER_CONFIRMED_FLAT' if ready else 'WAIT_FOR_NONEXPIRING_INDEX_EVIDENCE',
            requires=['CONFIRMED_OWNED_FLAT','FRESH_MARGIN_AND_BOOKS','CONFIRMED_EXCHANGE_SESSION_OPEN'],
            broker_writes=False),execution_enabled=False)


def expiry_transition(active, proposed, *, complete):
    """Never adopt, cancel or exit manual trades, even when their contracts match."""
    result = {"action":"WAIT","broker_writes":False,"reason":"COMPLETE_EXACT_POSITIONS_REQUIRED"}
    def identity(rows):
        signatures=[]
        for r in rows:
            if (not isinstance(r.get("symbol"),str) or not SYMBOL.fullmatch(r["symbol"])
                    or r.get("side") not in ("BUY","SELL") or type(r.get("quantity")) is not int or r["quantity"]<=0
                    or r.get("product") not in ("MIS","NRML")):
                raise ValueError("exact legs required")
            signatures.append((r["symbol"],r["side"],r["quantity"],r["product"]))
        return sorted(signatures)
    if not complete or not proposed:return result
    try:
        existing, wanted = identity(active), identity(proposed)
    except (ValueError,TypeError,KeyError):return result
    if all(signature in existing for signature in wanted):
        return dict(result,action="SKIP_MATCHING_POSITION",reason="ALREADY_AT_PROPOSED_POSITION")
    if sum(r["quantity"] for r in proposed if r["side"]=="BUY")!=sum(r["quantity"] for r in proposed if r["side"]=="SELL"):
        return dict(result,reason="EQUAL_BOUGHT_HEDGE_REQUIRED")
    proposed_symbols={r[0] for r in wanted}
    if any(r.get("ownership") != "ENGINE_VERIFIED" and r.get("symbol") in proposed_symbols for r in active):
        return dict(result,reason="MANUAL_OR_MIXED_CONTRACT_PROTECTED")
    owned=[r for r in active if r.get("ownership") == "ENGINE_VERIFIED"]
    return dict(result,action="REVIEW_REPLACE_OWNED_SPREAD" if owned else "REVIEW_NEW_EXPIRY_SPREAD",
        reason="MATCHING_OWNED_POSITION_ABSENT",exit_sequence="CONFIRM_OWNED_SPREAD_FLAT_BEFORE_ENTRY",
        manual_positions_untouched=True)


def expiry_short(policy, index, spot, strikes, trend, *, actual_expiry):
    """Owner's example: ATM 25,000, UP -> sell 24,850 PE, not three positions."""
    validate(policy)
    result = {"action":"WAIT","broker_writes":False,"reason":"ACTUAL_EXPIRY_AND_LISTED_STRIKES_REQUIRED"}
    if actual_expiry is not True or index not in ("NIFTY","SENSEX"):return result
    kind = policy["late_session"]["trend_mapping"].get(trend)
    if kind not in ("CE","PE"):return dict(result,reason="NO_DIRECTIONAL_TREND")
    spot = number(spot)
    strikes = sorted(set(number(s) for s in strikes))
    if spot<=0 or not strikes:return result
    atm = min(strikes,key=lambda s:(abs(s-spot),s))
    i = strikes.index(atm)
    offset = -3 if kind == "PE" else 3
    j = i+offset
    if not 0<=j<len(strikes):return result
    segment = strikes[min(i,j):max(i,j)+1]
    if any(b-a != policy["late_session"]["strike_step_points"][index] for a,b in zip(segment,segment[1:])):
        return dict(result,reason="CURRENT_STRIKE_STEPS_DISAGREE")
    return dict(result,action="REVIEW_HEDGED_EXPIRY_SHORT",reason="THREE_STRIKES_IN_OWNER_TREND_MAPPING",
        option_type=kind,atm_strike=atm,short_strike=strikes[j],hedge_required=True,maximum_lots=policy["maximum_lots"])
