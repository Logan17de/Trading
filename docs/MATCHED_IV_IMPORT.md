# Matching IV history connection

The three credit routes can now consume a reviewed private 252-session IV30
dataset and a current observation from the exact same index/provider/method.
This is an evidence adapter, not a supplied dataset or automatic activation.
No provider access, licensed export, historical data or live source is invented.
The existing ATM research proxy remains excluded from execution features.

## Private data contract

`iv_history.py` accepts JSON under the protected private state directory. Keep
source files, credentials and account identities out of Git. History has exactly
these top-level fields:

| Field | Required contents |
| --- | --- |
| format | `trading-matched-iv-history-v1` |
| series | provider and methodology identifiers, index `NIFTY`/`SENSEX`, tenor_days30, unit `annualized_percent` |
| calendar | exchange `NSE`/`BSE`, independently reviewed source, coverage_start/end dates, sorted unique actual session dates through today, `session_closes` mapping each date to its actual timezone-aware close |
| observations | exactly the latest252 completed sessions before today, in calendar order |

Each observation contains `day`, `value`, timezone-aware `observed_at`, and
`source_sha256` of the retained original provider payload. The timestamp must
belong to that exchange day and the final five minutes up to its independently
reviewed actual close. Exceptional sessions use their actual close rather than
the regular15:30 IST time. Missing,
duplicate, future, stale or intraday substitutes are rejected. Values are
annualized percentage volatility, not decimal fractions or price candles.
History must end within72 hours; the session calendar is not generated from
weekdays or the observations themselves. Calendar coverage/date review must
include exceptional sessions and actual holidays.

Validation without a database prints structural results and canonical SHA256s
only. It does not attest source truth or change state:

```powershell
.venv\Scripts\python.exe -I -m nifty_engine.agent_engine.iv_history `
  .agent-state\private-provider-history.json
```

A reviewed import also requires `--database`, `--reviewed-dataset-sha256` and
`--reviewed-calendar-sha256` matching the actual reviewed files. These hashes are
explicit operator attestations, not cryptographic proof of provider authenticity.
Review the original source, exchange-session coverage, sampling convention,
volatility units,30-day construction, point-in-time availability and usage rights
before installing. Do not simply copy validator hashes and declare provenance
verified. A NIFTY-only benchmark is not SENSEX data.

Run production imports on Oracle as `trading-observer` against its existing
private journal. Preserve root-only credentials; never make journals/WAL files
root-owned through an import. The import changes only matched-IV metadata,
never owner intent, switches, activation proof, broker orders or research dates.

## Current observations and ongoing refresh

The provider connector must supply current JSON with exactly `series`, `value`,
`observed_at`, `source_sha256`. Import it with `--current --database`. The series
identity must match the reviewed history. A current observation expires after
15seconds. Its independent provider timestamp is never refreshed by the
collector, UI, a restart or a report.

Production recomputes percentile against the252 reviewed prior closes. It checks
history/review hashes, matching current series and freshness again during each
preparation and validation. Forged feature-only bundles cannot bypass that path.
The next session needs an updated provider history/calendar and review; this
module does not fill missing days or implement an unverified network provider.
Synthetic fixtures exercise imports and three guarded routes; none are installed
in the production journal. The research proxy and38-day study remain separate.

## Source investigation

[Global Datafeeds GetHistoryGreeks](https://globaldatafeeds.in/global-datafeeds-apis/global-datafeeds-apis/greeks-api-global-datafeeds-apis/gethistorygreeks-returns-historical-greeks-data/)
documents timestamped historical per-contract IV/Greeks with date ranges and an
API access key. That is a candidate input, not proof of252-session retention,
SENSEX entitlement,30-day benchmark construction or a ready licensed feed.
Its example IV units and timestamp units differ from this normalized contract;
both need actual payload verification. No subscription was purchased or provider
secret created. Current Groww contract IV cannot be mixed with a different
provider's historical series merely because both are called IV.

Actual current data access and provider coverage remain required. Until then,
the dashboard shows reviewed sessions0/current missing and the routes stay blocked.
