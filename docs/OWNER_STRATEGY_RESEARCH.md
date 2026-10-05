# Owner strategies: local observation and evidence

Reviewed 2026-10-05. Use local market observation and research, with Oracle intended
as the later execution location. Evaluate the strategies before use. These commands
have no order-submission capability. Oracle stays running continuously and the
trading pause remains in force. See [current integration status](HEADLESS_INTEGRATION_STATUS.md)
for deployment, completed checks and remaining work.

## Latest PC rule, October 5

The owner's latest clarification overrides the earlier everyday hedge study:
**one active slot, one lot, buy a same-expiry call near the index ATM, up to three
listed strikes away**. No budget-based hedge ranking is required for this revised
rule. The short still starts at/after 13:15 JST: NIFTY spot +400 on Mon/Tue/Fri;
SENSEX spot +800 on Wed/Thu. A listed call strike must be at/above the offset.

**Skip actual underlying expiry day**, including a date shifted by a holiday.
The PC collector cross-checks Groww's expiry API against current listed contract
metadata; missing/disagreeing evidence blocks entry. Codex receives that
deterministic result and cannot invent an expiry date. The weekday preference is
index routing, not an expiry calendar. A specific trading expiry is still unselected.
[Groww expiry API](https://groww.in/trade-api/docs/python-sdk/backtesting)

An ATM long call below the short call normally creates a **bull call debit spread**.
Its payoff benefits from a rise, conflicting with the previously recorded rule
that flat/down favors holding and up is adverse. The app records this conflict as
`ATM_HEDGE_PAYOFF_REVIEW_REQUIRED`; it does not silently change either instruction
or activate entries. This is a payoff inference from the specified legs, not a
claim about future market direction.
[OIC bull call spread](https://www.optionseducation.org/strategies/all-strategies/bull-call-spread-debit-call-spread)

Background support/resistance research begins at 12:55 JST. Barrier proposals are
valid only from 13:00 inclusive to 18:15 exclusive. This new action cutoff does not
activate the separate later-session/expiry hypotheses outside it. Stops use option
premium/tick rules, not index-barrier values. Trailing distance, step and target
are unset, and Oracle entry/protective-order execution is undeployed. Manual trades
are persistently protected. No net-profit probability has been validated.

`config/pc_app.example.json` records the current PC overlay. The version 1/2
`owner_strategies.json`, offline references and higher-call hedge comparisons below
are retained for historical reproducibility; they do not choose the new ATM hedge.

## Recorded research rules, through October 1

There are four strategy hypotheses. All owner times use Japan time, which is
3 hours 30 minutes ahead of India.

| Idea | Japan time | India time | Research definition |
|---|---|---|---|
| Everyday hedged call | At/after 13:15 | At/after 09:45 | NIFTY on Monday, Tuesday and Friday: short call at least spot +400. SENSEX on Wednesday and Thursday: at least spot +800. Buy a higher-strike call with the same expiry and quantity. |
| Late-session premium decay | 17:45–18:45 | 14:15–15:15 | Compare hedged calls; INR 200–500 is a target to test, not assumed daily income. |
| Swing | 18:45 entry; next session 13:00 exit | 15:15 entry; next session 09:30 exit | Research choice of one overnight hold and 10/20 **listed strike intervals** between short and hedge. |
| Expiry jump/reversal | 18:50–19:00 signal window | 15:20–15:30 | SENSEX expiry only; first observed upward jump of at least 500 points relative to 18:45. |
| Reversal outcome | 19:06 | 15:36 | Last completed minute within ±200 points of the pre-auction baseline. This is not an official settlement price. |

Call spreads are the owner's preference. “10–20 places” was left to research
judgment: the two hedge distances and next-session exit above are explicit research
choices, not a claim about what the owner originally meant. No averaging into a
losing short position or automatic sizing is implemented.

`config/owner_strategies.json` uses version 2 to add the everyday hypothesis. The
three original rules remain unchanged, and version 1 stays supported for historical
reproduction. The protocol hash is stored with each result. The last 30% of observed
dates form a chronological holdout. Signals use
completed candles only; a 15:20 minute candle is not available until 15:21. Missing
windows are excluded and counted. Parameters cannot silently change after seeing
the holdout. Thirty independent holdout events is only a screening floor.

## Everyday entry and holding rule

Use the actual observed index level at the intended entry time, at or after
13:15 JST. From the supplied list of actual call strikes, take the first strike
at or above spot +400 for NIFTY or spot +800 for SENSEX. A listed strike alone
does not verify an expiry, contract, quote or fill. There is no BANKNIFTY everyday
rule or automatic fallback to it. Ordinary weekend routing is excluded; holidays
and any special session need a date-specific calendar.

The owner defined favorable/adverse by **index direction relative to entry**:

- Current index equal to or below entry: `FAVORABLE` / `HOLD_REVIEW`.
- Current index above entry: `ADVERSE` / `EXIT_REVIEW`.
- Missing observation: `UNKNOWN` / `DATA_REQUIRED`.

There is no added tolerance or fixed next-day exit. A carried position is reviewed
against its original entry level, including before the entry window and when the
other index is preferred for a new position. These are review outputs; continuous
position monitoring and order submission are not connected. Index direction does
not establish the spread's net P&L.

The margin budget and expiry-selection rule remain unset. The owner requested no
maximum-loss cap, so `max_spread_loss_inr` is null and comparison adds no loss-limit
gate. Calculated payoff losses are still displayed. The weekday preference does
not certify an expiry day or permit holding an expiring contract into the next
session; the exact expiry and settlement must be checked for the proposed contract.

On October 1, NSE's current permitted-lot file listed **NIFTY 65** for October 2026
and all other displayed maturities. The file's Last-Modified header was September
29, 2026. This agrees with the owner's reference; each actual contract's lot size
still needs verification. A larger lot scales both profit and loss at the same
point payoff. The strategy never increases quantity automatically.
[NSE current lot sizes](https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv)

For the historical screen, the earliest eligible 13:15 JST reference uses the
**09:44 IST completed candle**, available at 09:45. It does not use the future
close of the 09:45 candle or assume an entry fill. Applying version 2 to the existing
41-session datasets produced 25 NIFTY and 16 SENSEX references, routed by weekday.
BANKNIFTY remains outside this everyday hypothesis. The old version 1 results
were reproduced exactly for all three datasets. No option-profit probability is
derived from these spot references.

## The closing-auction distinction

Groww's own description places the start of indicative prices at 15:20 IST and
distinguishes real-time, reference, indicative and final closing values. It says
BSE indicative index prices are displayed on Groww. The owner's observation at
18:50 JST may concern that separate indicative value. Ordinary index OHLC is not
evidence that it contains the same series. [Groww price types](https://groww.in/blog/understanding-closing-prices)

At the September 30 reference check, NSE listed the derivatives close as
**15:40 IST / 19:10 JST**.
Date-specific session and settlement rules are needed; do not reuse the retired
simulator's assumed 15:30 expiry horizon. NSE's CAS documentation identifies a
separate indicative index. [NSE trading hours](https://www.nseindia.com/static/market-data/market-timings),
[NSE CAS](https://www.nseindia.in/static/products-services/closing-auction-session)

SEBI published a further consultation in September 2026. Proposals in that paper
are not assumed to be implemented rules. This is a changing market structure, so
older and newer regimes must remain separate.
[SEBI consultation](https://www.sebi.gov.in/reports-and-statistics/reports/sep-2026/consultation-paper-on-review-of-certain-aspects-of-the-closing-auction-session-market-timings-and-settlement-methodologies-for-derivative-contracts-_104464.html)

Theta is a model sensitivity with other inputs held constant. Observed premium
changes also reflect the underlying and implied volatility. A winning hour does
not isolate theta or establish the next day's probability.
[OCC/OIC theta explanation](https://www.optionseducation.org/advancedconcepts/theta)

## What was actually measured

On 2026-09-30 the read-only API returned **17,105 one-minute index candles for each
of NIFTY, BANKNIFTY and SENSEX** over the requested 2026-08-03–2026-09-29 period:
**51,315 candles, 41 observed sessions per index**. Download requests and expiry
queries succeeded. Missing endpoint coverage remains visible; HTTP 200 does not
establish a complete session or prove timestamp semantics.

The initial SENSEX screen has eight complete expiry windows: five development and
three holdout. **Zero qualifying upward 500-point minute-close events** occurred in the returned
ordinary candle series under the frozen definition. The event denominator is zero,
so the measured reversal rate and confidence interval are **unknown**, not 0% or
90%. The unverified indicative-series identity independently prevents a strategy
probability claim. This result neither validates nor refutes the separate visual
indicative-index observation.

One-minute closes can also miss spikes that reverse within a minute. The current
screen deliberately uses completed bars; it is not a tick-level reconstruction.

Late-hour and overnight **index moves** are saved as control observations. They
are not option returns. None of the four strategies has a validated net-profit
probability or has been selected for execution. We still need historical two-leg
bid/ask/depth, exact contract/lot/expiry information, costs, slippage/fill assumptions,
and enough comparable out-of-sample sessions.

Groww documents historical OHLC/volume/OI and expired contracts; these endpoints do
not supply a historical bid/ask or indicative-index field in their documented
response. No missing fields are invented.
[Groww backtesting data](https://groww.in/trade-api/docs/python-sdk/backtesting)

The actual local option response also showed:

- top-level bid/offer fields null, with valid buy/sell levels inside `depth`;
- epoch **seconds** for `last_trade_time`, despite the millisecond documentation;
- chain delta/gamma/theta/vega/IV, without a Greek observation timestamp;
- no independent book timestamp.

The reader now preserves five depth levels, derives the best available bid/ask,
labels their source, retains timestamp units, reports crossed books, and samples
the first at/above-spot call plus calls 10 and 20 listed strikes above it. These are
fixed data samples, not selected trades. Lot size and executable freshness remain
unverified in this diagnostic. Reception time never becomes book or Greek time.

## Hedge comparison

For a call credit spread, buy the **higher-strike call** with matching underlying,
exchange, expiry and quantity. In the owner's example, 75,000 short / 77,000 long
has width 2,000 points; changing the long call to 76,800 makes it 1,800 points.

For supplied quotes and quantity Q:

- Credit = short bid − hedge ask.
- Gross maximum expiry profit = credit × Q.
- Gross maximum expiry loss = (width − credit) × Q.
- Subtract supplied costs from profit and add them to loss.

These are the expiry payoff relationships for a call credit spread.
[OCC/OIC call credit spread](https://www.optionseducation.org/strategies/all-strategies/bear-call-spread-credit-call-spread)

The expiry bound assumes both legs stay intact and settle as contracted. Partial
fills, an uncovered short, early unwinds and changing margin need separate handling.
Broker margin is a funding requirement, not maximum loss or expected profit. A
closer hedge may reduce both credit and loss exposure. With a supplied margin
budget, comparisons that fit that budget are ranked by positive net maximum expiry
profit, at the same supplied quantity. Unknown margin, costs or budget cannot
receive a rank. The tool selects no hedge and reports no profit probability.
Maximum expiry profit is not expected next-day profit, and margin is not a forecast.

## Commands

Read current market values and sampled call books:

```powershell
.\scripts\Read-GrowwMarket.ps1
```

Reproduce the read-only historical study for an index:

```powershell
.\scripts\Research-GrowwStrategies.ps1 -Index SENSEX -Start 2026-08-03 -End 2026-09-29
```

Outputs are private JSON under `.agent-state/owner-studies/`. Failed requests are
recorded; absent data is never filled forward. A new output filename is required.

Offline everyday review of an observation and, optionally, an existing entry:

```powershell
.\.venv\Scripts\python.exe -I -m nifty_engine.agent_engine.owner_study everyday `
  --input .agent-state\everyday-input.json --output .agent-state\everyday-review.json
```

The input object has `index`, `spot` (or null), and offset-aware `observed_at`.
Optional `listed_call_strikes` is a list of actual listed strikes; optional
`entry_spot` adds the holding review. Keep source times and exact contracts in the
supporting private evidence. A fresh output filename is required. This command
uses no credentials or network requests and submits no order.

Bounded capture (provide a future offset-aware end time within two hours):

```powershell
$captureUntil = [DateTimeOffset]::UtcNow.AddMinutes(60).ToString('o')
$captureDirectory = '.agent-state\recordings\capture-' + [Guid]::NewGuid().ToString('N')
.\scripts\Record-GrowwMarket.ps1 -Until $captureUntil -OutputDirectory $captureDirectory
```

This example records for one hour from invocation. For a specific Japan-time
window, supply its future ISO deadline with `+09:00`, within the two-hour bound.
Keep the PC awake and online. In-flight HTTP reads have timeouts; no new request is
started after the deadline. Create `STOP` inside the output directory to stop capture.
It is a single run, not an installed scheduled task or trading daemon.

The completed September 30 run at `.agent-state/recordings/20260930-cas` contains
29 snapshots from 18:40 to 19:08 JST: 28 successful and one partial. It misses the
first part of the 17:45–18:45 hypothesis and is not a complete-session sample.
Its final manifest was written the following morning; the cause is unverified.
See [recording evidence](HEADLESS_INTEGRATION_STATUS.md#september-30-recording).

Offline owner-specified spread comparison:

```powershell
.\.venv\Scripts\python.exe -I -m nifty_engine.agent_engine.spread_review `
  --input .agent-state\spread-input.json --output .agent-state\spread-review.json
```

Input uses `instrument`, `exchange`, `expiry`, `short_strike`, `short_bid`,
`lot_size`, `quantity`, `round_trip_cost_inr` (or null), and `hedges` containing
`strike`, `ask`, `broker_margin_inr` (or null). Supply your own intended quantities
and verified inputs; unknown costs keep net outcomes unknown. Optional
`margin_budget_inr` enables the fixed-quantity comparison ranking. No maximum-loss
limit is applied; neither a ranking nor a review output becomes an order.

## Current architecture

Local Groww reader → private history/snapshots → deterministic evidence → isolated
Codex proposals → owner review. PC collection and research run independently of
the app window. The older recorder still needs a reviewed publisher before its
diagnostic JSON becomes a validated accounting/report snapshot.
This path needs no paper service. Unrelated trading/PAPER libraries were removed;
existing journals and private data are preserved.
Oracle hosts shared services; the latest local research code is not deployed there.
The local PC observer is installed; scheduled visual mail and real Oracle execution
remain unfinished and inactive. The owner-authorized local app reuses the existing
loopback viewer. No broker-permission change is introduced.

## Verification

After adding the October 1 everyday strategy, **92 tests passed**, with four POSIX-only skips
locally. Configuration, Python compilation, PowerShell syntax and the offline demo
passed. Headless CI runs on Linux and Windows. Tests cover weekday routing, the
13:15 JST boundary, exact listed-strike rounding, index-direction holding review,
existing-position review outside the new-entry window, fixed-quantity margin ranks,
missing data, no-look-ahead bar availability, chronological splits, zero-event
probabilities, synthetic/provenance rejection, hedge direction/payoffs, nested depth,
timestamp units, stop files and network refusal after the recording deadline.
Actual capture produced valid two-sided books for all nine sampled calls. This is
market-data verification, not a fill, liquidity guarantee or strategy approval.
