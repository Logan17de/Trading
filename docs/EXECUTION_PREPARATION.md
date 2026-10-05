# Oracle execution preparation

October 5, 2026. The owner retired news and requested Everyday/Late-session only.
News workers and news readiness gates are removed from the premium runtime,
desktop and visual email. Historical research and evidence remain provenance.

## Implemented

- Existing policy: NIFTY Mon/Tue/Fri short call near 20; SENSEX Wed/Thu near 80.
  Maximum two whole lots in one journal-owned basket; actual current master lot
  and tick sizes. Equal bought same-expiry hedge. Exact actual expiry determines
  Everyday's skip and Late-session's eligibility, including holiday shifts.
- Independent read-only preparation every minute in Oracle's existing service.
  Reads the chain, current master and bid/ask/depth, then requests hypothetical
  basket margin, bought-hedge-only margin and round-trip charge calculations.
  Uses actual option-buy/sell available balances, never investment or clear cash
  as an available-margin assumption. Missing costs/margin/books block ranking.
- Preparation samples one chain-target call plus four nearest hedge contracts.
  Ranks one/two-lot net maximum expiry payoff within supplied broker requirements.
  It explicitly reports omitted contracts and does **not** claim a globally
  optimal hedge or expected profit. A prepared comparison is never executable.
- Late-session: actual expiry after 18:00, UP -> put three listed strikes below
  ATM; DOWN -> call three above. Trend uses three consecutive completed five-minute
  observed reception-price closes, with actual quotes in each interval's final
  15 seconds. Missing closes -> UNKNOWN/wait; flat -> no entry. This definition
  is deterministic research logic, not a validated trend-profit forecast.
- Exact matching spread skips; manual/netted contracts are protected. An occupied
  owned basket prevents a separate Everyday entry. Expiry replacement remains a
  proposal requiring verified owned exits and flat confirmation first.
- Existing below-8 rollover, strict hedge improvement >100 after incremental
  costs, entry-minus-5 hold check and basket stop-priority review stay tested.
- Prepared replay gateway persists references before simulated writes, uses one
  journal slot, verifies exact acknowledgments/fills and reconciles by original
  reference after crashes/timeouts. Concurrent duplicate requests write once.
  Uncertain placement/cancellation is never blindly repeated. Partial hedge fills
  cannot start a short; partial short fills require remainder reconciliation and
  exact protection. Pause, owner Off, window/freshness and ownership gates apply.
- Stop-price calculation uses both actual fill prices, quantity, costs and tick
  size with conservative zero hedge value. This is not a guaranteed loss cap.

## Still required before real trading

The runtime still has `orders_enabled=false`, a read-only HTTP allowlist, owner
Off and the pause marker. Its button saves intent; it cannot activate trading.
PreparedOrderGateway rejects every real broker transport by construction.

Persistent Groww protection must be integrated and verified through actual broker
acknowledgments/readback, including generated child-order ownership, stop fills,
overnight carry, partial fills and rollback/exit/restart recovery. A DAY stop is
insufficient for an overnight position. Strategy replacement/roll orchestration
and live execution replay/session gates are not complete. No real orders may be
used to pretend those gates passed; activation requires a concrete reviewed
rollout and explicit owner approval. Do not delete the pause to test readiness.

## Sources

The pinned SDK is growwapi 1.5.0. The hypothetical margin operation is a POST to
`/v1/margins/detail/orders`; this calculation cannot place an order. Order create,
modify, cancel and smart-order write routes remain denied by the observer guard.

[Groww Orders](https://groww.in/trade-api/docs/python-sdk/orders),
[Margin](https://groww.in/trade-api/docs/python-sdk/margin),
[Smart Orders](https://groww.in/trade-api/docs/python-sdk/smart-orders),
[Live Data](https://groww.in/trade-api/docs/python-sdk/live-data).

Tests use synthetic values and injected simulated brokers; private account values,
orders and calculation evidence stay in the existing private journal.
