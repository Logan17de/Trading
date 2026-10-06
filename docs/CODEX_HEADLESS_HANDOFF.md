# Current Trading handoff

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
