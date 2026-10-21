"""Synthetic faults only: no provider requests, credentials or broker writes."""
from collections import deque
from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.broker_retry import BrokerRetry, KEY, MAX_TOKEN_ATTEMPTS, LocalAuthBudgetExceeded
from nifty_engine.agent_engine.oracle_runtime import Runtime
from nifty_engine.agent_engine.store import Store

NOW = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)
RATE_LIMIT = dict(ok=False, error_type='RateLimited', code='429')


def test_rate_limit_backoff_survives_restart_and_only_real_success_clears(tmp_path):
    store = Store(tmp_path/'journal.sqlite3')
    retry = BrokerRetry(store)
    for delay in (1800, 1800, 1800, 1800, 1800):
        retry.failed(RATE_LIMIT, NOW, stage='TOKEN')
        restarted = BrokerRetry(Store(store.path))
        assert restarted.error == RATE_LIMIT
        assert not restarted.ready(NOW + timedelta(seconds=delay-1))
        assert restarted.ready(NOW + timedelta(seconds=delay))
        retry = restarted
    assert retry.public(NOW, connected=False)['provider_remaining_quota'] is None
    retry.succeeded(NOW)
    assert BrokerRetry(store).error is None
    assert retry.public(NOW, connected=True)['status'] == 'CONNECTED'


def test_approval_failure_waits_thirty_minutes_and_honors_longer_retry_after(tmp_path):
    retry = BrokerRetry(Store(tmp_path/'j.sqlite3'))
    retry.failed(dict(RATE_LIMIT, code='403'), NOW, stage='TOKEN')
    assert not retry.ready(NOW + timedelta(minutes=29))
    assert retry.ready(NOW + timedelta(minutes=30))
    retry.failed(RATE_LIMIT, NOW, stage='TOKEN', retry_after_seconds=7200)
    assert not retry.ready(NOW + timedelta(seconds=7199))
    assert retry.ready(NOW + timedelta(hours=2))


def test_rolling_token_budget_is_committed_before_request_and_not_reset_by_restart(tmp_path):
    store = Store(tmp_path/'j.sqlite3')
    for _ in range(MAX_TOKEN_ATTEMPTS):
        BrokerRetry(store).reserve_token(NOW)
    retry = BrokerRetry(store)
    with pytest.raises(LocalAuthBudgetExceeded):
        retry.reserve_token(NOW + timedelta(hours=23, minutes=59))
    restarted = BrokerRetry(store)
    assert restarted.public(NOW, connected=False)['status'] == 'LOCAL_AUTH_BUDGET'
    assert not restarted.ready(NOW + timedelta(hours=24) - timedelta(microseconds=1))
    restarted.reserve_token(NOW + timedelta(hours=24))
    assert len(store.meta(KEY)['attempts']) == 1
    # A backward clock cannot make recorded future attempts disappear.
    assert restarted.public(NOW, connected=False)['local_token_attempts_24h'] == 1


def test_no_connection_before_1300_jst_and_restart_cannot_request_tokens_often(tmp_path):
    retry=BrokerRetry(Store(tmp_path/'j.sqlite3'))
    before=NOW.replace(hour=3,minute=59,second=59)
    assert not retry.ready(before)
    assert retry.ready(before+timedelta(seconds=1))
    retry.reserve_token(NOW)
    restarted=BrokerRetry(retry.store)
    assert not restarted.ready(NOW+timedelta(minutes=29),needs_token=True)
    assert restarted.ready(NOW+timedelta(minutes=30),needs_token=True)
    assert restarted.ready(NOW+timedelta(seconds=5)), 'healthy five-second reads continue'
    # Friday's late retry waits for Monday 13:00 JST.
    friday=NOW.replace(day=23,hour=10,minute=30)
    retry.failed(RATE_LIMIT,friday,stage='TOKEN')
    assert retry.value['retry_at']=='2026-10-26T04:00:00+00:00'


def test_upgrade_extends_old_short_retry_without_another_token_attempt(tmp_path):
    store=Store(tmp_path/'j.sqlite3')
    store.set_meta(KEY,dict(attempts=[],failures=1,failure=RATE_LIMIT,failed_at=NOW.isoformat(),
        retry_at=(NOW+timedelta(minutes=5)).isoformat()))
    value=BrokerRetry(store)
    assert not value.ready(NOW+timedelta(minutes=29))
    assert not value.value['attempts']


def runtime(tmp_path):
    value = Runtime.__new__(Runtime)
    value.root = tmp_path
    (tmp_path/'config').mkdir()
    (tmp_path/'config/owner_strategies.json').write_text('{}')
    value.output = tmp_path/'snapshot.json'
    value.broker_retry = BrokerRetry(Store(tmp_path/'j.sqlite3'))
    value.collector = value.executor = value.report_data = value.preparer = None
    value.error = None
    value.token_day = None
    value.access_token = value.access_token_day = None
    value.transport_audit = deque(maxlen=64)
    value.impulse = SimpleNamespace(detach=lambda:None)
    value.lean = False
    value.state = SimpleNamespace(monitor=SimpleNamespace(tick=lambda *args:None))
    return value


def test_waiting_ticks_keep_error_visible_and_do_not_reauthenticate(tmp_path, monkeypatch):
    from nifty_engine.agent_engine import oracle_runtime
    monkeypatch.setattr(oracle_runtime, 'collection_window', lambda now:True)
    value = runtime(tmp_path)
    calls = []
    class RateLimited(Exception):
        code = '429'
    def failed(now):
        calls.append(now)
        raise RateLimited('PRIVATE_PROVIDER_RESPONSE')
    value.connect = failed
    value.collection_step(NOW)
    original = value.error
    retry_at = datetime.fromisoformat(value.broker_retry.value['retry_at'])
    for seconds in (0, 5, 10, 60, 299):
        value.collection_step(retry_at - timedelta(seconds=300-seconds))
        assert value.error == original
    assert len(calls) == 1
    assert 'PRIVATE_PROVIDER_RESPONSE' not in value.output.read_text()
    assert 'PRIVATE_PROVIDER_RESPONSE' not in json.dumps(value.broker_retry.value)


def test_current_read_clears_fault_and_read_failures_do_not_generate_tokens(tmp_path, monkeypatch):
    from nifty_engine.agent_engine import oracle_runtime
    monkeypatch.setattr(oracle_runtime, 'collection_window', lambda now:True)
    value = runtime(tmp_path)
    value.access_token = 'SYNTHETIC_TOKEN'
    value.access_token_day = value.token_day = NOW.date()
    value.broker_retry.failed(RATE_LIMIT, NOW-timedelta(hours=1), stage='COLLECTION')
    value.error = RATE_LIMIT
    value.collector = SimpleNamespace(sample=lambda:dict(status='READ_ONLY_DATA_AVAILABLE', probes={}), close=lambda:None)
    value.executor = SimpleNamespace(lock=threading.Lock(), reconcile_before_collection=lambda:None)
    value.connect = lambda now:pytest.fail('must reuse the connected client')
    value.collection_step(NOW)
    assert value.error is None and BrokerRetry(value.broker_retry.store).error is None
    assert not value.broker_retry.value['attempts']
    def timeout():raise TimeoutError('PRIVATE')
    value.collector.sample = timeout
    value.collection_step(NOW)
    assert value.access_token == 'SYNTHETIC_TOKEN'
    assert value.collector is value.executor is None
    assert value.error['error_type'] == 'TimeoutError'


def test_session_token_reused_in_ram_and_daily_rollover_is_budgeted(tmp_path, monkeypatch):
    value = runtime(tmp_path)
    calls = []
    monkeypatch.setitem(sys.modules, 'growwapi', SimpleNamespace(GrowwAPI=SimpleNamespace(
        get_access_token=lambda **kwargs:calls.append(True) or 'SYNTHETIC_TOKEN')))
    monkeypatch.setenv('GROWW_OBSERVER_API_KEY', 'SYNTHETIC_KEY')
    monkeypatch.setenv('GROWW_OBSERVER_API_SECRET', 'SYNTHETIC_SECRET')
    assert value.session_token(NOW) == value.session_token(NOW)
    assert len(calls) == 1
    value.session_token(NOW + timedelta(days=1))
    assert len(calls) == 2
    saved = json.dumps(value.broker_retry.store.meta(KEY))
    assert 'SYNTHETIC' not in saved


def test_connection_failure_immediately_invalidates_last_good_cached_render(tmp_path):
    value=runtime(tmp_path)
    value.cache_thread_active=True;value.view_cache_lock=threading.Lock()
    value.view_cache_at=datetime.now(timezone.utc)
    value.sequence=4;value.at=value.view_cache_at.isoformat()
    value.error=RATE_LIMIT
    value.broker_retry.failed(RATE_LIMIT,value.view_cache_at,stage='TOKEN')
    at=value.view_cache_at.isoformat()
    value.view_cache=dict(runtime={},markets=[dict(received_at=at,price=123,series=[],options=[])],
        broker_pnl={},funds=dict(status='AVAILABLE',received_at=at,option_buy_available_inr=100),
        positions_status='AVAILABLE',orders_status='AVAILABLE',
        control=dict(algo=dict(desired_enabled=True,execution_enabled=True,blockers=[]),execution_enabled=True),
        execution_controller=dict(execution_enabled=True,status='READY',blockers=[]))
    result=value.read()
    assert result['runtime']['view_status']=='BROKER_UNAVAILABLE'
    assert result['funds']['option_buy_available_inr'] is None
    assert result['markets'][0]['price'] is None
    assert result['positions_status']=='UNAVAILABLE'
    assert not result['control']['algo']['execution_enabled']
    assert result['control']['algo']['desired_enabled'] is True
    assert 'BROKER_CONNECTION_UNAVAILABLE' in result['execution_controller']['blockers']
    assert result['funds']['received_at']==at
    assert value.view_cache['funds']['option_buy_available_inr']==100


def test_403_read_invalidates_token_and_no_order_interface_is_called(tmp_path, monkeypatch):
    from nifty_engine.agent_engine import oracle_runtime
    monkeypatch.setattr(oracle_runtime, 'collection_window', lambda now:True)
    value = runtime(tmp_path)
    value.access_token = 'SYNTHETIC_TOKEN'
    value.access_token_day = value.token_day = NOW.date()
    value.collector = SimpleNamespace(sample=lambda:dict(status='PARTIAL_MARKET_DATA',
        probes={'NIFTY_quote':{'ok':False,'code':'403'}}), close=lambda:None)
    value.executor = SimpleNamespace(lock=threading.Lock(), reconcile_before_collection=lambda:None)
    value.collection_step(NOW)
    assert value.access_token is None and value.access_token_day is None
    assert value.error['code'] == '403'
    assert value.broker_retry.public(NOW, connected=False)['status'] == 'APPROVAL_OR_ACCESS_REQUIRED'


def test_retry_after_is_allowlisted_and_readonly_guard_still_denies_orders(monkeypatch):
    import requests
    from nifty_engine.agent_engine.market_check import readonly_transport
    response = SimpleNamespace(status_code=429, headers={'Retry-After':'7200','private':'PRIVATE'},
        json=lambda:dict(error=dict(displayMessage='PRIVATE')))
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *args,**kwargs:response)
    audit = deque(maxlen=2)
    with readonly_transport(audit):
        requests.post('https://api.groww.in/v1/token/api/access')
        with pytest.raises(PermissionError):requests.post('https://api.groww.in/v1/order/create')
    assert audit[-1]['retry_after_seconds'] == 7200
    assert 'PRIVATE' not in json.dumps(list(audit))


def test_dashboard_displays_exact_sanitized_error_and_retry_time():
    node = shutil.which('node')
    if not node:pytest.skip('Node required for presentation check')
    path = Path(__file__).parents[1]/'src/nifty_engine/agent_engine/dashboard_assets/dashboard.js'
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const c=vm.createContext({dateTime:s=>s,timeZone:'Asia/Tokyo'});
vm.runInContext(source.slice(source.indexOf('function brokerConnectionMessage('),source.indexOf('function render()')),c);
c.view={runtime:{error:{code:'429'},broker_connection:{status:'PROVIDER_RATE_LIMIT',failure:{code:'429'},next_retry_at:'2026-10-21T09:00:00Z'}}};
let text=vm.runInContext('brokerConnectionMessage(view)',c);
assert.match(text,/HTTP 429/);assert.match(text,/2026-10-21T09:00:00Z JST/);assert.match(text,/remain unknown/);
c.view={runtime:{error:null}};assert.equal(vm.runInContext('brokerConnectionMessage(view)',c),null);
c.view={runtime:{error:{code:'403'}}};assert.match(vm.runInContext('brokerConnectionMessage(view)',c),/HTTP 403/);
"""
    result = subprocess.run([node, '-e', script, str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
