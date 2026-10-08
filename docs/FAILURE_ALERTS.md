# Failure email and Codex incident checks

The Oracle failure watchdog is a separate read-only service. It requests only
`{"action":"read"}` from the existing local observer socket. Its sole external
write is notification email through the existing approved Resend configuration.
It cannot call Groww order endpoints, prepare live mode, change Algo intent,
restart the executor, or modify a trading position.

## What it reports

- Unreachable/stuck observer and malformed heartbeat.
- Collector exceptions, dated stale snapshots, unavailable funds/positions/orders.
- Latched executor failures and uncertain broker protection/order reconciliation.
- Owner On with incomplete live setup or no executable strategy selected.
- Daily report delivery failures and research archive synchronization failures.
- An explicitly owner-started Buy + protect test with failed/uncertain execution,
  partial BUY/protection requiring review, or a running job checkpoint older than
  four minutes. These incidents apply even while the normal Algo is Off. The
  monitor never starts or retries the test; accepted mail is not broker recovery.

Normal Off, paper/pause while Off, no qualifying trade, research missing inputs
and closed-market collection inactivity do not generate failure alerts. Routine
failures must persist for 60 seconds; latched execution/protection faults are
queued on detection. The timer runs 30 seconds after each check finishes, so
network/VM latency adds to detection time. Healthy observations must persist for
60 seconds to mark recovery; an unreachable observer cannot prove an order fault
recovered. This monitor never makes a trading decision in response to a fault.

Each email identifies the incident, component, exact internal failure code,
UTC time, and available exception type/provider code/source file/function/line.
Raw broker exception messages, credentials, account IDs and prices are excluded.
Changed diagnostic causes receive different incident IDs. Emails freeze their
content and retry the same provider idempotency key every five minutes, stopping
unconfirmed retries before the provider's 24-hour deduplication window expires.
Acceptance and recipient-server delivery are recorded separately. Inbox delivery
is never assumed, including after a delivered/opened provider event.

Private monitor state and receipts are in Oracle's
`.agent-state/failure-monitor.sqlite3`, separate from the trading journal.
The service reuses `/etc/growing-trader/observer.env` and immediately discards
all environment values except the three existing report-mail settings. No new
broker permission or credential copy is needed. The service is resource-capped,
unprivileged, and installed from a separate immutable release. Installing it does
not upgrade the broker executor or change its commissioning binding.

## PC and Codex coverage

The existing independent PC outage alert remains active. Its email now includes
the incident ID, detection time, SSH/read failure type, or the last heartbeat.
It cannot confirm broker position/order state while Oracle is unreachable.

`Trading incident watch` is a best-effort five-minute heartbeat attached to this
chat. It reads the diagnostics, deduplicates reviewed incidents privately, and
reports meaningful failures/recoveries. It is not an immediate event callback
and an email does not prove the chat woke. The read-only command is:

```powershell
.\.venv\Scripts\python.exe -I -m nifty_engine.agent_engine.failure_alerts pc-status --root "D:\Money Trader\Trading"
```

Oracle can email application faults while the PC is asleep. Complete Oracle VM
or network loss requires the independent PC monitor to be awake and online.
Codex must be available for scheduled diagnostic wake-ups. There is no external
cloud outage monitor; no alert is guaranteed when both hosts are unavailable.
The diagnostic automation must not trade, activate live mode or restart an
active executor/unresolved order. Email failures do not change trading state.

Primary provider references:
[Resend idempotency](https://resend.com/docs/dashboard/emails/idempotency-keys) and
[email receipts](https://resend.com/docs/api-reference/emails/retrieve-email).
