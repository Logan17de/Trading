# Current Trading handoff

Reviewed 2026-10-01. Current repository: `Logan17de/Trading`.
Branch: `main`. Local checkout: `D:\Money Trader\Trading`.

The initial import takes only the 50 retained files from Growing-Trader commit
`767e198c4ae7c40ad0c3095707038aa2c57c2291`, with fresh Git history and a new
`AGENTS.md`. Continue all new development here. Previous PRs and deployment records
belong to the old repository. The Oracle installation still uses its existing
`/opt/growing-trader` and `/etc/growing-trader` paths; the repository move does not
deploy code or rename those paths.

## Owner direction

Use the local PC for headless market observation, recording and research of hedged
call selling. Keep Oracle running continuously; it is the intended later execution
host and currently hosts shared Qwen, mail and tunnel services. The current task
has no PAPER deployment requirement.

Read [integration status](HEADLESS_INTEGRATION_STATUS.md),
[owner strategy definitions](OWNER_STRATEGY_RESEARCH.md) and
[`config/owner_strategies.json`](../config/owner_strategies.json).
There are four fixed hypotheses: everyday, late-session, swing and expiry reversal.
The everyday rule starts at 13:15 JST: NIFTY spot +400 on Monday/Tuesday/Friday,
SENSEX spot +800 on Wednesday/Thursday, with a same-expiry higher call hedge.
Flat/down versus the entry index level means hold; up means exit review. Carried
positions keep the original entry reference. Margin budget and expiry rule are
unset; the owner requested no maximum-loss cap. No quantity increase is automatic.
Evening Japan-time windows and a preference for calls are confirmed. The overnight
exit and 10/20 listed-strike hedge distances are research choices, not confirmed
interpretations of the owner's phrase “10–20 places.”

## Implemented and verified

- Local reader, history download, bounded recorder and offline call-spread review
  extend the existing `agent_engine` package.
- The owner-authorized local Options Trader viewer is implemented in that package,
  with Groww quotes/orders on a five-second target cycle and five-minute option
  candles. Buy/sell panels use only matching actual orders or open positions.
  Start it with `scripts/Start-TradingDashboard.ps1`; see the local guide. Account
  P&L and strategy outcomes still require a private reviewed ledger.
- Everyday version 2 references and an offline structured holding-review command
  are implemented. Margin comparisons rank supplied hedges by net maximum expiry
  profit within a supplied budget at fixed quantity. No hedge or order is selected.
- NSE's September 29 current lot file, fetched October 1, lists NIFTY 65 for
  October 2026. Exact proposed contract metadata still needs verification.
  [NSE lot file](https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv)
- Local Groww access was reverified across all three indices on October 1 at
  16:22 JST: authentication and all 22 read-only probes passed. The earlier HTTP
  403 was resolved by approving the expired existing Codex key in the owner's
  Edge session. The study has 51,315 candles, with 41 observed sessions per index.
- The September 30 recording ended with 29 snapshots, 28 successful and one partial,
  covering 18:40–19:08 JST. It does not cover the full late-session hypothesis.
- The latest runtime/dashboard tests passed: 109 locally, four POSIX-only skips; the offline demo passed.
  The retained CI exercises Linux and Windows.
- The existing history produced 25 NIFTY and 16 SENSEX everyday references. Version
  1 results reproduce exactly across all three datasets; option-profit probability
  remains unknown. These references are not simulated fills or a validated strategy.
- Actual authenticated Codex structured-output runs passed on both hosts. A visual
  diagnostic email has separate provider-acceptance and inbox-placement evidence.
- The source repository's Linux/Windows CI passed. Its existing PRs remain
  unmerged; no deployment of the latest local research code has occurred.

## Remaining work

Groww approval lifecycle: the API key dashboard shows a daily 6 AM reset. On a
future authentication 403, inspect the existing Codex key in the owner's Edge
session. The owner authorizes Approve when that key shows Expired; verify Approved
and rerun the read-only helper. The page does not state a timezone, so preserve
the displayed reset time without inferring a conversion. This instruction does
not authorize creating keys, changing broker permissions or placing orders.

1. Record complete comparable sessions, exact option contracts and both leg books;
   identify indicative versus regular index prices and verify source timestamps,
   lot sizes, expiry calendars and settlement evidence.
2. Evaluate the fixed everyday, late-session, overnight and expiry-reversal hypotheses with
   costs, realistic fills, adverse gaps and chronological holdouts. No strategy
   has a validated net-profit probability or has been selected for execution.
   The everyday direction rule is defined; its margin budget, expiry selection and
   continuous holding review still need integration.
3. Connect reviewed local observations to the existing analysis/reporting schema.
   The recorder's diagnostic JSON is not automatically a validated engine snapshot.
4. Finish unattended collection, monitoring, Windows isolation and visual daily-mail
   integration. The viewer collector runs only with an active page lease.
   Headless scheduling is disabled; no retained workflow publishes
   local captures or delivers a recurring visual report.
5. Review Oracle deployment, shared resource capacity, backup/restore and independent
   outage monitoring before enabling any new service. Oracle execution remains
   unfinished and inactive.

## Operating constraints

Use the existing DPAPI-protected local credentials. No production encryption-key
rotation is needed for local reads. Keep existing encrypted Supabase records and
keys unchanged unless a deliberate migration is authorized and verified.

Preserve `.trader-paused`, disabled trading services and shared Colab/Qwen services.
Do not submit real orders, enable LIVE, change broker permissions or merge
PRs automatically. The owner submits real transactions through their authorized
execution flow. A profit target cannot increase exposure or bypass an evidence gate.

The repository contains the essential source import and current local extensions.
News/calendar evidence lives in `agent_engine.news`. Private historical studies,
recordings and verification evidence are copied into ignored `.agent-state/`;
the Windows helpers reuse the existing protected sibling credential vault.
Keep journals and credentials intact. Existing Oracle/shared-service resources
and the previous repository remain available for deployment provenance.
