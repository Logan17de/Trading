# Current Trading handoff

October 5, 2026 · `Logan17de/Trading` · `main` · `D:\Money Trader\Trading`.
Read AGENTS.md, PREMIUM_STRATEGY.md and HEADLESS_INTEGRATION_STATUS.md.
The active owner policy is premium v3; historical v1/v2 studies are provenance.

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
