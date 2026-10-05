# Current integration status

Reviewed 2026-10-05. The PC app and current Codex checks are from October 5.
Broker success and earlier viewer evidence below are from October 1; the email
and Oracle CLI evidence is from September 30 unless noted otherwise.
This is the current status record for the local research workflow, not an activation
runbook.

## Implemented and tested

| Capability | Verified result | Limit |
|---|---|---|
| Local Groww REST reader | October 5 at 10:45 JST: authentication and all 22 read-only probes passed after reapproving the existing expired key in Edge | Pre-market last-session observations; API success does not prove executable book freshness |
| Options Trader PC app | Desktop/Start shortcuts and sign-in startup installed; real quote/order/position and metadata reads; both actual-expiry checks agree | Awake, signed-in PC required; actual reboot and a full unattended market session not tested |
| Historical study | 17,105 one-minute candles per index; 51,315 total; 41 observed sessions per index | Index moves are not option P&L; source identity/timestamp semantics remain unverified |
| Owner protocol | Four reproducible hypotheses plus PC v2: NIFTY +500 Mon/Tue/Fri, SENSEX +1000 Wed/Thu, higher-call hedge ranked by supplied net-profit/margin | One slot/lot; exact expiry, costs, current books and executable plan remain unverified |
| Call-spread comparison | Higher-strike hedge, equal units, payoff/cost arithmetic, separate broker margin and optional budget ranking at fixed quantity | Owner-supplied inputs; no loss cap, quote verification, hedge selection or order submission |
| Runtime tests | 195 passed locally, four POSIX-only skips; live-line recovery/filtering and parallel-read coordination added to the retained suite | Functional verification is not strategy validation |
| Offline demo | Two persisted analysis jobs and HTML/text/MIME/PNG artifacts | Synthetic inputs and fake analyst; no email sent |
| SQLite operations | Backup API and restore-to-new-path preserved state/report identity | Automated off-host backups and retention are not installed |

## October 5 active-position charts and revised rule

### Live chart movement and refresh

Installed locally: default index/option lines now use actual LTP observations
from the existing SQLite journal at their reception times. They refresh with each
five-second target read instead of waiting for the next five-minute close. The
five-minute grid, entry/SL/target lines, point count and precise update caption
remain visible. Five-minute closes are selectable in settings and unchanged for
strategy research. Gaps over 20 seconds are disconnected; prices/curves are never
invented. An actual flat price still plots flat.

October 5 around 13:08–13:12 JST: Edge verified both existing manual NIFTY option
lines recovering more than 240 recorded points after restart, then increasing
automatically with changed SVG paths. The five-minute comparison and return to
live mode worked; the browser reported no console errors. Independent account/
quote/protection reads now overlap under the existing limiter. A real sample had
4.26–5.86-second observation gaps and a 3.14-second final read cycle. Network/
provider latency can extend five seconds; this is not a timing guarantee.
Private screenshot/cadence evidence remains in ignored `.agent-state/`.

The local suite passed 195 tests with four POSIX-only skips; configuration, JS
syntax and the offline demo passed. No real order, protective-order change,
trading activation or Oracle deployment occurred.

### News, scheduling, conflict policy and Groww money

Installed in the PC app: independent two-minute RBI/ET evidence collection;
structured Codex news assessment with exact cited IDs and 180-second expiry;
12:55/15:00/17:00 level research plus coalesced news/crossing triggers. The shared
eight-attempt daily cap and stale level status are visible. A weekday 12:55 JST
Codex thread heartbeat is ACTIVE; its first observed delivery arrived at 13:18:09
JST on October 5. The configured time and actual delivery time are distinct.
Oracle execution, recurring mail and off-host backups are not activated by this.

The journal now persists strategy ownership per slot. An everyday carry blocks
another entry and cannot be relabelled as swing; restart/legacy cases are tested.
Swing 18:45 is outside the 18:15 action cutoff, so it is research-only. No slot
is automatically released. Manual trades remain protected.

Real read-only Groww funds verification passed at 12:36 JST. The money card reads
clear cash, total used margin, separate option buying/selling balances and
collateral every 30 seconds. Signed provider fields are preserved; cash is not
treated as portfolio value, net P&L or permission to spend. Failed/stale reads
display unknown. The available-margin endpoint is GET-only in the existing
transport; broker writes and order-margin POST remain blocked.

Actual ET fetch returned 15 current articles; RBI returned no item within the
24-hour freshness policy. The app therefore shows UNKNOWN and blocks new-position
readiness. A real pinned Windows Codex run accepted the new strict output schema,
returned WAIT for missing market evidence and emitted zero tool events. The full
local suite passed (192 tests, four POSIX-only skips) and the offline demo produced
synthetic report artifacts without mail/orders. The 13:18 heartbeat verified both
12:55:01 app research requests completed with `WAIT / DATA_REQUIRED`, two of eight
daily attempts used, and no applied levels or rules. Each request contained 143
historical prices; the limited reason code does not identify exactly which
evidence the analyst considered insufficient. At 13:20 JST, current collector
timestamps, separate money reads and continuing two-minute news refreshes passed;
the existing Codex 0.160.0 pin/authentication passed. RBI still lacked fresh
mandatory evidence and news stayed UNKNOWN. Manual positions remained protected,
with no broker writes, activation or repairs. A full unattended session remains
unverified. Private dated evidence is `.agent-state/heartbeat-check-20261005-1318.json`.

**Installed in the local PC app:** option charts now require confirmed nonzero
positions. Historical fills, pending orders and closed positions do not qualify.
Blue entry lines use the actual Groww position average, with a quantity-matched
carry-forward fallback. Red SL/green target lines use matching pending SL orders
and active OCO/GTT exits; partial coverage is labelled. IDs/credentials never enter
the browser. Cancelled/completed/unrelated protection is excluded. Smart-order
lookup is bounded to the last 28 days; older smart orders may be absent. No order
was submitted or changed.

**Real read-only verification:** both existing manual NIFTY positions remain
protected. Groww supplied average entries and the OCO/GTT reads succeeded with zero
matching records. No SL/target price was invented. Before the session opens,
current-day chart history waits for completed five-minute prices. Labelled sample
preview verifies all six entry/SL/target lines, empty and one-sided position gating,
and a 390px layout without overflow; preview never enters the journal.

**Current strategy overlay:** NIFTY +500 Mon/Tue/Fri and SENSEX +1000 Wed/Thu,
from 13:15 JST; one active slot/lot; buy a higher same-expiry call. The comparison
recommends the greatest supplied maximum net expiry-profit within supplied margin.
Unknown costs/margin block recommendation. This is a payoff bound, not predicted
profit. The owner confirmed the higher call is bought and superseded the ATM buy.
Historical study/config versions remain reproducible. Execution remains inactive.

**Durable observation:** the existing SQLite Store now records deduplicated
whitelisted quote observations and completed five-minute bars, flags revisions and
publishes validated OBSERVE snapshots. Reception/source timestamps, missing news
assessment and unconnected accounting remain unknown. A live local capture reported
`RECORDED_AND_PUBLISHED`. Full-session coverage is not yet verified.

**Remaining broader integration:** the local maintenance/email-transport and Oracle
report-receiver drafts are not wired or deployed. Recurring mail, independent
outage monitoring, Oracle entry/protective execution, complete two-leg sessions
and strategy holdouts remain unfinished. Shared Oracle services and both pause
markers are preserved. This update does not activate any trading service.

## October 5 PC app integration

**Installed locally:** Desktop and Start `Options Trader.lnk`, plus a hidden
`Options Trader Monitor.lnk` in the current user's Windows Startup folder. Both
interactive app launch and the background-only startup entry point passed. The
server reports `background_monitor=true`, `chart_style=LINE` and
`execution_enabled=false`. A real logout/reboot was not performed.
The startup entry point and a real read-only dashboard capture also passed using
Windows PowerShell 5.1, the shell used by the installed sign-in shortcut.

**Implemented:** independent observation from 12:40–19:45 JST on weekdays;
support/resistance research from 12:55; fresh barrier crossings queue deduplicated
structured Codex requests. Rules cannot extend outside 13:00–18:15 JST, and the
everyday reference retains its 13:15 start. Requests expire after five minutes,
with eight daily analysis attempts. Windows CLI/hash pin changes fail preflight.
Broker credentials and IDs never enter the analyst context. Invalid/stale data and
invalid rule identities/levels/parameters are rejected.

Manual/unknown and mixed contracts are protected persistently. Exact private
intent/reference and acknowledged broker ID plus quantity reconciliation establish
engine ownership. There is no manual-adoption control. Pending acknowledgements
are not fills. One engine slot is enforced in the journal; no real entry executor
is installed. Actual expiry checks compare Groww expiry dates and the current
instrument master. The exact comparison covers the current month and nearest
listed expiry; differing distant-contract horizons cannot certify later dates.
Unknown/disagreeing evidence blocks entries. Holiday-shifted dates and year rollover
are tested. October 5 real-data revalidation agrees for both NIFTY and SENSEX.

The trailing planner and `OracleTrailingUpdater` are **source-only, disabled and
undeployed**. Fake-broker tests verify exact owned protective SL identity,
favorable tick-aligned ratchets, manual/stale/partial/time/pause rejection,
readback before confirmed-state advancement, and no resubmission after a timeout.
Provider acceptance does not mean a fill or verified modification. No actual SL
was placed or modified. Initial protective-order placement, Oracle transport and
service integration, approved stop/target values and payoff review remain.

**Current Codex:** actual Windows `codex-cli 0.160.0`, supported existing ChatGPT
authentication, pinned executable SHA-256 and a non-interactive schema-validated
WAIT run passed with zero tool events. The data-only worker uses the existing
CodexRunner and a private auth-only home. Windows OS privilege isolation remains
unverified; this is not a claim of a separate secured service account.

**Browser QA:** owner's Edge app window, all charts rendered as lines, zero chart
candlestick rectangles, actual unavailable/no-order panel gating, protected option
labels in sample preview, settings, keyboard focus and IST/JST switching. Layouts
have no document overflow at 320/390/768/1440 CSS pixels. Chart time-zone changes
do not change the Japan-time strategy schedule. Sample results remain labelled
and client-only. Temporary viewport overrides were reset.

**Local gates:** 183 tests passed with four POSIX-only skips. Configuration, Python
compilation, seven PowerShell helper syntax checks, JavaScript syntax and
`git diff --check` passed. The offline demo completed two persisted analysis jobs
and generated HTML/text/MIME/PNG report artifacts using synthetic data without
sending email. Current Codex schema verification completed in 7.509 seconds.
Private evidence and screenshots are ignored under `.agent-state/`.

**Current Groww readback:** October 5 authentication initially returned HTTP 403.
The existing Codex key showed Expired in the owner's Edge; the authorized approval
changed it to Approved, “Resets 6 AM tomorrow.” Authentication and all 22 probes
then passed from 10:45:08–10:45:37 JST. The app subsequently passed quote/order/
position reads and exact contract metadata; current-month/nearest-expiry agreement
was confirmed for both indices. Existing positions remain manual/unknown protected.
No key was replaced or permission changed. Pre-market option books were absent
and last trades were from October 1; these are not fresh executable quotes.
Current-day lines wait for completed session prices. Authentication failures back
off 60 seconds; failed reads never invent positions or results.

**Not deployed/activated:** Oracle entry execution, initial protective orders,
recurring visual mail, accounting reconciliation and independent outage monitoring.
`.trader-paused`, credentials, broker permissions and shared Oracle/Qwen/Colab/mail
services are preserved. No key rotation, real order, LIVE activation or merge.

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

## October 1 local dashboard verification

The owner-authorized local viewer extends the existing engine with static assets
and a loopback HTTP server. It is running manually on the PC at
`http://127.0.0.1:8765/`; it is not an Oracle deployment or trading activation.
Quotes/order records target five-second polling, and exact option histories use
completed five-minute candles. A separate history thread keeps chart retrieval
from stopping current quote reads. On October 1 at 18:46 JST, eight observed cycles
started 5.000–5.223 seconds apart; every market/order/position read was available.
This is measured behavior under that load, not a guaranteed network deadline.

Buy/sell panels are gated independently by matching broker order/open-position
records. Chain research samples do not create panels. Missing/partial broker
reads, pending orders, historical fills and current positions retain distinct
states. Exact option selection and real call/put history were verified in Edge.
P&L, margin and per-strategy returns are still unconnected to a reviewed private
ledger and remain unknown. Recorded non-loss/loss shares, including a 100% green
bar for completed trades without losses, pass the ledger tests and labelled
sample preview. No completed trades is “No results”.

The local suite passed 109 tests with four POSIX-only skips; configuration,
compilation, PowerShell syntax, JavaScript syntax and the offline demo passed.
Order gating, five-minute completion, history/quote independence, lease expiry,
secret filtering and local HTTP/Origin boundaries are covered. Browser QA includes
320/390/768/1440 CSS-pixel layouts, exact option selection, time-zone switching,
sample preview, keyboard focus and Escape dismissal. Further desktop/reference
and reduced-motion checks are recorded in the private QA evidence. An optional
second-port UI fixture was blocked by Edge's automation client; no-order and
single-side filtering are covered by unit tests. The real owner-opened dashboard
works in Edge. Private screenshots/cadence evidence are ignored by Git.

See [local dashboard setup and ledger format](LOCAL_HEADLESS.md#local-options-dashboard).
The October 5 PC observer adds unattended local collection. Account reconciliation,
strategy attribution, scheduled visual mail and Oracle execution remain unfinished/inactive. The engine pause,
broker permissions, credentials and shared VM lifecycle are unchanged.

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

## Historical strategy evidence, through October 1

The requested historical period was 2026-08-03 through 2026-09-29. The SENSEX expiry
screen has eight complete windows: five development and three holdout. It found
zero qualifying upward 500-point minute-close events in the unclassified broker
index series. With zero qualifying events, reversal probability and its confidence
interval are unknown. This neither validates nor refutes a separate indicative-price
observation; one-minute closes can also miss shorter spikes.

Version 2 adds the everyday hedged-call rule after 13:15 JST: NIFTY +400 on
Monday/Tuesday/Friday and SENSEX +800 on Wednesday/Thursday. The offline `everyday`
command reviews structured observations and existing entry levels. Flat/down
means hold review; any rise means exit review. It has no fixed next-day exit,
automatic quantity change or maximum-loss cap. Margin budget and expiry rule are
still unset. This direction check is not wired to continuous monitoring or orders.

Re-evaluation of the existing data produced 25 NIFTY and 16 SENSEX completed-minute
everyday references; BANKNIFTY is not applicable. Missing or non-preferred dates
are counted separately. The three previous version 1 study results reproduce
exactly. All four strategies have unknown net option-profit probabilities.
Required evidence includes verified indicative/regular series identity, source
timestamps, historical two-leg bid/ask/depth, exact contract/lot/expiry data, costs,
fill assumptions and enough independent out-of-sample sessions.

NSE's current lot file, fetched October 1 at 17:11 JST, has a September 29
Last-Modified header and lists NIFTY 65 for October 2026 and all displayed
maturities. This verifies the public reference, not a proposed broker contract or
its executable quote. The comparison uses the supplied contract lot and a fixed
quantity. [NSE permitted lot sizes](https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv)

The everyday change passed 92 local tests with four POSIX-only skips, configuration,
compilation, PowerShell syntax and the synthetic offline demo. The demo completed
two persisted jobs and generated visual artifacts without sending email.
Private evidence is `.agent-state/everyday-strategy-evidence-20261001.json` and
`.agent-state/nse-lot-metadata-20261001.json`. This source update is not deployed to Oracle.

## September 30 Codex verification

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
