# Current Trading handoff

Reviewed 2026-10-05. Repository: `Logan17de/Trading`, branch `main`.
Local checkout: `D:\Money Trader\Trading`.

Continue in the existing Python `src/nifty_engine/agent_engine/` runtime. The
previous Growing-Trader checkout retains deployment provenance and private
records. Oracle still uses `/opt/growing-trader` and `/etc/growing-trader`;
source changes here do not deploy or rename those locations.

Read [current status](HEADLESS_INTEGRATION_STATUS.md),
[local app/setup](LOCAL_HEADLESS.md) and [strategy definitions](OWNER_STRATEGY_RESEARCH.md).

## Latest owner direction

- Local Options Trader PC app; Desktop/Start access and automatic Windows sign-in
  startup. All charts are lines. Groww reads target five seconds; chart points use
  completed five-minute prices. Buy/sell panels require actual matching orders or
  open positions. Pending orders are not fills.
- Monitor in the background from 12:50–18:15 JST on weekdays. Research support/
  resistance from 12:55. Barrier-rule validity is 13:00 inclusive–18:15 exclusive.
  Everyday entry starts at 13:15. The PC must be awake, signed in and online.
- One active slot and one lot. Everyday short: NIFTY spot +400 on Mon/Tue/Fri;
  SENSEX spot +800 on Wed/Thu. Buy a same-expiry call near index ATM, up to three
  listed strikes away. The owner says budget ranking is not part of this rule.
- Skip actual expiry day. Cross-check Groww's expiry API against current instrument
  metadata, including holiday-shifted dates. Missing/disagreeing evidence blocks
  entry. Do not infer expiry from the weekday or ask Codex to invent a calendar.
- Never modify or exit manual trades. Exact journal reservations and acknowledged
  broker IDs establish ownership; unknown or mixed contracts remain protected.
- If a native trailing stop is unavailable, ratchet an engine-owned protective SL
  and verify its broker readback. Stop distance, step and target remain unset.
- Keep Oracle running continuously for shared Qwen/Colab/mail/tunnels. Oracle is
  the intended execution host; new trading services remain inactive.

Buying an ATM call while selling a higher call normally creates a **bullish debit
spread**. That conflicts with the previously recorded flat/down-hold, up-exit rule.
The app records both instructions and exposes a payoff-review blocker. No strategy
has a validated net-profit probability. Version 1/2 studies remain reproducible;
`config/pc_app.example.json` records the newer PC overlay.

## Implemented and verified

- PC app installed locally: Desktop and Start **Options Trader**, plus a hidden
  Windows Startup shortcut for its background monitor. The same startup entry
  point was run successfully; an actual logout/reboot has not been tested.
- Existing loopback server and local assets reused; no new GUI framework.
  Background collector, journal and Codex worker operate independently of the
  app window. Offline mode and sample preview remain explicitly labelled.
- Persistent manual-trade protection, one-slot reservation, current-expiry checks,
  support/resistance requests, crossing deduplication and strict structured-rule
  validation are implemented. Codex receives market evidence without credentials
  or broker IDs. Proposals are not executable orders.
- Actual Windows CLI `codex-cli 0.160.0`, executable/hash pin and supported existing
  authentication passed preflight and a non-interactive schema-validated WAIT run;
  zero tool events. Windows OS privilege isolation is still unverified.
- Trailing planner and disabled `OracleTrailingUpdater` source are implemented.
  Fake-broker tests verify favorable-only ratchets, exact SL identity, manual/stale/
  partial rejection, pause/time gates, acceptance-versus-readback and timeout
  reconciliation. No real protective SL was placed or modified.
- Retained tests, configuration checks and offline demo are required gates. See
  current status for final counts and dated browser evidence.

## Current broker access

October 5 authentication initially returned HTTP 403. The existing Codex key was
Expired in the owner's Edge session; the authorized Approve action changed it to
Approved. A 10:45 JST read passed authentication and all 22 read-only probes.
Current app order/position reads and exact contract metadata passed; both NIFTY
and SENSEX expiry-day checks agree with current contract metadata. Prices are
last-session observations before the market opens, not current executable books.

For a future authentication 403, inspect the same key in the owner's Edge.
Approve is authorized only when it shows Expired; verify Approved and rerun reads.
Do not create a replacement key, change permissions or purchase access. The page
displays “Resets 6 AM tomorrow” without an established timezone. Failure retries
back off for 60 seconds. Credentials and authentication details stay private.

## Remaining integration

1. Verify complete market-session collection and current five-minute option history
   after the session opens. Before open, the app labels current-day charts as waiting.
2. Resolve the ATM-spread payoff/direction conflict and approve exact contracts,
   selected expiry, lot/tick, stop/target and basket sequencing. No size increase
   follows from a monthly target. Overnight carried-position review is still a
   review output; the new 18:15 action cutoff must not imply overnight protection.
3. Complete and review the Oracle transport/executor, initial protective-order
   placement, partial-fill reconciliation, crash recovery and independent outage
   monitoring. Deploy only after shared-resource and backup/restore review and
   relevant owner activation approval. The trailing adapter is not deployment.
4. Record complete sessions and evaluate the four hypotheses with two-leg books,
   costs, realistic fills and chronological holdouts. Historical index moves are
   not option P&L. Source time/indicative-series semantics remain unverified.
5. Connect reviewed accounting/strategy attribution and local observations to the
   existing report publisher. Recurring visual daily email is not activated;
   provider acceptance and inbox delivery must remain separate evidence.

## Protected state

Preserve `.trader-paused`, private `.agent-state/`, the sibling DPAPI `.secrets`
vault, existing Supabase ciphertext/keys and shared Oracle services. Private PC
settings are `.agent-state/pc-app.json`; Codex authentication stays in the existing
auth-only home. Do not put credentials in Git, logs, analysis or reports. There
was no key rotation, broker-permission change, LIVE activation, real order or merge.
