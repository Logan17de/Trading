# Trading

Headless local Groww market observation and research of the owner's hedged-call
strategies. Oracle remains the intended execution host and stays running continuously
for shared services. Real Oracle execution is unfinished and inactive.

## What this repository contains

This is the current working repository: [`Logan17de/Trading`](https://github.com/Logan17de/Trading).
The local checkout is `D:\Money Trader\Trading`. It contains the 50 essential source
files from the cleaned engine plus `AGENTS.md` for future work.

| Path | Purpose |
|---|---|
| `src/nifty_engine/agent_engine/` | Market reader, recorder, owner study, spread review, SQLite triggers, isolated Codex, visual email, news and backups |
| `src/nifty_engine/brokers/` | Existing read-only Groww adapter and shared rate limiter |
| `config/` | Disabled headless settings, fixed owner hypotheses and manual-ticket example |
| `scripts/` | Local PowerShell commands, holdings helper, config check and diagnostic mail support |
| `deploy/` | Two inactive scheduler/worker templates and minimal dependency locks |
| `docs/` | Five current setup, engine, strategy, handoff and status guides |
| `tests/` | Tests for the retained headless workflow and boundaries |
| `.github/workflows/ci.yml` | Python verification on Linux and Windows; no deployment or power action |
| `AGENTS.md` | Repository identity, operating constraints and continuation instructions |

The initial `main` commit imports the verified headless workflow with fresh history.
Earlier development and deployment history remains in the previous repository.

## Strategies

Four hypotheses are recorded: everyday, late-session premium decay, swing and
expiry reversal. The everyday rule starts at 13:15 JST: NIFTY spot +400 on
Monday/Tuesday/Friday; SENSEX spot +800 on Wednesday/Thursday; buy a higher call
with matching expiry and quantity. Hold while the index is flat/down versus entry;
an upward move produces an exit review. There is no added maximum-loss cap.

The engine can review this rule offline and compare supplied hedges within a
margin budget at fixed quantity. Margin budget and expiry choice remain unset.
Continuous monitoring and real execution are unfinished; option-profit probability
is unknown. See [the strategy guide](docs/OWNER_STRATEGY_RESEARCH.md).

## Use locally

Follow [local setup](docs/LOCAL_HEADLESS.md), then from the repository in PowerShell:

```powershell
.\scripts\Read-GrowwMarket.ps1
.\scripts\Research-GrowwStrategies.ps1 -Index SENSEX -Start 2026-08-03 -End 2026-09-29
```

See [owner strategies](docs/OWNER_STRATEGY_RESEARCH.md) for Japan-time windows,
bounded recording and offline hedge comparison. Your strategy rules remain fixed;
no strategy, size or real order is automatically selected.

## Current evidence

Local access was reverified on October 1 at 16:22 JST for NIFTY, BANKNIFTY and SENSEX:
authentication and all 22 read-only probes passed. The earlier HTTP 403 was resolved
by approving the expired existing Codex key in the owner's Edge session. Groww
shows that approval resets at 6 AM tomorrow; no new credential was generated.
The study has 51,315 index candles
across 41 observed sessions per index. The September 30 recording has 29 snapshots
from 18:40–19:08 JST, with one partial snapshot. No strategy has a validated net
option-profit probability.

With the everyday strategy, **92 tests passed locally with four POSIX-only skips**. Configuration,
package installation, dependency checks and the offline demo passed. The retained CI
runs on Linux/Python 3.12 and Windows/Python 3.13.

Codex receives structured data and the owner-facing report is a visual email. Actual
Codex CLI checks and one diagnostic inbox delivery passed; continuous collection,
scheduled analysis/mail and deployment remain unfinished. See
[current status](docs/HEADLESS_INTEGRATION_STATUS.md).

## Operating rules

Keep `.trader-paused`, protected credentials and existing journals intact. Preserve
shared Oracle/Colab/Qwen/mail services. No real orders have been placed by this
integration; do not enable LIVE, change broker permissions or merge automatically.
Profit targets never raise position size, loss limits, leverage or allowed risk.

Start with [the handoff](docs/CODEX_HEADLESS_HANDOFF.md) and
[engine guide](docs/HEADLESS_CODEX_ENGINE.md).
