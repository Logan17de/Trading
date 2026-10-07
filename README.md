# Trading

**Options Trader** is the owner's local PC viewer/controller for the existing
Python engine on Oracle. Oracle runs Groww observation, premium-policy review,
read-only basket preparation, a durable order controller, private accounting and
visual daily reporting. The controller is implemented; live trading remains paused
pending controlled broker/protection verification and owner activation.

Current repository: [`Logan17de/Trading`](https://github.com/Logan17de/Trading).
Local checkout: `D:\Money Trader\Trading`.

## Open the PC app

Open **Options Trader** from Desktop or Start. Its background monitor is registered
to start automatically at Windows sign-in. Routine use needs no CMD window.
The app uses an Edge app window at [127.0.0.1:8765](http://127.0.0.1:8765/).
Viewing/control and the independent PC-based VM alerts require the PC awake and
online. Oracle collection and scheduled reporting run independently.

- Groww quotes/orders target a **five-second** cycle. Default index/option **lines**
  show actual recorded prices with a **five-minute** grid, update time and point
  count. Five-minute closes remain selectable in settings. Slow requests can
  extend a cycle; missing prices are not filled in.
- Buy/sell charts appear only for nonzero open option positions; no position means
  no chart. Broker average entry, matched SL and active OCO target prices are lines.
- Manual, unknown and mixed trades are protected. Engine ownership requires exact
  private journal and broker-identity matches.
- Today's gross P&L comes from Groww and actual quotes, split Self / Algo /
  Unassigned before charges. Net strategy results need the reviewed private
  ledger. Completed trades without losses show 100% green; no results stays unknown.
- Capital separately shows owner-recorded investment minus withdrawals minus
  monthly API fees. Use **Record investment** for money already added and
  **Record withdrawal** for money already withdrawn, with amount and JST date.
  Both are bookkeeping; neither transfers money or changes Groww cash/P&L.
- Oracle observation targets 12:40–19:45 JST weekdays. The visual daily email
  targets 19:30 JST every calendar day; configured timing and inbox delivery are
  reported separately.

See [app setup and operations](docs/LOCAL_HEADLESS.md#local-options-dashboard).
For another installation, run `scripts/Install-TradingApp.ps1` after local setup.

## Current strategy research

October 7: report-based NIFTY/SENSEX research replaces overlapping Everyday and
Late-session NEW entries and rolls. The dashboard/email show four strategies:

- Bull put spread: bullish trend and rich volatility, with a lower put hedge.
- Bear call spread: bearish trend and rich volatility, with a higher call hedge.
- Iron condor: range and rich volatility, with both short sides hedged.
- Calendar: low IV percentile and front/back inversion; payoff model still required.

These are unbacktested hypotheses. The existing executor now has guarded bull put,
bear call and iron condor adapters, plus four persistent strategy switches.
Calendar remains MONITOR_ONLY. Production is still Off/paper/paused: provider
activation, dated IV history and event coverage remain required. Start does not
bypass those blockers. See [execution status](docs/ORACLE_EXECUTION.md).
Missing IV history, Greeks, calendar, expiry, current books or exact margin keep
proposals unknown. One basket/max two lots and ₹1,000 risk bound are preserved.
Original records/exits/protection and manual trades are not relabeled or changed.
Read [the current rules and evidence requirements](docs/REPORT_STRATEGIES.md).

Owner On/Off persists until explicitly changed. The button cannot bypass the
pause or live-validation gates. Hedge-first entry, short-first exits, rolls and
expiry replacement use exact journal-owned orders. Actual preparation labels its
sampled scope; replay tests do not prove live persistent broker protection.
News workers, news entry gates and news dashboard/email wording are retired.
Historical offset, barrier, swing and news research remain provenance.
Read [the legacy management policy](docs/PREMIUM_STRATEGY.md) and
[execution preparation](docs/EXECUTION_PREPARATION.md) and
[the Oracle controller and activation boundary](docs/ORACLE_EXECUTION.md).

## What is retained

| Path | Purpose |
|---|---|
| `src/nifty_engine/agent_engine/` | Existing Oracle observer/policy/preparation, private journal, desktop viewer, visual email, replay execution guards and retained research |
| `src/nifty_engine/brokers/` | Existing Groww market adapter and shared rate limiter |
| `config/` | Latest PC policy, reproducible research protocols and disabled headless settings |
| `scripts/` | App installation/startup, local read/research helpers and verification |
| `deploy/` | Oracle observer isolation, historical scheduler/worker templates and dependency locks |
| `docs/` | Current setup, engine, strategy, handoff and evidence |
| `tests/` | Retained runtime and integration-boundary tests |
| `.github/workflows/ci.yml` | Linux/Windows verification; no deployment or VM power action |

Read [current status](docs/HEADLESS_INTEGRATION_STATUS.md) for dated test, broker,
Codex and mail evidence, and [the handoff](docs/CODEX_HEADLESS_HANDOFF.md) before
continuing development. Historical research does not activate entries. Neither
active rule has a validated net option-profit probability.

## Operating rules

Preserve `.trader-paused`, the protected credential vault and journals. Keep Oracle
and its shared Colab/Qwen/mail/tunnel services running. No broker permissions, LIVE
activation, real orders or automatic merges are introduced by this integration.
Profit targets never increase exposure or force a trade.
