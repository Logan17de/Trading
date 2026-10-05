# Trading workspace

- Current repository: `Logan17de/Trading`; local checkout: `D:\Money Trader\Trading`.
- Continue new work here. Read `docs/CODEX_HEADLESS_HANDOFF.md` and the current
  integration/strategy guides before changing the workflow.
- Extend `src/nifty_engine/agent_engine/`. Keep it headless: structured data for
  Codex, a visual daily email for the owner. Preserve the existing runtime.
  The owner also authorized the local loopback-only Options Trader PC app, Desktop/
  Start shortcuts and Windows sign-in startup. The background observer targets
  five-second Groww reads from 12:40–19:45 JST on weekdays, independently of the
  action window, covering normal-session observation and end-of-day preparation.
  A visible window may request reads anytime. All charts are lines; never render
  candles. The default index/option lines use actual journalled LTP observations
  at reception times with a five-minute grid, updating on the five-second target
  cycle. Show update time and point count; never manufacture movement or bridge
  outages. Completed five-minute closes remain a selectable comparison and the
  research input. Slow requests can extend the target cycle.
  Draw at the panel's actual dimensions so labels and strokes stay undistorted.
  Session/30-minute/15-minute views filter the recorded points, never synthesize them.
  Show each buy/sell panel only for confirmed nonzero open option positions.
  Historical fills, pending orders, closed positions and chain samples create no
  charts. Plot the broker position average entry and matched pending SL/active OCO or GTT exits
  target/SL, including partial coverage; unknown prices stay absent. No recorded
  losses means 100% non-loss only when completed trades exist; absent results stay
  unknown. Account/strategy P&L needs the private reviewed ledger, never index moves.
- Use the local PC for read-only Groww observation and strategy research. Oracle
  is the intended later execution host; execution remains unfinished and inactive.
- Support/resistance research starts at 12:55 JST. Fresh barrier crossings queue
  bounded, deduplicated structured Codex proposals. Action rules are restricted to
  13:00 inclusive–18:15 exclusive JST; everyday entry still starts at 13:15.
  Independent RBI/ET news collection runs every two minutes during observation;
  missing/stale sources stay UNKNOWN. Level reassessment is 15:00 and 17:00, with
  changed-news/crossing requests within the same eight-attempt daily cap. A weekday
  12:55 Codex thread follow-up checks the installed workers. Slot strategy ownership
  persists overnight; never relabel everyday orders as swing. Swing's recorded
  18:45 entry is outside the action cutoff and remains research-only.
  Groww clear cash and option-buy/sell money are read every 30 seconds, separately
  from reviewed P&L/investment value. Failed/stale balances remain unknown.
- Engine ownership needs a reserved journal reference AND the exact acknowledged
  broker order identity. Manual/unknown and mixed/netted contracts are protected
  persistently. Never adopt a manual trade or infer ownership from a prefix.
- Preserve `.trader-paused`, private journals and encrypted credentials. Do not
  place real orders, enable LIVE, change broker permissions or merge automatically.
- Keep Oracle running continuously and preserve shared Qwen/Colab/mail/tunnels.
  Existing Oracle paths and service identities are deployment state, not branding
  to rename during source changes.
- Local helpers reuse the protected sibling `.secrets/growing-trader` DPAPI vault.
  Keep credentials, account data and Codex authentication out of Git, logs,
  structured analysis inputs and generated reports. Private state is `.agent-state/`.
- Use the owner's Edge session for Groww browser checks. If authentication returns
  403, inspect `https://groww.in/trade-api/api-keys`. The owner authorizes clicking
  Approve for the existing Codex key only when it shows Expired. Verify Approved,
  then rerun the read-only helper. This refresh does not authorize new keys,
  changed broker permissions, subscription purchases or orders.
- Evaluate the owner's fixed Japan-time hypotheses with costs, fills and chronological
  holdouts before selecting any strategy. Profit targets never increase exposure,
  leverage or loss limits; record insufficient evidence and NO_TRADE when appropriate.
- Latest everyday direction: at/after 13:15 JST, NIFTY +500 on Mon/Tue/Fri and
  SENSEX +1000 on Wed/Thu; one active slot, one lot. Buy a higher-strike call with
  the same expiry and equal quantity. Compare alternative hedges by quoted maximum
  net expiry profit after costs within verified available margin; show loss exposure
  and never represent that bound as expected profit. Missing inputs block ranking.
  Skip the underlying's actual expiry day using
  Groww expiry dates AND current contract metadata, including holiday shifts;
  unknown/disagreeing evidence blocks entry. Weekday routing does not prove expiry.
- The owner clarified BUY for the higher call; this supersedes the former ATM-buy
  instruction and resolves that leg-direction conflict. Retain historical v1/v2
  research and historical PC v1 settings for reproducibility; PC v2 is current.
  No strategy-profit probability or executable contracts/expiry are established.
- Trailing SL source is present but disabled and undeployed. Modify only an exact,
  already-owned protective SL; ratchet only favorably, confirm provider readback
  before advancing state, and reconcile timeouts before another broker write.
  Index barriers are not option-premium SL prices. Stop/target amounts remain unset.
- Verify meaningful changes with configuration checks, the retained tests and the
  offline demo. Report implemented, tested, deployed and blocked work separately.
