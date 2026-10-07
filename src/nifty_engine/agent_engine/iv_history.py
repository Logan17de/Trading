"""Private matched IV evidence import. No network, orders or activation writes.

Structural validation is not provenance certification. An operator must review
the actual dataset and independent session calendar before installing their
exact hashes. The current provider series must match the reviewed history.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .contracts import EXCHANGES, IST, identity, keys, number, stamp
from .execution import fresh

FORMAT = 'trading-matched-iv-history-v1'
HISTORY = 'report-matched-iv-history-'
CURRENT = 'report-matched-iv-current-'


def series(value):
    keys(value, {'provider', 'methodology', 'index', 'tenor_days', 'unit'})
    if (value['index'] not in ('NIFTY', 'SENSEX') or value['tenor_days'] != 30
            or type(value['tenor_days']) is not int or value['unit'] != 'annualized_percent'
            or any(not isinstance(value[k], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value[k])
                   for k in ('provider', 'methodology'))
            or value['methodology'] == 'GROWW_ATM_VARIANCE30_RESEARCH_V1'):
        raise ValueError('MATCHED_PROVIDER_30_DAY_IV_SERIES_REQUIRED')
    return identity(value)


def validate(data, now):
    keys(data, {'format', 'series', 'calendar', 'observations'})
    sid = series(data['series'])
    if data['format'] != FORMAT: raise ValueError('MATCHED_IV_FORMAT_REQUIRED')
    calendar = data['calendar']
    keys(calendar, {'exchange', 'source', 'coverage_start', 'coverage_end', 'sessions'})
    if (calendar['exchange'] != EXCHANGES[data['series']['index']]
            or not isinstance(calendar['source'], str) or not 1 <= len(calendar['source']) <= 200):
        raise ValueError('INDEPENDENT_EXCHANGE_CALENDAR_REQUIRED')
    start, end = (date.fromisoformat(calendar[k]) for k in ('coverage_start', 'coverage_end'))
    today = now.astimezone(IST).date()
    days = calendar['sessions']
    if (not isinstance(days, list) or not 252 <= len(days) <= 2000
            or days != sorted(set(days)) or start > end or end != today
            or any(not start <= date.fromisoformat(d) <= end for d in days)):
        raise ValueError('COMPLETE_DATED_SESSION_CALENDAR_REQUIRED')
    prior = [d for d in days if date.fromisoformat(d) < today][-252:]
    rows = data['observations']
    if (len(prior) != 252 or not isinstance(rows, list) or len(rows) != 252
            or [r.get('day') for r in rows] != prior):
        raise ValueError('EXACT_252_PRIOR_SESSIONS_REQUIRED')
    for row in rows:
        keys(row, {'day', 'value', 'observed_at', 'source_sha256'})
        at = stamp(row['observed_at']).astimezone(IST)
        if (at.date().isoformat() != row['day'] or not (15, 25) <= (at.hour, at.minute) < (15, 31)
                or not 0 < number(row['value']) < 500
                or not isinstance(row['source_sha256'], str)
                or not re.fullmatch('[a-f0-9]{64}', row['source_sha256'])):
            raise ValueError('DATED_CLOSE_IV_AND_SOURCE_DIGEST_REQUIRED')
    last = rows[-1]['observed_at']
    if not fresh(last, now, 259200): raise ValueError('IV_HISTORY_END_STALE')
    return dict(index=data['series']['index'], series_sha256=sid, dataset_sha256=identity(data),
                calendar_sha256=identity(calendar), sessions=252, history_end_at=last)


def install(store, data, now, *, reviewed_dataset_sha256, reviewed_calendar_sha256):
    check = validate(data, now)
    if (reviewed_dataset_sha256 != check['dataset_sha256']
            or reviewed_calendar_sha256 != check['calendar_sha256']):
        raise ValueError('EXACT_DATASET_AND_CALENDAR_REVIEW_REQUIRED')
    record = dict(data=data, review=check, reviewed_at=now.isoformat())
    store.set_meta(HISTORY + check['index'], record)
    return dict(check, status='REVIEWED_IMPORT', broker_writes=False, execution_authorized=False)


def current(store, data, now):
    keys(data, {'series', 'value', 'observed_at', 'source_sha256'})
    sid = series(data['series'])
    if (not 0 < number(data['value']) < 500 or not fresh(data['observed_at'], now, 15)
            or not isinstance(data['source_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', data['source_sha256'])):
        raise ValueError('CURRENT_DATED_IV_SOURCE_REQUIRED')
    saved = store.meta(HISTORY + data['series']['index'], {})
    if sid != saved.get('review', {}).get('series_sha256'):
        raise ValueError('CURRENT_IV_METHOD_OR_INDEX_DIFFERS')
    if validate(saved['data'], now) != saved['review']:
        raise ValueError('REVIEWED_HISTORY_CHANGED')
    store.set_meta(CURRENT + data['series']['index'], data)


def features(store, index, now):
    """Revalidate each use: no stale cached percentile or mixed provider methods."""
    try:
        saved = store.meta(HISTORY + index, {})
        check = validate(saved['data'], now)
        if check != saved['review'] or check['index'] != index:
            raise ValueError('REVIEWED_HISTORY_CHANGED')
        point = store.meta(CURRENT + index, {})
        if (series(point['series']) != check['series_sha256']
                or not fresh(point['observed_at'], now, 15) or not 0 < number(point['value']) < 500
                or not re.fullmatch('[a-f0-9]{64}', point['source_sha256'])):
            raise ValueError('CURRENT_MATCHED_IV_REQUIRED')
        common = dict(source=point['series']['provider'], observed_at=point['observed_at'],
            series_sha256=check['series_sha256'], dataset_sha256=check['dataset_sha256'])
        return dict(iv30=dict(common, value=point['value'], unit='annualized_percent'),
            iv_percentile=dict(common, value=sum(r['value'] <= point['value'] for r in saved['data']['observations']) / 252 * 100,
                unit='percentile', sessions=252, history_end_at=check['history_end_at']))
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}


def public(store, index, now):
    saved = store.meta(HISTORY + index, {})
    available = bool(features(store, index, now))
    return dict(status='MATCHED_CURRENT' if available else 'MATCHED_IV_INPUTS_REQUIRED',
        reviewed_sessions=saved.get('review', {}).get('sessions', 0),
        history_end_at=saved.get('review', {}).get('history_end_at'),
        current_available=available, execution_authorized=False, broker_writes=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--database', type=Path)
    p.add_argument('--current', action='store_true')
    p.add_argument('--reviewed-dataset-sha256')
    p.add_argument('--reviewed-calendar-sha256')
    a = p.parse_args()
    try:
        if a.input.stat().st_size > 2_000_000: raise ValueError('IV_IMPORT_TOO_LARGE')
        data = json.loads(a.input.read_text(encoding='utf-8-sig'))
        now = datetime.now(timezone.utc)
        if not a.database:
            if a.current: raise ValueError('CURRENT_IMPORT_NEEDS_PRIVATE_JOURNAL')
            result = dict(validate(data, now), status='STRUCTURALLY_VALID_REVIEW_REQUIRED', broker_writes=False)
        else:
            from .store import Store
            store = Store(a.database)
            if a.current:
                current(store, data, now)
                result = public(store, data['series']['index'], now)
            else:
                result = install(store, data, now, reviewed_dataset_sha256=a.reviewed_dataset_sha256,
                    reviewed_calendar_sha256=a.reviewed_calendar_sha256)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        p.exit(1, 'MATCHED_IV_IMPORT_REJECTED; original data and owner intent unchanged\n')


if __name__ == '__main__': main()
