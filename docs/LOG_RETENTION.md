# 60-day trading log retention

The Oracle observer automatically prunes **disposable logs and time-series
research records** after 60 days. It runs during quiet hours (20:00–10:59 JST)
on an independent background worker. When no expired records exist, a successful
scan runs at most once per Japanese calendar day; a backlog is drained in
small 1,000-row-per-table batches once per minute.

Expiration uses UTC timestamps for timestamped rows and an ISO calendar-date
cutoff for daily datasets. A 65-second guard band prevents an SQLite audit
trigger race at the expiration boundary.

## Pruned after expiry

- Strategy audit events (append-only during their retention window)
- Premium impulse raw tick observations and completed outcome/events
- Broker P&L **observations**, market snapshots, chart bars
- Daily research summaries and IV30 study observations
- Nonessential engine events, calls, completed jobs/requests and delivered
  or terminal report logs
- Stale daily backup **status metadata**, not backup files

The retention worker saves per-table deletion counts, UTC cutoff and last-run
time in `journal-retention-status` and exposes them with the Trade Log response.
It never sends broker orders, enables trading, modifies the live/paper mode, or
alters the retained owner pause.

## Deliberately NOT considered logs

- Account credentials, runtime/strategy configuration, capital ledger
- Original broker order receipts, fill proof, protection GTT parent/child
  records and ownership reservations, including CLOSED records
- Open orders, pending requests, retryable jobs, undelivered reports
- Research sync cursors/deduplication markers and pending outbox rows
- Raw premium ticks/P&L observations not yet captured into the durable local
  research sync outbox; these await capture rather than being silently lost
- Verified SQLite backup files (separate recovery policy)

Older trade history in backups is unaffected. Cloud copies may have their own
separate retention settings. This local observer policy cannot delete data in
Supabase, Groww, OS logs or backups.

## Database size

SQLite retains freed database pages for subsequent inserts. Pruning does not
automatically reduce the on-disk `pc-monitor.sqlite3` size; a VACUUM of a
multi-gigabyte trading journal is intentionally not run during observation.

## Operational status

The cleanup routine is fail-closed. Errors are recorded as
`journal-retention-error`, never interpreted as success. Trade Audit deletes
are rejected in SQLite until expiry, and UPDATE remains blocked at all ages.
Synthetic tests validate current-row protection, old-row cleanup, open order
preservation, broker data exclusions and clock/schedule rules.
