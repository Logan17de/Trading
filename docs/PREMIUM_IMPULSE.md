# Read-only streaming premium impulse detector

The October 6 multi-threshold research specification is implemented for both
indexes in [PREMIUM_IMPULSE_RESEARCH.md](PREMIUM_IMPULSE_RESEARCH.md). That module
locks event contracts, records raw observations/outcomes and privately archives
research/algo data to Supabase. The score below remains an exploratory observer;
it is isolated from order authorization and is not an optimized strategy.

Owner clarification October 6: detect a NIFTY +₹10 or SENSEX +₹30 ATM option
premium move over five seconds. Aim to confirm at 80/100 within 2–3 seconds,
with a ten-second deadline measured from the provider's impulse timestamp.
This is an observation layer for the existing two strategies, not a new entry
strategy, order authorization, win probability or guaranteed reaction time.

| Signal | Points | Implemented evidence |
| --- | ---: | --- |
| Futures direction | 25 | Current front-month future's five-second change agrees with CE-up/PE-down market direction |
| Actual breakout | 20 | Fresh spot or future breaks the previous 60-second high/low, excluding the current point |
| Order-flow | 20 | Best-quote event OFI has the correct sign and current bid/ask quantity imbalance is at least 0.20 in that direction; future or impulsing option |
| Volume acceleration | 15 | Actual streamed cumulative futures-volume increment over 60 seconds ≥2× median one-minute volume at that time of day, from ≥5 distinct prior sessions |
| Adjacent strikes | 10 | Same-expiry/type ATM−1 and ATM+1 listed strikes both rise with the premium impulse |
| Major-stock breadth | 10 | ≥60% of a five-stock major constituent sample agrees; equal votes, not full weighted index breadth |

The breadth sample is RELIANCE, HDFCBANK, ICICIBANK, INFY and BHARTIARTL,
using exact NSE cash master tokens for both indexes. SENSEX breadth therefore
uses same-company NSE quotes as a proxy. No inferred index weights are used.
Expiry and exact exchange tokens come from the current Groww master. Spot
ticks update the monitored ATM; additional adjacent contracts are subscribed
as needed by an independent 30-second universe supervisor, capped at 100 tokens.
Missing subscribed neighbours/futures stay UNKNOWN. A bounded reconnect can
require history warm-up again; no synthetic history bridges a gap.

Confirmation needs ≥80 points, known evidence for all six signals, and passes
for futures, breakout, OFI and neighbours. Missing/stale evidence earns no
points and is never renormalized. Futures disagreement is labelled possible
IV/noise, not proof of its cause. Zero/unpopulated volume is UNKNOWN. Historical
volume backfill is independent and provider failure keeps volume UNKNOWN.

## Timing and transport

- GrowwFeed callbacks enqueue changed price/depth topics. A dedicated worker
  scores them immediately; it does not wait for the five-second REST observer.
- Three-second provider freshness, duplicate/out-of-order rejection and queue
  overflow resets prevent old events from becoming current evidence.
- Breakout/volume need 60 seconds of real stream history. Impulse needs five
  seconds. Volume also needs the prior-session baseline. Report waiting first.
- Local processing time and provider-to-score lag are reported separately;
  broker/network delays and VM scheduling prevent an instantaneous guarantee.
- Confirmed events persist in the private SQLite journal. Historical events
  remain explicitly dated and are not advertised as current confirmations.
- A fixed read-only `watch_impulse` Unix-socket command travels over one
  persistent protected SSH connection to the PC. Loopback SSE updates the
  detector display independently of the dashboard's five-second refresh.
- Account/positions/money retain their five-second observer and ownership
  checks. Streaming never calls the executor or changes an owned/manual trade.

The SDK requires ephemeral socket authentication. Only its exact token route
is additionally allowed by the read-only HTTP guard. Its socket JWT/seed files
are redirected to private mode-0700 state with mode-0600 files, never written
into the immutable SDK/source tree, printed, or sent to the PC. Original broker
keys are reused, not rotated. Reconnect closes only this feed's own NATS client.
Keep `.trader-paused`, paper mode, owner Off and shared services intact.

## Sources and limits

[Groww feed documentation](https://groww.in/trade-api/docs/python-sdk/feed)
documents callback price/depth subscriptions. The installed pinned 1.5.0 price
protobuf also exposes volume; its live population must be checked separately.
[Cont, Kukanov and Stoikov](https://arxiv.org/abs/1011.6402) study best-quote event
OFI using US-stock data. Their results do not validate this score, these weights,
or NIFTY/SENSEX profitability. These thresholds are an owner research heuristic.
