"""Pure, completed-price direction check. No API, persistence or order authority."""
from datetime import datetime, timedelta, timezone
import math

from .contracts import IST, number, stamp
from .execution import fresh


POLICY = dict(format='intraday-price-action-v1', lookback_minutes=30,
    bar_minutes=5, minimum_directional_bars=4, minimum_efficiency=.6,
    minimum_move_bps=2.5, receipt_max_age_seconds=90,
    bar_max_age_seconds=390, live_retention_fraction=.5)


def history(payload, index, received_at):
    """Read the documented Groww five-minute OHLC response, never partial bars.

    Groww's naive exchange timestamps are IST/bar starts. Retain their exact
    times and only today's completed intervals. Never interpolate or deduplicate.
    """
    if (not isinstance(payload, dict) or payload.get('interval_in_minutes') != 5
            or payload.get('candle_interval', '5minute') != '5minute'
            or not isinstance(payload.get('candles'), list)
            or len(payload['candles']) > 100):
        raise ValueError('GROWW_FIVE_MINUTE_HISTORY_REQUIRED')
    rows, seen = [], set()
    now = received_at.astimezone(IST)
    for raw in payload['candles']:
        if not isinstance(raw, list) or len(raw) < 5:
            raise ValueError('VALID_INTRADAY_OHLC_REQUIRED')
        at = (datetime.fromisoformat(raw[0]) if isinstance(raw[0], str)
              else datetime.fromtimestamp(number(raw[0]), timezone.utc))
        if at.tzinfo is None: at = at.replace(tzinfo=IST)
        at = at.astimezone(IST)
        if at > now: raise ValueError('FUTURE_INTRADAY_BAR')
        if at.date() != now.date(): continue
        if at.second or at.microsecond or at.minute % 5 or not '09:15' <= at.strftime('%H:%M') < '15:40':
            raise ValueError('EXCHANGE_FIVE_MINUTE_BAR_START_REQUIRED')
        if at in seen: raise ValueError('DUPLICATE_INTRADAY_BAR')
        seen.add(at)
        opened, high, low, close = [number(v) for v in raw[1:5]]
        if not 0 < low <= min(opened, close) <= max(opened, close) <= high:
            raise ValueError('VALID_INTRADAY_OHLC_REQUIRED')
        if at + timedelta(minutes=5) <= now:
            rows.append(dict(at=at.isoformat(), open=opened, high=high, low=low, close=close))
    return dict(format='groww-intraday-bars-v1', index=index,
        source='GROWW_5MIN_OHLC', received_at=received_at.isoformat(),
        interval_minutes=5, bars=sorted(rows, key=lambda r:r['at'])[-12:])


def assess(index, evidence, spot, now, policy=POLICY):
    """Six completed five-minute intervals; daily MAs/ADX cannot veto direction.

    Parameters are declared hypotheses, not fitted or proven profit thresholds.
    A tiny drift, one jump or back-and-forth movement is not sufficient.
    """
    result = dict(method=policy['format'], status='UNKNOWN', regime='UNKNOWN',
        lookback_minutes=30, bar_minutes=5, reasons=[], broker_writes=False)
    def wait(reason, status='UNKNOWN'):
        result.update(status=status, reasons=[reason])
        return result
    try:
        if (not isinstance(evidence,dict) or evidence.get('format') != 'groww-intraday-bars-v1'
                or evidence.get('index') != index or evidence.get('source') != 'GROWW_5MIN_OHLC'
                or evidence.get('interval_minutes') != 5
                or not fresh(evidence['received_at'], now, policy['receipt_max_age_seconds'])):
            return wait('CURRENT_TREND_INPUT_REQUIRED:intraday')
        spot = number(spot)
        if spot <= 0: raise ValueError()
        rows = evidence['bars']
        if not isinstance(rows, list) or len(rows) > 12: raise ValueError()
        if len(rows) < 6: return wait('INTRADAY_TREND_WARMUP', 'WAIT')
        rows = rows[-6:]
        times = [stamp(r['at']).astimezone(IST) for r in rows]
        if (any(t.date() != now.astimezone(IST).date() or t.second or t.microsecond
                or t.minute % 5 or not '09:15' <= t.strftime('%H:%M') < '15:40' for t in times)
                or any(b-a != timedelta(minutes=5) for a,b in zip(times,times[1:]))):
            return wait('CURRENT_TREND_INPUT_REQUIRED:intraday_contiguous_bars')
        end = times[-1]+timedelta(minutes=5)
        if end > now or not fresh(end.isoformat(), now, policy['bar_max_age_seconds']):
            return wait('CURRENT_TREND_INPUT_REQUIRED:intraday_bar_age')
        for r in rows:
            opened, high, low, close = [number(r[k]) for k in ('open','high','low','close')]
            if not 0 < low <= min(opened,close) <= max(opened,close) <= high: raise ValueError()
        prices = [rows[0]['open']] + [r['close'] for r in rows]
        moves = [b-a for a,b in zip(prices,prices[1:])]
        net, path = prices[-1]-prices[0], math.fsum(abs(v) for v in moves)
        direction = 1 if net > 0 else -1
        agreeing = sum(v*direction > 0 for v in moves)
        minimum = prices[0]*policy['minimum_move_bps']/10000
        efficiency = abs(net)/path if path else 0
        retained = (spot-prices[0])*direction >= abs(net)*policy['live_retention_fraction']
        result.update(window_start_at=times[0].isoformat(), window_end_at=end.isoformat(),
            source_received_at=evidence['received_at'], bar_count=6,
            start_price=prices[0], close_price=prices[-1], change_points=net,
            change_bps=net/prices[0]*10000, agreeing_bars=agreeing,
            efficiency=efficiency, minimum_move_points=minimum,
            minimum_directional_bars=policy['minimum_directional_bars'],
            minimum_efficiency=policy['minimum_efficiency'], live_direction_retained=retained)
        if abs(net) < minimum: return wait('INTRADAY_MOVE_BELOW_NOISE_FLOOR', 'WAIT')
        if agreeing < policy['minimum_directional_bars'] or efficiency < policy['minimum_efficiency']:
            return wait('INTRADAY_DIRECTION_NOT_SUSTAINED', 'WAIT')
        if not retained: return wait('INTRADAY_DIRECTION_REVERSED', 'WAIT')
        result.update(status='CONFIRMED', regime='BULLISH' if direction > 0 else 'BEARISH')
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return wait('CURRENT_TREND_INPUT_REQUIRED:intraday')
