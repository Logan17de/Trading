import sqlite3

from nifty_engine.agent_engine.oracle_runtime import observer_error


def test_observer_failure_locates_sqlite_error_without_private_message_or_source():
    db = sqlite3.connect(':memory:')
    try:
        db.execute('SELECT private_credential FROM missing_table')
    except sqlite3.OperationalError as exc:
        result = observer_error(exc)
    finally:
        db.close()
    assert result['sqlite_code'] == 'SQLITE_ERROR'
    assert result['frames'][-1]['function'].startswith('test_observer_failure')
    assert result['frames'][-1]['file'] == 'test_observer_error.py'
    assert 'private_credential' not in str(result) and 'missing_table' not in str(result)


def test_provider_message_and_forged_sqlite_code_never_reach_observer_failure():
    exc = ValueError('secret API response')
    exc.sqlite_errorname = 'https://private/api-key'
    assert observer_error(exc) == dict(ok=False, error_type='ValueError', code=None, frames=[])
