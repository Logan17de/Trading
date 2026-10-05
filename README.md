# Trading

**Options Trader** is the owner's local PC viewer/controller for the existing
Python engine on Oracle. Oracle runs Groww observation, premium-policy review,
read-only basket preparation, private accounting and visual daily reporting.
Live broker execution is unfinished and inactive.

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
  monthly API fees. Recording a withdrawal is bookkeeping, not a money transfer.
- Oracle observation targets 12:40–19:45 JST weekdays. The visual daily email
  targets 19:30 JST every calendar day; configured timing and inbox delivery are
  reported separately.

See [app setup and operations](docs/LOCAL_HEADLESS.md#local-options-dashboard).
For another installation, run `scripts/Install-TradingApp.ps1` after local setup.

## Current premium rules

Everyday and Late-session are the only active review policies. The action window
is 14:00 inclusive–19:00 exclusive JST. One algo basket may contain up to two lots,
within actual available broker margin, with an equal-quantity same-expiry bought hedge.

- Everyday: NIFTY Mon/Tue/Fri short CALL near ₹20; SENSEX Wed/Thu near ₹80.
  Skip actual expiry proved by Groww and the current master, including holiday shifts.
- Below ₹8, review closing the old short before replacing it with the next listed
  short above ₹8. Keep the hedge unless improvement after incremental costs exceeds ₹100.
- By 19:00, hold when short premium is above its entry minus ₹5; otherwise queue
  a return to the index's target premium for the next allowed window.
- Late-session: actual expiry after 18:00, UP → PUT three listed strikes below
  ATM; DOWN → CALL three above; flat/unknown → no entry. Matching positions skip;
  replacement requires verified algo exits and flat confirmation. Manual trades stay protected.
- ₹2,000 basket-loss stop has priority over rolls. Broker protection is not
  deployed; this trigger is not a guaranteed maximum loss.

Owner On/Off persists until explicitly changed. The button cannot bypass the
unfinished executor or pause. Actual read-only preparation labels its sampled
scope; replay recovery tests do not prove persistent broker protection.
News workers, news entry gates and news dashboard/email wording are retired.
Historical offset, barrier, swing and news research remain provenance.
Read [the exact premium policy](docs/PREMIUM_STRATEGY.md) and
[execution preparation and remaining work](docs/EXECUTION_PREPARATION.md).

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
