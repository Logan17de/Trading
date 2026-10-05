"""Owner-declared capital accounting. No bank transfers or broker-side effects."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, timezone, datetime
from decimal import Decimal, DecimalException
from pathlib import Path

from .contracts import dumps, keys
from .pc_control import JST

KEY = "capital-ledger-v1"
MAX_PAISE = 10**12


def paise(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 64:
        raise ValueError("INVALID_AMOUNT")
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount != amount.to_integral_value() or not 0 <= amount <= MAX_PAISE:
            raise ValueError("INVALID_AMOUNT")
        if positive and amount == 0:
            raise ValueError("POSITIVE_AMOUNT_REQUIRED")
        return int(amount)
    except DecimalException:
        raise ValueError("INVALID_AMOUNT") from None


def month_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("INVALID_FEE_MONTH")
    try:
        day = date.fromisoformat(value + "-01")
    except ValueError:
        raise ValueError("INVALID_FEE_MONTH") from None
    if day.year < 2000:
        raise ValueError("INVALID_FEE_MONTH")
    return day.year * 12 + day.month - 1


def withdrawal(value, now):
    keys(value, {"id", "amount_inr", "effective_date"})
    if not isinstance(value["id"], str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", value["id"]):
        raise ValueError("INVALID_RECORD_ID")
    try:
        day = date.fromisoformat(value["effective_date"])
    except (TypeError, ValueError):
        raise ValueError("INVALID_WITHDRAWAL_DATE") from None
    if day.isoformat() != value["effective_date"] or day > now.astimezone(JST).date():
        raise ValueError("FUTURE_WITHDRAWAL_DATE")
    return {"id": value["id"], "amount_paise": paise(value["amount_inr"], positive=True),
            "effective_date": day.isoformat()}


def empty(status="NOT_CONFIGURED"):
    return {"status": status, "source": "OWNER_DECLARED", "invested_inr": None,
            "withdrawn_inr": None, "api_fees_inr": None, "remaining_capital_inr": None,
            "monthly_api_fee_inr": None, "first_fee_month": None, "fees_through_month": None,
            "accounting_day_jst": None, "updated_at": None}


class CapitalLedger:
    def __init__(self, store):
        self.store = store

    def initialize(self, invested_inr, monthly_api_fee_inr, first_fee_month, now):
        """One-time private provisioning; retries cannot reset withdrawals or fees."""
        config = {"format": KEY, "invested_paise": paise(invested_inr, positive=True),
                  "monthly_fee_paise": paise(monthly_api_fee_inr, positive=True),
                  "first_fee_month": first_fee_month}
        month_number(first_fee_month)
        with self.store.transaction() as db:
            old = db.execute("SELECT body FROM meta WHERE key=?", (KEY,)).fetchone()
            if old:
                value = json.loads(old[0])
                if any(value.get(k) != v for k, v in config.items()):
                    raise ValueError("CAPITAL_ALREADY_CONFIGURED")
            else:
                value = dict(config, seeded_at=now.isoformat(), withdrawals=[], api_fees={})
                db.execute("INSERT INTO meta(key,body) VALUES(?,?)", (KEY, dumps(value)))
        return self.summary(now)

    @staticmethod
    def _validate(value):
        keys(value, {"format", "invested_paise", "monthly_fee_paise", "first_fee_month",
                     "seeded_at", "withdrawals", "api_fees"})
        if value["format"] != KEY:
            raise ValueError("INVALID_CAPITAL_LEDGER")
        for k in ("invested_paise", "monthly_fee_paise"):
            if type(value[k]) is not int or not 0 < value[k] <= MAX_PAISE:
                raise ValueError("INVALID_CAPITAL_LEDGER")
        month_number(value["first_fee_month"])
        if not isinstance(value["withdrawals"], list) or len(value["withdrawals"]) > 10000:
            raise ValueError("INVALID_CAPITAL_LEDGER")
        ids = set()
        for row in value["withdrawals"]:
            keys(row, {"id", "amount_paise", "effective_date", "recorded_at"})
            if (not isinstance(row["id"], str) or row["id"] in ids or
                type(row["amount_paise"]) is not int or not 0 < row["amount_paise"] <= MAX_PAISE):
                raise ValueError("INVALID_CAPITAL_LEDGER")
            date.fromisoformat(row["effective_date"])
            ids.add(row["id"])
        if not isinstance(value["api_fees"], dict) or len(value["api_fees"]) > 3600:
            raise ValueError("INVALID_CAPITAL_LEDGER")
        for month, amount in value["api_fees"].items():
            if (month_number(month) < month_number(value["first_fee_month"]) or
                type(amount) is not int or amount != value["monthly_fee_paise"]):
                raise ValueError("INVALID_CAPITAL_LEDGER")

    def accrue(self, now):
        """Book each due JST calendar month exactly once, including downtime."""
        value = self.store.meta(KEY)
        if value is None:
            return None
        self._validate(value)
        current = now.astimezone(JST).strftime("%Y-%m")
        first, last = month_number(value["first_fee_month"]), month_number(current)
        if last - first > 3599:
            raise ValueError("INVALID_CAPITAL_LEDGER")
        due = [f"{n // 12:04d}-{n % 12 + 1:02d}" for n in range(first, last + 1)]
        if all(month in value["api_fees"] for month in due):
            return value
        with self.store.transaction() as db:
            value = json.loads(db.execute("SELECT body FROM meta WHERE key=?", (KEY,)).fetchone()[0])
            self._validate(value)
            for month in due:
                value["api_fees"].setdefault(month, value["monthly_fee_paise"])
            db.execute("UPDATE meta SET body=? WHERE key=?", (dumps(value), KEY))
        return value

    def summary(self, now):
        try:
            value = self.accrue(now)
            if value is None:
                return empty()
            today = now.astimezone(JST).date().isoformat()
            current = today[:7]
            invested = value["invested_paise"]
            withdrawn = sum(r["amount_paise"] for r in value["withdrawals"] if r["effective_date"] <= today)
            fees = sum(v for m, v in value["api_fees"].items() if m <= current)
            return {"status": "AVAILABLE", "source": "OWNER_DECLARED", "invested_inr": invested / 100,
                    "withdrawn_inr": withdrawn / 100, "api_fees_inr": fees / 100,
                    "remaining_capital_inr": (invested - withdrawn - fees) / 100,
                    "monthly_api_fee_inr": value["monthly_fee_paise"] / 100,
                    "first_fee_month": value["first_fee_month"],
                    "fees_through_month": max((m for m in value["api_fees"] if m <= current), default=None),
                    "accounting_day_jst": today, "updated_at": now.isoformat()}
        except (ValueError, TypeError, KeyError):
            return empty("INVALID_LEDGER")

    def record_withdrawal(self, body, now):
        record = withdrawal(body, now)
        with self.store.transaction() as db:
            old = db.execute("SELECT body FROM meta WHERE key=?", (KEY,)).fetchone()
            if old is None:
                raise ValueError("CAPITAL_NOT_CONFIGURED")
            value = json.loads(old[0])
            self._validate(value)
            existing = next((r for r in value["withdrawals"] if r["id"] == record["id"]), None)
            if existing:
                if any(existing[k] != v for k, v in record.items()):
                    raise ValueError("WITHDRAWAL_ID_CONFLICT")
                status = "ALREADY_RECORDED"
            else:
                if len(value["withdrawals"]) >= 10000:
                    raise ValueError("WITHDRAWAL_LIMIT")
                value["withdrawals"].append(dict(record, recorded_at=now.isoformat()))
                db.execute("UPDATE meta SET body=? WHERE key=?", (dumps(value), KEY))
                status = "RECORDED"
        return {"status": status, "record_id": record["id"], "capital_summary": self.summary(now),
                "broker_writes": False, "money_moved": False}


def revalidate(summary, now, *, cached=False):
    """A cached prior-month balance must not omit a newly due monthly fee."""
    if not summary or summary.get("status") not in ("AVAILABLE", "CACHED", "FEE_UPDATE_PENDING"):
        return summary or empty()
    if (summary.get("accounting_day_jst") or "")[:7] != now.astimezone(JST).strftime("%Y-%m"):
        summary.update(status="FEE_UPDATE_PENDING", api_fees_inr=None, remaining_capital_inr=None)
    elif cached:
        summary["status"] = "CACHED"
    return summary


def main():
    """Provision approved amounts from private stdin, never tracked config."""
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file():
        raise ValueError("EXISTING_JOURNAL_REQUIRED")
    raw = sys.stdin.read(1025)
    if len(raw) > 1024:
        raise ValueError("BOUNDED_INPUT_REQUIRED")
    config = json.loads(raw)
    keys(config, {"invested_inr", "monthly_api_fee_inr", "first_fee_month"})
    from .store import Store
    result = CapitalLedger(Store(args.database)).initialize(**config, now=datetime.now(timezone.utc))
    if result["status"] != "AVAILABLE":
        raise ValueError("INVALID_CAPITAL_LEDGER")
    print(dumps({"status": "CAPITAL_CONFIGURED", "money_moved": False, "broker_writes": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"status":"CAPITAL_PROVISIONING_FAILED","money_moved":false,"broker_writes":false}')
        raise SystemExit(2) from None
