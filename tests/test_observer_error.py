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


def test_research_preparation_keeps_sixty_second_network_budget(tmp_path, monkeypatch):
    from nifty_engine.agent_engine import oracle_runtime
    runtime = Runtime.__new__(Runtime)
    runtime.root = tmp_path
    runtime.output = tmp_path/'snapshot.json'
    runtime.output.write_text(json.dumps({'execution_observation':{'complete':True}}))
    network, waits = [], []
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return len(waits) > 2
    runtime.stop = Stop()
    runtime.report_data = SimpleNamespace(check=lambda source: network.append(source))
    runtime.preparer = None
    monkeypatch.setattr(oracle_runtime.time, 'monotonic', lambda:0)
    runtime.preparation_loop()
    assert waits == [5,5,5] and len(network) == 1


def test_busy_journal_during_failure_status_does_not_kill_research_retry(tmp_path, monkeypatch):
    from nifty_engine.agent_engine import report_data
    runtime = Runtime.__new__(Runtime)
    runtime.output = tmp_path/'snapshot.json'
    runtime.output.write_text(json.dumps({'execution_observation':{'complete':True}}))
    runtime.research_catalog = {'indices':['NIFTY','SENSEX']}; runtime.normal_catalog = None
    def busy(*args): raise sqlite3.OperationalError('private busy journal')
    runtime.state = SimpleNamespace(pnl_lines=SimpleNamespace(store=SimpleNamespace(set_meta=busy)))
    monkeypatch.setattr(report_data, 'connect_snapshot', busy)
    runtime.connect_research_snapshot()
    restored = []
    monkeypatch.setattr(report_data, 'connect_snapshot', lambda *args: restored.append(True))
    runtime.connect_research_snapshot()
    assert restored == [True]


def test_cached_observer_reads_do_not_wait_for_slow_renderer():
    import threading
    import time
    from datetime import datetime, timezone
    r=Runtime.__new__(Runtime)
    r.cache_thread_active=True
    r.view_cache_lock=threading.Lock()
    r.view_cache_at=datetime.now(timezone.utc)
    r.view_cache_error=None
    r.view_cache={
        'format':'trading-dashboard-v1','order_capability':False,
        'markets':[],'funds':{'status':'AVAILABLE','received_at':r.view_cache_at.isoformat(),
            'option_buy_available_inr':1,'option_sell_available_inr':1},
        'broker_pnl':{'status':'UNAVAILABLE'},'control':{'algo':{'desired_enabled':False,
            'execution_enabled':False,'status':'OFF','blockers':[]},
            'execution_enabled':False},
        'execution_controller':{'execution_enabled':False,'blockers':[]},
        'runtime':{},'as_of':r.view_cache_at.isoformat()}
    r.sequence=2;r.at=r.view_cache_at.isoformat();r.error=None
    r.collector=None;r.broker_retry=SimpleNamespace(public=lambda *args,**kwargs:{'status':'NOT_CONNECTED'})
    def blocked_render():
        time.sleep(4)
        return {}
    r._build_view=blocked_render
    start=time.perf_counter()
    first=r.read()
    second=r.read()
    assert time.perf_counter()-start<.5
    assert first['runtime']['view_status']=='FRESH_RENDER'
    assert not first['control']['algo']['execution_enabled']
    assert second['runtime']['view_age_seconds']>=first['runtime']['view_age_seconds']
    assert first['funds']['status']=='AVAILABLE'
    assert r.view_cache['runtime']=={}  # no cross-request mutation


def test_stale_cached_oracle_view_fails_closed_and_revalidates_account():
    from datetime import datetime,timezone,timedelta
    import threading
    r=Runtime.__new__(Runtime)
    r.cache_thread_active=True;r.view_cache_lock=threading.Lock()
    past=datetime.now(timezone.utc)-timedelta(minutes=3)
    r.view_cache_at=past;r.sequence=4;r.at=past.isoformat();r.error=None
    r.collector=None;r.broker_retry=SimpleNamespace(public=lambda *args,**kwargs:{'status':'NOT_CONNECTED'})
    r.view_cache={'format':'trading-dashboard-v1','order_capability':False,
        'markets':[],'funds':{'received_at':past.isoformat(),'status':'AVAILABLE',
            'option_buy_available_inr':123,'option_sell_available_inr':456},
        'broker_pnl':{'status':'UNAVAILABLE'},
        'control':{'algo':{'desired_enabled':True,'execution_enabled':True,
            'status':'ON_READY','blockers':[]},'execution_enabled':True},
        'execution_controller':{'execution_enabled':True,'blockers':[]},'runtime':{}}
    value=r.read()
    assert value['runtime']['view_status']=='STALE_RENDER'
    assert value['funds']['status']=='STALE'
    assert value['funds']['option_buy_available_inr'] is None
    assert not value['control']['algo']['execution_enabled']
    assert 'OBSERVER_VIEW_STALE' in value['control']['algo']['blockers']
    assert not value['execution_controller']['execution_enabled']
    assert not value['order_capability']
    assert r.view_cache['funds']['option_buy_available_inr']==123


def test_running_observer_reports_initializing_quickly_without_trading_capability():
    import threading
    r=Runtime.__new__(Runtime)
    r.cache_thread_active=True;r.view_cache_lock=threading.Lock()
    r.view_cache=None;r.view_cache_at=None
    result=r.read()
    assert result=={'status':'OBSERVER_VIEW_INITIALIZING',
                    'order_capability':False,'broker_writes':False}
