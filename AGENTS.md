# Trading workspace

- Current repository: `Logan17de/Trading`; local checkout: `D:\Money Trader\Trading`.
- Continue new work here. Read `docs/CODEX_HEADLESS_HANDOFF.md` and the current
  integration/strategy guides before changing the workflow.
- Extend `src/nifty_engine/agent_engine/`. Keep it headless: structured data for
  Codex, a visual daily email for the owner. Preserve the existing runtime.
  The owner also authorized the local loopback-only Options Trader PC app, Desktop/
  Start shortcuts and Windows sign-in startup. The background observer targets
  five-second Groww reads from 12:50–18:15 JST on weekdays, independently of the
  window. A visible window may request reads anytime. All charts are lines using
  completed five-minute prices; never render candles.
  Show each buy/sell panel only for that side's actual orders or open positions;
  chain samples must not create charts. Pending orders are not fills. No recorded
  losses means 100% non-loss only when completed trades exist; absent results stay
  unknown. Account/strategy P&L needs the private reviewed ledger, never index moves.
- Use the local PC for read-only Groww observation and strategy research. Oracle
  is the intended later execution host; execution remains unfinished and inactive.
- Support/resistance research starts at 12:55 JST. Fresh barrier crossings queue
  bounded, deduplicated structured Codex proposals. Action rules are restricted to
  13:00 inclusive–18:15 exclusive JST; everyday entry still starts at 13:15.
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
- Latest everyday direction: at/after 13:15 JST, NIFTY +400 on Mon/Tue/Fri and
  SENSEX +800 on Wed/Thu; one active slot, one lot; buy a same-expiry call near the
  index ATM, up to three listed strikes away. No budget-based hedge ranking is
  required for this revised rule. Skip the underlying's actual expiry day using
  Groww expiry dates AND current contract metadata, including holiday shifts;
  unknown/disagreeing evidence blocks entry. Weekday routing does not prove expiry.
- The ATM long / higher short normally forms a bullish debit spread, conflicting
  with the earlier flat/down-hold, up-exit direction. Record the conflict; do not
  silently change the owner's rule or activate it. Version 1/2 research remains
  reproducible; `pc_app.example.json` is the latest PC policy overlay.
- Trailing SL source is present but disabled and undeployed. Modify only an exact,
  already-owned protective SL; ratchet only favorably, confirm provider readback
  before advancing state, and reconcile timeouts before another broker write.
  Index barriers are not option-premium SL prices. Stop/target amounts remain unset.
- Verify meaningful changes with configuration checks, the retained tests and the
  offline demo. Report implemented, tested, deployed and blocked work separately.
