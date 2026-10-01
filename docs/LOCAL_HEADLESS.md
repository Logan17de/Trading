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
is the only permitted POST; explicit market/profile/expiry reads are GETs. Historical
study mode adds only the required candle/contract GET routes. Redirects are refused
and requests are bounded. No order route is permitted.

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

## Verification and remaining work

Local read-only access was reverified for all three indices on October 1 at
16:22 JST: authentication and all 22 probes passed after the existing expired
Codex key was approved in the owner's Edge session.
The migrated runtime suite passed 77 tests with four POSIX-only skips, and the
offline demo passed in the new checkout.
Headless CI runs on Linux and Windows. `nifty-engine` now invokes the headless CLI.

For a future authentication 403, inspect
[`Groww API Keys`](https://groww.in/trade-api/api-keys) in the owner's Edge session.
When the existing Codex key shows Expired, the owner authorizes clicking Approve.
Verify Approved and rerun `Read-GrowwMarket.ps1`. Groww displays a daily 6 AM reset;
the page does not establish its timezone. Reapproval does not require replacement
credential files or expanded broker permissions. No automated browser renewal
task is installed.

Continuous collection, validated engine-snapshot publication, Windows isolation and
local scheduled visual mail remain unfinished. Existing headless settings stay
`enabled=false` and `send_email=false`. Oracle order execution is not implemented or
activated by these commands. See [current status](HEADLESS_INTEGRATION_STATUS.md).
