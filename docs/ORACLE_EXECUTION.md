# Oracle premium execution controller

October 6, 2026. This extends the existing Python runtime; the PC remains a viewer
and protected owner-intent controller. No additional trading service/framework,
public port or GUI was created. No real order was placed during implementation.

## Implemented lifecycle

- Durable one-basket state machine in `premium_executor.py`; strategy attribution
  is immutable. A replacement uses a new slot after confirmed flat positions.
- Exact SDK transport in `oracle_orders.py`, pinned to Groww SDK 1.5.0. Its actual
  installed smart-order methods exist; the former note saying they were absent
  was incorrect. The usual HTTP guard still denies writes. A thread-local,
  single-request capability grants only the exact method/path/JSON and rechecks
  the activation, pause, owner intent and freshness immediately before network I/O.
- Hedge first, full fill and exact broker net-position confirmation before any
  short. Incomplete hedge is cancelled/reconciled and unwound. A partial short's
  remainder is cancelled/reconciled and its actual filled quantity is protected.
- GTT BUY protection: write-ahead parent and generated-child reference reservation,
  exact parent readback and validity beyond the contract's last day. The GTT parent
  persists; DAY is the validity of its generated exchange order. Acceptance alone
  is not proof of protection or a fill. A stop-limit may trigger without filling.
- Trail from actual recorded basket cash/fills and net liquidation high water,
  using the approved basket stop distance and broker-quoted costs. Never loosen a
  stop. A conservative persistent trigger assumes all bought hedges could become
  worthless; the independent basket monitor also uses both legs. This can exit
  earlier than a spread-mark-only stop. Charges are broker calculation estimates
  until the reviewed fee/statement ledger exists; no guaranteed loss cap is claimed.
- Sixty-percent premium-reduction short roll (NIFTY ₹8 / SENSEX ₹32): confirm old short flat before new short; preserve the
  hedge unless the whole-basket improvement after incremental broker costs exceeds
  the strict approved threshold. Buy a replacement hedge before disposing of the
  original. No roll increases lots. Refresh books and margin after the close.
- End-of-day entry-minus-offset hold/next-window roll. Actual expiry/18:00 mapped
  Late-session replacement closes only the owned basket first. Exact matching
  positions skip; manual/unknown/mixed contracts are never adopted or managed.
- At 19:00 on the basket's confirmed actual expiry, queue its owned short-first
  exit through the existing controller. Save the other-index successor as a
  read-only proposal; it cannot bypass the 19:00 new-entry cutoff. Unknown expiry
  evidence does not cause an expiry exit. Existing risk exits retain priority.
- Original operation references and monotonic fills reconcile uncertain writes.
  The actual sampled manual-order detail route returned GA004; the adapter can
  fall back to a fully paginated order list with an exact unique broker ID/reference
  and all immutable fields. Incomplete/ambiguous lists remain blocked. Day-scoped
  missing history preserves a previously verified terminal fill, while each
  management action still requires fresh complete exclusive net positions.
  A cancellation acknowledgment is not terminal; triggered GTT children are
  reconciled before another BUY. A process lock prevents a second runtime stealing
  the socket/state. Collector ownership reads and writes are serialized; historical
  reads, margin preparation and reporting remain independent.
- Expired baskets release the slot only after two complete flat position reads,
  all standard orders terminal and owned GTT inactive. Manual conflicts or unknown
  children still freeze reconciliation. This records risk flat, never an invented
  settlement fill or net P&L; settlement accounting remains unknown without its
  official broker/statement evidence.

## Current activation boundary

Collection authenticates/connects at 12:40 JST on weekdays. Before that, an
unconnected executor reports WAITING_FOR_COLLECTION_WINDOW alongside the actual
pause/paper/owner/validation blockers. An authentication failure reports
ORACLE_BROKER_CONNECTION_RETRY; CONNECTED means transport constructed, not broker
writes verified. Unexpected executor-worker failures latch a visible
EXECUTOR_WORKER_RECONCILIATION_REQUIRED blocker rather than silently terminating
the thread or retrying an uncertain write. Restart reconciliation uses the
existing durable references. Outside collection hours, monitoring reads the
saved snapshot without changing its timestamp; it never manufactures freshness.

Maintenance preserves `.trader-paused`, `EXECUTION_MODE=paper` and the actual owner
Off. All production order writes remain disabled. Algo Start saves durable On;
it cannot bypass the pause or activate LIVE. The dashboard reports code installed,
actual phase and readiness separately.

The private `premium-execution-activation` journal record must bind the exact
immutable source release and policy hash, and record owner approval, replay,
static-IP authorization, broker-write, persistent-GTT and generated-child linkage
verification. There is no desktop/maintenance command that manufactures these
proofs or removes the pause. Changing a source release invalidates its activation.

Controlled live validation and activation need the owner's explicit approval of
the concrete deployed build. Preserve shared Oracle services and manual positions.
The first controlled validation must establish real request/receipt behavior,
GTT carry validity and exact generated-child linkage with the existing approved
API key/static-IP configuration. Do not rotate keys or alter broker permissions.

Groww's published GTT read schema does **not** document the generated child's ID
or original reference. The controller requires the child to return the exact
previously reserved parent reference plus exact broker ID/order fields. This
linkage has synthetic tests, not live proof. If Groww does not preserve it, the
current adapter cannot safely manage triggered children; do not activate it.
Unknown parent creation/modify/cancel/child results freeze further writes rather
than guessing or submitting a duplicate. These states need reconciliation.

## Remaining limits

The owner authorized preparation/verification of connection, broker adapter,
persistent protection and lifecycle on October 6 without autonomous activation.
Installed SDK wire-format tests exercise actual methods against intercepted
synthetic responses; isolated lifecycle tests cover partial fills, request
timeouts, GTT creation/modify/cancel races, generated-child linkage, tightening,
short-first rollover, pre-14:00 carried-basket risk exits and expiry-19:00 closes.
They are not actual Groww write/receipt/protection evidence. No maintenance
command submits a test order or creates an activation proof. Actual server-side
GTT carry validity and generated-child identity remain required before activation.
Pre-14:00 short rolls and the requested 19:00 successor entry remain proposals:
their separately reviewed execution-window changes are not implemented here.

Preparation samples a bounded chain/hedge set and explicitly labels its scope;
maximum payoff means maximum among verified sampled candidates. It is not proof
of a global optimum, strategy expectancy or a profit probability. Books/margins
expiring during calculations remain unknown, with no automatic policy relaxation.
An Off or paused engine makes no new broker requests; previously armed broker GTTs
remain broker-managed. Existing exchange orders may fill while Off. Stale/unreachable
VM state cannot be repaired by pretending its broker stop executed. PC outage
alerts require an awake/online PC; no external always-on watchdog is installed.

Primary contracts: [Groww orders](https://groww.in/trade-api/docs/python-sdk/orders),
[Groww smart orders](https://groww.in/trade-api/docs/curl/smart-orders),
[Groww margin](https://groww.in/trade-api/docs/python-sdk/margin).
