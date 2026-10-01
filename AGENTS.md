# Trading workspace

- Current repository: `Logan17de/Trading`; local checkout: `D:\Money Trader\Trading`.
- Continue new work here. Read `docs/CODEX_HEADLESS_HANDOFF.md` and the current
  integration/strategy guides before changing the workflow.
- Extend `src/nifty_engine/agent_engine/`. Keep it headless: structured data for
  Codex, a visual daily email for the owner. Preserve the existing runtime.
- Use the local PC for read-only Groww observation and strategy research. Oracle
  is the intended later execution host; execution remains unfinished and inactive.
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
- Verify meaningful changes with configuration checks, the retained tests and the
  offline demo. Report implemented, tested, deployed and blocked work separately.
