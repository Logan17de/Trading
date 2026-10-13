# Owner's exclusive safety-email policy (October 13, 2026)

**Only these four classes may generate automatic email:** Oracle VM outages, Groww
authentication failure, broker trade-entry/exit failure, and stop-loss/protection
failure. No routine EOD email, market commentary, research failures, fund-read
problems, or housekeeping email. Manual test emails must be explicitly requested.
No alert process can enter, modify, cancel or exit a trade.

## Schedule (JST)

| Verified issue | With active or unverified exposure | Confirmed flat / no active trade |
| --- | --- | --- |
| Oracle VM unreachable / heartbeat stuck | Initial detection; repeat **every 20 minutes** while unresolved, 24/7 | Initial detection; repeat **every 2 hours**, during regular market notification hours only |
| Groww authentication 401 / 403 / GA005 | **12:00 and 15:00 JST** only, maximum one per checkpoint, if a fresh authenticated probe actually failed | Same |
| Entry / exit request failed or reconciliation required | Initial detection; repeat **every 20 minutes** while unresolved, 24/7 | Initial detection; repeat **every 2 hours**, during regular market notification hours only |
| Current protective stop missing/unknown for a live engine-owned basket | Initial detection; repeat **every 20 minutes** until reconciled, 24/7 | No false SL incident if no owned active basket |

The two-hour idle window is **12:45 through 19:00 JST** on a reviewed 2026
regular Indian trading weekday. Holiday and new-year calendars need annual
review; no automatic inference of an unfamiliar market date. A genuine
active/unknown-exposure alert **ignores** routine quiet hours.

"Unknown" exposure is **not** interpreted as flat. On VM loss the last
verified position state is retained, marked as historical, and a missing
current broker read is clearly described. Cloud sends a VM-down warning without
claiming the protective orders were filled or the account was flat.
The notification provider's **acceptance** is not proof of inbox delivery.

### Oracle / local watchdog

- `auth_probe.py`: a read-only Groww profile check occurs at the 12:00 and
  15:00 JST checkpoint. Unauthorized status must be **observed** (HTTP 401,
  403, or Groww GA005); unrelated connection errors remain UNKNOWN.
- `failure_alerts.py`: other incidents remain visible in diagnostics, but the
  outbound mail allowlist excludes them. The persisted journal records accepted
  rounds with distinct per-round Resend idempotency keys, so successful alerts
  repeat on schedule and ambiguous retries cannot create duplicate orders.
- `report_loop`: still evaluates research and capital accounting, but **no
  longer sends/queues EOD email**. Existing reports and mail receipts are
  retained, not deleted.
- `oracle_link.py` and `vm_alert.py`: Windows-PC fallback emits timed VM
  reminders. PC alarms cannot work while that computer is sleeping/offline.

### Cloud independent VM monitor

**Implemented in the Growing-Trader Supabase project**

- RLS tables: `public.trading_safety_state` and
  `public.trading_vm_safety_outbox`.
- Private service-role-only atomic claim/ack RPCs, protecting sender retries and
  the 20-minute/2-hour schedule across serverless invocations.
- Authenticated Supabase Edge Function: `trading-vm-safety`, deployed with
  `verify_jwt=true`. A VM heartbeat requires only the existing private Oracle
  `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` and contains no account
  identifiers or trading payload.

**External alerting is NOT yet active.** The Edge Function intentionally
returns `ALERT_TRANSPORT_UNCONFIGURED` until these three secrets are set in
Supabase Edge Function Secrets **directly through the dashboard**:

- `TRADING_SAFETY_RESEND_KEY` = an approved Resend API credential
- `TRADING_SAFETY_FROM` = verified sending address
- `TRADING_SAFETY_TO` = approved destination email

Do **not** paste any secret, Groww key or service-role token into chat or Git.
Use Supabase's dashboard secret manager.

Then enable the Supabase **pg_cron** and **pg_net** extensions and schedule
`trading-vm-safety` **every minute** using an authenticated invocation.
Follow Supabase's *Scheduling Edge Functions* documentation, storing a legacy
JWT scheduler key securely in Vault for `verify_jwt=true` (or migrate
explicitly to Supabase's documented secret-key auth before changing this
function's configuration). **Do not schedule an unauthenticated function.**

After deployment, verify a recent Oracle heartbeat, then **test on a day with
confirmed flat broker positions**, including cloud claim, Resend acceptance and
receipt. Only after cloud outage notifications work reliably, set the
**Windows-PC viewer** environment variable
`TRADING_CLOUD_VM_ALERTS_PRIMARY=1` to suppress duplicate fallback VM mails.
Do not turn off the local runtime's entry/exit/stop protection alerts.

## Mandatory live-trading gates still outstanding

This notification work **does not** supply real broker commissioning, generated
GTT child-linkage or stop-order persistence evidence. It does not remove
`.trader-paused`, change the paper environment, or approve Algo Start.

Before owner-approved real trading, independently prove:

1. Groww order acceptance and true fills, including timeout/retry races.
2. Broker-hosted protective stop persists after the VM disappears; stop-limit
   orders can still gap/unfill and are not guaranteed loss caps.
3. Owner-controlled test of partial fills and emergency exits against the
   actual broker, not only synthetic fixtures.
4. Fresh account/position/order reconciliation and no adopted manual positions.
5. Both Oracle and external cloud monitors accept test emails and recover
   correctly after VM loss.
6. Execution broker login succeeds with real daily API key approval; the 12:00
   and 15:00 schedule is distinct from the broker's approval workflow.

Do not interpret an email as automatic risk closure. When an active-position
alert arrives, inspect the original orders, broker-side GTT and live position
in Groww.

Code and cloud infrastructure may be deployed independently of activation.
The broker executor remains paused/paper/Off until the separate approval process.
