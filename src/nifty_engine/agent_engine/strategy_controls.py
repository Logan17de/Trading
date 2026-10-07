"""Private owner preferences. Switches permit entries, never authorize orders."""
from __future__ import annotations

from .contracts import dumps
from .report_strategies import IDS

KEY = 'report-strategy-controls-v1'
EXECUTABLE = IDS[:-1]


def validate_command(body):
    if (not isinstance(body, dict) or set(body) != {'id', 'enabled', 'revision'}
            or body['id'] not in IDS or type(body['enabled']) is not bool
            or type(body['revision']) is not int or not 0 <= body['revision'] < 2**31):
        raise ValueError('INVALID_STRATEGY_SWITCH')
    return body


def read(store):
    value = store.meta(KEY, {})
    if not value:
        return dict(format=KEY, revision=0, enabled={sid: False for sid in IDS}, updated_at=None)
    if (value.get('format') != KEY or set(value.get('enabled', {})) != set(IDS)
            or any(type(v) is not bool for v in value['enabled'].values())
            or type(value.get('revision')) is not int):
        raise ValueError('STRATEGY_CONTROLS_CORRUPT')
    return value


def set_switch(store, body, now):
    validate_command(body)
    with store.transaction() as db:
        current = read(store)
        if current['revision'] != body['revision']:
            return dict(status='REVISION_CONFLICT', controls=current, broker_writes=False)
        if current['enabled'][body['id']] == body['enabled']:
            return dict(status='UNCHANGED', controls=current, broker_writes=False)
        current['enabled'][body['id']] = body['enabled']
        current.update(revision=current['revision'] + 1, updated_at=now.isoformat())
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (KEY, dumps(current)))
    return dict(status='SAVED', controls=current, broker_writes=False)
