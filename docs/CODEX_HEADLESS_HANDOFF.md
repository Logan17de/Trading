# Current Trading handoff

October 5, 2026 · `Logan17de/Trading` · `main` · `D:\Money Trader\Trading`.
Read AGENTS.md, PREMIUM_STRATEGY.md and HEADLESS_INTEGRATION_STATUS.md.
The active owner policy is premium v3; historical v1/v2 studies are provenance.

## Architecture

Oracle hosts the existing five-second read-only Groww collector, SQLite journal,
premium policy monitor, independent RBI/ET news and visual mail outbox. The PC
loopback app displays/control-requests Oracle through private SSH and a fixed
Unix-socket client; no public trading port. Keep Oracle running continuously for
Qwen/Colab/mail/tunnels. The old trader remains disabled and paused.

Owner On/Off is durable. Only explicit button requests change it. Restarts,
windows and faults retain On but separately block trading readiness. The actual
broker executor is unfinished: On must never be described as trading activated.
Only Everyday and Late-session appear in strategy results. Manual/unknown/mixed
contracts remain protected. Read exact premium/expiry/roll/stop rules in the guide.

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
This does not transfer money, change broker cash, P&L, margin or trading readiness.
Cached prior-month totals wait for Oracle's fee update; no desktop fee booking.

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

Real broker execution, initial protective orders, two-leg partial-fill/crash
recovery, actual margin/quoted candidate ranking and execution replay sessions
remain unfinished. No validated profit probability or guaranteed income exists.
Windows reboot/sign-in and external cloud monitoring are unverified. Inbox
receipt requires mailbox evidence even after provider reports delivery. Dated
status records actual tests, deployment and receipts, separately from schedules.

Preserve `.trader-paused`, `.secrets`, private journals, production Supabase keys/
ciphertext and shared Oracle services. Never place/modify orders, enable LIVE,
rotate keys, change broker permissions, merge or shut down Oracle in a check.

October 5 deployment and visual-preview inbox verification are complete; the first
real scheduled EOD email remains later today. See the current status table for
exact release, test and backup evidence. The persistent On setting still cannot
place orders while the executor is unfinished and the pause remains.
