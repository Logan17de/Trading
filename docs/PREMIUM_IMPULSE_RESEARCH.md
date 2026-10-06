# Premium Impulse Monitor · NIFTY and SENSEX

October 6 owner specification: **OBSERVE → RECORD → LABEL → ANALYZE**.
This is MONITOR_ONLY. It cannot place, modify or cancel orders, change a strategy,
set owner intent, remove the pause, or influence the executor. The existing
six-signal score is an exploratory observer; it is not an entry approval or a
profit probability. No threshold has been optimized.

## Architecture and files

GrowwFeed price/depth callbacks → bounded queue → existing stream worker →
`PremiumImpulseMonitor` → private Oracle SQLite journal. A one-second supervisor
saves explicitly aged snapshots and batches raw writes outside the processing
lock. A separate `SupabaseArchive` thread durably queues/upserts private records.
Network failure retains the local journal/outbox; it never changes trading state.
The existing private SSH/SSE viewer shows monitor and archive status.

- `impulse_research.py`: config validation, rolling movements, locked events,
  outcomes, summaries, private inspection and offline replay.
- `impulse_feed.py`: actual master tokens, callback normalization, top-five depth,
  nullable OI/IV/Greeks, receive timestamps and independent research maintenance.
- `research_sync.py`: server-only archive, local cursors/outbox, lossless raw
  chunks, idempotent retries, filtered algo state/order/P&L exports.
- `config/premium_impulse_monitor.json`: enable flag, MONITOR_ONLY, both ladders,
  freshness/lock/idle settings and exploratory report sample count.
- `oracle_runtime.py`: archive worker/status; existing trading gates unchanged.
- Existing dashboard HTML/JS: small monitor/archive status line, same application.
- `deploy/premium-research-supabase.sql`: additive archive DDL, applied through
  Supabase migration `trading_private_research_archive` to the existing project.
- `tests/test_impulse_research.py`, `tests/test_research_sync.py`, retained
  `tests/test_impulse.py`: stream, event, lifecycle, storage and retry verification.

## Instruments and event semantics

Both indexes: spot, nearest expiry ATM CE/PE and ±1/±2 listed strikes. The stream
subscribes ±3 to allow movement, extends subscriptions every 30 seconds, and
retains old contracts while events are locked. Actual master expiry is used.
Up to three listed futures expiries are sampled. Research picks the nearest with
fresh two-sided depth and positive observed five-second volume increment;
otherwise front-month evidence is labelled **liquidity NOT_VERIFIED**, not liquid.

| Index | Five-second premium thresholds |
| --- | --- |
| NIFTY | ₹5 / ₹7.5 / ₹10 / ₹12.5 / ₹15 |
| SENSEX | ₹15 / ₹20 / ₹25 / ₹30 / ₹40 |

The first ATM CE/PE threshold starts one event per index. CE implies UP, PE DOWN;
this is a hypothesis. The same event records higher rolling-window crossings.
Each crossing stores its own observed timestamp/window anchor and change. First
callback wins a simultaneous CE/PE tie; opposite-option movement is recorded and
both rising is flagged, not treated as proof of direction.
Expiry, ATM, CE/PE and all ten neighbouring contracts remain fixed for at least
30 seconds. No new event replaces this lock even if spot/master moves. It closes
after the lock and ten seconds without another impulse. Underlying follow-up
continues separately to 120 seconds; quiet/no-feed snapshots do not invent ticks.

Duplicate/out-of-order/stale/missing timestamp packets are recorded as rejected
observations, not scored. Per-symbol gaps clear that symbol's rolling history.
Reconnect, overflow, day rollover or restart interrupt pending follow-ups.
Already completed outcomes are retained. Coverage is checked separately for
spot/futures: a quiet option does not erase a complete underlying observation.

## Exact stored fields

Private SQLite tables in `.agent-state/pc-monitor.sqlite3`:

| Table | Indexed columns and JSON body |
| --- | --- |
| `premium_impulse_ticks` | id, trade_date, exchange_timestamp (nullable), receive_timestamp, processing_timestamp, generation, symbol, kind, body |
| `premium_impulse_events` | event_id, trade_date, index_name, start, body |
| `premium_impulse_outcomes` | event_id + horizon primary key, body |
| `daily_research_summary` | trade_date primary key, generated_at, body |

Tick body: symbol/kind, normalized exchange/receive/processing epoch seconds,
raw provider timestamp/unit, connection generation, research config hash,
accepted/rejection_reason, actual instrument metadata; price, volume,
open_interest, iv, delta, gamma, theta, vega. Depth adds bid/ask, bid/ask quantities,
spread, top5_bids/asks, static order_book_imbalance, best-quote order_flow_event and
complete-window order_flow_imbalance_5s. Aggressive buy/sell volume, trade-flow
imbalance and CVD are null without a reliable aggressor source.

Snapshot body: snapshot_time, original timestamped observations for spot, locked
options and sampled futures; per-option premium_change (1/2/3/5/10 seconds),
velocity (1/3/5), acceleration_1s; explicit LAST_OBSERVED semantics. A missing/
stale observation is null. Snapshot time is not an exchange/trade timestamp.

Event body: ID/index/direction/type/symbol/trade_date; trigger, receive, processing
and initial premium-window timestamps; premium_start/at_trigger; locked_expiry,
locked_atm_strike, locked_contracts, lock_until; thresholds_crossed entries with
exchange timestamp, seconds_from_event_start/window_start and actual rolling
anchor/change; last_impulse, closed_at/status/followup_status/data_gap; spot/
future start price and original timestamp, future_symbol/liquidity status;
research_parameters/config hash; time_bucket_ist/DTE; 30/60/300-second high/low
context/distances, full-session extrema UNKNOWN, separately labelled observed
history extrema; observed break-level crossing times; continuation point/times;
trigger/latest_relationships; signal_combinations; provisional labels; latest
false_flags and first-ten-second early_false_flags; completed_horizons.

Relationships: locked-neighbour offset/type, 1/3/5/10-second movements, volume/OI/
IV/Greeks, volume_delta_5s/OI_delta_5s, fresh depth and nullable flow fields; leading/
opposite five-second changes, directional_option_spread, number_of_strikes_
confirming_direction; expected_option_move/excess using supplied delta/gamma and
the same underlying window; futures volume/OI/change_5s and IV regime UNKNOWN.

Outcome body: horizon_seconds, OBSERVED/UNKNOWN, real endpoint_exchange_timestamp/
delay_seconds, spot_change/futures_change, maximum_favorable_move/adverse_move,
continuation flags for 5/10/15/20/30/50 points, data_gap. Response horizons are
0.25/0.5/1/2/3/5/10/15/30/60/120 seconds. Endpoint is the first actual tick at/after
the deadline within one second, with its delay disclosed. Extrema/continuation
use only actual ticks at/before the horizon. Sparse feed cannot prove a 250ms
reaction. Crossing times are observed, not interpolated.

Labels: DIRECTIONAL_CANDIDATE, IV_EXPANSION_CANDIDATE,
LIQUIDITY_SPIKE_CANDIDATE, BREAKOUT_CANDIDATE, FAILED_BREAKOUT, UNKNOWN.
These rules are descriptive research heuristics, not proof of institutional flow.

## Supabase archive and privacy

Existing project `Growing-Trader` (`imirspxhbnerxknyynqx`), table
`public.trading_research_records`. Primary key: source_id + record_key. Columns:
category, trade_date, index_name, observed_at, body, content_sha256, mode,
uploaded_at. RLS enabled; PUBLIC/anon/authenticated grants revoked. Only the
server role receives SELECT/INSERT/UPDATE. No public/browser policy or endpoint.
RLS-without-policy advisor INFO is intentional for this server-only table.
Existing unrelated shared-project advisory warnings were not changed.

Categories: impulse_ticks, impulse_event, impulse_outcome, daily_summary,
algo_order, algo_state, algo_pnl. Raw chunks contain up to 100 observations as
lossless gzip+base64 JSON, original-byte SHA256 and research configuration maps.
Event/outcome/summary keys upsert their latest complete content. Algo state/order
changes append content-hashed versions. Only journal-owned Everyday/Late-session
orders and algo P&L are exported; unknown P&L stays null. Manual orders, capital
ledger, credentials, authentication settings and raw error/response bodies are
excluded. Actual broker identifiers in selected algo state are hashed.

Oracle reuses existing SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY from the approved
root0600 environment; no key generation/rotation. No server key reaches the PC.
Local transaction queues a batch and advances its source cursor atomically.
Provider acceptance clears that batch. Lost acknowledgments/restarts retry the
same cloud key. HTTP failures have bounded backoff; only safe status codes are
reported. Pending records/local uncaptured ticks remain visible. Cloud uploads
can lag; local journal remains authoritative. No trading-readiness dependency.

## Reports, start/stop and inspection

Read-only research starts with the existing `trading-observer` service once its
Groww session is available. Collection schedule stays 12:40–19:45 JST weekdays.
To disable **only research**, deploy config with `enabled:false` and restart only
the observer. Re-enable with true. `trading_enabled:true` or another mode fails
closed. Never stop Oracle/shared services or remove `.trader-paused` to collect.

Summaries are generated after 20:00 JST with catch-up for missed completed days.
They use IST buckets specified by the owner, CE/PE separation, actual calendar
DTE 0/1/2/3+, and UNKNOWN IV regime until a calibrated baseline exists.
Each threshold/condition compares impulse alone, +futures, +opposite, +neighbours,
+quote OFI, and all four. Combination evidence is frozen at the trigger to avoid
hindsight selection; later relationships remain available for further study.
Rates show known/unknown denominators, descriptive continuation percentages,
Wilson 95% intervals and INSUFFICIENT_SAMPLES below 30 (configurable). False-signal
flags/rates use the first ten seconds. Favorable/adverse means and medians are
underlying points, not trading profit. No missing outcome is counted as a loss.

On Oracle, using the deployed release's isolated Python and service user:

```sh
sudo -u trading-observer <release>/venv/bin/python -I -m nifty_engine.agent_engine.impulse_research --database /var/lib/trading-observer/.agent-state/pc-monitor.sqlite3 --events --day 2026-10-06
sudo -u trading-observer <release>/venv/bin/python -I -m nifty_engine.agent_engine.impulse_research --database /var/lib/trading-observer/.agent-state/pc-monitor.sqlite3 --day 2026-10-06
```

Save inspection output only in private state. Omit --day for today's IST date.
Add `--replay-to <new-private-db>` for offline replay. Destination must not exist;
it never overwrites production. Replays use the recorded config and generation
boundaries. They have no network or broker client.

Supabase SQL editor, as the authorized project owner:

```sql
select trade_date,category,index_name,count(*)
from public.trading_research_records
where trade_date = current_date
group by trade_date,category,index_name;
```

Tick chunk decoding for private study:

```python
raw = gzip.decompress(base64.b64decode(record['body']['data']))
assert hashlib.sha256(raw).hexdigest() == record['body']['raw_sha256']
ticks = json.loads(raw)
```

Example **synthetic fixture**, not live evidence or a probability estimate:

| Index/type | Threshold | Events | Known 5s outcomes | +10 points by 5s | Sample status |
| --- | ---: | ---: | ---: | ---: | --- |
| NIFTY CE | ₹5 | 1 | 1 | 1/1 = 100% | INSUFFICIENT_SAMPLES; Wilson lower bound ≈20.7% |
| SENSEX PE | ₹15 | 0 | 0 | UNKNOWN | INSUFFICIENT_SAMPLES |

## Source limits and unchanged trading behavior

[Groww Feed docs](https://groww.in/trade-api/docs/python-sdk/feed) document price/
index/depth callbacks, not a guaranteed tick cadence or populated Greeks/IV/OI.
SDK 1.5.0 fields are retained when actually supplied; missing/default zeros stay
null. Depth is aggregate quote demand/supply, not authenticated trader identity.
No aggressive-volume/CVD/institution identification is inferred. Full-session
extrema are unknown when collection did not cover the session. The older score's
historical volume baseline may use minute volumes; **research movement uses raw
ticks only**, never minute candles. Actual latency needs live evidence.

[Supabase API security](https://supabase.com/docs/guides/api/securing-your-api)
supports the combined explicit grants/RLS boundary. No privileged RPC, view,
trigger, broker command or trading permission was added. Both strategies, their
risk settings, manual protection, owner intent, paper mode and trading pause
remain unchanged. Monitor order methods raise MONITOR_ONLY_NO_BROKER_WRITES.
