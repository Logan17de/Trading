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
  startup. All charts are lines. Groww reads target five seconds; default index/
  option lines use real journalled LTP observations at reception times with a
  five-minute grid and update-time/point-count captions. Completed five-minute
  closes remain selectable and are retained for research. Missing prices are not
  fabricated; slow requests can extend the cycle. Buy/sell panels require confirmed nonzero open
  positions; pending orders, historical fills and closed positions do not qualify.
  Show broker average entry, matching pending SL and active OCO/GTT exit target/SL lines.
  Missing protection prices stay absent; a failed protection read is labelled.
- Observe in the background from 12:40–19:45 JST on weekdays. Research support/
  resistance from 12:55. Barrier-rule validity is 13:00 inclusive–18:15 exclusive.
  Everyday entry starts at 13:15. The PC must be awake, signed in and online.
- One active slot and one lot. Everyday short: NIFTY spot +500 on Mon/Tue/Fri;
  SENSEX spot +1000 on Wed/Thu. Buy a higher call with the same expiry and quantity.
  Rank candidate hedges by quoted maximum net expiry profit after costs within
  available margin. The earlier fixed +1000 hedge example is not a strike floor:
  the owner subsequently allowed alternative higher strikes to improve profit.
  Missing costs/margin/quotes block recommendation; payoff bounds are not forecasts.
- Skip actual expiry day. Cross-check Groww's expiry API against current instrument
  metadata, including holiday-shifted dates. Missing/disagreeing evidence blocks
  entry. Do not infer expiry from the weekday or ask Codex to invent a calendar.
- Never modify or exit manual trades. Exact journal reservations and acknowledged
  broker IDs establish ownership; unknown or mixed contracts remain protected.
- If a native trailing stop is unavailable, ratchet an engine-owned protective SL
  and verify its broker readback. Stop distance, step and target remain unset.
- Keep Oracle running continuously for shared Qwen/Colab/mail/tunnels. Oracle is
  the intended execution host; new trading services remain inactive.

The owner confirmed BUY for the higher call and superseded the earlier ATM buy.
PC policy version 2 records the +500/+1000 short offsets and higher-call comparison.
Historical PC v1 retains its payoff-review blocker; historical research v1/v2 is
unchanged. No strategy has a validated net-profit probability or executable plan.

## Implemented and verified

- October 5 live-line correction is installed locally. Both active option lines
  recovered hundreds of actual same-day points from SQLite after restart and
  changed automatically in Edge without a manual refresh. Independent quote,
  order, position and protection reads overlap under the existing limiter;
  no collector cycles overlap. A dated sample measured 4.26–5.86 seconds between
  observations and a 3.14-second final cycle. This verifies the target cadence,
  not a strict network timing guarantee or full-session coverage. Local tests:
  195 passed, four POSIX-only skips; configuration, JS syntax and offline demo
  passed. Trading and manual-position protection are unchanged.

- Weekday **12:55 JST** Codex thread follow-up installed as
  `trading-news-and-barrier-check`; configured ACTIVE, first scheduled run not yet
  observed. The persistent app collects RBI + ET Markets evidence independently
  every 120 seconds during 12:40–19:45 JST. Support/resistance requests run from
  12:55, with 15:00/17:00 reassessment and changed-news/barrier triggers. Eight
  daily attempts remain the global cap; crossings take queue priority. Missing
  news stays UNKNOWN; assessments must cite actual IDs from both hosts for LOW
  and expire after 180 seconds. LOW is not a forecast. Level updates are research.
- Strategy ownership is persisted for each reserved slot, including overnight.
  Same-slot reuse cannot reattribute everyday orders to swing. An occupied slot
  blocks a second entry; no automatic release/reassignment is implemented. Owned
  exit/protection review takes priority. Swing's recorded **18:45 JST** entry is
  outside the 18:15 cutoff and remains research-only. Both strategies may be
  analysed concurrently; manual positions remain protected.
- Groww money is now read using the existing SDK's read-only available-margin
  endpoint every 30 seconds, independently of account/strategy P&L. The app shows
  clear cash, total used margin, option-buy/sell balances and collateral with the
  provider response timestamp; stale/failing reads show unknown amounts. Clear
  cash is not investment value. Strategy accounting remains unconnected.

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
and SENSEX expiry-day checks agree with current contract metadata. The 10:45 check
used last-session observations before the market opened. Later live-line
verification used current-session read-only quotes and preserved the existing
manual positions; it does not certify executable book freshness.

For a future authentication 403, inspect the same key in the owner's Edge.
Approve is authorized only when it shows Expired; verify Approved and rerun reads.
Do not create a replacement key, change permissions or purchase access. The page
displays “Resets 6 AM tomorrow” without an established timezone. Failure retries
back off for 60 seconds. Credentials and authentication details stay private.

## Remaining integration

1. Verify complete market-session collection and current five-minute option history
   after the session opens. Before open, the app labels current-day charts as waiting.
2. Verify/approve exact contracts, affordable hedge, costs,
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

October 5 news/funds verification: 192 tests passed, four POSIX-only skips; offline
demo completed with synthetic inputs and no mail/orders. Actual pinned Windows
Codex accepted the expanded structured schema, returned WAIT for missing market
evidence and emitted zero tool events. Real Groww money read passed. ET yielded
15 dated items; RBI supplied no item within the 24-hour policy, so news remained
UNKNOWN. This evidence does not verify the first scheduled market-session run.

## Protected state

Preserve `.trader-paused`, private `.agent-state/`, the sibling DPAPI `.secrets`
vault, existing Supabase ciphertext/keys and shared Oracle services. Private PC
settings are `.agent-state/pc-app.json`; Codex authentication stays in the existing
auth-only home. Do not put credentials in Git, logs, analysis or reports. There
was no key rotation, broker-permission change, LIVE activation, real order or merge.
