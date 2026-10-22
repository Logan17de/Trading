# Normal Theta: trend-first automatic basket selection

The automated Normal Theta engine does **not** depend on the desktop manual BUY/SELL/SL test.
The real order route is the existing Oracle PremiumExecutor -> NormalExecution ->
OracleOrderGateway/PersistentProtection, with durable reference IDs, broker fill
reconciliation and exclusive ownership. It is not an atomic basket-order API.

## Pre-trade selection (no broker order writes)

1. Gather the current Groww index spot, historical 20/50-session moving averages,
   ADX14, signed delta/theta, dated option master/expiry intersection, executable
   bid/ask/depth, account buying/selling balances, and protected positions.
2. Confirm trend **before** fetching direction-specific option quotes:
   * bullish, spot > MA20 > MA50 and ADX >= 25: PE credit vertical only
   * bearish, spot < MA20 < MA50 and ADX >= 25: CE credit vertical only
   * otherwise: no new candidate and no Normal basket margin calls
3. For the first eligible 14-45 DTE expiry, sample up to three short candidates
   per confirmed side and three nearby same-expiry bought wings each, with a
   bounded budget of 14 independent option quote requests.
4. Enumerate one-/two-lot, equal-quantity hedged pairs with delta, positive net
   *model* theta, spread width, quote depth and <= INR 1,000 modeled worst-case
   expiry loss prefilters. No synthetic bid/ask or market depth.
5. Rank plausible baskets by gross expiry credit for a maximum of 12 Groww
   read-only FNO basket-margin comparisons. Each candidate requires the
   documented combined-basket, hedge-only, and closing-cost estimates via
   get_order_margin_details. Compare actual buying/selling available balances,
   net credit after estimated commissions, expected expiry maximum loss and
   hedge-only upfront debit.
6. Among affordable, verified candidates, select **highest net potential maximum
   expiry profit** (with lower margin and lower worst-case loss breaking ties).
   Compare candidates across supported indices. This is not a probability,
   guaranteed return or expected-value estimator.
7. Immediately before entry, the executor must independently revalidate fresh
   broker books, expiry, account funds, margin, current strategy switch and
   exclusive position state. Stale quotes never gain a refreshed timestamp.

The bounded search is **not** an exhaustive global chain scan. The public
`normal_theta.indices[index].basket_scan` status reports number of scanned
quotes, margin checks and affordable baskets and marks the search non-exhaustive.
Absent market data, margin, validated trend, available funds or affordable defined
risk means **no trade**, not a fallback guess.

## Execution after owner authorization

The existing engine buys the long wing first. It waits for a verified full fill
and owned-position readback before submitting the short leg; then it attaches
and verifies the protective BUY-UP GTT stop. If the short cannot be entered,
it unwinds the hedge with durable child/order reconciliation. Monitoring tracks
net spread P&L, profit target, loss trigger and 7-DTE close. Position ownership
and original stop cancellation are checked before exits. A ₹1,000 stop is not
guaranteed to cap realized losses.

The standard Algo Start intent now auto-selects Normal Theta as the *only*
executable strategy. Research remains non-trading. This selection alone neither
switches Oracle from paper to live mode nor clears the pause or creates an
activation receipt. Those are separate existing owner release controls.
Never claim a synthetic broker test proves real fills or stop behavior.

## Validation

Use `tests/test_normal_theta.py`, `tests/test_report_data.py`,
`tests/test_execution_input_timing.py`, and existing gateway/protection tests
with a fake Groww broker. Do not send actual orders during maintenance.
