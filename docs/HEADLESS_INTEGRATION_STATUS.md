# Current integration status

Reviewed 2026-10-01. Cleanup/test evidence and the latest broker renewal/read check
are from October 1; CLI and email evidence is from September 30 unless noted otherwise.
This is the current status record for the local research workflow, not an activation
runbook.

## Implemented and tested

| Capability | Verified result | Limit |
|---|---|---|
| Local Groww REST reader | October 1 at 16:22 JST: authentication plus 22 profile, index, expiry, chain and sampled-call probes passed across NIFTY, BANKNIFTY and SENSEX | Read-only; successful retrieval does not establish executable freshness |
| Historical study | 17,105 one-minute candles per index; 51,315 total; 41 observed sessions per index | Index moves are not option P&L; source identity/timestamp semantics remain unverified |
| Owner protocol | Fixed Japan-time windows, call preference, 70/30 chronological partition and session-level statistics | No strategy selected; profit probability unknown |
| Call-spread comparison | Higher-strike hedge, equal units, credit/payoff/cost arithmetic and separate broker margin | Owner-supplied inputs; no quote verification or order submission |
| Runtime tests | 77 passed locally after cleanup, four POSIX-only skips; Linux/Windows headless CI configured | Functional verification is not strategy validation |
| Offline demo | Two persisted analysis jobs and HTML/text/MIME/PNG artifacts | Synthetic inputs and fake analyst; no email sent |
| SQLite operations | Backup API and restore-to-new-path preserved state/report identity | Automated off-host backups and retention are not installed |

## Current repository

The current working home is [`Logan17de/Trading`](https://github.com/Logan17de/Trading),
branch `main`, local checkout `D:\Money Trader\Trading`. It imports the 50 retained
source files from Growing-Trader commit `767e198` plus `AGENTS.md`, with fresh Git
history. The existing headless engine, Groww adapter, owner study, recorder, spread
review, manual tickets, visual email, news/calendar evidence, backups and two
inactive service templates remain the working implementation.

News code and its tests were moved into the retained package. Python dependencies
and lock files were reduced to the actual retained requirements. `nifty-engine` now
invokes the headless CLI. Configuration, compilation, editable installation,
`pip check`, 77 tests and the synthetic offline demo passed locally. Four POSIX-only
checks run in Linux CI. This cleanup is not deployed to Oracle.

Only verification CI is configured in Trading. Historical automation and hosted
resources belong to the previous repository; they were not transferred or
activated. The two old GitHub VM stop workflows remain disabled. Private data,
credentials, journals and `.trader-paused` remain intact; no shared service, key
or permission was changed.

Migration verification in `D:\Money Trader\Trading`: a new Python 3.13 environment
installed from the retained dependency lock; `pip check`, configuration checks,
compilation, PowerShell syntax, 77 tests with four POSIX-only skips and the offline
demo passed. All runtime/helper/config/test/template files match the source.
The 51 staged files passed actual-credential and sensitive-token scans. Private
research/authentication files were hash-verified, ignored by Git and stored with
restricted ACLs; helpers reuse the existing DPAPI vault. Holdings credentials
were copied locally with their restricted ACLs and were excluded from Git.

Groww checks at 15:58 and 16:00 JST initially returned authentication HTTP 403 in
both checkouts. In the owner's Edge session, the API dashboard showed the existing
Codex key as Expired. The owner-authorized Approve action changed it to Approved,
with the displayed status “Resets 6 AM tomorrow.” A fresh Trading check from
16:22:30 to 16:22:53 JST then passed authentication and all 22 read-only probes,
using the same SDK and protected credentials. No credential was regenerated,
permission changed or order submitted. `.trader-paused` remains present.

Private evidence: `.agent-state/groww-renewal-verification-20261001.json` and the
approval screenshot. This daily approval is an operator action through the owner's
Edge session; no recurring browser task has been installed. The displayed reset
time's timezone is not established by the page.

## September 30 recording

Private directory: `.agent-state/recordings/20260930-cas/`.

- 29 snapshots, nominal 60-second interval.
- Actual observation span: **18:40:25–19:08:38 JST**, September 30.
- 28 snapshots report `READ_ONLY_DATA_AVAILABLE`; the final one reports
  `MARKET_DATA_INCOMPLETE` because SENSEX chain, expiry and LTP probes failed.
  Its SENSEX quote was still available.
- 20 snapshots were labeled within the closing-auction time window. That label
  does not prove that the broker returned an indicative-auction index value.
- Requested deadline: 19:10 JST. No snapshot started after that deadline. The
  manifest was finalized at 09:21:50 JST on October 1; the reason for the late
  finalization is unverified. Do not interpret it as overnight market collection.
- Recorder status is `CAPTURE_COMPLETE`, with execution disabled. It was a single
  bounded run, not a continuously installed collector or complete market session.

The private evidence record is `.agent-state/owner-strategy-evidence.json`.
No raw account data or credentials belong in this document.

## Strategy evidence

The requested historical period was 2026-08-03 through 2026-09-29. The SENSEX expiry
screen has eight complete windows: five development and three holdout. It found
zero qualifying upward 500-point minute-close events in the unclassified broker
index series. With zero qualifying events, reversal probability and its confidence
interval are unknown. This neither validates nor refutes a separate indicative-price
observation; one-minute closes can also miss shorter spikes.

All three strategies remain `INSUFFICIENT_EVIDENCE` for net option profitability.
Required evidence includes verified indicative/regular series identity, source
timestamps, historical two-leg bid/ask/depth, exact contract/lot/expiry data, costs,
fill assumptions and enough independent out-of-sample sessions.

## Codex verification

| Host | Observed CLI | Authentication and structured result |
|---|---|---|
| Windows | `codex-cli 0.158.0-alpha.2.1` | Supported ChatGPT authentication; schema-validated WAIT; completed turn; zero tool events; 7.927 seconds |
| Oracle | `codex-cli 0.158.0` | Supported ChatGPT authentication; schema-validated WAIT; completed turn; zero tool events; 10.979 seconds |

The Oracle run used `growing-analyst`, a read-only system, ProtectHome, private temp,
a 192 MiB memory cap and a 25% CPU cap. This verifies one actual run, not sustained
concurrent capacity. The adapter pins version/hash, strips broker/mail credentials,
uses a private auth-only home and rejects unexpected tools or invalid output.
Scheduled analysis remains disabled.

## Visual email

One authorized NO_DATA visual email was accepted by Resend at
2026-09-30 01:52:24 UTC. Independent mailbox inspection found the matching message
in INBOX and verified plain text, HTML and an inline PNG. The existing approved
sender and recipient were reused. Provider acceptance and inbox placement are
separate facts.

The headless scheduler has `send_email=false`. Historical default-branch
report/export automation belongs to the previous repository.
The retained engine has no activated recurring local report or verified end-to-end
daily delivery. It needs reviewed local-snapshot integration and mail/isolation
configuration. Provider acceptance, recipient-server acceptance and actual inbox
placement must remain separate states.

## Oracle and deployment

October 1 readback: Oracle was reachable with 27 days of uptime. Qwen gateway,
Zetbros mail/MCP and both checked tunnels were active. Gateway service activity
does not prove that a Colab inference worker is online.

- `Oracle Shared VM Idle Stop` (342568515) and `Oracle Market Stop` (333263426)
  remain `disabled_manually` in GitHub.
- `growing-trader.service`, `growing-trader-market-start.timer` and
  `growing-trader-preopen-auth.timer` remain disabled.
- Both `/home/ubuntu/Growing-Trader/.trader-paused` and
  `/opt/growing-trader/.trader-paused` are present.
- Earlier integration code from `0159642` was staged under `/opt/growing-trader`.
  The latest local research code is not deployed there. New persistent headless
  units have not been installed or enabled.
- Service templates passed `systemd-analyze verify`; dedicated identities and
  cross-UID sanitized-state access/secret denial were tested. Simultaneous
  collector/analyst/Qwen capacity on the approximately 954 MiB RAM host remains
  unmeasured. Existing memory caps are limits, not capacity certification.
- No local PC wake/stop task or recurring Codex power automation was installed.
  No automatic off-host backup destination or independent outage monitor is
  configured. A separate OCI-console lifecycle inventory remains unverified.

Oracle must stay running continuously. Preserve shared services and the trading
pause. No real orders have been placed by this integration and LIVE is not enabled.

## Private data, news and model state

Local DPAPI credential backups are verified and the original credential files have
restricted ACLs. The originals are still plaintext; DPAPI requires the original
Windows profile. Helpers pass credentials through stdin and suppress sensitive SDK
output. Git and research output contain no secret values.

The most recent inference/feed tests, on September 30, found Qwen `/health` 200,
authenticated `/v1/models` 200 with `qwen3.8-27b`, and `/ready` 503:
`QWEN_WORKER_OFFLINE`. Inference was unverified. ET returned 50 fresh articles;
RBI returned none within the strict 24-hour window; the NSE FeedBurner redirect was
refused. News risk therefore stayed `UNKNOWN`. These are dated checks, not current
online/freshness assertions. Missing news must never silently become LOW.

## Repository state and next work

Historical source-repository state: at the October 1 status check, PRs
[#48](https://github.com/Logan17de/Growing-Trader/pull/48) and
[#49](https://github.com/Logan17de/Growing-Trader/pull/49) were open with no submitted
reviews. Their cleanup commit was subsequently verified by Linux/Windows CI.
The old Vercel preview check failed after dashboard removal; that hosted project
was not transferred or reconfigured. Trading's headless CI verifies Python on
Linux and Windows. No automatic merge is configured.

Next work: complete-session collection and option evidence; fixed-protocol spread
replays including costs and adverse cases; structured integration into continuous
analysis and visual reporting; capacity/backup/monitoring review before deployment.
Oracle execution remains unfinished and inactive.
