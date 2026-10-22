# Normal theta spread

**October 16 supersedes the historical research/GTT sections below:** Normal is
the only active strategy. Research and bulk recording/upload are retired.
New entries use ordinary DAY LIMIT orders and filled-position ordinary trailing
SL orders; exits close exact owned shorts before hedges. Read
[ORDINARY_TRAILING.md](ORDINARY_TRAILING.md) for current behavior and limitations.
The remaining older sections are retained implementation provenance.

October 7 owner requested a normal selling strategy derived from the report,
separate from the historical-IV research. The dashboard has two private choices:
**Normal theta spread** and **Research**. Both default Off; no migration enables
either or changes global Algo intent. Research never grants order permission.
The original four preferences and exact position ownership remain provenance.

## Normal policy

October 19 replaces daily-MA/ADX entry alignment with the owner's confirmed
**last 30 minutes** of completed five-minute Groww index prices. MA20/MA50 and
daily ADX are optional graph context; neither their alignment nor ADX 25 gates
new Normal entries. No fixed 100/200/300-point move is required.

The declared `intraday-price-action-v1` hypothesis uses six consecutive completed
five-minute OHLC intervals from the current exchange session. It compares their
closing-price steps from the first interval's open: at least four of six steps
must agree with the net direction, and net movement divided by total absolute
step movement must be at least 60%. Net movement must be at least 2.5 basis points
(0.025%) of the window's starting price, a small noise floor, not a profit target.
The fresh live quote must retain at least half that directional move. For example,
at an index level of 23,000 the noise floor is 5.75 points; a sustained 12-point
move can pass without crossing a daily average. These settings are declared
engineering hypotheses, not empirically optimized or backtested probabilities.

The history response must have its original receipt within 90 seconds and the
last completed interval must end no more than 390 seconds ago. Partial/future,
wrong-session, duplicate, missing or malformed intervals never become a trend.
Sideways/tiny/isolated jumps wait; missing/stale inputs remain UNKNOWN. A restart
fetches current session history into bounded RAM, at most 12 completed bars per
index, with no database tick history, Supabase upload or research campaign.
Read-only preparation refreshes at its existing approximately one-minute cadence;
the collector/executor still target five seconds. New bar confirmation can lag a
close by the preparation/network delay; do not claim instant trend prediction.
The current OHLC request/response contract follows
[Groww's historical-candles documentation](https://groww.in/trade-api/docs/python-sdk/backtesting).
That API documentation does not validate the chosen direction thresholds.

This is a new, unbacktested hypothesis for both NIFTY and SENSEX, evaluated when
fresh evidence is available. It does not guarantee a daily trade or profit.
Owner-defined weekday routing is active: **NIFTY on Monday, Tuesday and Friday; SENSEX on Wednesday and Thursday**. There is no fixed ₹20/₹80 target, expiry-only entry or automatic rollover.

| Check | Rule |
| --- | --- |
| Direction | Sustained 30-minute rise: bull put. Sustained fall: bear call. Six completed five-minute prices, declared consistency/noise checks and fresh live quote; otherwise WAIT/UNKNOWN. Daily MAs/ADX are context only. |
| Short | Out-of-the-money option with signed absolute delta .15–.25 |
| Hedge | Equal quantity, same actual expiry; lower put or higher call. Buy first and verify its fill before selling. |
| Expiry | API/current-master intersection, 14–45 calendar days remaining; no weekday assumption |
| Theta | Both long-option model theta values negative; bought theta minus sold theta positive. Same Groww provider convention. |
| Quotes | Positive two-sided quotes, sufficient displayed quantity, spread ≤10% of midpoint; dated Greeks and books required |
| Size | One owned basket; compare one/two lots using current actual balances and exact hypothetical broker margin/charges |
| Risk | Quoted expiry loss after estimated round-trip charges ≤₹1,000; hedge-only debit plus costs also ≤₹1,000 |
| Selection | Highest estimated net maximum expiry profit among bounded, margin-verified, affordable baskets, then lower margin; not a global optimum or expected-return estimate |
| Entry | Correct weekday index, 14:00 inclusive–18:30 exclusive JST, confirmed broker session and all execution gates |
| Exit | 18:44 JST initiate mandatory owned-basket unwind, with 19:00 JST target flat deadline; also profit/loss and DTE exits. Cancel/confirm protective stop before short close, short before hedge. Fills cannot be guaranteed |

Both NSE and BSE F&O regular sessions close at 15:40 IST (19:10 JST as of August 2026). The owner-specified **19:00 JST deadline is earlier than exchange close**. The system initiates short-first liquidation at 18:44 JST; it must report unsettled positions instead of asserting they are closed. Global Algo Off/paused execution can prevent order submission or exit. No new spreads begin at or after 18:30.

Held exact-owned baskets are reviewed outside the fresh-entry window while the
exchange is open. Disabling their strategy stops new entries/incomplete entry,
but retains completed-basket protection/exits while global Algo remains On.
Global Off/pause/paper deny all engine writes, including exits. Already armed
broker instructions can still act independently. No manual trade is adopted.

## Current exit and next-basket rules

The 18:44 JST rule starts the existing short-first unwind on the first eligible
fresh monitoring tick at or after that time. A clock trigger is not proof of a
fill; the existing 19:00 target remains a reconciliation/flatness target, and
unfilled orders must remain visible. New entries still stop at 18:30 JST.

Normal exits the whole owned spread when any of these applies:

- Net executable basket P&L reaches 50% of the actual entry credit after estimated
  round-trip costs. For example, a INR 1,000 net credit basis targets INR 500.
- Basket loss reaches the smaller of INR 1,000 or 1.5 times gross entry credit.
  This is a per-basket trigger, not a guaranteed fill or cumulative daily-loss cap.
- A tightened trailing stop is hit, or the broker stop has filled/partly triggered.
- Seven or fewer calendar days remain to the held contract's actual expiry.
- The scheduled 18:44 JST unwind is due, or an incomplete entry/protection failure
  requires the existing cancel/reconcile/unwind path.

Before another basket, reconcile pending entries, cancel/read back the exact SL,
buy back the remaining short, sell the remaining hedge and confirm terminal
orders/flat owned positions. Only then can the slot be reused. If Algo remains
On, it rechecks the currently scheduled index, 14:00–18:30 entry window, trend,
quotes/theta, actual basket margin/charges, cash and risk. It may select the same
strike pair again if that is still the best qualifying sampled basket; there is
no forced next-strike progression or automatic loss-based cooldown in Normal.

The old fixed NIFTY INR 8 / SENSEX INR 32 short roll and 60% premium reduction
belong to retired Everyday entries. Normal does not use them, does not keep an
old hedge while rolling a short, and does not reopen after the 18:44 time exit.
Manual/unknown trades remain protected throughout.

## One-click real Algo On (owner consent)

On the first owner-clicked **Algo Start**, the PC requests explicit real-money
confirmation. Oracle performs an authenticated **read-only** account preflight:
the exact deployed release, Groww account and F&O access, direct egress IP,
complete identified F&O positions protected as manual/unknown, terminal ordinary F&O orders, no active GTT/OCO orders,
available option BUY/SELL balances, Algo Off and both pause markers must all
qualify. The first response stores an expiring source/account-bound nonce;
the exact digest is consumed after owner consent. Privileged setup prepares
LIVE mode with Algo still Off, unlinks only the observer's pause, restarts the
observer, and verifies the fresh live readback. If restart verification fails,
the paper environment and observer pause are restored. **Only then** is the
owner's Algo On instruction sent. No order is submitted by preview/arming or
by reading the dashboard; only the live Normal executor can submit when all
trend, basket margin, price, risk, depth, weekday and session gates pass.

This owner-selected first live trade is explicitly **uncommissioned**:
the first Groww stop/child/fill path has no prior real provider proof. A separate
owner-risk-acceptance receipt binds the exact release, policy and Groww account.
It never sets `provider_verified` or counterfeits an earlier GTT observation.
If Groww rejects a protective stop or an order is partial/unknown, the durable
executor uses its existing reconciliation/short-before-hedge close handling.
There is no guaranteed ₹1,000 loss limit, stop fill, exit before 19:00 or profit.
An independent cloud outage-alert watchdog remains separately uncommissioned.

Algo Off remains a direct owner switch and does not re-arm or place orders.
The original shared trader pause is **never** removed by this flow. A change to
the immutable release, strategy policy or Groww account requires renewed
owner authorization. No maintenance task automatically enables LIVE or toggles On.

Stop triggers, limits and a theoretical expiry payoff bound do not guarantee a
maximum realized loss during gaps, leg execution or fees. Net positive theta
does not neutralize direction, gamma or volatility. The Groww API documentation
defines theta as time decay but does not specify its time/unit convention; code
uses only the relative sign and stores `groww_native`, not an INR/day forecast.

The 14–45 DTE, trend, quote-width and exit parameters are explicitly declared
implementation hypotheses, not empirically optimized recommendations. A ₹1,000
bound can exclude all listed spreads. Missing or unaffordable data stays WAIT /
NO_TRADE rather than relaxing risk or selling without a hedge.

The normal policy does **not** use the 252-session IV percentile, volatility risk
premium or strict 38-day event-free horizon. The official scheduled-event status
remains attached and visible as risk information; it is not an entry gate for
this separate hypothesis. Research's original calendar/IV policy is unchanged.
The owner's retired news collectors/gates stay retired.

## Research and integration

Bull put, bear call, iron condor and calendar remain four monitor-only studies
inside the Research group. The original private 38-calendar-day campaign keeps
collecting even when the group is deselected; selecting Research neither resets
the study nor promotes it to execution. Its 252-session historical-IV requirement
is unchanged. Research selection is a saved owner preference, not permission to
place research orders. Existing report-owned baskets retain original management.

Normal uses the existing PremiumExecutor / hedged-basket lifecycle, journal,
exact-reference order gateway, persistent protection and manual-trade exclusion.
No new runtime or public trading endpoint. A separate evidence cache prevents
normal samples from replacing report studies. Hypothetical margin calls do not
place orders. The archive allowlist includes normal evaluations/contracts and
group preferences; accounting values and secrets remain private and out of Git.

Live activation still requires actual reviewed provider-write, persistent GTT and
generated-child linkage evidence for the immutable release/policy. Synthetic
replays cannot provide it. Start saves intent; it cannot bypass these gates.
Maintenance leaves Algo Off, paper mode and `.trader-paused` intact.

## Primary references

- [OIC bear call spread](https://prd-web.optionseducation.org/strategies/all-strategies/bear-call-spread-credit-call-spread): same-expiry short call plus higher long call; bounded expiry reward/risk; time decay can be offset by the long hedge.
- [OIC bull put spread](https://prd-web.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread): short put plus lower long put; bullish limited-risk credit structure.
- [Groww live data](https://groww.in/trade-api/docs/curl/live-data): option-chain signed Greeks and independent market quotes.

OIC examples describe US equity options. Their assignment rules and lot sizes
are not transplanted into Indian index contracts; current Groww/master metadata
and actual expiry are required. Fictional SPX report performance is not used.
