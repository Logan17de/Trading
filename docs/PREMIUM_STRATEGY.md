# Active owner policy · version 3

The October 5 premium instructions replace the old strike-offset, barrier, swing
and expiry-reversal entries. Preserve studies/history; display Everyday and
Late-session only.

| Rule | Definition |
| --- | --- |
| On/Off | Persist until explicit owner change; faults/windows block readiness without clearing On |
| Action window | 14:00 inclusive–19:00 exclusive JST, weekdays |
| Everyday | NIFTY Mon/Tue/Fri short CALL near ₹20; SENSEX Wed/Thu near ₹80 |
| Size | One owned basket, maximum two lots; actual margin determines affordability |
| Hedge | Buy equal quantity/same expiry; rank quoted maximum net expiry profit after costs within actual margin |
| Everyday expiry | Skip actual date from Groww AND current master, including holiday shifts |
| Short rollover | Strictly below ₹8: close old short first, then next listed short above ₹8 |
| Hedge rollover | Keep unless whole-basket improvement after incremental costs is strictly >₹100 |
| By 19:00 | Hold if current short premium > entry minus ₹5; otherwise review return to ₹20/₹80 |
| Stop | ₹1,000 basket-loss exit trigger; close short before hedge; not a guaranteed loss cap |
| Late-session | Actual expiry, 18:00: UP → PUT 3 listed strikes below ATM; DOWN → CALL 3 above; FLAT → no entry |
| Example | NIFTY ATM 25,000, 50-point steps, UP → 24,850 PUT; SENSEX steps 100 |
| Existing position | Exact contract/side/quantity/product match → skip; otherwise replace only verified algo basket after confirming flat |
| Ownership | Reservation + exact broker acknowledgment + reconciled fills; manual/unknown/mixed untouched |

Three strike intervals do not mean three lots/positions. Weekday routing never
proves expiry. At/after 19:00, roll proposals wait for the next window.

Late-session has no configured timed exit or fixed profit target. Its basket
stop/trail applies; 19:00 ends new-entry permissions, not an automatic close.
After expiry the journal requires two fresh complete broker-flat snapshots and
terminal order/protection evidence before marking the basket closed. It does not
invent an expiry settlement fill or settlement P&L. Live execution is paused.

October 6 clarification: fresh, exactly owned existing Everyday baskets receive
read-only reviews from market open (12:45 JST), independently of the 14:00
fresh-entry start. Premium at or below ₹8 produces a rollover review; basket
loss at ₹1,000 or the recorded trailing threshold produces an exit review first.
The dashboard exposes these reviews even while owner intent is Off or the
trading pause is present. Stale, incomplete and manual/mixed positions remain
unknown/protected. This does not authorize an order. The live controller's entry
and roll gates remain unchanged; pre-14:00 rollover execution is not enabled.

These deterministic proposals feed the implemented Oracle order controller only
after separate activation, ownership and freshness gates. Bounded preparation,
durable hedge-first entry, persistent GTT/child reconciliation, trailing updates,
short-first roll/exit and expiry replacement are implemented and replay tested;
see ORACLE_EXECUTION.md and EXECUTION_PREPARATION.md. Real broker-write/protection
validation and activation remain pending. The pause is preserved. A payoff bound is
not expected profit; no validated net-profit probability exists. The owner retired
news workers and news entry requirements on October 5.
