# Trading

**Options Trader** is the owner's local PC app for Groww observations and strategy
research. It extends the existing Python engine. Oracle remains the intended
execution host; live execution is unfinished and inactive.

Current repository: [`Logan17de/Trading`](https://github.com/Logan17de/Trading).
Local checkout: `D:\Money Trader\Trading`.

## Open the PC app

Open **Options Trader** from Desktop or Start. Its background monitor is registered
to start automatically at Windows sign-in. Routine use needs no CMD window.
The app uses an Edge app window at [127.0.0.1:8765](http://127.0.0.1:8765/).
Keep the PC awake and online.

- Groww quotes/orders target a **five-second** cycle; all charts are **lines** with
  completed **five-minute** prices. Slow requests can extend a cycle.
- Buy/sell charts appear only for actual matching orders or open positions.
- Manual, unknown and mixed trades are protected. Engine ownership requires exact
  private journal and broker-identity matches.
- P&L and strategy outcomes need the reviewed private accounting ledger. Completed
  trades without losses show 100% green; no completed trades shows “No results”.
- Background collection starts at 12:50 JST, support/resistance research at 12:55.
  Structured barrier proposals are restricted to 13:00–18:15 JST.

See [app setup and operations](docs/LOCAL_HEADLESS.md#local-options-dashboard).
For another installation, run `scripts/Install-TradingApp.ps1` after local setup.

## Latest everyday rule

From 13:15 JST: NIFTY spot +400 on Mon/Tue/Fri; SENSEX spot +800 on Wed/Thu.
One active slot, one lot; buy a same-expiry call near index ATM, up to three listed
strikes away. Skip actual expiry day using Groww expiry dates and current contract
metadata, including holiday shifts. Unknown evidence blocks entry.

The ATM buy changes the earlier call-credit study's payoff. The app exposes that
conflict and prepares reviews. Automatic entry and broker SL updates are inactive.
Trailing-stop source has fake-broker verification; Oracle transport, initial
protective-order placement, reviewed stop/target parameters and activation remain.
See [strategy details](docs/OWNER_STRATEGY_RESEARCH.md).

## What is retained

| Path | Purpose |
|---|---|
| `src/nifty_engine/agent_engine/` | PC app, read-only collector, journal, owner research, isolated Codex, trailing adapter, visual email, news and backups |
| `src/nifty_engine/brokers/` | Existing Groww market adapter and shared rate limiter |
| `config/` | Latest PC policy, reproducible research protocols and disabled headless settings |
| `scripts/` | App installation/startup, local read/research helpers and verification |
| `deploy/` | Inactive Oracle scheduler/worker templates and dependency locks |
| `docs/` | Current setup, engine, strategy, handoff and evidence |
| `tests/` | Retained runtime and integration-boundary tests |
| `.github/workflows/ci.yml` | Linux/Windows verification; no deployment or VM power action |

Read [current status](docs/HEADLESS_INTEGRATION_STATUS.md) for dated test, broker,
Codex and mail evidence, and [the handoff](docs/CODEX_HEADLESS_HANDOFF.md) before
continuing development. Four strategy hypotheses are recorded; none has a validated
net option-profit probability.

## Operating rules

Preserve `.trader-paused`, the protected credential vault and journals. Keep Oracle
and its shared Colab/Qwen/mail/tunnel services running. No broker permissions, LIVE
activation, real orders or automatic merges are introduced by this integration.
Profit targets never increase exposure or force a trade.
