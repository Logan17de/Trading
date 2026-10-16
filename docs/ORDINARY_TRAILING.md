# Normal Theta: ordinary orders and trailing stops

October 16 owner instruction supersedes active research, recording and GTT
requirements in older documents. The existing headless Oracle runtime and
desktop viewer remain in use. No real orders are sent during maintenance.

## Lifecycle

1. Owner-clicked Algo Start retains the protected release/account-bound setup
   and durable On/Off controls. Reads and deployment never authorize orders.
2. Normal retains trend, DTE, weekday index, one-basket/two-lot, margin, quote
   and modeled-risk checks. Buy the hedge with an ordinary DAY LIMIT at the
   current ask. After its complete fill and owned-position readback, sell the
   short with an ordinary DAY LIMIT at the current bid. These are executable
   limits, not conditional GTT entries. A move can leave a limit unfilled.
3. After the exact short fill/readback, attach an ordinary DAY BUY SL LIMIT.
   Calculate its trigger from reconciled basket cash, current hedge liquidation
   bid, costs and `min(INR 1000, entry credit * 1.5)` loss distance. The BUY limit
   is one listed tick above the trigger. Keep the bought hedge.
4. Persist best observed net basket P&L. A new high raises the loss floor by the
   same amount. Modify the exact stop ID only when its trigger is lower; never
   loosen. Uncertain replies are reconciled against that ID and desired fields,
   never blindly repeated. An already-crossed trailing floor queues an exit.
5. Stop fill/partial trigger, target, loss, DTE or scheduled exit settles pending
   entries and cancels/verifies the SL before a separate short BUY. Close the
   exact remaining owned short quantity, then SELL the owned hedge. A fill racing
   cancellation changes remaining quantity; no duplicate BUY. Replace a terminal
   unfilled exit only after its terminal readback and fresh position evidence.
6. Confirm terminal orders and flat positions before closing the slot. External
   closures retain the Self label; manual positions are never adopted. Existing
   legacy protections keep their original IDs for reconciliation/cleanup.

Trailing follows fresh five-second observer samples and broker replies, not
every exchange tick. Network delay can extend this interval. DAY stops do not
protect overnight. Mandatory unwind begins 18:44 JST with a 19:00 flat target.
Off/pause/paper deny all engine writes including exits. Accepted broker orders
can still act. Fills, loss limits, profit and exit time are not guaranteed. The
strategy remains unbacktested.

## Recording removed

No active Research switch, research campaign, impulse stream/raw ticks, scheduled
event collection, IV study, bulk quote/P&L/session journal or Supabase upload.
Current Normal features/contracts/margins are RAM caches fetched again after
restart. Daily history must have been fetched for the current session; an earlier
day's cache cannot be redated as current evidence.

Live price and Self/Algo P&L curves keep at most 6,000 actual points per line for
one session; inactive option buffers are removed. They reset on restart. The
latest account snapshot is atomically overwritten for the existing protocol,
not accumulated as history.

The small private operational SQLite journal retains ownership, reservations,
receipts, trailing high water, controls, capital and email outbox for recovery.
Offline compaction copies these records to a new verified journal, retaining the
previous database privately. Rollout does not irreversibly delete old history.

## Verification

`test_ordinary_trailing.py` checks both indices/directions, hedge-first ordinary
entry, filled-position SL, trailing/restart, partial/full stop fills, rejected
stop unwind, lost modify replies, manual exclusion and write gates.
`test_lean_runtime.py` checks RAM-only caches, current-day features and exact
operational-copy preservation. Synthetic tests are not actual broker fill proof.

[Groww ordinary order contract](https://groww.in/trade-api/docs/python-sdk/orders).
