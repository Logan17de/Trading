import sqlite3
import json
from types import SimpleNamespace

from nifty_engine.agent_engine.oracle_runtime import observer_error
from nifty_engine.agent_engine.oracle_runtime import Runtime


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


def test_background_research_keeps_snapshot_timestamps_and_unavailable_stays_unknown(tmp_path, monkeypatch):
    from nifty_engine.agent_engine import report_data
    from nifty_engine.agent_engine.store import Store
    runtime = Runtime.__new__(Runtime)
    store = Store(tmp_path/'worker.sqlite3')
    runtime.state = SimpleNamespace(pnl_lines=SimpleNamespace(store=store))
    runtime.output = tmp_path/'snapshot.json'
    runtime.research_catalog = {'indices':['NIFTY','SENSEX']}
    runtime.normal_catalog = {'format':'normal-theta-v1'}
    calls = []
    monkeypatch.setattr(report_data, 'connect_snapshot', lambda *args: calls.append(args))
    source = {'finished_at':'2026-10-07T04:00:00+00:00',
              'execution_observation':{'complete':True,'received_at':'2026-10-07T03:59:59+00:00'}}
    runtime.output.write_text(json.dumps(source))
    runtime.connect_research_snapshot()
    assert calls[0][1] == source
    runtime.output.write_text('{bad')
    runtime.connect_research_snapshot()
    assert len(calls) == 1
    assert store.meta('report-data-status-NIFTY')['reason'] == 'RESEARCH_SNAPSHOT_UNAVAILABLE'


def test_preparation_keeps_five_second_connections_and_sixty_second_network_budget(tmp_path, monkeypatch):
    from nifty_engine.agent_engine import oracle_runtime
    runtime = Runtime.__new__(Runtime)
    runtime.root = tmp_path
    runtime.output = tmp_path/'snapshot.json'
    runtime.output.write_text(json.dumps({'execution_observation':{'complete':True}}))
    connected, network, waits = [], [], []
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return len(waits) > 2
    runtime.stop = Stop()
    runtime.connect_research_snapshot = lambda: connected.append(True)
    runtime.report_data = SimpleNamespace(check=lambda source: network.append(source))
    runtime.preparer = None
    monkeypatch.setattr(oracle_runtime.time, 'monotonic', lambda:0)
    runtime.preparation_loop()
    assert waits == [5,5,5] and len(connected) == 2 and len(network) == 1
