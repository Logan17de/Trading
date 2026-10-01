# Local headless workflow

Reviewed 2026-10-01. Use the existing engine from the local PC to read, record and
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
and online; no login task or continuous collector has been installed.

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

Start the local viewer in the owner's Edge:

```powershell
.\scripts\Start-TradingDashboard.ps1
```

It reuses [http://127.0.0.1:8765/](http://127.0.0.1:8765/) and binds only to loopback.
`-NoBrowser` starts without opening a tab. To stop the recorded viewer process:

```powershell
.\scripts\Start-TradingDashboard.ps1 -Stop
```

`-Offline` displays saved observations without broker requests. Stop the existing
viewer before changing its mode. This viewer uses Python's HTTP server and local
HTML/CSS/JavaScript assets inside the existing engine package; no new web framework
or trading service is installed.

Quotes, the day's FNO orders and open FNO positions are read on a five-second target
cycle. The worker authenticates once, uses four bounded quote threads and the
existing shared rate limiter. Slow network/API responses or rate limits can extend
the cycle; no overlapping polling cycles are started. Five-minute historical reads
run independently and refresh when a new bar can complete or an ordered contract
appears. Only completed bars are plotted. Broker naive timestamps remain an explicit
IST/bar-start assumption, not verified market timestamp semantics.

Each buy/sell chart is visible only for that side's actual option orders or open
positions. Rejected/cancelled unfilled orders and chain samples do not qualify.
Pending orders, executed orders, partial fills and open positions have distinct
labels. These are contract-level charts, not inferred spread pairings or fill
reconciliation. Calls and puts retain their actual symbol, expiry and strike;
Groww's instrument master confirms the exact historical contract. A missing option
history stays empty. A failed order read is shown as unavailable rather than zero
orders. Reads are bounded to four order pages, 1,000 position rows and 40 option
side/contract records; truncation is reported as incomplete.

Auto-refresh is enabled by default while the page is visible. Closing/hiding it,
turning off auto-refresh or using sample preview stops lease renewals. The collector
exits after the 20-second lease expires and any current bounded read finishes.
Each viewer process has its own lease/snapshot file, avoiding overlapping output
after a server restart. A worker has an eight-hour upper bound. It replaces one
private snapshot per worker instead of writing a new file every five seconds.
Snapshots, leases, PID and sanitized logs stay in ignored `.agent-state/`.

The browser receives whitelisted observations only. Broker credentials stay in
the DPAPI reader subprocess, never browser assets/API responses. Loopback/Host/
Origin checks, a per-process refresh token, no-store headers and explicit asset
routes prevent arbitrary file serving and cross-site refresh requests. All broker
order writes remain blocked. `.trader-paused` and Oracle services are unchanged.

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
and SVG candle charts are local; no external fonts or chart library are loaded.
At narrower widths, the grid reflows to stacked cards. Motion is limited to a
request spinner, disabled by reduced-motion preference.

Real Groww authentication, index quotes, order-list/position GETs, exact option
metadata and five-minute histories passed on October 1. A measured eight-cycle
run had starts 5.000–5.416 seconds apart with every requested read available.
Tests cover no-order gating, side/status filtering, private-field exclusion,
five-minute completion, quote/history independence, lease expiry, local HTTP
boundaries and observed strategy outcomes. Private browser QA evidence and
screenshots stay under `.agent-state/`; see current status for verified viewports.

## Verification and remaining work

Local read-only access was reverified for all three indices on October 1 at
16:22 JST: authentication and all 22 probes passed after the existing expired
Codex key was approved in the owner's Edge session.
The current runtime/dashboard suite passed 109 tests with four POSIX-only skips, and the
offline demo passed in the new checkout.
Headless CI runs on Linux and Windows. `nifty-engine` now invokes the headless CLI.

For a future authentication 403, inspect
[`Groww API Keys`](https://groww.in/trade-api/api-keys) in the owner's Edge session.
When the existing Codex key shows Expired, the owner authorizes clicking Approve.
Verify Approved and rerun `Read-GrowwMarket.ps1`. Groww displays a daily 6 AM reset;
the page does not establish its timezone. Reapproval does not require replacement
credential files or expanded broker permissions. No automated browser renewal
task is installed.

Unattended collection, validated engine-snapshot publication, Windows isolation and
local scheduled visual mail remain unfinished. Existing headless settings stay
`enabled=false` and `send_email=false`. Oracle order execution is not implemented or
activated by these commands. See [current status](HEADLESS_INTEGRATION_STATUS.md).
