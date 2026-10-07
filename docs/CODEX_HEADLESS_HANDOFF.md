# Current Trading handoff

## October 7 additional investment control

Added Record investment beside Record withdrawal in the capital card. It accepts
an amount and past/current JST date through the existing protected loopback POST,
fixed SSH command and Oracle socket. Investment additions append privately to
`capital-ledger-v1`; its original seed, withdrawals and fee history are preserved.
Old ledgers without an additions list remain readable. The existing summary now
includes due additions in invested/remaining capital, shared by dashboard, email
preview and the daily email. Groww cash, trading P&L and broker state stay separate.

Integer-paise validation, duplicate/restart/concurrent retry protection and
cross-kind ID conflicts are tested. Unconfirmed submissions keep their exact
request ID, amount/date; receipts must match and explicitly report no money moved
or broker writes. Synthetic tests never book entries in the owner's real ledger.
No amount was supplied for this request, so no actual investment was added.
Bookkeeping controls accept a current-month saved capital summary independently
of market/heartbeat freshness. Broker data freshness gates are unchanged. Oracle
still validates and acknowledges every record; connection failures keep the
pending request for an exact retry.

Verification: full513 tests passed509 with four platform skips before the small
UI availability follow-up; all37 accounting tests, including the actual JS
handlers with lost/wrong/duplicate receipts, passed after that follow-up. Config,
JS syntax, diff checks and the synthetic offline demo passed. Consistent SQLite
backup `pre-capital-investments-quiescent-20261007.sqlite3` passed full integrity
checking; exact capital-body hash and owner controls matched production. Initial
background backup checks timed out, so the paused observer was briefly stopped
for a consistent copy and resumed. Shared services stayed active. The protected
PC→Oracle investment route rejected a zero-amount validation probe; no record
was created. Final deployed code release: `a1c09d286d8da84a5a801cfb2a675b5e454d2c54`.
Observer and all four shared services are active. Paper/pause, default owner Off
and both strategy groups Off are unchanged; exact capital-body hash still matches
the backup and there are zero addition records. PC assets and private fixed SSH
release binding were refreshed. Owner Edge showed both enabled capital controls,
all four amounts, and the investment amount/JST-date form. Its actual email
preview displayed the shared four totals. No email was sent for verification.
Final protected-route invalid-amount probe was rejected without a ledger entry.
Observer heartbeat reached HEALTHY after restart; slow/stale market collection
remains visible and unresolved. Bookkeeping confirmation does not prove live
trading readiness, five-second data cadence or email inbox delivery.

## October 7 normal theta and two owner groups

Latest owner requested a normal selling strategy derived from research, rather
than restoring Everyday. Read NORMAL_THETA.md. Added a separate unbacktested
trend-aligned, positive-net-model-theta same-expiry vertical for NIFTY/SENSEX.
14–45 calendar DTE, signed short delta .15–.25, prior MA20/50 and ADX14, current
two-sided depth, exact costs/margin and INR 1,000 quoted expiry-risk/hedge-debit
bounds are required. It has no historical-IV or strict event-free-horizon gate;
scheduled-event status remains visible information. No trade is forced.

The two private owner choices are Normal theta spread and Research, both default
Off. Normal uses the existing PremiumExecutor basket lifecycle; Research cannot
authorize new orders. Old switches/records and exact legacy management remain.
The four studies and original private38-day campaign are not reset, shortened or
promoted. Research group selection is a saved preference; ongoing campaign
collection continues independently. Normal evaluations/contracts/preferences
are included in the existing one-way private archive allowlist. Actual archive
acknowledgments must be checked after rollout; code configuration is not a send.

Added startup handling: a missing/malformed snapshot before an executor call
waits for the first valid input. A fault after tick starts still latches and
exposes only error type/stage/filename/function/line, never raw messages or
responses. Journal failure also preserves the memory latch. The prior actual
Oracle worker latch's root cause was not recoverable from its old receipt;
an isolated copy returned an expected paper gate instead of reproducing it.
Do not claim a diagnosed root cause or automatically retry uncertain orders.

Live read-only Groww auth, listed expiries and chain signed Greeks worked.
Current API/master listing had no NIFTY30–45 DTE short on October7; SENSEX had
November12. Book diagnostics now distinguish missing depth, invalid schemas
and request failures. Normal samples are cached separately. PC viewer was
stopped at turn start and restarted. It later alternated healthy/reconnecting;
actual cash/market values sometimes exceeded freshness limits during collection.
Cash reads now target5seconds instead of30; no freshness threshold was relaxed,
and network/worker delays are not claimed to meet a guaranteed5second cadence.

Verification: full493 tests:489 passed/four platform skips; new normal replays
cover both indexes/directions, hedge-first entry, GTT readback, restart, profit
and pre-entry-window loss exit, Off/pause/paper/proof/manual controls, and exact
strategy group/fixed transport behavior. Follow-up cash cadence/failure test,
config/JS checks and offline demo passed. Synthetic provider evidence is not
real broker-write/GTT/child validation. No broker writes or owner intent changes.

Verified pre-rollout SQLite backup475250688bytes, integrity and SHA256
72c692a1531cbc34f7bb135aeb4bb2014178a474778238650cce87e8027718ca,
pre-observer-startup-fix-20261007.sqlite3. The earlier
pre-live-commissioning copy was used for diagnostic replay and is not a pristine
deployment backup. Rollout receipts will follow this section after verification.

Initial normal release9a12391 deployed with two Off groups, IDLE, null worker
fault and original campaign dates preserved. Allfive services active; archive
SYNCED. Verified the pristine backup's exact hash off-host,475250688bytes.
Actual REST cycle observed5.621seconds, but heartbeat publication followed
collection by over20seconds. Removed repeated stream attachment from each REST
cycle; attachment is now once per authenticated market client. This removes a
specific coupling to the research lock, not a guarantee of five-second cadence.
Follow-up35 startup/normal-runtime/stream regressions and JS validation passed.
Final release/actual cadence receipts follow after rollout verification.

Stream/research status publication now waits at most50ms per observation lock.
Busy panels show UNKNOWN/RESEARCH_STATUS_BUSY rather than blocking the entire
account response or fabricating confirmation. Two concurrent-lock tests verify
return while the research lock remains held;37 targeted regressions passed.
Tick research and its archive remain monitor-only and independent of execution.

Final rollout: immutable9d95eace2e7f87020c332a11c396e0149d9a8eb0 on Oracle;
PC source pin/assets refreshed and background viewer restarted. Only observer
restarted; allfive shared services active. Root/service checks confirmed paper,
pause, global Off, both groups Off, zero active owned-order rows and no activation.
Actual heartbeat advanced1→3→7; runtime error/worker fault null and phase IDLE.
The prior worker latch is absent after restart, without pretending its old
underlying cause was identified. Original campaign remains October7
11:46:38.519787 JST through November14 11:46:38.519787 JST.

Collection/viewer latency remains unresolved: the final PC snapshots alternate
HEALTHY/RECONNECTING and correctly label stale market/funds values unknown;
Normal remains WAIT for fresh snapshot/spot/funds. Neither lock isolation nor
the five-second cash schedule is claimed to prove continuous five-second data.
Do not weaken freshness to make it appear ready. Actual broker-write/persistent
GTT/generated-child commissioning also remains absent. Start still saves intent
and cannot bypass pause/paper/provider validation or stale data. Historical IV
is not a Normal blocker; the original research dataset requirement is preserved.

Private archive actual provider acknowledgments are SYNCED. Authenticated
read-only Supabase retrieval confirmed a normal-theta evaluation uploaded at
2026-10-07 05:31:11.027278 UTC. A first retrieval used the wrong category prefix
and returned no rows; corrected algo_state prefix confirmed the record. The
email preview contains both group names; no new email was sent or inbox delivery
claimed. Scheduled email remains19:30 JST. Served JS contains the normal rules
and two group labels. Owner Edge visual verification failed with the existing
ERR_BLOCKED_BY_CLIENT localhost restriction; no permissions were changed.
The noon heartbeat prompt was updated to the two groups and preserved campaign.
User drafts trading_report_receiver.py and pc_maintenance.py remain untouched.

## October 7 Start button and selected strategies

Verified the desktop HTTP Start/Stop and strategy-switch requests through the
fixed SSH command and Oracle command handler into the existing executor using
an isolated simulated broker. Only the selected executable strategy enters;
owner On and strategy switches persist across journal reopening. Repeated Start
only saves intent, and Off denies subsequent writes. These tests use synthetic
provider receipts; they are not actual broker activation or protection evidence.

The PC now rejects mismatched/missing Oracle intent acknowledgments. Start/Stop
transport failures return a sanitized503 response and appear as unconfirmed in
the app. Start waits while a strategy switch is being saved or the VM is unhealthy.
The dashboard lists selected entry strategies separately from selected monitoring
strategies and displays execution permission independently from owner On. Off also
shows current activation blockers before Start is clicked. Calendar never enters.

Local background viewer restarted and updated assets verified served. Actual
Oracle heartbeat advances, error is null, owner Algo and all four switches stay
Off. Oracle remains on immutable dc38ead5cc51d415e88bbe272427a40aeb6b2337;
this update changes desktop control confirmation/display, not deployed broker code.
The pause, paper mode, private activation requirements and original38-day campaign
remain unchanged. Real matched IV history/current provider inputs and actual
broker-write/GTT/child proof are still absent; strict scheduled event risk still
blocks entries. Clicking Start currently saves On and reports blocked readiness,
not live activation. No real orders or intent changes were sent during verification.

Verification: full suite448 passed/four platform skips, config and JavaScript
checks passed, and the offline synthetic demo completed with no broker connection
or email send. New desktop assets were verified served; owner Edge visual
rendering was not reverified in this update.


## October 7 matched-IV import and protection recovery

Added a private reviewed dataset/current-IV adapter and production revalidation
for NIFTY/SENSEX. Read MATCHED_IV_IMPORT.md. It verifies252 actual prior session
rows against an independently reviewed calendar, exact dataset/calendar hashes,
close-time/units, current provider/method/index identity and15-second freshness.
Production cannot use arbitrary feature-only IV bundles. No real dataset or
provider stream has been installed; the research proxy remains excluded.
Per-strategy/index code readiness and current live blockers are now explicit in
the existing viewer. Calendar still has no execution route.

Fixed uncertain GTT creation recovery to search its recorded original creation
day in all provider states. Trigger evidence overrides ACTIVE/cancel status;
COMPLETED requires child reconciliation. Exact returned parent references must
match. Unknown child identity still prevents another close. Old uncertain rows
without a creation timestamp remain unknown; they are not assigned guessed dates.

Actual Oracle read-only authentication and explicit28-day ACTIVE/COMPLETED/
CANCELLED GTT scans succeeded with zero returned parents. There is no real GTT
carry/child proof to validate, and no test order was created. Allfive shared
services remain active. Pre-change SQLite integrity/off-host hash verified:
pre-matched-iv-protection-20261007.sqlite3,114733056 bytes,
SHA256855722d4f02fe98d359b40c01d2a905e7265b4b9d0bff4a24858a7b0acb22b63.

Source investigation found Global Datafeeds' documented GetHistoryGreeks endpoint;
access,252-session/SENSEX coverage and matching30-day series construction remain
unverified. No subscription was purchased and no provider credential invented.
Strict38-day event-risk policy and original study horizon remain unchanged.
The three credit adapters are software implemented, not declared live-ready.
Rollout: immutable source dc38ead5cc51d415e88bbe272427a40aeb6b2337 deployed;
PC pin/assets refreshed. Only observer restarted; five shared services active.
Actual PC response verified advancing healthy VM, null runtime error, Algo Off,
four switches Off, no execution, matched-IV reviewed sessions0/current missing
for both indexes, three BLOCKED installed routes and Calendar RESEARCH_ONLY.
The original study start/end survived unchanged. This is a premarket check:
collection window has not started, so no intraday/current-source cadence claim.
New JS assets were served; owner Edge's actual visual rendering remains unverified.

Full suite436 passed/four platform skips. Final24 matched-IV checks passed after
adding exact per-session close times, including exceptional sessions, and a
prepared/current IV fingerprint check. Config, JS, diff checks and offline demo
passed. Synthetic replay is not actual broker-write/protection verification.

Post-source-update SQLite integrity/off-host hash verified:
post-matched-iv-protection-20261007.sqlite3,114733056 bytes,
SHA25694c63ccb8c5365cfec63d9c4e5fecc801afdff926d851e7fe4057a4f7afcfa41.
Actual deployed-source GTT audit completed12:12:40 JST with all three lists
AVAILABLE and zero records in the explicit28-day range. The viewer received that
actual scope/time; private archive status is SYNCED. These are read/diagnostic
results, not GTT/child or order-write proof.

Zero owned-order rows, activation absent and original pause present were checked.
No real orders, key rotation, broker permissions, LIVE or owner intent changes.


## October 7 readiness evidence and requested 38-day research

Read docs/READINESS_RESEARCH.md. Owner chose 38 calendar days of research instead
of changing the restrictive event-free holding-horizon gate. The three credit
routes are implemented but not provider-validated/live-ready; calendar is still
research-only. No owner intent or strategy switch is changed by the campaign.

Added official RBI/Fed schedule parsers and a latest-MoSPI-document/hash check,
dated ATM variance30 research observations for both indexes, a GET-only GTT list
audit and visible readiness/campaign progress. The proxy is never promoted into
strategy execution features. Actual near-close observations persist privately;
no missing sessions are filled and no 252-session history is manufactured.
Fixed the archive filter to include the report catalog's owned orders. Campaign,
IV day and source diagnostics use the existing private one-way Supabase outbox.

Actual Oracle authentication and three default-range GTT list reads succeeded
this morning, each returning zero orders. Empty reads prove API access, not
protection persistence or generated-child linkage. No parent/child/order was
created for validation. Broker-write/protection proof remains absent.

Pre-update SQLite backup and off-host hash verified:
`pre-readiness-20261007.sqlite3`, 114733056 bytes,
SHA256 `2b4da0060310ab9a9ed5676a4497b7719a0a84b00e57ce0752e397b854acf168`.
Deployment and actual source/campaign checks are recorded after rollout below.

Rollout: immutable source `b86a6aaeaea5033cd259177ff4eb9dfae8e003b1` deployed to
Oracle; PC source pin/assets refreshed. Only observer restarted. All five shared
services remain active. Actual private campaign start October 7 11:46:38 JST,
end November 14 11:46:38 JST. Restart preserved that period. Zero observed
market days so far; ordinary session collection has not begun at this premarket
check. No collected research or backtest success is claimed for that schedule.

Actual new-source calendar check passed RBI, FOMC and the latest reviewed MoSPI
document with no missing sources; six scheduled risk dates in the horizon yield
EVENT_RISK, not clear. New-source authentication and three GTT list reads passed,
all empty in the provider's default date range. No protection/child proof exists.
PC response: healthy advancing VM, null runtime error, global Algo Off, all four
switches Off, execution false. Readiness JS served; Edge visual rendering remains
unverified. Private archive is SYNCED with actual provider acknowledgments for
the campaign/source diagnostics. Fresh IV/Greek collection remains market-hours
verification, not a completed run. Noon heartbeat prompt updated for the study.

Final full suite406 passed/four skipped; last archive timestamp change17 focused
passed. Config/JS/diff checks and synthetic offline demo passed. No real orders,
mail sends, mode/permission/key changes or activation were performed.

Post-update SQLite integrity/hash verified and copied off-host:
`post-readiness-20261007.sqlite3`, 114733056 bytes,
SHA256 `4d3b0c68791529692d16188c904cccff92080bc39b5327c9ecfc8587c6a34355`.
Actual Supabase SELECT confirmed one uploaded campaign, one official-calendar
record and one broker-read audit. No new IV day records exist at this premarket
check. Pause present, activation absent, zero owned-order rows verified again.

## October 7 guarded report execution and per-strategy controls

Owner requested finishing software setup while retaining Off until the owner
clicks Start. Three same-expiry report basket routes now share the existing
PremiumExecutor, transport, lock, journal slot and persistent protection; the
calendar remains monitoring-only. Four private revision-checked owner switches
default Off and persist. Disabling a held strategy stops new entries, preserving
owned management while global Algo is On; global Off denies all engine writes.
Read ORACLE_EXECUTION.md for the full staged entry/protection/exit contract.

Missing data was confirmed by the owner: no IV history/event source is available.
Owner approved continued monitoring if public sources cannot be verified. NSE
India VIX is not substituted for matched SENSEX/contract IV history. Dated sampled
contract IV/Greeks/books now join the private one-way Supabase archive. Fixed a
real reader schema error that had rejected valid books containing Greek metadata;
strict book validation now precedes adding separately dated Greeks.

Real provider order/GTT/generated-child verification remains pending. Setup does
not fabricate activation proofs, enable LIVE, remove pause or send test orders.
Actual production remains Off/paper/paused with no owned orders. A Start click
alone cannot overcome these validation/data blockers. Calendar settlement risk
and out-of-sample strategy validation also remain unfinished.

Final immutable Oracle source is `12b4cb13939b63438fa2764079c7a3ae565d276d`;
PC protected transport pin and assets refreshed. Heartbeat advances, runtime
error is null, all five shared services active. Actual global owner Off, four
switches Off, execution false, pause and paper preserved, zero owned-order rows.
All four protected no-op Off submissions returned UNCHANGED on Oracle. No
activation proof or real order was created. Automated owner Edge loopback
navigation remains ERR_BLOCKED_BY_CLIENT; rendered UI is unverified, not passed.

Actual read-only deployed execution-source check passed authentication, both
index quotes, positions/orders and funds; the final source follow-up changes
only visual reporting. Normal continuous collection remains scheduled from
12:40 JST and had not started at this morning check. Sampled market-hours
Greek/book/margin cadence still needs actual verification.

Full suite393 passed/four skipped (397 collected). Final execution-source
retest24 passed; visual-mail follow-up42 passed/two skipped. Config/JS/compile,
diff checks and synthetic offline demo passed. Replay covers both indexes,
three credit structures, exact protection/child races, staged condor cash,
restart, partial fill, switch race, manual protection, expiration and CSRF.
These are not real provider order/protection or strategy profitability proofs.

Pre/post backups passed SQLite integrity. Final private off-host copy matches
`e13984443ad214b0ff7c35cb27574ad0b54b2c94a1dc2cff9a93a5192b17b2f6`,
114,733,056 bytes. Private final email preview contains all four strategy names
and selection badges; no test email sent. Scheduler remains19:30 JST. The email
now distinguishes On/blocked from actual permission to write; no fabricated
inbox delivery. Existing October6 recipient-server receipt remains historical.
Supabase archive SYNCED; actual readback122 evaluation versions at02:11 UTC.
The new contract-observation export is configured, with no current contract
rows yet before collection; do not claim a completed Greek-data upload.
No capital/balance/credential fields were added to the cloud archive. Noon
automation instructions now describe the guarded routes and remaining blockers.

## October 7 read-only connections and explicit owner Start

Owner requested connecting the components while retaining Off until an explicit
Start click. Deployed source `14e88733d8ff41e6b0033c75a12541dade23deac` to Oracle
and refreshed the PC's protected transport pin. New report_data bridge connects
the five-second collector snapshot to the studies; independent minute research
reads fetch prior daily OHLC, exact API/master expiry intersections, chain Greeks,
books and bounded hypothetical basket margins. Actual response metadata accepts
1440-minute daily bars and1day explicitly;120-day requests respect the verified
180-day provider limit. Unavailable next-year expiries remain separate unknown
coverage, not invented dates. Greek timestamps expire independently of books.
Research errors do not change owner intent or stop the account collector/mail.

Initial Oracle authentication failed. Owner signed in and approved the existing
key; owner Edge then visibly showed Codex Approved, resetting6AM tomorrow.
No key, permission or static-IP change was made. Corrected isolated real read-only
preflight passed authentication, both quotes, complete positions/orders, money,
current expiry/master intersections and81 prior completed daily OHLC sessions
per index. Before market open no valid sampled option books passed validation;
market-hours Greeks/books/margin cadence remains unverified. Current IV30,
252-session matched IV history and dated event evidence still need a verified
source. Studies WAIT, MONITOR_ONLY; no fixture/isolated read journal was imported
into production. Collection remains scheduled12:40–19:45 JST; the actual
continuous collection run for today's market session has not yet occurred.

Full suite365 passed/four skipped (369 collected); subsequent real-response fixes
passed33 focused tests. Config/JS/compile and offline synthetic demo passed.
Isolated tests with otherwise valid activation prove Off denies ENTRY, ROLL, EXIT
and PROTECT capabilities. Only an explicit protected boolean owner command changes
intent, which persists across restart. Production remains owner Off/paper/paused,
zero owned-order records. Start saves On intent; new strategies remain monitor-only
and cannot bypass missing research inputs or pending live provider validation.

Oracle heartbeat advances, PC connection is healthy, Supabase evaluation archive
is SYNCED with no pending rows and direct readback remains MONITOR_ONLY. All five
shared observer/Qwen/mail/tunnel services remain active. Pre/post SQLite backups
passed integrity; final private off-host backup hash verified:
`e924050d47812e11cd6b3c2324eed2832658ec17e56103b5c7637818c88feca7`,
114,733,056 bytes. Existing19:30 daily email scheduler/settings are connected;
October6 receipt records provider and recipient-server acceptance, not inbox
verification. No new email/order was sent by these checks. Private preview
contains four current strategy rows. Fresh rendered owner Edge dashboard is
still unverified because automated loopback navigation was blocked.

## October 7 report-based strategy replacement

Owner requested strategies from the supplied SPX/ES research report, replacing
overlapping originals. Current NIFTY/SENSEX catalog: bull put, bear call, iron
condor and calendar; MONITOR_ONLY, not executable or backtested. Read
REPORT_STRATEGIES.md. Source hash, exact declared hypotheses, point-in-time
features, short30–45 DTE / calendar60–90 DTE, half-credit exit studies, ≤7 DTE
exit and bounded margin/risk comparisons are documented. Fictional US Sharpe/win
tables and inconsistent directional examples are excluded. Calendar loss/payoff
model remains unknown. No naked short straddle, GEX guess or news-worker restart.

New Everyday/Late-session entries/rolls are explicitly retired in the production
gate and preparer. Original journals, attribution, owned exits/protection and
manual trades are preserved. The catalog is required; combined policy hash binds
research and legacy management. Current dashboard/email list has only the four
new research strategies with unknown performance. Research reevaluates private
stored evidence every30 seconds without affecting mail when inputs fail; versioned
evaluations reuse the private Supabase outbox as algo_state/research state, never
orders or cloud commands. Empty/stale/insufficient history/calendar inputs WAIT.
No verified252-session matched Indian IV history or continuously provisioned
event calendar exists yet; acquire these and run chronological out-of-sample
replays before proposing execution. Feature replay/import CLI is documented.

354 tests passed, four platform skips (358 collected); focused24 tests, config,
JS/compile checks and synthetic offline demo passed. Oracle pre-update journal
backup integrity verified; zero algo-owned order records. Pause/paper/actual owner
intent and shared services remain unchanged. Noon heartbeat prompt updated to
the new catalog, preserving its schedule and quiet notifications.

Deployed immutable source `6c225f9649e11f65ebf200c6a011a01931b9fa25` to Oracle
and refreshed the protected PC viewer interpreter pin. Only trading-observer
was restarted; all five shared observer/Qwen/mail/tunnel services remain active.
Current loopback API confirms four new rows, MONITOR_ONLY, owner Off, execution
disabled, no runtime error, healthy VM and heartbeat advancement from 35 to52.
Before collection starts at12:40 JST, saved market values are stale and correctly
unknown. Both indexes WAIT for actual inputs; no fixture was imported into the
production research journal. Archive is SYNCED with zero pending; direct private
Supabase readback verified five evaluation versions at10:11 JST, all MONITOR_ONLY.

Pre/post SQLite backups passed integrity. Matching private off-host post copy:
114,733,056 bytes, SHA256
`163b75a951205c12607907f93db437456b18df2a229a7ea006b92d8d90ad4af8`.
Private generated email preview contains all four strategy names and unknown
results. No diagnostic email was sent; today's scheduled19:30 send, provider
acceptance and inbox delivery are not yet verified. Owner Edge automation refused
loopback navigation with ERR_BLOCKED_BY_CLIENT; no fresh rendered-dashboard
verification is claimed. Dashboard API and generated email HTML are the evidence.

## October 6 executor connection and lifecycle preparation

Owner authorized items 1–4 without autonomous activation. The existing executor
connected automatically at the configured 12:40 JST collection start; its earlier
connection blocker was a pre-market wait, not missing wiring. Runtime now exposes
CONNECTED / WAITING_FOR_COLLECTION_WINDOW / ORACLE_BROKER_CONNECTION_RETRY and
the independent paper/pause/Off/activation/freshness gates. Unexpected worker
exceptions latch a sanitized reconciliation blocker instead of killing the
thread or retrying an uncertain write. Outside collection hours the monitor reads
the timestamped saved snapshot, avoiding an unbound/stale loop-local sample.

Added gated 19:00 confirmed-expiry owned short-first exit. The other-index
successor remains a proposal behind the existing new-entry cutoff; pre-14:00
roll execution is also not enabled. Existing carried-basket stop exits are
replay-tested before 14:00. Tests additionally cover unknown-expiry no-exit,
no successor submission, pre-market diagnostics and worker-failure freeze.
Actual SDK wire-format and isolated partial-fill/GTT/child/trail/roll lifecycle
tests do not prove actual provider writes. Real broker acceptance, persistent
GTT carry validity and generated-child identity remain unverified; no real orders,
activation proof, LIVE change, pause removal or manual-position action occurred.
See ORACLE_EXECUTION.md for the remaining provider-validation boundary.

Deployed immutable source `c5985dad31813c424c07affcffb05d78523e7518` to Oracle
and refreshed the PC viewer's protected transport pin. Full suite: 335 passed,
four platform skips (339 collected); config/JavaScript checks and offline demo
passed. Current API reports CONNECTED, healthy heartbeat advancing from sequence
2 to 9, owner Off, execution disabled and archive SYNCED. Shared five services
remain active. Pre/post journal backups passed integrity; private off-host post
copy is 45,580,288 bytes, SHA256
`a71d82f0ada3893ab2bfb9f01c980f3a485d16539be46e9fb97bc2fba992c478`.
No fresh owner Edge visual check was performed; dashboard API is the evidence.

## October 6 Premium Impulse Monitor and private Supabase archive

The attached multi-threshold research specification applies to **both NIFTY and
SENSEX**. Read PREMIUM_IMPULSE_RESEARCH.md for exact storage fields, thresholds,
30-second ATM/expiry locks, duplicate/gap/restart behavior, provisional labels,
120-second objective outcomes, conditional summaries and offline replay.
It is MONITOR_ONLY and never calls or influences the executor. Missing fields,
IV regimes, aggressor flow and incomplete outcomes stay null/UNKNOWN. The older
weighted score remains exploratory, not an optimized threshold or entry gate.

Existing Oracle journal is authoritative. A separate durable outbox privately
upserts raw research chunks/events/outcomes/summaries and journal-owned algo
order/state versions and algo-only gross P&L into the existing Growing-Trader
Supabase project's `trading_research_records`. No manual order/capital ledger or
credentials are exported. RLS enabled, PUBLIC/anon/authenticated access revoked,
server SELECT/INSERT/UPDATE verified. Existing server settings were securely
merged from approved root0600 call-seller.env into observer.env without rotation.
Archive retries do not affect collection or trading. Dashboard shows archive
pending records and local uncaptured ticks; server credentials never reach PC.

Implemented and deployed immutable source
`7f566c19c9eb84af176a7d38d11ac4d14e7676d8` to Oracle and the PC viewer.
Isolated pinned-SDK suite: 330 passed, four platform skips (334 collected).
JS syntax, config validation, offline demo (two successful synthetic analysis
jobs) and replay checks passed. Ordinary Python found user-site SDK1.2.0; use
`-I` for SDK1.5.0 as production already does.

Owner Edge showed the existing Codex API key Expired; its authorized Approve
action renewed it to Approved. Actual Oracle authentication and NIFTY/SENSEX
quote reads then passed. Socket preflight connected with 41 instruments and
75 acknowledged NATS topics. The observed exact same-origin canonical socket
auth route is requested directly; authentication redirects remain disabled.
This pre-market test received zero ticks: market-hours cadence, field population
and the 2–3-second confirmation target are still unverified.

Private Supabase migration `trading_private_research_archive` applied; RLS and
server-only grants verified, existing server-key HTTP200 read passed. Cloud
readback contains 3,846 algo-P&L observations and two algo-state records, with a
content hash matching Oracle. Last provider acknowledgment was 11:59:56 JST;
dashboard archive SYNCED, zero pending/uncaptured at 12:04 JST. These are recorded
observations, not executed trades: the journal owns zero orders. Real research
tick/event/outcome capture and the first daily summary await market data.

Pre/post SQLite backups passed integrity checks. Post backup
`post-premium-research-20261006T030127Z.sqlite3` is 43,347,968 bytes; the private
off-host copy has matching SHA256
`fe73283e173bb59ef1d2acfbc8f8ccccd39a1b0f0b514f409bd788d4468e869c`.
Only observer/viewer restarted; all five observer/shared services are active.
PC dashboard API verified; a new Edge dashboard visual check is not recorded.
Pause, paper mode and owner Off remain intact. No broker orders were written,
no activation proof created, and no credentials rotated.

October 6, 2026 · `Logan17de/Trading` · `main` · `D:\Money Trader\Trading`.
Read AGENTS.md, PREMIUM_STRATEGY.md and HEADLESS_INTEGRATION_STATUS.md.
The active owner policy is premium v3; historical v1/v2 studies are provenance.

## October 6 streaming premium impulse implementation

Added an independent read-only GrowwFeed price/depth observer in the existing
Oracle runtime. Five-second +₹10 NIFTY/+₹30 SENSEX ATM premium impulses are
scored with owner weights 25/20/20/15/10/10. Confirmation is 80/100 with a
2–3-second target and ten-second provider-time deadline. Unknown signals stay
UNKNOWN, not normalized; futures/breakout/OFI/neighbours must agree. Sixty-second
warm-up and ≥5 prior-session time-of-day volume samples are needed. Breadth is
an explicitly sampled equal-vote major-stock proxy, not full index breadth.
Read PREMIUM_IMPULSE.md for precise thresholds, data dependencies and limits.

The callback worker has no five-second timer and never invokes the broker
executor. Exact feed authentication is allowed separately from order routes;
ephemeral SDK credentials stay in private 0700/0600 state. Confirmations persist
privately. Fixed Unix-socket watch → persistent protected SSH → loopback SSE
updates the PC detector display independently of regular five-second account
polling. No new GUI/framework/service and no public port. Live event reception,
volume population and measured network latency require actual stream evidence;
synthetic tests do not prove live latency or profit probability.

## October 6 percentage rollover clarification

Owner clarified rollover as a 60% reduction from the configured index premium
target: NIFTY ₹20 → ₹8; SENSEX ₹80 → ₹32. At or below the threshold, propose
closing the original short then the next listed short with executable premium
above that index threshold. The formula uses the index target, not the actual
fill or the current weekday's preferred index. Shared hardcoded ₹8 selectors
were removed from policy review, preparation, controller and dashboard wording.
Config uses `roll_reduction_pct: 60`; dashboard readiness exposes both derived
thresholds. Stop/trail remains ₹1,000, hedge improvement strictly >₹100, one
basket/two-lot cap and manual protection unchanged. Real writes remain paused.
Deployed immutable source `6598b7b95e70fd8422011bb569f5e56efa27858b`; 63 relevant
tests, JavaScript/config checks and offline demo passed. Remote derived thresholds
verified ₹8/₹32. Pre/post integrity-checked SQLite backups made; only observer
and viewer restarted. Shared services active, pause/Off preserved. Current Edge
visual confirmation not performed; dashboard API is the verification evidence.

## October 6 expiry handoff proposal

Owner requested expiry-basket close at 19:00 then switching indexes. The public
read-only position review now proposes close short before hedge on confirmed
actual expiry, followed by SENSEX ₹80 if NIFTY expired or NIFTY ₹20 if SENSEX
expired. The successor requires confirmed flat, actual nonexpiring-index evidence,
fresh margin/books and confirmed exchange session. Both-expiring/unknown evidence
blocks the successor. This supersedes the older no-timed-review description.
Manual/mixed/stale positions wait; no state or broker writes occur in this review.
Real-money timed closing and the 19:00 new-entry exception are not enabled.
Deployed immutable source `e8602aeb892bf9768e8c4219ee1f110f4d3b3253`; 40
policy/controller tests, config/JavaScript checks and offline demo passed. Remote
synthetic handoff also verified with no broker writes. Pre/post journal backups
passed integrity checks; shared services remained active, pause and Off retained.
PC transport/assets refreshed; current visual Edge verification remains pending.

## October 6 stop threshold clarification

Owner reduced basket loss trigger and trailing distance to ₹1,000. Policy,
validation, stop calculations, dashboard label and current guide now agree;
historical ₹2,000 rollout records below describe the former policy. New source
is `565a137307b7b516e3b165e32baefbdce5038ae2`. There is still no expiry timed
exit or fixed take-profit: 19:00 is an entry cutoff, not a liquidation rule.
Expiry accounting needs actual broker-flat/order-terminal/protection evidence,
not an invented settlement fill. Maintenance preserves paper, Off and pause.
58 relevant tests, JavaScript/config checks and offline demo passed. Pre/post
SQLite integrity-checked backups were taken. Observer and PC viewer now use the
new immutable release; shared services stayed active. Real-money writes remain
disabled. Remote policy verified ₹1,000; fresh owner Edge visual check pending.

## October 6 carried-position review update

Deployed source `fc0e4f09e543a1c64769cfaddc5458e4d8cfc8ed` adds a read-only
`execution_controller.position_review` to the dashboard. Exactly owned, fresh
Everyday baskets can propose rollover at or below ₹8 from 12:45 JST, before
the 14:00 fresh-entry window. Basket loss/trailing exit reviews take precedence;
stale/incomplete/manual/mixed evidence waits. Reviews run independently of owner
Off/pause, do not create orders, and do not mutate basket state. Pre-14:00 live
roll execution remains disabled; activation gates are unchanged.

36 policy/controller tests passed with isolated pinned SDK, including pre-entry
reviews under pause, stop priority and manual-position rejection with zero writes.
JavaScript syntax, config and synthetic offline demo passed. SQLite backup and
integrity verified before rollout. Existing isolated credentials reused, no
rotation. Only read-only trading observer and PC viewer restarted; shared services
remained active. PC transport now points to this immutable release. Latest view
was checked through the API, not newly visually verified in owner Edge.

## October 6 VM viewer check · 10:14 JST

Owner requested VM-hosted strategy monitoring with PC visualization after the
autonomous live activation request was declined. No activation was performed.
The existing deployment already provides this connection; no new service is
needed. The local dashboard returned source Oracle VM / Groww read-only,
collector host ORACLE and a healthy heartbeat advancing from sequence 11168 to
11175. Both Everyday and Late-session appear, with no invented strategy outcomes.
Trading observer and shared Qwen/mail/tunnel services are active. Desktop,
Start-menu and Windows sign-in shortcuts point to the current PC app entry.

The viewer can be opened any time while the PC is online and Oracle reachable.
Five-second collection targets the existing weekday 12:40–19:45 JST window;
before that window, prior market data must remain stale/unknown. Current owner
intent remains Off, order writes disabled and pause preserved. Monitoring and
strategy preparation do not constitute live execution. This check inspected the
dashboard API, not a new owner Edge visual confirmation. Today's daily email
shows scheduled; provider acceptance and inbox delivery are not yet verified.

Relevant premium policy/controller, PC control and dashboard tests passed using
isolated Python (`-I`), with the pinned Groww SDK 1.5.0. An initial non-isolated
run imported a Windows user-site SDK and failed the exact scoped-request test;
the pinned isolated rerun passed without changing request protections. Installed
viewer startup already uses `-I`. Configuration validation and the synthetic
offline demo passed; the demo sent no email and made no real orders.

## October 5 controller rollout · 18:35 JST

Oracle now runs immutable source `1ad924810b8531346a20c4b2297c03e5408ca983`.
The existing runtime contains the durable entry/GTT/trailing/roll/exit/expiry
controller; no new framework/service/public port. 283 local tests passed, four
POSIX skips; exact-release Linux/Windows CI passed (run37290829567). Offline demo,
config, compile and JavaScript checks passed. Existing DPAPI and Oracle keys were
reused without rotation. Pre/post SQLite backups verified; the 35,397,632-byte
post-update off-host hash matched. Only trading-observer was restarted; shared
Qwen/mail/tunnels remained active. Private desktop transport/assets were refreshed.

Actual SDK1.5.0 authentication and authenticated smart-order reads passed at
18:15 JST. A sampled manual detail GET returned GA004; the complete order-list
fallback verified an exact ID and actual field types at 18:39 JST. Current quotes,
positions, orders, money and private execution evidence are available. Heartbeat
advancement and capital summary survived rollout. Zero owned orders; owner Off,
paper environment, pause intact, no activation proof and no real order writes.

The dashboard API reports code installed, Idle and explicit activation blockers.
The automated owner Edge preview was blocked by ERR_BLOCKED_BY_CLIENT; current
visual confirmation was requested from the owner. Do not claim this latest view
was visually verified. Earlier capital/email visual evidence remains historical.
Daily mail remains scheduled for 19:30; today's first scheduled run is still
pending at this check. Provider acceptance/inbox evidence are separate.

Controlled real broker/GTT/generated-child verification and owner activation
remain pending. Read ORACLE_EXECUTION.md before considering activation. The
button saves intent; it cannot bypass pause, mode or exact release/policy proofs.

## Architecture

Oracle hosts the existing five-second read-only Groww collector, SQLite journal,
premium policy monitor, read-only basket preparation and visual mail outbox. The PC
loopback app displays/control-requests Oracle through private SSH and a fixed
Unix-socket client; no public trading port. Keep Oracle running continuously for
Qwen/Colab/mail/tunnels. The old trader remains disabled and paused.

Owner On/Off is durable. Only explicit button requests change it. Restarts,
windows and faults retain On but separately block trading readiness. The actual
broker controller is implemented, but live provider validation and activation remain
pending: On must never be described as trading activated while blocked.
Only Everyday and Late-session appear in strategy results. Manual/unknown/mixed
contracts remain protected. Read exact premium/expiry/roll/stop rules in the guide.
Owner retired news on October 5: no active fetch worker, entry gate or news UI.
The noon API/VM automation was updated to preserve that choice.
Read docs/EXECUTION_PREPARATION.md. The independent minute worker samples actual
chain/master/books and hypothetical basket/hedge/exit margin/cost calculations.
It labels sampled scope; a prepared comparison is never an order or global optimum.
PreparedOrderGateway is replay-only, persists intent before a simulated write and
reconciles original references/fills without duplicating uncertain submissions.
The replay-only gateway still rejects real transport. The separate Oracle gateway,
durable one-basket controller, persistent GTT and trailing/roll/exit/replacement
paths are now implemented in the existing runtime; read ORACLE_EXECUTION.md.
The pause and actual owner Off are retained. Every network write needs an exact
thread-local capability and private release/policy-bound activation evidence.
Preparation deployed October 5 at approximately 17:19 JST as immutable release
`492c7355827fac857aa14d72269c74703f22de9d`. Groww's actual hypothetical basket-margin
API was verified independently at 16:59:50 JST; no broker order routes were used.
Missing/protected books and prices expiring during calculations have separate
readiness reasons. Freshness remains strict; no live selected strategy or
persistent protection has been verified. 259 tests passed, four platform skips;
Ubuntu/Windows CI passed for the deployed release (run37282655954). Pre/post
SQLite backups verified; 30,027,776-byte post-deployment off-host hash matched.
Shared Qwen/mail/tunnels stayed active, owner Off, zero owned orders/withdrawals.

## Display/accounting

Actual line observations persist across restarts; five-minute grids, responsive
labels, no fabricated points or outage bridges. Active option positions only,
with evidence-based entry/SL/target lines. Today's gross index-option P&L splits
Self / exact Algo / Unassigned, before charges. Stale/incomplete/carry-day basis
stays unknown. Groww cash, used margin and option-buy/sell balances are separate
from P&L/investment value. Net strategy results still need the reviewed ledger.

Capital accounting uses private Oracle SQLite meta `capital-ledger-v1`, provisioned
once from approved stdin rather than tracked configuration. Dashboard, preview
and daily email share `capital_summary`: invested, withdrawn, accumulated API fees
and remaining capital. Fees start in October 2026, once per month at the first JST
day, catching up missed months. The small Record withdrawal form sends a dated
owner record through protected loopback POST and fixed SSH/socket accounting
command. Idempotent request IDs survive retries/restarts; conflicts are rejected.
Unconfirmed browser requests persist in local owner-profile storage; original
amount/date stay locked until acknowledgment or a definitive validation rejection.
This does not transfer money, change broker cash, P&L, margin or trading readiness.
Cached prior-month totals wait for Oracle's fee update; no desktop fee booking.
Deployed and verified October 5 at 16:00 JST: observer release
`b5a54faa59370771320bb822e745527fd19913ec`; all four amounts in owner Edge and
email preview. Production has October's fee and zero withdrawal records. The
November arithmetic check used an isolated fixture, never a future production
charge. Pre/post journal backups verified; post-update off-host hash matched.
238 tests passed, four platform skips; Ubuntu/Windows runtime CI passed.

## Schedules and faults

- Visual email: attempts start 19:29 JST, targeting by 19:30 every calendar day; frozen retry outbox; existing
  approved sender/recipient. Provider acceptance, recipient-server acceptance and
  inbox evidence are distinct. Missing values stay unknown.
- Existing heartbeat `trading-news-and-barrier-check` is now named **Trading noon
  API and VM check**, ACTIVE weekdays **12:00 JST**. In owner's Edge, approve only
  existing Codex key when Expired, verify Approved and retest read-only access.
  Wakeups are best effort; actual completion needs evidence. The old 12:55 barrier
  follow-up is superseded, not duplicated.
- PC liveness uses SSH plus advancing heartbeat/sequence. After stale/unreachable detection and three bad reads, alert by approved mail;
  deduplicate incidents through restarts, require 60 healthy seconds for recovery, and retry mail
  failures. PC must be awake, signed in and online. No always-on external cloud
  outage monitor is installed. Never auto-reboot/shut down Oracle. Retain On.

## Deployment/private state

Immutable root-owned releases: `/opt/growing-trader/releases/<commit>`.
Read-only service: `trading-observer.service`; state `/var/lib/trading-observer`,
socket `observer.sock`, journal `.agent-state/pc-monitor.sqlite3`. Original
approved mail source `/etc/growing-trader/call-seller.env`; isolated root0600
`observer.env` reuses existing Groww pair/mail settings without rotation. The PC
vault keeps a DPAPI mail copy for independent alerts. Never print these values.
Private PC transport: `.agent-state/oracle-viewer.json`; SSH key remains in its
original protected location. Preserve original journals, exact ownership and all
pause markers. SQLite backups use its backup API; restore only to a new path.

## Remaining blockers

Real broker acceptance, persistent protection and generated-child reference linkage
still need controlled live validation before activation. Entry/protection/trail,
replacement/roll/exit orchestration are implemented, with full synthetic sessions
and uncertain-write/partial-fill/cancellation-race/restart tests. These are not proof
of actual broker behavior. The public GTT schema omits generated-child linkage; no
guessed child adoption or duplicate close is allowed.
No validated profit probability or guaranteed income exists.
Windows reboot/sign-in and external cloud monitoring are unverified. Inbox
receipt requires mailbox evidence even after provider reports delivery. Dated
status records actual tests, deployment and receipts, separately from schedules.

Preserve `.trader-paused`, `.secrets`, private journals, production Supabase keys/
ciphertext and shared Oracle services. Never place/modify orders, enable LIVE,
rotate keys, change broker permissions, merge or shut down Oracle in a check.

October 5 deployment and visual-preview inbox verification are complete; the first
real scheduled EOD email remains later today. See the current status table for
exact release, test and backup evidence. The persistent On setting still cannot
place orders while the pause/LIVE/provider-verification gates remain blocked.
