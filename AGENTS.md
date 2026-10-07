# Trading workspace

- Owner-operated commissioning is implemented in broker_commissioning.py and
  owner_setup.py; read docs/LIVE_SETUP.md. The desktop/observer never calls its
  submit/close/cancel/arm commands. Maintenance may test them with synthetic
  brokers only. Actual evidence is private and separate from strategy ownership.
  Production uses the v2 evidence-bound activation receipt, not hand-set flags.
  The owner arm command prepares live mode with Algo Off, clears only this
  runtime's pause link and retains the original shared trader pause. Never run
  broker-writing commissioning or arm commands as maintenance. Deployment must
  preserve existing mode/pause and refuse owner On/open owned orders or an active
  live unpaused runtime; new source requires reviewed commissioning.

- External closure uses `external_close.py`: exact terminal Self offsets, an
  ownership-safe baseline and two fresh complete flat reads. Self means outside
  this engine, not verified personal/device origin. Keep manual flags and external
  orders protected. Only the exact owned orphan GTT may be cleaned up under the
  live gate; Off/pause/paper prohibit even that cancellation. Do not release the
  slot with pending orders, active protection, unknown children or partial exits.
  Read-only closure accounting is allowed while Off; never fabricate exit fills.

- October 7 latest catalog: two owner choices, Normal theta spread and Research.
  Read NORMAL_THETA.md. Normal is a separate unbacktested, trend-aligned 14–45
  DTE hedged credit spread using positive net model theta; no historical-IV gate.
  Both choices default Off. Production entry gates permit normal_theta only;
  the four report studies are monitoring only. Preserve their original private
  38-day campaign, strict event horizon, IV requirements and legacy management.
  Official scheduled-event status is visible information for Normal, not its
  entry gate. Retired Everyday/Late-session entries remain retired. Do not
  manufacture provider validation, activate LIVE or trade during maintenance.

- Work in `D:\Money Trader\Trading` / `Logan17de/Trading`. Read
  `docs/CODEX_HEADLESS_HANDOFF.md` and `docs/PREMIUM_STRATEGY.md` first.
- Extend the existing headless `src/nifty_engine/agent_engine/` runtime. Oracle
  is the observer/policy/report host; the loopback PC app is viewer/controller.
  No new GUI or execution framework. Keep shared Qwen/Colab/mail/tunnels running.
- Retained research studies are bull put, bear call, iron condor and calendar
  for NIFTY/SENSEX. Read REPORT_STRATEGIES.md and ORACLE_EXECUTION.md.
  Three same-expiry studies retain a guarded adapter inside PremiumExecutor;
  calendar stays MONITOR_ONLY. Pure research evaluation never authorizes orders.
  Four previous private switches are preserved for provenance; current owner
  controls are the two groups above. Group switches use revision checks.
  Switch Off prevents new entries, not existing owned management while global
  Algo is On. Global Off/pause/paper still deny every broker write. Missing IV
  history/calendar and actual provider validation keep production blocked.
  October 7 owner chose 38 calendar days of research; read READINESS_RESEARCH.md.
  Preserve that private campaign's original horizon. Its completion never
  activates trading or replaces 252 prior IV sessions. Official scheduled-event
  sources and versioned ATM IV30 proxy collection are research evidence; the
  proxy is not an execution feature. Do not shorten the strict event horizon.
  Read MATCHED_IV_IMPORT.md for the private reviewed history/current adapter.
  Production rejects unreviewed feature-only IV bundles; synthetic imports never
  belong in the real journal. No provider dataset/stream is supplied by that code.
  New Everyday/Late-session entries and rolls are retired even if intent is On.
  Keep original records/ownership/exits/protection; never relabel legacy positions
  or migrate fictional SPX win rates. New research requires point-in-time IV/RV,
  trend, signed Greeks, expiry, event and margin evidence; UNKNOWN blocks proposals.
  Do not restore retired news workers. Preserve pause, paper and manual protection.
- Legacy management policy is premium v3. Historical
  offsets, barriers, swing and reversal studies remain provenance, not entries.
  Action window 14:00 inclusive–19:00 exclusive JST; max two lots in one basket.
  Bought equal-quantity same-expiry hedge required. Actual margin/costs required.
  NIFTY Mon/Tue/Fri targets short CALL premium ₹20; SENSEX Wed/Thu ₹80.
  Everyday skips actual expiry from Groww AND current master, never weekdays.
  Actual expiry at 18:00: UP → PUT three listed strikes below ATM; DOWN → CALL
  three above. Matching position skips entry; otherwise only owned replacement.
  At 19:00 on actual expiry, propose closing the owned expiry basket then review
  the other index (NIFTY expiry → SENSEX ₹80; SENSEX expiry → NIFTY ₹20). Confirm
  flat/session/expiry/margin before a successor; live entry gates stay unchanged.
  At 60% reduction (NIFTY ≤₹8 / SENSEX ≤₹32), propose short roll; keep hedge
  unless net improvement >₹100.
  By 19:00, hold if short premium > entry minus ₹5. ₹1,000 basket stop is a
  trigger, not a guaranteed loss cap. See the policy guide for exact semantics.
- Only explicit owner On/Off changes durable desired intent. Preserve On through
  restarts/windows/faults. Expose blocked readiness separately: broker executor
  is implemented but live provider validation/activation remain pending. Read
  docs/ORACLE_EXECUTION.md. Preserve `.trader-paused`. Never activate LIVE or place/
  modify real orders during maintenance/tests. Manual/unknown/mixed trades are
  protected; ownership needs reservation, exact broker acknowledgment and fills.
- Five-second read-only observer targets 12:40–19:45 JST weekdays. Network delays
  can extend cadence. Premium Impulse Monitor research covers NIFTY and SENSEX with config-driven
  five-second threshold ladders, 30-second ATM/expiry locks, raw ticks, nullable
  fields and private outcomes/reports. Read PREMIUM_IMPULSE_RESEARCH.md. It must
  never call or influence the executor. Private Supabase archive is one-way from
  journal/outbox; server credentials stay on Oracle, public/browser access denied.
  A separate callback-driven premium impulse observer uses
  GrowwFeed: five-second +₹10 NIFTY/+₹30 SENSEX impulses, 80/100 confirmation,
  2–3 second target/ten-second deadline. Read PREMIUM_IMPULSE.md. Missing evidence
  stays UNKNOWN; this observation layer never authorizes orders. PC SSE uses a
  persistent private read-only watch connection; ordinary account polling is five seconds.
  All charts are actual LTP lines at reception times with
  five-minute grids. No fabricated movement/outage bridges/candles. Option
  panels require confirmed nonzero active positions; SL/target/entry evidence
  must match actual broker records. Do not adopt manual positions.
- Today's P&L is Groww realized plus signed qty × (LTP minus average), before
  charges. Split Self / exact Algo / Unassigned. Persist actual points; stale,
  incomplete or unverified carry-day basis stays unknown. Cash is not investment
  value. Net strategy returns require reviewed ledger; no outcomes means unknown.
- Capital is an independent owner-declared ledger in private Oracle SQLite meta
  `capital-ledger-v1`: investment minus recorded withdrawals minus monthly API
  fees. Fee months accrue once on the first JST day, with downtime catch-up.
  Never infer withdrawals from broker balances or count API fees again in P&L.
  Protected Record investment and Record withdrawal actions are bookkeeping,
  never money transfers. Dated additions append to the original investment;
  retries cannot duplicate records or reset the initial capital, withdrawals/fees.
  Keep owner capital amounts and ledger exports out of Git.
- Owner retired news on October 5. No active news collection, news entries or
  news entry gate for premium v3. Preserve historical evidence for provenance.
  GrowwPreparation independently compares actual books and hypothetical broker
  basket/hedge/exit calculations, with explicit sampled scope; never calls order
  methods. PreparedOrderGateway is replay-only and rejects a real broker even
  if someone removes the pause or switches the owner preference On. The separate
  OracleOrderGateway and premium controller use exact SDK routes behind thread-local
  one-request capabilities, owner intent, pause, LIVE and private release/policy-bound
  validation gates. Maintenance never creates activation proof. Generated-child
  reference linkage is synthetically tested, not verified against real fills;
  unknown create/cancel/modify/child states freeze further writes.
- Visual daily email at 19:30 JST every calendar day, minimal words, existing
  approved sender/recipient. Retry frozen content without changing trading state.
  Distinguish provider acceptance, recipient-server acceptance and inbox evidence.
- At 12:00 JST weekdays use owner's Edge to check existing Codex API key. Approve
  only if Expired; verify Approved and retest reads. Never create/rotate keys,
  change permissions/subscriptions or claim a scheduled heartbeat actually ran.
- Keep Oracle continuously running. Verified private SSH and fixed Unix-socket
  client; no public trading port. Liveness needs heartbeat advancement. PC alerts
  require PC awake/signed in/online; no external cloud monitor is installed.
- Protect `.agent-state`, sibling `.secrets/growing-trader` DPAPI vault, original
  production keys/ciphertext and Codex authentication. No secrets/account IDs in
  Git/logs/prompts/reports. Root-only observer credentials reuse existing keys for
  isolation; never rotate broker encryption automatically or shut down Oracle.
- SQLite backup API, restore only to new paths; root-owned immutable releases and
  isolated resource-capped service. Preserve old deployment identities and pause.
  No automatic merge. Run retained tests, config/JS checks and offline demo;
  distinguish implemented/tested/deployed/blocked. Profit bounds are not forecasts.
