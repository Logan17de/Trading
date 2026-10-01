# Existing headless Codex engine

Reviewed 2026-10-01. The active task is local market recording and evaluation of the
owner's hedged-call hypotheses. The implementation remains in
`src/nifty_engine/agent_engine/`; it is not being replaced with another framework.

Read [current status](HEADLESS_INTEGRATION_STATUS.md),
[handoff](CODEX_HEADLESS_HANDOFF.md) and
[local commands](LOCAL_HEADLESS.md).

## Available runtime

- Durable SQLite observations, conditions, job leases and validated decisions.
- Scheduler and isolated non-interactive Codex worker as separate processes.
- Strict structured-output validation with WAIT / REVIEW / RESEARCH decisions.
- Bounded follow-up watches, daily invocation caps, timeouts and failed-job records.
- Independent HTML/text/MIME/PNG reporting with an immutable, retryable mail outbox.
- Health inspection, SQLite backup API and restore to a new path.
- Local reader, owner history study, bounded recorder and offline spread comparison.
- News/calendar evidence in `agent_engine.news`, independent of any trading runtime.

These capabilities exist in code. The continuous scheduler/worker and scheduled
headless mail are disabled. The local recorder was a single bounded run. Its JSON
needs a reviewed adapter before it can feed the persistent engine schema.

## Codex boundary

Actual authenticated structured-output runs passed on Windows
`codex-cli 0.158.0-alpha.2.1` and Oracle `codex-cli 0.158.0` on September 30. Both
returned a validated WAIT decision with a completed turn and zero tool events.
Those are observed versions, not claims about the newest available release.

The adapter verifies the configured CLI version/hash, uses supported authentication
in a private auth-only home and an empty per-run workspace, and strips unrelated
parent environment variables. Broker, Supabase and email secrets are not passed
into analysis. Shell, apps, MCP, browser and subagent capabilities are disabled.
Unexpected tool/error events and invalid output fail validation. File permissions,
process identity and resource limits remain separate operating requirements.

Codex receives structured observations and research context. It has no order
submission role. The Python engine evaluates persistent conditions; Codex is invoked
for bounded analysis rather than used as a continuously running market monitor.

## Offline verification

From the configured Windows environment, use a new output directory:

```powershell
.\.venv\Scripts\python.exe -I -m pytest -q
.\.venv\Scripts\python.exe -I -m nifty_engine.agent_engine demo .agent-state\new-demo
```

The demo uses synthetic prices and a fake analyst, completes two persisted jobs
and writes HTML, text/MIME and a PNG. It does not authenticate to Groww or Codex and
cannot send its REPLAY email. After cleanup, 77 tests passed locally
with four POSIX-only skips. Headless CI runs on Linux and Windows.

## Inspect disabled local configuration

```powershell
.\.venv\Scripts\python.exe -I -m nifty_engine.agent_engine `
  --config config/agent_engine.local.json doctor
```

This local file is ignored and machine-specific. It currently has `enabled=false`
and `send_email=false`; `doctor` performs no network checks. Keep the research DB
separate from broker/strategy journals. Use `contracts.snapshot` for engine inputs,
not arbitrary diagnostic JSON or credentials.

## Reporting and operations

The owner receives a visual report. Missing observations, account values and P&L
are shown as unknown; synthetic fixtures are labeled. Monthly and daily targets
cannot change position size, loss limits, leverage or strategy risk.

Provider acceptance and inbox delivery have separate verification. One diagnostic
was accepted and independently found in INBOX; recurring local delivery remains
unfinished. See [email status](HEADLESS_INTEGRATION_STATUS.md#visual-email).

Service files under `deploy/agent-*.service.example` are templates. Dedicated Oracle
identities, isolated file access and one bounded CLI run were tested; persistent
units remain uninstalled/disabled and concurrent capacity is unmeasured. Back up
SQLite with its backup API and inspect restores at a new path. Preserve journals,
report identities, authentication state and `.trader-paused` during upgrades.

No activation command is part of the current documentation. Follow the remaining
work in [the handoff](CODEX_HEADLESS_HANDOFF.md) while keeping Oracle and shared
Colab/Qwen/mail services available.

## Retained operational configuration

`nifty-engine` now invokes this headless CLI. The legacy command/runtime and its
PAPER-only research/publisher entrypoints have been removed. CI runs tests, config
and compilation checks, PowerShell syntax checks and the offline demo; it performs
no deployment, service activation, email send or VM power action.

Use the existing restricted Oracle `/etc/growing-trader/call-seller.env` for mail
and optional news-model settings; do not duplicate broker credentials. News uses
`CALL_SELLER_LLM_URL`, `CALL_SELLER_LLM_MODEL` and `CALL_SELLER_LLM_KEY` for compatibility
with the approved environment. Trusted initial feeds remain
`https://rbi.org.in/pressreleases_rss.xml` and
`https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms`, with matching
allowed hostnames. NSE FeedBurner is excluded until the redirect test passes.
Unavailable/stale mandatory news or inference remains UNKNOWN. The news helper is
retained code; it is not a newly activated collector or execution authority.

Back up SQLite with its backup API and restore to a new path for inspection.
Automated retention, off-host backups, sustained shared-host capacity and independent
outage monitoring remain unfinished. See [deployment status](HEADLESS_INTEGRATION_STATUS.md#oracle-and-deployment).
