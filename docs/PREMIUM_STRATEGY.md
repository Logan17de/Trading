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
| Stop | ₹2,000 basket-loss exit trigger; close short before hedge; not a guaranteed loss cap |
| Late-session | Actual expiry, 18:00: UP → PUT 3 listed strikes below ATM; DOWN → CALL 3 above; FLAT → no entry |
| Example | NIFTY ATM 25,000, 50-point steps, UP → 24,850 PUT; SENSEX steps 100 |
| Existing position | Exact contract/side/quantity/product match → skip; otherwise replace only verified algo basket after confirming flat |
| Ownership | Reservation + exact broker acknowledgment + reconciled fills; manual/unknown/mixed untouched |

Three strike intervals do not mean three lots/positions. Weekday routing never
proves expiry. At/after 19:00, roll proposals wait for the next window.

These are deterministic **review proposals**, not broker orders. Bounded actual
book/margin preparation and a replay-only durable order gateway are implemented;
see EXECUTION_PREPARATION.md. Persistent protective-order/child-fill integration,
roll/exit orchestration and real execution remain unfinished. A payoff bound is
not expected profit; no validated net-profit probability exists. The owner retired
news workers and news entry requirements on October 5.
