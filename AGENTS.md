# Trading workspace

- Work in `D:\Money Trader\Trading` / `Logan17de/Trading`. Read
  `docs/CODEX_HEADLESS_HANDOFF.md` and `docs/PREMIUM_STRATEGY.md` first.
- Extend the existing headless `src/nifty_engine/agent_engine/` runtime. Oracle
  is the observer/policy/report host; the loopback PC app is viewer/controller.
  No new GUI or execution framework. Keep shared Qwen/Colab/mail/tunnels running.
- Active owner policy is premium v3: Everyday and Late-session only. Historical
  offsets, barriers, swing and reversal studies remain provenance, not entries.
  Action window 14:00 inclusive–19:00 exclusive JST; max two lots in one basket.
  Bought equal-quantity same-expiry hedge required. Actual margin/costs required.
  NIFTY Mon/Tue/Fri targets short CALL premium ₹20; SENSEX Wed/Thu ₹80.
  Everyday skips actual expiry from Groww AND current master, never weekdays.
  Actual expiry at 18:00: UP → PUT three listed strikes below ATM; DOWN → CALL
  three above. Matching position skips entry; otherwise only owned replacement.
  Below ₹8, propose short roll; keep hedge unless net improvement >₹100.
  By 19:00, hold if short premium > entry minus ₹5. ₹2,000 basket stop is a
  trigger, not a guaranteed loss cap. See the policy guide for exact semantics.
- Only explicit owner On/Off changes durable desired intent. Preserve On through
  restarts/windows/faults. Expose blocked readiness separately: broker executor
  is implemented but live provider validation/activation remain pending. Read
  docs/ORACLE_EXECUTION.md. Preserve `.trader-paused`. Never activate LIVE or place/
  modify real orders during maintenance/tests. Manual/unknown/mixed trades are
  protected; ownership needs reservation, exact broker acknowledgment and fills.
- Five-second read-only observer targets 12:40–19:45 JST weekdays. Network delays
  can extend cadence. All charts are actual LTP lines at reception times with
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
  The protected Record withdrawal action is bookkeeping, never a money transfer.
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
