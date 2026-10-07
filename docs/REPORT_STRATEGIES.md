# Report-based strategy research · October 7, 2026

The owner asked to derive strategies from the supplied deep-research report and
replace overlapping original strategies. Both NIFTY and SENSEX are studied. The
new catalog is the current dashboard/email strategy list. It is MONITOR_ONLY;
there is no new broker execution path or activation.

## Replacement and provenance

| Previous entry rule | Replacement |
| --- | --- |
| Everyday ₹20/₹80 call selling, fixed rollover thresholds | Regime/IV-selected bull put or bear call spread; half-credit exit study |
| Late-session expiry theta selling near ATM | Non-expiry volatility/range condor study; no 0DTE entry |
| No existing equivalent | Term-structure calendar research |

Everyday and Late-session are retired from NEW entries and rolls even if the
owner preference is On or other activation gates are satisfied. The runtime
requires the new catalog, hashes it with the legacy management policy, and blocks
legacy preparation. Existing journals, P&L attribution, GTT references, original
exit/trailing code and manual protection remain intact. Existing baskets are not
renamed, deleted, adopted, closed or modified by this migration.

The supplied report assumes SPX/ES. Its comparison tables are explicitly fictional;
its performance charts are placeholders and its numbered references are not a
reproducible source bibliography. No reported Sharpe, win rate or profit estimate
is imported. Bullish bear-call and other inconsistent directional examples were
not followed. This is an adaptation of hypotheses, not demonstrated Indian alpha.
Source file SHA256: `e5251ed43d84d0b59c81543cd9366c1bc80dd527da619f7bacd64efcdb1d50a6`.
The original private download is not copied into Git.

## Declared hypotheses

The following defaults make the report testable; they are not optimized settings.
The owner loss trigger remains ₹1,000 and maximum size two lots in one basket.
Research comparisons cover both indexes without imposing the former weekday bias.
Entry research window remains 14:00–19:00 JST weekdays.

| Strategy | Required regime/volatility | Structure |
| --- | --- | --- |
| Bull put | Spot > prior MA20 > MA50; ADX14 ≥25; IV percentile ≥70; IV30 − RV30 ≥5 annualized vol points | Sell put with absolute delta .15–.25, buy lower-strike same-expiry put |
| Bear call | Spot < prior MA20 < MA50; ADX14 ≥25; same rich-volatility filters | Sell call with delta .15–.25, buy higher-strike same-expiry call |
| Iron condor | ADX14 <20; same rich-volatility filters | Both credit spreads; short put below short call; all four legs same expiry/lot size |
| Calendar | IV percentile ≤30; actual front-contract IV > back-contract IV | Buy 60–90 calendar-DTE option; sell same strike/type 30–45 DTE option |

All short expiries are 30–45 calendar days from actual current API/master evidence.
There is no invented expiry, fixed strike step, US point multiplier or lot size.
Signed deltas and quotes belong to exact contracts. ADX20–25 and conflicting MA
conditions are TRANSITION; no directional credit-spread entry is inferred.
Expiry, event evidence and units are checked separately from quote freshness.

Credit structures need dated two-sided depth, equal quantities, exact basket
margin and round-trip cost evidence. Conservative entry marks: SELL at bid, BUY
at ask. Max expiry profit = net credit × quantity − estimated round-trip costs.
Worst expiry loss = (maximum wing width − net credit) × quantity + those costs.
Both one and two lots are compared, but structures exceeding ₹1,000 worst expiry
loss or actual margin are excluded. Ranking is profit/worst-loss, then lower
margin, among at most 200 contracts and 500 combinations per strategy. It is not
a global optimum or expectancy estimate. Only one combined-index proposal wins.
Broker costs remain estimates, not final settled charges; stops cannot guarantee
fills or cap realized loss during gaps/failures.

Exit studies for credit structures: half of quoted net expiry profit; loss trigger
at the smaller of ₹1,000 or 1.5× entry credit; exit with ≤7 DTE to avoid expiry
gamma exposure. Stops take precedence. No automatic rollover or unlimited
replacement entry occurs. Calendars have no verified payoff/loss bound or broker
multi-expiry margin model: RISK_MODEL_REQUIRED, never selected for execution or
treated as loss-limited merely by their debit.

## Required evidence and current limitations

`historical_features` derives MA20/MA50, annualized 30-return realized volatility,
Wilder ADX14 from prior completed daily OHLC, and current-IV percentile against
252 consecutive supplied prior-session IV observations. Duplicate/future sessions
are rejected; insufficient data omits the feature. Dates must be verified exchange
sessions by the supplying dataset; the function does not certify that provenance.
Its test fixtures are synthetic, not historical Indian option backtests.

Input unit conventions: spot/MAs `index_points`, ADX `index_score`, IV percentile
`percentile`, IV/RV `annualized_percent` (25 means 25%, not .25). Each feature has
`value`, `unit`, `source`, `observed_at`. Historical features also carry
`history_end_at` and `sessions`; the history ends before the evaluation day and
must be within 72 hours. Snapshot/spot/current IV/books/funds have a 15-second
freshness limit. Holidays outside that conservative history window wait.

Research requires a verified event calendar checked within one hour and covering
the maximum prospective short holding period (45 −7 days). Missing, stale or
scheduled risk remains UNKNOWN; it never becomes a presumed clear day. This is
new research evidence, not restoration of the retired RBI/ET news worker or its
old trading gates. Unverified dealer GEX, aggressive flow/CVD, spot-index VWAP,
overnight futures and next-day probabilities are not inferred. The existing
Premium Impulse Monitor remains a separate monitor-only dataset.

There is currently no verified 252-session matched Indian IV series or continuously
provisioned event calendar in this runtime. Empty/stale inputs produce WAIT;
historical index minute prices are not substituted for an option-volatility
history. The module does not fetch or purchase SPX datasets. All new strategy
performance bars remain unknown until reviewed closed-trade/out-of-sample data
exists; old strategy results are not copied to the new strategies.

## Private replay / evidence import

### Automatic Oracle connections

The existing five-second collector now connects actual index spot, complete
position/order evidence, manual protection, dated funds and expiry evidence to
the studies. Receipt times are preserved; failed or stale reads never become
fresh. A separate minute worker shares the rate limiter and fetches completed
Groww daily OHLC for MA/ADX/realized volatility, exact API/master intersections
up to90 DTE, option-chain signed Greeks/percentage IV and current contract books.
It samples at most eight contracts per expiry and six hypothetical basket
comparisons per index. Margin/charge calculations are broker analytical requests,
never order submissions. Greek receipts expire independently of newer book reads.
Daily history is fetched once per day, with hourly retry after failure. A research
request covers120 calendar days, within the actual daily-candle180-day limit.
This supplies price indicators only, not252-session IV history. A research
fault is isolated from the collector, owner intent and report delivery.

Current-IV30/252-session matched IV history and dated event-calendar evidence
still need a verified source; no substitute or presumed clear calendar is made.
The worker does not certify a global optimal hedge or always-ready snapshot.
Minute research reads can expire under the15-second evidence policy; this leaves
WAIT. Five-second account collection remains independent. Status/counts and
specific missing inputs are visible in the dashboard and private evaluations.

Owner intent remains Off until the protected Start action is explicitly invoked.
Restart, reconnection, research and email never set it On. A Start click saves
intent; these monitor-only studies and pending live validation still prevent
trading. No click or local endpoint bypasses paper/pause/activation/manual gates.

### Supplied evidence

Pure replay from a supplied private structured evidence file:

```powershell
.venv\Scripts\python.exe -I -m nifty_engine.agent_engine.report_strategies `
  --root . --features .agent-state\report-evidence.json `
  --at 2026-10-07T14:05:00+09:00
```

Input bundle: index, received_at, features, positions_complete, protected_symbols,
expiry_evidence, event_calendar, funds, contracts and margins. See the isolated
fixtures in `tests/test_report_strategies.py` for the exact schema. `margins` keys
are the SHA256 `identity` of `{legs: [[symbol, side], ...], quantity: units}` in
BUY hedge / SELL short order (put pair then call pair for condors). Inputs are
evidence, not orders. Never place credentials or account identities in this file.

An explicit `--database <private-journal-path>` imports the evidence into that
journal and records the evaluations; it does not touch broker state, capital,
owner intent or activation. Use isolated replay journals for synthetic fixtures.
Production snapshots must be real, dated, independently sourced and never a
test fixture. The runtime reevaluates stored evidence every 30 seconds; freshness
expires normally. Existing private archive exports versioned evaluations under
`algo_state` / `report-strategy-evaluations`, through the same idempotent outbox.
This category includes research state, not proof of executed trades. No table,
privilege, public access or cloud-to-engine command path was added.

Before promotion: acquire verified Indian daily options/IV history, populate
current exact-contract data and a dated event calendar, replay with realistic
entry/exit spreads/charges and partial-fill assumptions, use chronological
walk-forward splits, and validate on untouched later sessions. Net expectancy,
drawdown, tail losses and uncertainty are required, not fictional win rates.
The existing live broker/GTT/child validation and owner activation remain separate.

## Primary references checked

- [Cboe iron-condor methodology](https://cdn.cboe.com/api/global/us_indices/governance/CNDR_Methodology.pdf): hedged put and call structures; SPX benchmark, not an Indian backtest.
- [Cboe 0DTE research discussion](https://www.cboe.com/insights/posts/henry-schwartzs-zero-day-spx-iron-condor-strategy-a-deep-dive): expiry gamma exposure, not evidence of this catalog's profitability.
- [NSE India VIX](https://www.nseindia.com/static/products-services/indices-indiavix-index): volatility derived from NIFTY options; not a directional prediction or a substitute for per-contract IV.
- [Supabase API security](https://supabase.com/docs/guides/api/securing-your-api): existing server-only grants and RLS boundary preserved.
