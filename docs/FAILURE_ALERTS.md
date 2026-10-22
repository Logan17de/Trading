# Failure email and Codex incident checks

## Connection outage notifications

The October 21 policy sends one alert for a continuous collector outage. Retry
attempts, changed HTTP codes and changed source lines update the incident without
creating another email. Missing funds, positions, orders, market data and Normal
inputs remain recorded, with their notifications suppressed by the connection
incident. Existing duplicate records retain their evidence and are marked
notification-suppressed, not recovered. A confirmed recovery permits a later
outage notification. Independent order/protection uncertainty still alerts.

Groww reconnection waits at least 30 minutes after a failure, from 13:00 JST
weekdays within the existing 19:45 collection cutoff. Retry metadata survives
restarts, including the minimum spacing of token requests. A longer provider
Retry-After or local budget wait is honored. Healthy five-second collection is
unchanged. No token or credential is saved in this retry metadata.

The Oracle failure watchdog is a separate read-only service. It requests only
`{"action":"read"}` from the existing local observer socket. Its sole external
write is notification email through the existing approved Resend configuration.
It cannot call Groww order endpoints, prepare live mode, change Algo intent,
restart the executor, or modify a trading position.

## Email quiet hours

The owner disabled after-market Trading email on October 8. All report, incident
and PC outage sends/retries use **12:45 inclusive–19:00 exclusive JST**, weekdays
excluding the reviewed 2026 NSE F&O holidays. This is a conservative notification
window, not a real-time exchange-halt feed. Special evening/weekend sessions are
not opted in; unreviewed calendar years stay quiet until reviewed. The calendar
source is recorded in `mail_window.py`.

Collection, incident/recovery recording and read-only provider receipt checks
continue after hours. Pending unresolved incidents can send next session;
recovered incidents do not. Quiet hours consume no delivery attempts. The final
provider transport checks the clock again, including on retry. A message already
accepted by the provider before close can still arrive later.

The visual daily report is frozen after 19:29 JST on regular trading days and
queued for the next eligible session, preserving its original date and contents.
The previous 19:30 send schedule is retired. These changes do not alter trading
state, owner intent, broker protection or unrelated shared mail services.

## Reported failures

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

The owner removed the Codex Trading schedules on October 13. Oracle/PC monitoring
continues independently; an email does not prove this chat woke. The manual
read-only diagnostic command is:

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
