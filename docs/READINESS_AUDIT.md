# Read-only trading readiness audit

This audit converts **existing** Oracle/PC dashboard observations into an evidence
checklist. It does **not** enable LIVE, start the algorithm, modify `.trader-paused`,
send orders, reconcile uncertain fills by guessing, or install activation records.

A check marked `OBSERVED` means that the validated read-only snapshot **reports**
the named evidence. It does not independently certify Groww's order execution or
the profitability of a strategy. `BLOCKED` denotes a known missing requirement;
`UNKNOWN` means the source does not provide adequate current proof. Even with
all checks observed, the overall result is `REVIEW_REQUIRED`, **never** "ready to
trade" or an automatic owner approval.

## Run from the Windows PC repository

From the installed checkout `D:\\Money Trader\\Trading` with the **existing**
`.agent-state/oracle-viewer.json` SSH settings:

```powershell
.\\.venv\\Scripts\\python.exe -I -m nifty_engine.agent_engine.readiness_audit
```

This calls the **same fixed, private, read-only** Oracle command as the PC viewer:
`oracle_link.request(settings, {"action": "read"})`. The private viewer credential
is not printed and there is no independent access-token or broker connection.

To audit a previously captured, private **dashboard view** instead of contacting
Oracle:

```powershell
.\\.venv\\Scripts\\python.exe -I -m nifty_engine.agent_engine.readiness_audit --snapshot .agent-state\\my-dashboard-view.json
```

To include a **saved** incident-watch diagnostic:

```powershell
.\\.venv\\Scripts\\python.exe -I -m nifty_engine.agent_engine.readiness_audit --snapshot .agent-state\\my-dashboard-view.json --monitor .agent-state\\my-monitor-view.json
```

During live PC reads, the expected SHA comes from the reviewed pinned
SSH interpreter path and is compared with the **running** Oracle process release
reported by `oracle_runtime.py`. The optional
`--expected-release <40-character SHA>` overrides the pinned SHA, or can be
used for offline snapshots.

This PR adds the read-only runtime release field. Until that exact code release
is independently deployed to Oracle, older Oracle snapshots do **not** supply
the field and the audit honestly says `RUNTIME_RELEASE_ID_NOT_VERIFIED`.
A pinned SSH executable path alone does not prove which release a process uses.

The command writes one JSON object to stdout. It emits **only static evidence
codes**, timestamps and statuses. It does not echo raw broker quotes, balances,
credentials, account identities or input payloads. Do not commit the input files
or the outputs if you enrich them with private evidence.

## What the checks mean

- Oracle heartbeat and fresh broker dashboard: evidence of observation, not a
  successful or safe real trade; closed-session market freshness may be unknown.
- Exact deployed SHA: separately verifies the code identity before live validation.
- Broker commissioning, persistent stop/GTT and generated-child linkage:
  require original, immutable, provider-verified receipts. GTT list API success,
  empty lists or synthetic replays alone do **not** prove this.
- Live execution gate: reflects what the runtime reports; while paper/paused/Off
  it is correctly `BLOCKED`. Do not flip the flags to make the audit green.
- Normal Theta candidate and costed historical validation: a current theoretical
  candidate is different from demonstrated, out-of-sample profitability.
- Matched 252-session IV for NIFTY and SENSEX, ongoing 38-day research,
  reviewed net P&L, private archive and independent failure monitoring:
  separately reported so unfinished research cannot be mistaken for activated
  execution.
- Full VM outage detection and unattended full-session recovery remain
  `UNKNOWN` unless independently verified outside the current dashboard.

The live-blocker array is a **diagnostic checklist**, not a new authorization
gate or a justification to bypass existing protections. Existing strategy
production gates remain the sole code-level order authority.

## Validate changes

```powershell
.\\.venv\\Scripts\\python.exe -m pytest -q tests/test_readiness_audit.py
```

The GitHub Action also runs the existing Linux/Windows test suite. This tool is
not installed on Oracle until separately reviewed and deployed.

For real-order commissioning requirements, read
[LIVE_SETUP.md](LIVE_SETUP.md), [ORACLE_EXECUTION.md](ORACLE_EXECUTION.md)
and [CODEX_HEADLESS_HANDOFF.md](CODEX_HEADLESS_HANDOFF.md).
