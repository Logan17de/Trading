"""Reviewed Oracle integration point for owned SL updates. Not deployed or invoked on PC.

Default is disabled. The caller must provide fresh complete broker observations and
an already-owned protective SL. Never adopt, modify or close a manual order/position.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json

from .contracts import dumps, identity
from .pc_control import broker_update_guard, digest, trail_update


class OracleTrailingUpdater:
    def __init__(self, journal, broker, pause_file, *, deployed=False, clock=lambda:datetime.now(timezone.utc)):
        self.journal, self.broker = journal, broker
        self.pause_file, self.deployed = Path(pause_file), deployed
        self.clock = clock

    def update(self, rule, observation, premium, now):
        """One serialized, idempotent update; acceptance is never an execution fill."""
        params = {key:rule[key] for key in ("symbol","position_side","quantity","current_trigger",
            "current_limit","best_premium","distance","step","tick_size","limit_gap","target")}
        old = self.journal.store.meta("trailing-"+rule["sl_reference"])
        if old:
            params["current_trigger"],params["current_limit"] = old["trigger_price"],old["price"]
            params["best_premium"] = old["best_premium"]
        plan = trail_update(params,premium)
        if plan["status"] != "SL_UPDATE_PROPOSAL":
            if old and "best_premium" in plan:
                self.journal.store.set_meta("trailing-"+rule["sl_reference"],dict(old,best_premium=plan["best_premium"]))
            return plan
        command = dict(plan,entry_reference=rule["entry_reference"],sl_reference=rule["sl_reference"],
                       position_side=rule["position_side"])
        broker_update_guard(self.journal,command,observation,now,
                            paused=self.pause_file.exists(),deployed=self.deployed)
        broker_id = observation.get("sl_broker_id")
        sl = self.journal.store.read("SELECT * FROM pc_orders WHERE reference=?",(rule["sl_reference"],))[0]
        if not isinstance(broker_id,str) or digest(broker_id) != sl["broker_hash"]:
            raise ValueError("protective SL broker identity differs")
        key = "sl-update-"+identity({k:command[k] for k in ("sl_reference","trigger_price","price","quantity")})
        # Persist PENDING before any write. A timeout requires reconciliation, not resubmission.
        with self.journal.store.transaction() as db:
            pending = db.execute("SELECT body FROM meta WHERE key=?",(key,)).fetchone()
            if pending:
                return {"status":"DUPLICATE_OR_RECONCILIATION_REQUIRED","broker_write":False}
            other = db.execute("SELECT body FROM meta WHERE key='active-sl-update'").fetchone()
            if other and json.loads(other[0])["status"] != "CONFIRMED":
                return {"status":"PREVIOUS_UPDATE_RECONCILIATION_REQUIRED","broker_write":False}
            db.execute("INSERT INTO meta(key,body) VALUES(?,?)",(key,dumps({"status":"PENDING"})))
            db.execute("INSERT OR REPLACE INTO meta(key,body) VALUES('active-sl-update',?)",
                       (dumps({"status":"PENDING","key":key}),))
        write_attempted = False
        try:
            details = self.broker.get_order_detail(segment="FNO",groww_order_id=broker_id,timeout=5)
            self._verify(details,rule,sl,trigger=params["current_trigger"],limit=params["current_limit"])
            # Broker reads can consume the last seconds of the action window.
            # Recheck pause, freshness and the real clock immediately before writing.
            broker_update_guard(self.journal,command,observation,self.clock(),
                                paused=self.pause_file.exists(),deployed=self.deployed)
            write_attempted = True
            response = self.broker.modify_order(order_type="SL",segment="FNO",groww_order_id=broker_id,
                quantity=plan["quantity"],price=plan["price"],trigger_price=plan["trigger_price"],timeout=5)
            accepted = response.get("groww_order_id") == broker_id and response.get("order_status") in ("OPEN","TRIGGER_PENDING")
            if not accepted:
                raise ValueError("broker acceptance not confirmed")
            after = self.broker.get_order_detail(segment="FNO",groww_order_id=broker_id,timeout=5)
            self._verify(after,rule,sl,trigger=plan["trigger_price"],limit=plan["price"])
            self.journal.store.set_meta("trailing-"+rule["sl_reference"],
                {"trigger_price":plan["trigger_price"],"price":plan["price"],"best_premium":plan["best_premium"]})
            self.journal.store.set_meta(key,{"status":"CONFIRMED","at":now.isoformat()})
            self.journal.store.set_meta("active-sl-update",{"status":"CONFIRMED","key":key})
            return dict(plan,status="BROKER_SL_UPDATE_CONFIRMED",broker_write=True,provider_accepted=True,filled=False)
        except Exception:
            self.journal.store.set_meta(key,{"status":"RECONCILIATION_REQUIRED","at":now.isoformat()})
            self.journal.store.set_meta("active-sl-update",{"status":"RECONCILIATION_REQUIRED","key":key})
            return {"status":"BROKER_SL_RECONCILIATION_REQUIRED","broker_write_may_have_occurred":write_attempted,"filled":False}

    @staticmethod
    def _verify(row,rule,sl,*,trigger,limit):
        if (row.get("groww_order_id") is None or digest(row["groww_order_id"]) != sl["broker_hash"]
                or row.get("order_reference_id") != rule["sl_reference"]
                or row.get("trading_symbol") != rule["symbol"] or row.get("segment") != "FNO"
                or row.get("transaction_type") == rule["position_side"]
                or row.get("transaction_type") not in ("BUY","SELL") or row.get("order_type") != "SL"
                or row.get("quantity") != rule["quantity"] or row.get("filled_quantity") != 0
                or row.get("order_status") not in ("OPEN","TRIGGER_PENDING")
                or row.get("trigger_price") != trigger or row.get("price") != limit):
            raise ValueError("broker protective order changed or is unverified")
