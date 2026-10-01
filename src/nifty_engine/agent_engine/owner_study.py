"""Read-only owner hypothesis research, independent of paper activation.

Historical candles measure price events, never executable option profits. Auction
indicative values require explicit, verified provenance before a probability claim.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import logging
import math
import os
import statistics
import sys
import time as wall_time
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from ..brokers.groww_data import GrowwMarketData
from .contracts import EXCHANGES, INDICES, IST, dumps, identity, keys, number
from .market_check import SDK_VERSION, read_credentials, readonly_transport, run_checks, safe_error

JST = timezone(timedelta(hours=9))
FORMAT = "owner-history-v1"
CAS_START = date(2026, 8, 3)


def probability(successes, trials):
    """Wilson interval for independent sessions; zero evidence stays unknown."""
    if type(successes) is not int or type(trials) is not int or not 0 <= successes <= trials:
        raise ValueError("invalid event counts")
    if trials == 0:
        return {"successes": 0, "trials": 0, "rate": None, "wilson_95": None}
    rate, z = successes / trials, 1.959963984540054
    denominator = 1 + z*z/trials
    center = (rate + z*z/(2*trials))/denominator
    radius = z*math.sqrt(rate*(1-rate)/trials + z*z/(4*trials*trials))/denominator
    return {"successes": successes, "trials": trials, "rate": rate,
            "wilson_95": [max(0, center-radius), min(1, center+radius)]}


def normalize_candles(payload):
    if payload.get("interval_in_minutes") != 1:
        raise ValueError("one-minute candles required")
    rows = payload.get("candles")
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError("invalid candle count")
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5 or not isinstance(row[0], str):
            raise ValueError("invalid candle")
        at = datetime.fromisoformat(row[0])
        # Groww returns naive wall-clock labels. Retain this assumption in provenance.
        at = at.replace(tzinfo=IST) if at.tzinfo is None else at.astimezone(IST)
        if at.second or at.microsecond:
            raise ValueError("not a minute boundary")
        values = row[1:5]
        if not all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in values):
            raise ValueError("nonfinite/nonpositive candle")
        opened, high, low, closed = values
        if not low <= min(opened, closed) <= max(opened, closed) <= high:
            raise ValueError("inconsistent OHLC")
        result.append({"at": at.isoformat(), "open": opened, "high": high, "low": low, "close": closed})
    return result


def collect_history(market, index, start, end):
    """Seven-day chunks avoid large responses; no forward filling or hidden retries."""
    if index not in INDICES or not 0 <= (end-start).days <= 366:
        raise ValueError("index or date range invalid")
    if end >= datetime.now(IST).date():
        raise ValueError("only completed historical dates may be downloaded")
    exchange, bars, failures, windows, expiries = EXCHANGES[index], {}, [], [], []
    for year in range(start.year, end.year+1):
        try:
            market.limiter.wait()
            raw = market.groww.get_expiries(exchange=exchange, underlying_symbol=index, year=year, timeout=15)
            expiries.extend(date.fromisoformat(s).isoformat() for s in raw.get("expiries", []))
        except Exception as exc:
            failures.append({"kind": "EXPIRIES", "year": year, **safe_error(exc)})
    cursor = start
    while cursor <= end:
        last = min(cursor+timedelta(days=6), end)
        try:
            market.limiter.wait()
            payload = market.groww.get_historical_candles(exchange=exchange, segment="CASH",
                groww_symbol=exchange+"-"+index, start_time=f"{cursor} 09:00:00",
                end_time=f"{last} 16:00:00", candle_interval="1minute", timeout=15)
            chunk = normalize_candles(payload)
            for row in chunk:
                if not cursor <= datetime.fromisoformat(row["at"]).date() <= last:
                    raise ValueError("candle outside requested dates")
                if row["at"] in bars and bars[row["at"]] != row:
                    raise ValueError("conflicting duplicate candle")
            bars.update((r["at"], r) for r in chunk)
            windows.append({"start": cursor.isoformat(), "end": last.isoformat(), "count": len(chunk),
                            "last_returned": chunk[-1]["at"] if chunk else None})
        except Exception as exc:
            failures.append({"kind": "CANDLES", "start": cursor.isoformat(), "end": last.isoformat(), **safe_error(exc)})
        cursor = last+timedelta(days=1)
    return {"format": FORMAT, "index": index, "source": "GROWW_HISTORICAL_CANDLES",
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "requested_start": start.isoformat(),
        "requested_end": end.isoformat(), "synthetic": False,
        "value_kind": "BROKER_INDEX_CANDLE_UNCLASSIFIED", "timestamp_semantics_verified": False,
        "timestamp_assumption": "Naive response labels interpreted as Asia/Kolkata minute starts",
        "expiries": sorted(set(expiries)), "expiries_complete": not any(f["kind"] == "EXPIRIES" for f in failures),
        "windows": windows, "failures": failures, "candles": [bars[k] for k in sorted(bars)]}


def value_at(rows, day, hhmm):
    """Last fully completed minute; never use the close of an unfinished bar."""
    target = datetime.combine(day, time.fromisoformat(hhmm), IST)-timedelta(minutes=1)
    row = rows.get(target.isoformat())
    return row["close"] if row else None


def distribution(values):
    return {"count": len(values), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "minimum": min(values) if values else None, "maximum": max(values) if values else None}


def validate_protocol(protocol):
    if not isinstance(protocol, dict) or protocol.get("format") not in (
            "owner-strategy-hypotheses-v1", "owner-strategy-hypotheses-v2"):
        raise ValueError("unsupported protocol")
    expected = {"format", "timezone", "preferred_spread", "execution_enabled", "late_session",
                "swing", "expiry_reversal", "statistics"}
    if protocol["format"] == "owner-strategy-hypotheses-v2":
        expected.add("everyday")
    if set(protocol) != expected:
        raise ValueError("unsupported protocol")
    if protocol["timezone"] != "Asia/Tokyo" or protocol["preferred_spread"] != "CALL_CREDIT" or protocol["execution_enabled"] is not False:
        raise ValueError("unsupported execution or strategy")
    # Keep the original hypotheses reproducible under v1; v2 adds the everyday
    # rule explicitly. Never silently change parameters on existing holdouts.
    late, swing, reversal, stats = (protocol[k] for k in ("late_session", "swing", "expiry_reversal", "statistics"))
    if (late["entry_jst"], late["exit_jst"]) != ("17:45", "18:45"):
        raise ValueError("unsupported late-session window")
    if (swing["entry_jst"], swing["exit"], swing["hedge_listed_strike_offsets"]) != ("18:45", "NEXT_SESSION_09:30_IST", [10,20]):
        raise ValueError("unsupported swing variant")
    if (reversal["instrument"], reversal["baseline_jst"], reversal["signal_window_jst"], reversal["outcome_jst"]) != ("SENSEX", "18:45", ["18:50","19:00"], "19:06"):
        raise ValueError("unsupported reversal times/index")
    if (reversal["minimum_upward_jump_points"], reversal["return_band_half_width_points"], reversal["required_value_kind"]) != (500,200,"INDICATIVE_AUCTION_INDEX"):
        raise ValueError("unsupported reversal threshold/value kind")
    if stats["chronological_holdout_fraction"] != .3 or stats["minimum_holdout_events"] < 30 or stats["confidence_level"] != .95:
        raise ValueError("unsupported statistics")
    if "everyday" in protocol:
        everyday = protocol["everyday"]
        keys(everyday, {"entry_not_before_jst", "frequency", "short_call_offset_points",
            "default_index", "sensex_weekdays", "owner_reported_lot_size", "lot_size_policy", "carry_policy",
            "listed_strike_policy", "hedge_policy", "hedge_objective", "margin_budget_inr",
            "max_spread_loss_inr", "exit_policy", "exit_criteria", "expiry_rule",
            "allow_position_size_increase", "status"})
        if everyday["entry_not_before_jst"] != "13:15" or everyday["frequency"] != "EACH_TRADING_SESSION":
            raise ValueError("unsupported everyday window")
        if (everyday["short_call_offset_points"] != {"SENSEX":800,"NIFTY":400}
                or any(type(v) is not int for v in everyday["short_call_offset_points"].values())):
            raise ValueError("unsupported everyday offsets")
        if everyday["default_index"] != "NIFTY" or everyday["sensex_weekdays"] != ["WEDNESDAY","THURSDAY"]:
            raise ValueError("unsupported everyday weekday preference")
        if (everyday["owner_reported_lot_size"] != {"NIFTY":65}
                or type(everyday["owner_reported_lot_size"]["NIFTY"]) is not int
                or everyday["lot_size_policy"] != "VERIFY_CURRENT_CONTRACT_METADATA"):
            raise ValueError("unsupported everyday lot-size policy")
        if (everyday["listed_strike_policy"] != "AT_OR_ABOVE_SPOT_PLUS_OFFSET"
                or everyday["hedge_policy"] != "HIGHER_STRIKE_CALL_SAME_EXPIRY_EQUAL_QUANTITY"
                or everyday["hedge_objective"] != "COMPARE_NET_MAX_PROFIT_WITHIN_MARGIN_AT_FIXED_QUANTITY"
                or everyday["allow_position_size_increase"] is not False):
            raise ValueError("unsupported everyday hedge policy")
        if (everyday["exit_policy"] != "HOLD_WHILE_FAVORABLE_EXIT_WHEN_ADVERSE"
                or everyday["carry_policy"] != "REASSESS_NEXT_SESSION"
                or everyday["exit_criteria"] != "INDEX_RELATIVE_TO_ENTRY_FLAT_OR_DOWN_HOLD_UP_EXIT"
                or everyday["status"] != "UNTESTED_OPTION_PROFITABILITY"):
            raise ValueError("unsupported everyday exit/status")
        for field in ("margin_budget_inr", "max_spread_loss_inr"):
            limit = number(everyday[field], nullable=True)
            if limit is not None and limit <= 0:
                raise ValueError("positive everyday limit required")
        for field in ("exit_criteria", "expiry_rule"):
            value = everyday[field]
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 500):
                raise ValueError("invalid everyday review rule")


def everyday_reference(protocol, index, spot, observed_at, listed_strikes=None):
    """Apply the owner's offset to one observation; never select a hedge/order.

    Historical references use the first eligible completed minute. Live callers
    must supply their actual observation and exact listed call strikes; a strike
    match alone does not verify a contract, expiry, book, margin or fill.
    """
    validate_protocol(protocol)
    rule = protocol.get("everyday")
    if rule is None or index not in rule["short_call_offset_points"]:
        return {"status":"NOT_APPLICABLE", "execution_enabled":False}
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("offset-aware everyday observation required")
    local = observed_at.astimezone(JST)
    weekdays = ("MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY")
    if local.weekday() >= 5:
        return {"status":"ORDINARY_WEEKDAY_PLAN_NOT_APPLICABLE", "execution_enabled":False}
    preferred = "SENSEX" if weekdays[local.weekday()] in rule["sensex_weekdays"] else rule["default_index"]
    if index != preferred:
        return {"status":"NOT_PREFERRED_INDEX_TODAY", "preferred_index":preferred, "execution_enabled":False}
    if local.time() < time.fromisoformat(rule["entry_not_before_jst"]):
        return {"status":"BEFORE_EVERYDAY_WINDOW", "execution_enabled":False}
    if number(spot) <= 0:
        raise ValueError("positive everyday spot required")
    offset = rule["short_call_offset_points"][index]
    floor = number(spot + offset)
    listed = None
    if listed_strikes is not None:
        if not isinstance(listed_strikes, list) or len(listed_strikes) > 10000:
            raise ValueError("bounded listed call strikes required")
        for strike in listed_strikes:
            if number(strike) <= 0:
                raise ValueError("positive listed strike required")
        listed = min((s for s in listed_strikes if s >= floor), default=None)
    return {"status":"EVERYDAY_REFERENCE_ONLY", "index":index,
        "observed_at":observed_at.isoformat(), "reference_spot":spot,
        "short_call_offset_points":offset, "minimum_short_call_strike":floor,
        "listed_short_call_strike":listed, "contract_and_quote_verified":False,
        "lot_size_verified":False, "exit_policy":rule["exit_policy"], "hedge_selected":None,
        "probability_of_net_option_profit":None, "execution_enabled":False}


def everyday_direction_review(protocol, entry_spot, current_spot):
    """Owner-defined index direction review, independent of spread profit/loss.

    An EXIT_REVIEW is data for the owner, not a broker command. Unknown observations
    never become favorable. No tolerance, option-profit or execution claim is added.
    """
    validate_protocol(protocol)
    if "everyday" not in protocol:
        raise ValueError("everyday protocol required")
    for spot in (entry_spot, current_spot):
        if spot is not None and number(spot) <= 0:
            raise ValueError("positive index observations required")
    if entry_spot is None or current_spot is None:
        return {"assessment":"UNKNOWN", "review":"DATA_REQUIRED", "spot_move_points":None,
            "spread_profit_verified":False, "order_submitted":False, "execution_enabled":False}
    move = current_spot - entry_spot
    return {"assessment":"ADVERSE" if move > 0 else "FAVORABLE",
        "review":"EXIT_REVIEW" if move > 0 else "HOLD_REVIEW", "spot_move_points":move,
        "spread_profit_verified":False, "order_submitted":False, "execution_enabled":False}


def review_everyday(value, protocol):
    """Review structured observations offline, including an existing position.

    A held position is assessed even before the entry window or on a day when the
    other index is preferred. This does not select an expiry, hedge or order.
    """
    keys(value, {"index", "spot", "observed_at"}, {"entry_spot", "listed_call_strikes"})
    if value["index"] not in INDICES or not isinstance(value["observed_at"], str):
        raise ValueError("supported index and observation timestamp required")
    observed_at = datetime.fromisoformat(value["observed_at"])
    if observed_at.utcoffset() is None:
        raise ValueError("offset-aware everyday observation required")
    if value["spot"] is not None and number(value["spot"]) <= 0:
        raise ValueError("positive everyday spot required")
    validate_protocol(protocol)
    if "everyday" not in protocol:
        raise ValueError("everyday protocol required")
    reference = (everyday_reference(protocol, value["index"], value["spot"], observed_at,
                                   value.get("listed_call_strikes"))
                 if value["spot"] is not None else
                 {"status":"DATA_REQUIRED", "execution_enabled":False})
    position = (everyday_direction_review(protocol, value["entry_spot"], value["spot"])
                if "entry_spot" in value and value["index"] in protocol["everyday"]["short_call_offset_points"]
                else None)
    return {"format":"owner-everyday-review-v1", "status":"OWNER_REVIEW_ONLY",
        "index":value["index"], "observed_at":observed_at.isoformat(),
        "protocol_hash":identity(protocol), "reference":reference, "position_review":position,
        "source_timestamps_and_contracts_verified":False, "order_submitted":False,
        "execution_enabled":False}


def evaluate_history(dataset, protocol):
    validate_protocol(protocol)
    if dataset.get("format") != FORMAT or dataset.get("index") not in INDICES:
        raise ValueError("invalid dataset")
    if type(dataset.get("synthetic")) is not bool or type(dataset.get("timestamp_semantics_verified")) is not bool:
        raise ValueError("explicit dataset provenance required")
    ordered = [r["at"] for r in dataset["candles"]]
    if ordered != sorted(set(ordered)):
        raise ValueError("unique chronological candles required")
    days = defaultdict(dict)
    for row in dataset["candles"]:
        at = datetime.fromisoformat(row["at"])
        if at.utcoffset() != IST.utcoffset(at):
            raise ValueError("expected explicit IST timestamps")
        normalize_candles({"interval_in_minutes":1,"candles":[[row['at'],row['open'],row['high'],row['low'],row['close']]]})
        day = at.date()
        days[day][row["at"]] = row
    ordered_days = sorted(days)
    split = max(1, int(len(ordered_days)*.7))
    holdout_days = set(ordered_days[split:])
    records, excluded, events, everyday_observations = [], Counter(), [], []
    expiries = set(dataset["expiries"])
    for day in ordered_days:
        partition = "HOLDOUT" if day in holdout_days else "DEVELOPMENT"
        daily = protocol.get("everyday")
        if daily and dataset["index"] in daily["short_call_offset_points"]:
            # Earliest eligible reference only, not an assumed daily entry/fill.
            preferred = "SENSEX" if day.weekday() in (2,3) else daily["default_index"]
            if day.weekday() >= 5:
                excluded["EVERYDAY_ORDINARY_WEEKDAY_PLAN_NOT_APPLICABLE"] += 1
            elif dataset["index"] != preferred:
                excluded["EVERYDAY_NOT_PREFERRED_INDEX_TODAY"] += 1
            else:
                reference_at = datetime.combine(day, time.fromisoformat(daily["entry_not_before_jst"]), JST).astimezone(IST)
                reference_spot = value_at(days[day], day, reference_at.strftime("%H:%M"))
                if reference_spot is None:
                    excluded["EVERYDAY_REFERENCE_MINUTE_MISSING"] += 1
                else:
                    observation = everyday_reference(protocol, dataset["index"], reference_spot, reference_at)
                    everyday_observations.append({"day":str(day), "partition":partition, **observation})
        late_start, late_end = (value_at(days[day], day, t) for t in ("14:15", "15:15"))
        if late_start is None or late_end is None:
            excluded["LATE_WINDOW_ENDPOINT_MISSING"] += 1
        else:
            records.append({"day":str(day), "partition":partition,
                "regime":"CAS" if day >= CAS_START else "PRE_CAS", "late_spot_move_points":late_end-late_start})
        if dataset["index"] != "SENSEX" or day.isoformat() not in expiries or day < CAS_START:
            continue
        base, outcome = value_at(days[day], day, "15:15"), value_at(days[day], day, "15:36")
        # All 10 complete signal-window minutes must exist; gaps cannot become non-events.
        signal_rows = [days[day].get(datetime.combine(day, time(15, minute), IST).isoformat()) for minute in range(20,30)]
        if base is None or outcome is None or any(r is None for r in signal_rows):
            excluded["EXPIRY_WINDOW_INCOMPLETE"] += 1
            continue
        event = next((r for r in signal_rows if r["close"]-base >= 500), None)
        events.append({"day":str(day), "partition":partition, "signal":event is not None,
            "baseline":base, "outcome":outcome,
            "signal_available_at":(datetime.fromisoformat(event["at"])+timedelta(minutes=1)).isoformat() if event else None,
            "returned_to_band":abs(outcome-base) <= 200 if event else None})
    overnight = []
    for before, after in zip(ordered_days, ordered_days[1:]):
        # A missing intervening session cannot silently count as a one-session hold.
        between = (before+timedelta(days=n) for n in range(1,(after-before).days))
        if (after-before).days > 4 or any(day.weekday() < 5 for day in between):
            excluded["SWING_SESSION_GAP"] += 1
            continue
        first, last = value_at(days[before], before, "15:15"), value_at(days[after], after, "09:30")
        if first is None or last is None:
            excluded["SWING_WINDOW_ENDPOINT_MISSING"] += 1
            continue
        if (before in holdout_days) != (after in holdout_days):
            excluded["SWING_CROSSES_HOLDOUT_SPLIT"] += 1
            continue
        overnight.append({"day":str(before), "exit_day":str(after),
            "partition":"HOLDOUT" if before in holdout_days else "DEVELOPMENT", "spot_move_points":last-first})
    assumptions = []
    if dataset["value_kind"] != "INDICATIVE_AUCTION_INDEX": assumptions.append("INDICATIVE_AUCTION_SERIES_NOT_VERIFIED")
    if not dataset["timestamp_semantics_verified"]: assumptions.append("TIMESTAMP_SEMANTICS_NOT_VERIFIED")
    if dataset["synthetic"]: assumptions.append("SYNTHETIC_DATA")
    if not dataset["expiries_complete"]: assumptions.append("EXPIRY_CALENDAR_INCOMPLETE")
    partitions = {}
    for part in ("DEVELOPMENT", "HOLDOUT"):
        selected = [r for r in events if r["partition"] == part and r["signal"]]
        partitions[part] = {"complete_expiry_sessions":sum(r['partition']==part for r in events),
            "candidate_series_events":probability(sum(r['returned_to_band'] for r in selected),len(selected)),
            "late_spot_move_points":distribution([r['late_spot_move_points'] for r in records if r['partition']==part]),
            "swing_spot_move_points":distribution([r['spot_move_points'] for r in overnight if r['partition']==part])}
    measured = partitions['HOLDOUT']['candidate_series_events']
    if measured['trials'] < protocol['statistics']['minimum_holdout_events']:
        assumptions.append('HOLDOUT_EVENT_COUNT_INSUFFICIENT')
    result = {"format":"owner-study-result-v1", "index":dataset['index'], "protocol_hash":identity(protocol),
        "dataset_hash":identity(dataset), "source_value_kind":dataset['value_kind'],
        "status":"INSUFFICIENT_EVIDENCE" if assumptions else "PRICE_EVENT_REVIEW_REQUIRED",
        "strategy_reversal_probability":None if assumptions else measured,
        "probability_of_net_option_profit":None, "strategy_selected":None, "execution_enabled":False,
        "blockers":assumptions+['OPTION_BID_ASK_COSTS_AND_FILL_MODEL_REQUIRED'],
        "coverage":{"observed_days":len(days),"holdout_days":[str(d) for d in sorted(holdout_days)],
            "excluded":dict(excluded), "download_failures":dataset.get('failures',[])},
        "partitions":partitions, "expiry_events":events, "late_spot_observations":records,
        "swing_spot_observations":overnight,
        "limitations":["Index moves are not option P&L or isolated theta",
            "Candles cannot establish tradable bid/ask, simultaneous fills, fees or margin",
            "Wilson intervals assume independent sessions; small samples and regime changes remain material",
            "10/20-strike hedges are hypotheses, not approval to average into a losing position"]}
    if "everyday" in protocol:
        rule = protocol["everyday"]
        result.update(format="owner-study-result-v2", strategy_count=4,
            everyday_spot_observations=everyday_observations,
            everyday_strategy={"status":"UNTESTED_OPTION_PROFITABILITY",
                "applicable":dataset["index"] in rule["short_call_offset_points"],
                "entry_not_before_jst":rule["entry_not_before_jst"],
                "default_index":rule["default_index"], "sensex_weekdays":rule["sensex_weekdays"],
                "exit_policy":rule["exit_policy"], "margin_budget_inr":rule["margin_budget_inr"],
                "exit_criteria":rule["exit_criteria"], "expiry_rule":rule["expiry_rule"],
                "carry_policy":rule["carry_policy"], "lot_size_verified":False,
                "max_spread_loss_inr":rule["max_spread_loss_inr"],
                "unresolved":[name for name in ("margin_budget_inr","expiry_rule","exit_criteria") if rule[name] is None],
                "probability_of_net_option_profit":None, "hedge_selected":None, "execution_enabled":False})
        result["limitations"].append("Everyday strike references are observations, not entries or next-day option returns")
    return result


def record_market(market, directory, until, *, interval_seconds=60, clock=lambda:datetime.now(timezone.utc), sleep=wall_time.sleep):
    """Bounded local capture with one authenticated client and a manual STOP file."""
    now=clock()
    if until.tzinfo is None or not 0 < (until-now).total_seconds() <= 7200:
        raise ValueError('recording must end within two hours')
    if type(interval_seconds) is not int or not 30 <= interval_seconds <= 300:
        raise ValueError('recording interval must be 30–300 seconds')
    directory=Path(directory)
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    manifest={'format':'owner-recording-v1','started_at':now.isoformat(),'until':until.isoformat(),
        'interval_seconds':interval_seconds,'execution_enabled':False,'snapshots':0,'status':'RECORDING'}
    def save_manifest():
        temporary=directory/'manifest.tmp'
        temporary.write_text(dumps(manifest),encoding='utf-8')
        temporary.replace(directory/'manifest.json')
    save_manifest()
    while clock() < until and not (directory/'STOP').exists():
        began=clock()
        local=began.astimezone(IST)
        snapshot=run_checks(market,clock=clock)
        snapshot.update(recorded_at=began.isoformat(),finished_at=clock().isoformat(),
            index_value_kind='BROKER_QUOTE_UNCLASSIFIED',execution_enabled=False,
            in_cas_window=(local.date() >= CAS_START and time(15,15) <= local.time().replace(tzinfo=None) < time(15,35)),
            note='CAS window label does not establish that an index value is indicative')
        path=directory/f"snapshot-{manifest['snapshots']:04}.json"
        with path.open('x',encoding='utf-8') as out:
            out.write(dumps(snapshot))
        manifest['snapshots']+=1
        manifest['last_snapshot_at']=snapshot['finished_at']
        save_manifest()
        # Do not start another full set of bounded HTTP reads close to the deadline.
        remaining=(until-clock()).total_seconds()
        if remaining <= 60 or (directory/'STOP').exists():
            break
        elapsed=(clock()-began).total_seconds()
        sleep(min(max(0,interval_seconds-elapsed),remaining))
    manifest['status']='STOP_FILE' if (directory/'STOP').exists() else 'CAPTURE_COMPLETE'
    manifest['finished_at']=clock().isoformat()
    save_manifest()
    return manifest


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command',required=True)
    sub = subs.add_parser('download')
    sub.add_argument('--credentials-stdin',action='store_true',required=True)
    sub.add_argument('--index',choices=INDICES,required=True)
    sub.add_argument('--start',type=date.fromisoformat,required=True)
    sub.add_argument('--end',type=date.fromisoformat,required=True)
    sub.add_argument('--output',type=Path,required=True)
    sub = subs.add_parser('record')
    sub.add_argument('--credentials-stdin',action='store_true',required=True)
    sub.add_argument('--until',type=datetime.fromisoformat,required=True)
    sub.add_argument('--interval-seconds',type=int,default=60)
    sub.add_argument('--output',type=Path,required=True)
    sub = subs.add_parser('evaluate')
    sub.add_argument('--dataset',type=Path,required=True)
    sub.add_argument('--protocol',type=Path,default=Path('config/owner_strategies.json'))
    sub.add_argument('--output',type=Path,required=True)
    sub = subs.add_parser('everyday',help='Offline review of structured everyday observations')
    sub.add_argument('--input',type=Path,required=True)
    sub.add_argument('--protocol',type=Path,default=Path('config/owner_strategies.json'))
    sub.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError('new output in existing private directory required')
    if args.command == 'everyday':
        if args.input.stat().st_size > 262144 or args.protocol.stat().st_size > 16384:
            raise ValueError('input too large')
        result = review_everyday(json.loads(args.input.read_text(encoding='utf-8')),
                                json.loads(args.protocol.read_text(encoding='utf-8')))
    elif args.command == 'evaluate':
        if args.dataset.stat().st_size > 100_000_000 or args.protocol.stat().st_size > 16384:
            raise ValueError('input too large')
        result = evaluate_history(json.loads(args.dataset.read_text(encoding='utf-8')),json.loads(args.protocol.read_text(encoding='utf-8')))
    else:
        if args.command == 'download' and (not 0 <= (args.end-args.start).days <= 366 or args.end >= datetime.now(IST).date()):
            raise ValueError('bounded completed historical dates required')
        if args.command == 'record' and (args.until.tzinfo is None or not 0 < (args.until-datetime.now(timezone.utc)).total_seconds() <= 7200):
            raise ValueError('recording must end within two hours')
        audit=[]
        previous=logging.root.manager.disable
        with open(os.devnull,'w') as muted, contextlib.redirect_stdout(muted), contextlib.redirect_stderr(muted):
            logging.disable(logging.CRITICAL)
            try:
                if importlib.metadata.version('growwapi') != SDK_VERSION:
                    raise ValueError('SDK version mismatch')
                credentials=read_credentials(sys.stdin)
                from growwapi import GrowwAPI
                with readonly_transport(audit,history=args.command=='download',
                                        deadline=args.until if args.command=='record' else None):
                    try:
                        token=GrowwAPI.get_access_token(api_key=credentials['api_key'],secret=credentials['api_secret'])
                    finally:
                        credentials.clear()
                    market=GrowwMarketData(token)
                    del token
                    if args.command == 'record':
                        result=record_market(market,args.output,args.until,interval_seconds=args.interval_seconds)
                    else:
                        result=collect_history(market,args.index,args.start,args.end)
                result['http_audit']=audit
            finally:
                logging.disable(previous)
    if args.command != 'record':
        with args.output.open('x',encoding='utf-8') as out:
            out.write(dumps(result))
    print(dumps({'status':result.get('status','HISTORY_SAVED_WITH_EXPLICIT_COVERAGE'),
        'output':str(args.output),'candles':len(result.get('candles',[])), 'failures':len(result.get('failures',[])),
        'execution_enabled':False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(dumps({'status':'BLOCKED','failure':safe_error(exc)}))
        raise SystemExit(2) from None
