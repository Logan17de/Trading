# Local headless workflow

Reviewed 2026-10-05. Use the existing engine from the local PC to read, record and
research NIFTY, BANKNIFTY and SENSEX. These commands do not require Oracle, SSH,
Supabase or a production encryption-key migration. Oracle remains running for shared
services and the intended later execution integration.

## Environment

The current repository is [`Logan17de/Trading`](https://github.com/Logan17de/Trading).
Use `D:\Money Trader\Trading` for local commands and future development:

```powershell
Set-Location 'D:\Money Trader\Trading'
```

The verified workstation uses Python 3.13.2 in `.venv` and `growwapi==1.5.0`.
Observed dependencies are pinned in
[`deploy/requirements-local-win-py313.lock.txt`](../deploy/requirements-local-win-py313.lock.txt).
For a new Windows Python 3.13 environment, from the repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -I -m pip install -r deploy\requirements-local-win-py313.lock.txt
.\.venv\Scripts\python.exe -I -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -I -m pip check
```

The credential store must already exist under the original Windows profile. A
fresh checkout alone does not provision credentials.

## Credentials and output

The helpers decrypt `groww-api-key.dpapi` and `groww-api-secret.dpapi` from the
restricted sibling `.secrets/growing-trader` directory. DPAPI is bound to the original
Windows account/profile. The local `api_key.txt` and `api_secret.txt` used by the
holdings helper are ignored and have restricted ACLs, but remain plaintext.
No credential is imported into the new repository's Git history.

Credentials are piped into an isolated Python process through stdin; SDK output and
sensitive exception details are suppressed. No credential appears in command
arguments, Git, JSON configuration, research output or Codex input. Authentication
is the only permitted broker POST; explicit market/profile/expiry reads are GETs.
Historical study mode adds candle/contract GET routes. Dashboard mode also permits
only the order-list and positions GETs and Groww's public instrument CSV. All broker
order writes remain denied. Redirects are refused and requests are bounded.

Private results live under ignored `.agent-state/`. Market-check exit code 0 means
the requested data was available; code 2 means failed or incomplete. Authentication
alone does not establish market access. Reception time does not establish book or
Greek freshness, and ordinary index quotes are not verified indicative values.

## Commands

Read current values, chains and bounded call samples:

```powershell
.\scripts\Read-GrowwMarket.ps1
```

Download and evaluate the fixed historical study:

```powershell
.\scripts\Research-GrowwStrategies.ps1 -Index SENSEX -Start 2026-08-03 -End 2026-09-29
```

See [owner strategies](OWNER_STRATEGY_RESEARCH.md) for the bounded recorder,
Japan-time windows, study limits and offline spread comparison. Keep the PC awake
and online. The PC app sign-in startup and background observer are installed.

Check holdings yourself using the small script requested by the owner:

```powershell
.\.venv\Scripts\python.exe -I .\scripts\check_holdings.py
```

It reads the original credential files and prints private holdings locally. Its
syntax was checked; the integration agent did not execute this holdings query.

Prepare an offline ticket from your own intended details:

```powershell
.\scripts\Prepare-GrowwTicket.ps1 `
  -InputFile .agent-state\my-ticket.json `
  -OutputFile .agent-state\my-ticket.md
```

Start with [`config/manual_ticket.example.json`](../config/manual_ticket.example.json)
and replace its deliberately invalid placeholders. The formatter chooses no
contract, quantity or price and submits no order. Contract, margin, lot/tick and
quote checks remain owner review items; separate tickets do not prove both legs filled.

## Local options dashboard

Open **Options Trader** from Desktop or Start. The installed background monitor
starts at Windows sign-in; opening the shortcut displays its Edge app window.
No command window is needed for daily use. Startup registration and the same
startup entry point were verified; an actual logout/reboot was not performed.
Windows PowerShell 5.1 startup and DPAPI-backed read-only capture were verified too.

For another Windows installation, after setting up the local environment:

```powershell
.\scripts\Install-TradingApp.ps1
.\scripts\Start-TradingApp.ps1
```

It reuses [http://127.0.0.1:8765/](http://127.0.0.1:8765/) and binds only to loopback.
`Start-TradingApp.ps1 -BackgroundOnly` starts the monitor without opening a window.
The underlying `Start-TradingDashboard.ps1` remains a maintenance helper.
To stop the recorded app server and let its collector lease expire:

```powershell
.\scripts\Start-TradingDashboard.ps1 -Stop
```

`Start-TradingDashboard.ps1 -Offline` displays saved observations without broker requests. Stop the existing
viewer before changing its mode. This viewer uses Python's HTTP server and local
HTML/CSS/JavaScript assets inside the existing engine package; no new web framework
or trading service is installed.

Quotes, the day's FNO orders and open FNO positions are read on a five-second target
cycle. The worker authenticates once, overlaps independent bounded reads and uses the
existing shared rate limiter. Slow network/API responses or rate limits can extend
the cycle; no overlapping polling cycles are started. Authentication failures back
off for 60 seconds before restarting the reader. Five-minute historical reads
run independently and refresh when a new bar can complete or an ordered contract
appears. Default index/option **lines** plot the actual journalled LTP prices at
their reception times, with a five-minute grid. The line updates each observation
cycle rather than waiting for a completed bar. Its caption shows the latest point
time and count. Gaps longer than 20 seconds are left disconnected; movement is
never fabricated. Same-day observations are recovered after an app restart.
Settings offers completed five-minute closes for comparison; those bars remain
the research input. No candlesticks are rendered anywhere. Reception times are
not exchange transaction timestamps. Broker naive historical timestamps remain
an explicit IST/bar-start assumption, not verified market timestamp semantics.

Each buy/sell chart is visible only for that side's confirmed nonzero open option
positions. Historical fills, pending orders, closed positions and chain samples do
not create charts. The horizontal blue line is Groww's position average entry;
carried-position average is used only when its signed quantity matches exactly.
Red SL and green target lines require a matching pending SL or active OCO/GTT exit
for the same contract, product and closing side. Cancelled/completed protection is
excluded. Partial covered quantities are labelled. Missing/failed protection reads
never invent prices. Smart-order lookup is bounded to orders created in the last
28 days, matching the provider's maximum one-month query range. Older smart orders
may be absent. These are contract-level charts, not inferred spread pairings or
fill reconciliation. Calls and puts retain their actual symbol, expiry and strike;
Groww's instrument master confirms the exact historical contract. A missing option
history stays empty. A failed order read is shown as unavailable rather than zero
orders. Reads are bounded to four order pages, 1,000 position rows and 40 option
side/contract records; truncation is reported as incomplete.

The PC app renews the collector lease independently of the page from **12:40–19:45
JST on weekdays**. Closing the app window leaves this monitor running. Outside
that window, visible auto-refresh can request reads anytime; hiding the page,
turning off view refresh or using sample preview stops those page renewals. When
neither source renews the lease, the collector exits after 20 seconds and any
current bounded read finishes. The PC must remain awake, signed in and online.
Each viewer process has its own lease/snapshot file, avoiding overlapping output
after a server restart. A worker has an eight-hour upper bound. It replaces one
private snapshot per worker instead of writing a new file every five seconds.
Snapshots, leases, PID and sanitized logs stay in ignored `.agent-state/`.
The existing SQLite journal now durably retains whitelisted quote observations and
completed bars for replay. The OBSERVE publisher keeps unverified timestamps, news
and missing accounting unknown; no broker identities enter published snapshots.

The browser receives whitelisted observations only. Broker credentials stay in
the DPAPI reader subprocess, never browser assets/API responses. Loopback/Host/
Origin checks, a per-process refresh token, no-store headers and explicit asset
routes prevent arbitrary file serving and cross-site refresh requests. All broker
order writes remain blocked. `.trader-paused` and Oracle services are unchanged.

### Strategy monitor and ownership

The monitor reuses the existing SQLite Store in private
`.agent-state/pc-monitor.sqlite3`. It reserves an engine intent before any future
submission and records the acknowledged broker identity as a hash. Ownership
requires the exact reference, ID, contract, side, quantity and filled-quantity
agreement. A reference prefix proves nothing. Unmatched/manual activity, including
a manual round trip in the same contract or unexplained net quantity, protects
that contract persistently. Incomplete reads block modifications. There is no
UI action to adopt a manual trade. The current collector submits no orders.

The latest settings are [`config/pc_app.example.json`](../config/pc_app.example.json);
machine-specific pins and approved parameters live in ignored
`.agent-state/pc-app.json`. No broker credentials belong in this JSON. Current
private settings pin the verified Codex executable/hash and reuse an auth-only
home. Collection warms at 12:40 JST; support/resistance research starts at 12:55
using completed five-minute index history. Daily jobs and fresh barrier crossings
are persisted and deduplicated, with a five-minute request lifetime and a daily
eight-attempt bound. Independent analysis does not suspend quote monitoring.

Codex receives structured market evidence without broker IDs or credentials. Its
strict output echoes request/snapshot identities, chooses evidence-backed levels,
and can propose a rule only within **13:00 inclusive–18:15 exclusive JST**. Missing
stop/target amounts remain null. Invalid, stale or out-of-window results are
rejected. The app displays the resulting levels, request state and latest rule.
Rules remain proposals; no PC-to-Oracle execution transport is installed.

The everyday reference keeps the 13:15 boundary, fixed index offsets, one active
slot and one lot. PC policy v2 sells NIFTY +500 or SENSEX +1000, then buys a higher
same-expiry call. The comparison recommends the highest supplied net expiry-profit
bound within supplied margin at fixed quantity; absent costs/margin block ranking.
This is a review output, not an order or expected-profit forecast. The underlying's actual
expiry dates must agree between Groww's expiry API and current instrument master
for the current month and nearest listed expiry;
unknown evidence blocks entry. Dates refresh hourly when confirmed and retry after
five minutes when unavailable. Weekday preference never determines expiry.

`oracle_trailing.py` is disabled, undeployed source for modifying an **already-owned
protective SL**. It uses exact broker identity, fresh complete ownership evidence,
the repository pause and Japan window gates. Option-premium stops ratchet favorably
in valid tick increments; they never loosen. Provider acceptance is followed by
order-detail readback before confirmed stop state advances. A timeout or mismatch
requires reconciliation, not another write. Tests use a fake broker. Initial SL
placement, selected expiry, exact order sequencing, Oracle transport/service
integration and reviewed stop/target values remain incomplete. Groww documents
GTT/OCO orders; no native trailing field was verified.
[Groww smart-order documentation](https://groww.in/trade-api/docs/python-sdk/smart-orders)

To remove only this app's shortcuts and sign-in registration, use
`Install-TradingApp.ps1 -Remove`; journals and the vault are preserved. This does
not stop an already-running monitor. Use the maintenance stop command first.

### Reviewed account results

Account capital, margin, portfolio value, P&L and per-strategy results currently
require the owner-reviewed private `.agent-state/dashboard-account.json` ledger.
Missing/invalid/stale values are labelled and never replaced with sample profits.
The exact supported ledger shape is:

```json
{
  "format": "dashboard-account-v1",
  "as_of": "REPLACE_WITH_ACTUAL_AWARE_ISO_TIMESTAMP",
  "capital_inr": null,
  "portfolio_value_inr": null,
  "used_margin_inr": null,
  "available_margin_inr": null,
  "unrealized_inr": null,
  "strategy_capital_inr": {},
  "closed_trades": [],
  "pnl_series": [],
  "portfolio_series": []
}
```

Each closed trade requires a unique `id`, a known `strategy` (`everyday`,
`late_session`, `swing`, `expiry_reversal`), aware ISO `closed_at` and
`net_pnl_inr` after costs. Series rows require aware ISO `at` and numeric `value`,
in unique chronological order. Strategy capital, when supplied, must be positive.
Green is the percentage of completed trades with net P&L >= 0; red is the percentage
below zero. A strategy with completed trades and no losses is 100% green. Zero
completed trades is “No results”. The separate P&L % is total net P&L divided by
reviewed strategy capital. These are recorded outcomes, not a predicted probability.
Automatic broker-account P&L reconciliation and strategy attribution are unfinished.

### Dashboard design and verification

Preserve the owner's white/mint Options Trader reference: navy Segoe UI headings,
green gains, orange portfolio accents, soft card borders, 19px corners, three
index cards, two conditional option panels and bottom performance cards. Assets
and SVG line charts are local; no external fonts or chart library are loaded.
At narrower widths, the grid reflows to stacked cards. Motion is limited to a
request spinner, disabled by reduced-motion preference.

Real Groww authentication, index quotes, order-list/position GETs, exact option
metadata and five-minute histories passed on October 1. A measured eight-cycle
run had starts 5.000–5.416 seconds apart with every requested read available.
Tests cover no-order gating, side/status filtering, private-field exclusion,
five-minute completion, quote/history independence, lease expiry, local HTTP
boundaries and observed strategy outcomes. Private browser QA evidence and
screenshots stay under `.agent-state/`; see current status for verified viewports.
October 5 Edge QA verifies all-line rendering, sample labels, actual no-order
gating, ownership labels, settings/focus/time-zone switching and no document
overflow at 320/390/768/1440 CSS pixels. Sample results never enter the live API
or journal. October 5 reapproval of the existing expired key resolved HTTP 403.
Authentication and all 22 probes passed at 10:45 JST; app order/position/metadata
reads and both expiry-day checks passed. Last-session prices and absent pre-market
books remain distinct from executable freshness. Current-day lines wait for
session prices.

## Verification and remaining work

Local read-only access was reverified for all three indices on October 1 at
16:22 JST: authentication and all 22 probes passed after the existing expired
Codex key was approved in the owner's Edge session.
See current status for the latest test count and offline-demo verification.
Headless CI runs on Linux and Windows. `nifty-engine` now invokes the headless CLI.

For a future authentication 403, inspect
[`Groww API Keys`](https://groww.in/trade-api/api-keys) in the owner's Edge session.
When the existing Codex key shows Expired, the owner authorizes clicking Approve.
Verify Approved and rerun `Read-GrowwMarket.ps1`. Groww displays a daily 6 AM reset;
the page does not establish its timezone. Reapproval does not require replacement
credential files or expanded broker permissions. No automated browser renewal
task is installed.

The local observer now has sign-in startup and background collection. Validated
report-snapshot publication, Windows privilege isolation and scheduled visual mail
remain unfinished. Existing headless settings stay
`enabled=false` and `send_email=false`. Oracle order execution is not implemented or
activated by these commands; the disabled trailing adapter is source only.
See [current status](HEADLESS_INTEGRATION_STATUS.md).
