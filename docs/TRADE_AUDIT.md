# Options Trader: private trade audit (local-first)

## What is recorded

The append-only `pc_trade_audit` table in the existing private Oracle
`pc-monitor.sqlite3` journal is the source of truth for the Trade Log screen.
It starts logging at installation; previous trades are **not** backfilled or
misrepresented as freshly observed events.

Events capture original UTC observation timestamps and monotonic SQLite event
IDs; the UI displays JST. Only reviewed fields are stored:

* CONTROL: explicit owner Algo On/Off instruction.
* STRATEGY: changes to Normal Theta / Research preferences.
* EVALUATION: Normal Theta trend/expiry/margin decision states, deduplicated.
* DECISION: executor reason changes, including blocked/recovery states.
* BASKET: phase changes, strategy, index, expiry, size and exit reason.
* ORDER: durable reservation, actual broker acknowledgement, reconciled
  quantities/average fills, cancellation attempts, uncertainty and terminal
  status. `SUBMITTING` is an **intent**, never proof of a completed trade.
* PROTECTION: GTT reserved/accepted/verified/triggered/inactive/uncertain
  status and declared limit/trigger price. A parent ACK is not a child fill.

Provider-backed status is identified separately from write-ahead journal state.
No raw access tokens, secret keys, account fingerprints, broker order IDs,
broker references or arbitrary raw API payloads are exported. Manual trades
remain protected and are not counted as algo-owned. Actual net strategy P&L is
kept separate from pre-charge Groww gross P&L; none is fabricated by logging.

## Reliability and retrieval

Each audit event is committed in the same SQLite transaction as the associated
`meta` or broker-order journal transition. Matching identical states do not
produce duplicate events. The audit table cannot be UPDATEd, and DELETE
is prohibited until a record is older than 60 days. Quiet-hours retention
then removes expired rows in bounded batches. The Trade Log shows the most
recent 120 retained transitions and exports the visible window as CSV.
Separate private database backups retain their original recovery snapshots.

Only the private Oracle observer writes these records. The PC dashboard and
`/api/trade-log` are read-only and cannot place, cancel, protect or authorize
orders. A stale/unavailable observer must never claim a live broker fill.
Source-code release changes do not transfer prior owner authorization receipts.

## Installation and validation

Roll out to Oracle only through the existing guarded, immutable-release
procedure with owner Off, paper/paused state, no open engine orders, a fresh
reviewed SQLite backup, and no changes to shared services. Synthetic tests
validate the audit contract; they are not Groww provider proof. The first
real hedge, short, protective GTT and linked child remain uncommissioned until
actual provider evidence is observed.
