"""Private, read-only index-option P&L. Separate from reviewed net accounting.

Groww realised_pnl plus signed open quantity * (LTP - net_price), before
charges. Carry rows require a verified day basis before claiming today's P&L.
Only numeric summaries reach the loopback browser; never broker identities.
"""
from datetime import datetime, timezone

from .contracts import EXCHANGES, IST, dumps, stamp
from .market_check import finite
from .pc_control import SYMBOL

BUCKETS = ("self", "algo", "unassigned")
AMOUNTS = ("realized_inr", "unrealized_inr", "total_inr")


def empty(status="UNAVAILABLE", at=None):
    return {"status":status, "received_at":at, "observed_at":at, "scope":"INDEX_OPTIONS",
        "basis":"GROWW_GROSS_BEFORE_CHARGES", "closed_contracts":None,
        **{k:None for k in AMOUNTS},
        "buckets":{b:{**{k:None for k in AMOUNTS}, "contracts":None} for b in BUCKETS}}


def summarize(positions, options, owners, *, now, received_at, complete):
    result = empty(at=received_at)
    try:
        at = stamp(received_at)
        if not complete or not 0 <= (now-at).total_seconds() <= 15 or at.astimezone(IST).date() != now.astimezone(IST).date():
            return result, {}
    except (ValueError, TypeError):
        return result, {}
    buckets = {b:dict(realized_inr=0, unrealized_inr=0, total_inr=0, contracts=0) for b in BUCKETS}
    quotes = {(o["symbol"],o["side"]):o for o in options}
    legs, seen, closed = {}, set(), 0
    for row in positions:
        if not isinstance(row, dict):
            return empty("INCOMPLETE",received_at), {}
        symbol, exchange, product = row.get("trading_symbol"), row.get("exchange"), row.get("product")
        index = next((i for i in EXCHANGES if isinstance(symbol,str) and symbol.startswith(i) and symbol[len(i):len(i)+1].isdigit()), None)
        if (row.get("segment") != "FNO" or index is None or not SYMBOL.fullmatch(symbol)
                or exchange != EXCHANGES[index] or product not in ("MIS","NRML")):
            return empty("UNSUPPORTED_CONTRACT",received_at), {}
        key = symbol, exchange, product
        if key in seen:
            return empty("INCOMPLETE",received_at), {}
        seen.add(key)
        quantity, realized = finite(row.get("quantity")), finite(row.get("realised_pnl"))
        buys, sells = finite(row.get("credit_quantity")), finite(row.get("debit_quantity"))
        # The API supplies no verified previous-day mark in the position response.
        # Do not count an overnight lifetime return as today's movement.
        carry = [finite(row.get(k)) for k in ("net_carry_forward_quantity",
            "carry_forward_credit_quantity", "carry_forward_debit_quantity")]
        if any(v is None or v != 0 for v in carry):
            return empty("CARRY_DAY_BASIS_UNVERIFIED",received_at), {}
        if (quantity is None or realized is None or buys is None or sells is None
                or buys < 0 or sells < 0 or buys-sells != quantity or quantity != int(quantity)):
            return empty("INCOMPLETE",received_at), {}
        unrealized = 0
        if quantity:
            side = "BUY" if quantity > 0 else "SELL"
            option = quotes.get((symbol,side),{})
            price, entry = finite(option.get("quote",{}).get("last_price")), finite(row.get("net_price"))
            try:
                quoted_at = stamp(option.get("received_at"))
                fresh = 0 <= (now-quoted_at).total_seconds() <= 15 and quoted_at.astimezone(IST).date() == now.astimezone(IST).date()
            except (ValueError,TypeError):
                fresh = False
            if price is None or price < 0 or entry is None or entry <= 0 or not fresh:
                return empty("QUOTE_UNAVAILABLE",received_at), {}
            unrealized = quantity*(price-entry)
            leg_key = symbol, side
            legs[leg_key] = legs.get(leg_key,0)+unrealized
        elif buys or sells:
            closed += 1
        bucket = owners.get(symbol,"unassigned")
        bucket = bucket if bucket in BUCKETS else "unassigned"
        b = buckets[bucket]
        b["realized_inr"] += realized
        b["unrealized_inr"] += unrealized
        b["contracts"] += 1
    for b in buckets.values():
        b["total_inr"] = b["realized_inr"]+b["unrealized_inr"]
        for k in AMOUNTS:
            b[k] = round(b[k],2)
    result.update(status="AVAILABLE",observed_at=now.isoformat(),closed_contracts=closed,buckets=buckets,
        **{k:round(sum(b[k] for b in buckets.values()),2) for k in AMOUNTS})
    return result, {k:round(v,2) for k,v in legs.items()}


def public(value, now):
    """Revalidate the saved snapshot at response time; stale totals stay unknown."""
    if not isinstance(value,dict):
        return empty()
    at = value.get("received_at")
    try:
        parsed = stamp(at)
        if not 0 <= (now-parsed).total_seconds() <= 15 or parsed.astimezone(IST).date() != now.astimezone(IST).date():
            return empty("STALE",at)
    except (ValueError,TypeError):
        return empty()
    if value.get("status") != "AVAILABLE":
        status = value.get("status")
        return empty(status if status in ("INCOMPLETE","UNSUPPORTED_CONTRACT","CARRY_DAY_BASIS_UNVERIFIED","QUOTE_UNAVAILABLE") else "UNAVAILABLE",at)
    result = empty("AVAILABLE",at)
    try:
        observed = stamp(value.get("observed_at"))
        if not parsed <= observed <= now:
            return empty("INCOMPLETE",at)
        result["observed_at"] = observed.isoformat()
    except (ValueError,TypeError):
        return empty("INCOMPLETE",at)
    buckets = value.get("buckets",{})
    for b in BUCKETS:
        source = buckets.get(b,{}) if isinstance(buckets,dict) else {}
        if not isinstance(source,dict) or any(finite(source.get(k)) is None for k in AMOUNTS) or type(source.get("contracts")) is not int or source["contracts"] < 0:
            return empty("INCOMPLETE",at)
        result["buckets"][b] = {k:source[k] for k in (*AMOUNTS,"contracts")}
        if abs(source["total_inr"]-source["realized_inr"]-source["unrealized_inr"]) > .011:
            return empty("INCOMPLETE",at)
    for k in AMOUNTS:
        total = finite(value.get(k))
        if total is None or abs(total-sum(result["buckets"][b][k] for b in BUCKETS)) > .011:
            return empty("INCOMPLETE",at)
        result[k] = total
    count = value.get("closed_contracts")
    result["closed_contracts"] = count if type(count) is int and count >= 0 else None
    return result


class PnlJournal:
    """Actual observed totals, with null gaps, persisted in the existing SQLite DB."""
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS pc_pnl_observations(at REAL PRIMARY KEY,day TEXT NOT NULL,body TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS pc_pnl_day ON pc_pnl_observations(day,at)")

    def record(self, value, now):
        value = public(value,now)
        if not value["received_at"]:
            return
        at = stamp(value["observed_at"])
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO pc_pnl_observations VALUES(?,?,?)",
                (at.timestamp(),at.astimezone(IST).date().isoformat(),dumps(value)))

    def series(self, now):
        import json
        rows = self.store.read("SELECT at,body FROM pc_pnl_observations WHERE day=? AND at<=? ORDER BY at DESC LIMIT 6000",
            (now.astimezone(IST).date().isoformat(),now.timestamp()))
        result = []
        for row in reversed(rows):
            try:
                at = datetime.fromtimestamp(row["at"],timezone.utc)
                value = public(json.loads(row["body"]),at)
                result.append({"at":at.isoformat(),**{b:value["buckets"][b]["total_inr"] for b in BUCKETS}})
            except (ValueError,TypeError,KeyError):
                continue
        return result
