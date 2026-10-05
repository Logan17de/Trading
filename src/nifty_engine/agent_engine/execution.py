"""Prepared Oracle order operations. Not wired to a production write transport.

The existing journal owns references and fills. Tests inject a simulated broker;
production observation remains protected by its HTTP allowlist and pause marker.
Uncertain writes are reconciled, never automatically submitted again.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

from .contracts import EXCHANGES, dumps, identity, integer, keys, number, stamp
from .pc_control import JST, SYMBOL, digest
from . import premium_strategy as policy

TERMINAL = {"EXECUTED", "CANCELLED", "REJECTED", "EXPIRED"}


def fresh(at, now, seconds=15):
    return 0 <= (now-stamp(at)).total_seconds() <= seconds


def leg(value):
    keys(value, {"symbol", "index", "expiry", "strike", "lot_size", "tick_size",
                 "bid", "ask", "bid_quantity", "ask_quantity", "received_at"})
    if value["index"] not in ("NIFTY", "SENSEX") or not SYMBOL.fullmatch(value["symbol"]):
        raise ValueError("INDEX_OPTION_REQUIRED")
    if not value["symbol"].startswith(value["index"]):
        raise ValueError("INDEX_SYMBOL_MISMATCH")
    date.fromisoformat(value["expiry"])
    integer(value["lot_size"], 1, 100000)
    for k in ("strike", "tick_size", "bid", "ask", "bid_quantity", "ask_quantity"):
        if number(value[k]) <= 0:
            raise ValueError("POSITIVE_CONTRACT_AND_BOOK_REQUIRED")
    if value["bid"] > value["ask"]:
        raise ValueError("CROSSED_BOOK")
    tick = Decimal(str(value["tick_size"]))
    if any(Decimal(str(value[k])) % tick for k in ("bid", "ask")):
        raise ValueError("BOOK_OFF_TICK")
    stamp(value["received_at"])
    return value


def basket_key(short, hedge, quantity):
    return identity({"short":short["symbol"], "hedge":hedge["symbol"], "quantity":quantity})


def rank_baskets(cfg, chain, margins, funds, expiry_evidence, now, *, strategy="EVERYDAY",
                 trend="FLAT", active=(), complete=False):
    """Rank supplied *current* broker books/costs, never estimates from cash/LTP.

    `margins` contains basket and hedge-only broker requirements and round-trip
    charge evidence keyed by exact symbols/quantity. Missing candidates stay
    unranked; maximum means maximum among the explicitly evaluated candidates.
    Nearest confirmed listed expiry is used. These are proposals, never orders.
    """
    policy.validate(cfg)
    result = {"status":"WAIT", "reason":"FRESH_COMPLETE_DATA_REQUIRED", "broker_writes":False,
              "strategy":strategy, "selected":None, "evaluated":0, "unranked":0}
    index = policy.preferred_index(now)
    day = now.astimezone(JST).date().isoformat()
    if not policy.entry_window(now):
        return dict(result, reason="OUTSIDE_1400_1900_JST")
    if (not complete or expiry_evidence.get("status") != "CONFIRMED_CURRENT_MASTER"
            or expiry_evidence.get("day_jst") != day):
        return result
    dates = sorted(d for d in expiry_evidence.get("expiries", []) if d >= day)
    if not dates:
        return dict(result, reason="CURRENT_EXPIRY_REQUIRED")
    expiry = dates[0]
    if strategy == "EVERYDAY" and day in dates:
        return dict(result, reason="ACTUAL_EXPIRY_DAY_SKIPPED")
    if strategy not in ("EVERYDAY", "LATE_SESSION"):
        raise ValueError("ACTIVE_OWNER_STRATEGY_REQUIRED")
    if strategy == "LATE_SESSION" and (expiry != day or now.astimezone(JST).hour < 18):
        return dict(result, reason="WAIT_FOR_ACTUAL_EXPIRY_1800_JST")
    try:
        if not fresh(funds["received_at"], now):
            return result
        buy_balance = number(funds["option_buy_available_inr"])
        sell_balance = number(funds["option_sell_available_inr"])
        if min(buy_balance, sell_balance) < 0:
            return dict(result, reason="AVAILABLE_MARGIN_REQUIRED")
        rows = [leg(r) for r in chain if r["index"] == index and r["expiry"] == expiry]
        if len({r["symbol"] for r in rows}) != len(rows):
            raise ValueError("DUPLICATE_CONTRACT")
    except (ValueError, TypeError, KeyError):
        return dict(result, reason="CONTRACT_BOOK_OR_FUNDS_INVALID")
    if not rows:
        return result
    kind = "CE" if strategy == "EVERYDAY" else cfg["late_session"]["trend_mapping"].get(trend)
    shorts = [r for r in rows if r["symbol"].endswith(kind or "NO_TRADE") and fresh(r["received_at"], now)]
    if strategy == "LATE_SESSION":
        # Spot is explicit evidence, not inferred from option premiums.
        spot = expiry_evidence.get("spot")
        try:
            wanted = policy.expiry_short(cfg, index, spot, expiry_evidence.get("listed_strikes", [r["strike"] for r in shorts]), trend, actual_expiry=True)
        except (ValueError, TypeError):
            return result
        shorts = [r for r in shorts if r["strike"] == wanted.get("short_strike")]
    elif shorts:
        target = cfg["short_call_target_rupees"][index]
        distance = min(abs(r["bid"]-target) for r in shorts)
        shorts = [r for r in shorts if abs(r["bid"]-target) == distance]
    eligible = []
    protected = {r.get("symbol") for r in active if r.get("ownership") != "ENGINE_VERIFIED"}
    for short in shorts:
        hedges = [r for r in rows if r["symbol"].endswith(kind) and fresh(r["received_at"], now)
                  and (r["strike"] > short["strike"] if kind == "CE" else r["strike"] < short["strike"])]
        for hedge in hedges:
            if hedge["lot_size"] != short["lot_size"]:
                continue
            for lots in range(1, (cfg["maximum_lots"] or 0)+1):
                qty = lots*short["lot_size"]
                signatures = {(r.get("symbol"), r.get("side"), r.get("quantity"), r.get("product")) for r in active}
                if ((short["symbol"], "SELL", qty, "NRML") in signatures
                        and (hedge["symbol"], "BUY", qty, "NRML") in signatures):
                    return dict(result, status="SKIP", reason="MATCHING_POSITION_ALREADY_EXISTS")
                if short["symbol"] in protected or hedge["symbol"] in protected:
                    continue
                m = margins.get(basket_key(short, hedge, qty))
                if not m or not fresh(m["received_at"], now):
                    result["unranked"] += 1
                    continue
                try:
                    requirement = number(m["basket_requirement_inr"])
                    hedge_requirement = number(m["hedge_requirement_inr"])
                    charges = number(m["round_trip_charges_inr"])
                    if min(requirement, hedge_requirement, charges) < 0:
                        raise ValueError()
                except (ValueError, TypeError, KeyError):
                    result["unranked"] += 1
                    continue
                result["evaluated"] += 1
                credit = short["bid"]-hedge["ask"]
                width = abs(hedge["strike"]-short["strike"])
                profit = credit*qty-charges
                if (not 0 < credit < width or profit <= 0 or requirement > sell_balance
                        or hedge_requirement > buy_balance or hedge["ask"]*qty > buy_balance
                        or short["bid_quantity"] < qty or hedge["ask_quantity"] < qty):
                    continue
                eligible.append({"key":basket_key(short, hedge, qty), "index":index, "expiry":expiry,
                    "strategy":strategy, "short":short, "hedge":hedge, "quantity":qty, "lots":lots,
                    "product":"NRML", "net_max_expiry_profit_inr":round(profit, 2),
                    "net_max_expiry_loss_inr":round((width-credit)*qty+charges, 2),
                    "basket_requirement_inr":requirement, "round_trip_charges_inr":charges,
                    "prepared_at":now.isoformat(), "broker_writes":False})
    if not eligible:
        return dict(result, reason="NO_VERIFIED_AFFORDABLE_HEDGED_CANDIDATE")
    eligible.sort(key=lambda r:(-r["net_max_expiry_profit_inr"], r["basket_requirement_inr"], r["lots"], r["key"]))
    selected = eligible[0]
    if any(r.get("ownership") == "ENGINE_VERIFIED" for r in active):
        return dict(result, status="REVIEW_OWNED_REPLACEMENT" if strategy == "LATE_SESSION" else "OCCUPIED",
                    reason="CONFIRM_OWNED_BASKET_FLAT_BEFORE_ENTRY", selected=selected)
    return dict(result, status="PREPARED", reason="RANKED_CURRENT_CANDIDATES", selected=selected,
                ranking_scope="SUPPLIED_VERIFIED_CANDIDATES", all_candidates_ranked=result["unranked"] == 0)


def protective_stop(short_fill, hedge_fill, quantity, costs, tick, loss_limit=2000):
    """Conservative short premium trigger if the bought hedge falls to zero.

    The live basket monitor must also track both legs. Trigger/limit prices cannot
    guarantee fills or a maximum realized loss. DAY orders cannot protect a carry.
    """
    for n in (short_fill, hedge_fill, tick, loss_limit):
        if number(n) <= 0:
            raise ValueError("POSITIVE_PROTECTION_INPUT_REQUIRED")
    integer(quantity, 1, 1000000)
    if number(costs) < 0:
        raise ValueError("NONNEGATIVE_COST_REQUIRED")
    trigger = Decimal(str(short_fill))-Decimal(str(hedge_fill))+(Decimal(str(loss_limit))-Decimal(str(costs)))/quantity
    step = Decimal(str(tick))
    trigger = (trigger/step).to_integral_value(rounding=ROUND_FLOOR)*step
    if trigger <= Decimal(str(short_fill)):
        raise ValueError("INSUFFICIENT_STOP_ROOM")
    return {"trigger_price":float(trigger), "price":float(trigger+step), "quantity":quantity,
            "direction":"UP", "transaction_type":"BUY", "order_type":"SL",
            "loss_cap_guaranteed":False, "carry_protection_required":True}


class PreparedOrderGateway:
    """Crash-safe standard-order gateway for replay and future reviewed wiring.

    It is deliberately absent from Runtime's broker-write path. A caller must
    provide a verified deployment gate plus explicit owner On and fresh complete
    observations on *every* write. Read-only Runtime cannot enable it by a button.
    """
    def __init__(self, journal, broker, pause_file, *, deployed=False, clock):
        self.journal, self.broker = journal, broker
        self.pause_file, self.deployed, self.clock = Path(pause_file), deployed, clock

    def _gate(self, observation):
        now = self.clock()
        # The prepared path is executable only against an explicitly simulated
        # broker. Runtime has no way to turn a real Groww SDK into this transport.
        if getattr(self.broker, "simulated", False) is not True:
            raise ValueError("LIVE_TRANSPORT_NOT_APPROVED")
        if not self.deployed or self.pause_file.exists() or not policy.intent(self.journal.store)["enabled"]:
            raise ValueError("EXECUTION_DISABLED_OR_PAUSED")
        if not policy.entry_window(now) or not fresh(observation["received_at"], now, 10) or observation.get("complete") is not True:
            raise ValueError("FRESH_COMPLETE_ACTION_WINDOW_REQUIRED")

    def submit(self, key, slot, strategy, order, observation):
        keys(order, {"trading_symbol", "exchange", "transaction_type", "quantity", "price",
                     "product", "order_type"}, {"trigger_price"})
        if (not SYMBOL.fullmatch(order["trading_symbol"]) or order["product"] != "NRML"
                or order["exchange"] != EXCHANGES.get("SENSEX" if order["trading_symbol"].startswith("SENSEX") else "NIFTY")
                or order["transaction_type"] not in ("BUY", "SELL") or order["order_type"] not in ("LIMIT", "SL")
                or number(order["price"]) <= 0):
            raise ValueError("EXACT_LIMIT_OR_PROTECTION_ORDER_REQUIRED")
        integer(order["quantity"], 1, 1000000)
        if order["order_type"] == "SL" and number(order.get("trigger_price")) <= 0:
            raise ValueError("STOP_TRIGGER_REQUIRED")
        symbol = order["trading_symbol"]
        if self.journal.store.read("SELECT symbol FROM pc_protected WHERE symbol=?", (symbol,)):
            raise ValueError("MANUAL_OR_UNKNOWN_CONTRACT_PROTECTED")
        self._gate(observation)
        fingerprint = identity(order)
        operation_key = "prepared-order-"+identity({"operation":key})
        # Reserve + write-ahead record are one transaction. Concurrent callers
        # cannot race duplicate submissions or split the one-slot reservation.
        with self.journal.store.transaction() as db:
            old = db.execute("SELECT body FROM meta WHERE key=?", (operation_key,)).fetchone()
            if old:
                record = json.loads(old[0])
                if record["fingerprint"] != fingerprint:
                    raise ValueError("OPERATION_ID_CONFLICT")
                return {"status":"RECONCILIATION_REQUIRED", "reference":record["reference"], "broker_writes":False}
            if db.execute("SELECT 1 FROM pc_orders WHERE slot<>? AND state<>'CLOSED'", (slot,)).fetchone():
                raise ValueError("ONE_ACTIVE_ENGINE_BASKET_ONLY")
            if strategy not in ("EVERYDAY", "LATE_SESSION"):
                raise ValueError("ACTIVE_STRATEGY_REQUIRED")
            owner = db.execute("SELECT strategy FROM pc_slots WHERE slot=?", (slot,)).fetchone()
            if owner and owner[0] != strategy:
                raise ValueError("NO_STRATEGY_REASSIGNMENT")
            import uuid
            reference = "GT"+uuid.uuid4().hex[:18]
            db.execute("INSERT OR IGNORE INTO pc_slots VALUES(?,?)", (slot, strategy))
            db.execute("INSERT INTO pc_orders(reference,slot,symbol,side,quantity) VALUES(?,?,?,?,?)",
                       (reference, slot, symbol, order["transaction_type"], order["quantity"]))
            record = {"status":"SUBMITTING", "reference":reference, "fingerprint":fingerprint,
                      "order":order, "slot":slot, "operation_key":operation_key}
            db.execute("INSERT INTO meta VALUES(?,?)", (operation_key, dumps(record)))
        try:
            # A pause/Off can arrive after the journal commit but before the write.
            self._gate(observation)
        except (ValueError, PermissionError):
            record["status"] = "ABORTED_BEFORE_WRITE"
            self.journal.store.set_meta(operation_key, record)
            with self.journal.store.transaction() as db:
                db.execute("UPDATE pc_orders SET state='CLOSED' WHERE reference=?", (reference,))
            raise
        try:
            receipt = self.broker.place_order(segment="FNO", validity="DAY", order_reference_id=reference,
                                              timeout=5, **order)
            if receipt.get("order_reference_id") != reference or not isinstance(receipt.get("groww_order_id"), str):
                raise ValueError("UNVERIFIED_BROKER_RECEIPT")
            self.journal.acknowledge(reference, receipt["groww_order_id"])
            record.update(status="ACKNOWLEDGED", broker_id=receipt["groww_order_id"])
        except Exception as exc:
            from .execution_gate import ExecutionDenied
            if isinstance(exc,ExecutionDenied):
                record['status']='ABORTED_BEFORE_WRITE'
                with self.journal.store.transaction() as db:
                    db.execute("UPDATE pc_orders SET state='CLOSED' WHERE reference=?",(reference,))
            else: record["status"] = "RECONCILIATION_REQUIRED"
        self.journal.store.set_meta(operation_key, record)
        return {"status":record["status"], "reference":reference, "filled":False, "broker_write_attempted":True}

    def reconcile(self, key):
        operation_key = "prepared-order-"+identity({"operation":key})
        record = self.journal.store.meta(operation_key)
        if not record or record["status"] == "ABORTED_BEFORE_WRITE":
            return {"status":"NO_BROKER_SUBMISSION", "broker_writes":False}
        ref = record["reference"]
        retrieved = False
        try:
            response = self.broker.get_order_status_by_reference(segment="FNO", order_reference_id=ref, timeout=5)
            broker_id = response["groww_order_id"]
            row = self.broker.get_order_detail(segment="FNO", groww_order_id=broker_id, timeout=5)
            retrieved = True
            order = record["order"]
            if (row.get("order_reference_id") != ref or row.get("groww_order_id") != broker_id
                    or row.get("segment") != "FNO" or row.get("validity") != "DAY"
                    or any(row.get(k) != v for k, v in order.items())
                    or (record.get("broker_id") and record["broker_id"] != broker_id)):
                raise ValueError("BROKER_ORDER_CHANGED")
            filled = integer(row.get("filled_quantity"), 0, order["quantity"])
            average = number(row.get("average_fill_price")) if filled else None
            if filled and average <= 0:
                raise ValueError("ACTUAL_FILL_PRICE_REQUIRED")
            status = row.get("order_status")
            if status not in TERMINAL | {"OPEN", "TRIGGER_PENDING", "PENDING"}:
                raise ValueError("UNKNOWN_ORDER_STATUS")
            if status == "EXECUTED" and filled != order["quantity"]:
                raise ValueError("EXECUTED_QUANTITY_MISMATCH")
            self.journal.acknowledge(ref, broker_id)
            with self.journal.store.transaction() as db:
                old = db.execute("SELECT filled FROM pc_orders WHERE reference=?", (ref,)).fetchone()[0]
                if filled < old:
                    raise ValueError("FILLS_CANNOT_DECREASE")
                db.execute("UPDATE pc_orders SET filled=? WHERE reference=?", (filled, ref))
            record.update(status="FULLY_FILLED" if filled == order["quantity"] and status in TERMINAL else
                          "TERMINAL_PARTIAL" if filled and status in TERMINAL else
                          "TERMINAL_EMPTY" if status in TERMINAL else "PARTIAL" if filled else "PENDING",
                          broker_id=broker_id, filled_quantity=filled, average_fill_price=average, broker_status=status,
                          checked_at=self.clock().isoformat())
            self.journal.store.set_meta(operation_key, record)
            return {"status":record["status"], "reference":ref, "filled_quantity":filled,
                    "average_fill_price":average, "broker_writes":False}
        except Exception:
            # Groww's order list is day-scoped. A previously verified terminal
            # order cannot accept new fills or be modified after that day. Keep
            # its exact historical fill proof; current exclusive net positions
            # are verified independently before every management action.
            try:
                if (not retrieved and record.get('status') in ('FULLY_FILLED','TERMINAL_PARTIAL','TERMINAL_EMPTY')
                        and record.get('broker_status') in TERMINAL and record.get('broker_id')
                        and stamp(record['checked_at']).astimezone(policy.JST).date()<self.clock().astimezone(policy.JST).date()):
                    return {"status":record['status'],"reference":ref,"filled_quantity":record.get('filled_quantity',0),
                            "average_fill_price":record.get('average_fill_price'),"broker_writes":False,"historical_terminal_proof":True}
            except (KeyError,ValueError,TypeError): pass
            # Missing status or a changed contract does not mean the order failed.
            return {"status":"RECONCILIATION_REQUIRED", "reference":ref, "broker_writes":False}

    def cancel(self, key, observation):
        result = self.reconcile(key)
        if result["status"] not in ("PENDING", "PARTIAL"):
            return result
        operation_key = "prepared-order-"+identity({"operation":key})
        record = self.journal.store.meta(operation_key)
        self._gate(observation)
        if self.journal.store.read("SELECT symbol FROM pc_protected WHERE symbol=?", (record["order"]["trading_symbol"],)):
            raise ValueError("MANUAL_OR_MIXED_CONTRACT_PROTECTED")
        with self.journal.store.transaction() as db:
            latest = json.loads(db.execute("SELECT body FROM meta WHERE key=?", (operation_key,)).fetchone()[0])
            if latest.get("cancel_attempted"):
                return {"status":"CANCEL_RECONCILIATION_REQUIRED", "broker_writes":False}
            latest["cancel_attempted"] = True
            db.execute("UPDATE meta SET body=? WHERE key=?", (dumps(latest), operation_key))
        try:
            self._gate(observation)
            self.broker.cancel_order(segment="FNO", groww_order_id=record["broker_id"], timeout=5)
        except Exception as exc:
            from .execution_gate import ExecutionDenied
            if isinstance(exc,ExecutionDenied):
                latest['cancel_attempted']=False
                self.journal.store.set_meta(operation_key,latest)
                raise
        # Cancellation acceptance is not a terminal order; fills can race it.
        return self.reconcile(key)


def next_entry_step(hedge, short, protection, *, complete, hedge_position, short_position, same_contract_conflict=False):
    """Entry recovery decisions. Caller must use exact receipts, not LTP/intent.

    No short is submitted until a fully filled hedge is reconciled to the net
    position. No further entry while cancellation/fill/protection is uncertain.
    Partial short fills require cancellation reconciliation and exact protection.
    """
    if not complete or same_contract_conflict:
        return "WAIT_FOR_EXCLUSIVE_COMPLETE_POSITIONS"
    if hedge is None:
        return "SUBMIT_BOUGHT_HEDGE"
    if hedge.get("status") != "FULLY_FILLED":
        return "CANCEL_AND_RECONCILE_HEDGE" if hedge.get("status") == "PARTIAL" else "WAIT_FOR_HEDGE_RECONCILIATION"
    qty = hedge.get("filled_quantity")
    if type(qty) is not int or qty <= 0 or hedge_position != qty:
        return "WAIT_FOR_EXCLUSIVE_COMPLETE_POSITIONS"
    if short is None:
        return "SUBMIT_SHORT_UP_TO_FILLED_HEDGE"
    if short.get("status") == "PARTIAL":
        return "CANCEL_AND_RECONCILE_SHORT_REMAINDER"
    if short.get("status") not in ("FULLY_FILLED", "TERMINAL_PARTIAL"):
        return "UNWIND_BOUGHT_HEDGE" if short.get("status") == "TERMINAL_EMPTY" else "WAIT_FOR_SHORT_RECONCILIATION"
    sold = short.get("filled_quantity")
    if type(sold) is not int or not 0 < sold <= qty or short_position != -sold:
        return "WAIT_FOR_EXCLUSIVE_COMPLETE_POSITIONS"
    if protection is None:
        return "CREATE_PERSISTENT_OWNED_SHORT_PROTECTION"
    if protection.get("status") != "VERIFIED_ACTIVE" or protection.get("quantity") != sold:
        return "WAIT_FOR_PROTECTION_RECONCILIATION"
    return "MONITOR_OWNED_BASKET"
