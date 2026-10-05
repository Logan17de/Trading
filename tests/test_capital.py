import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from nifty_engine.agent_engine.capital import CapitalLedger, KEY, paise, revalidate
from nifty_engine.agent_engine.dashboard import DashboardState, handler, view_model
from nifty_engine.agent_engine.oracle_link import request
from nifty_engine.agent_engine.oracle_runtime import Runtime
from nifty_engine.agent_engine.pc_control import JST
from nifty_engine.agent_engine.store import Store
from nifty_engine.agent_engine.visual_report import build

NOW = datetime(2026, 10, 5, 16, tzinfo=JST)


def ledger(tmp_path):
    result = CapitalLedger(Store(tmp_path / 'journal.sqlite3'))
    result.initialize(100000, 588, '2026-10', NOW)
    return result


def test_capital_subtraction_does_not_include_groww_cash_or_trading_pnl(tmp_path):
    capital = ledger(tmp_path)
    result = capital.summary(NOW)
    assert result['invested_inr'] == 100000 and result['withdrawn_inr'] == 0
    assert result['api_fees_inr'] == 588 and result['remaining_capital_inr'] == 99412
    result = capital.record_withdrawal({'id': 'owner-1', 'amount_inr': '1200.25', 'effective_date': '2026-10-04'}, NOW)
    assert not result['money_moved'] and not result['broker_writes']
    assert result['capital_summary']['remaining_capital_inr'] == 98211.75
    assert not capital.store.read('SELECT * FROM snapshots')
    assert not capital.store.read('SELECT * FROM events')


def test_month_first_day_jst_downtime_catchup_and_restart(tmp_path):
    capital = ledger(tmp_path)
    boundary = datetime(2026, 10, 31, 15, tzinfo=timezone.utc)
    assert capital.summary(boundary - timedelta(seconds=1))['api_fees_inr'] == 588
    assert capital.summary(boundary)['api_fees_inr'] == 1176
    reopened = CapitalLedger(Store(tmp_path / 'journal.sqlite3'))
    december = NOW.replace(month=12)
    for _ in range(3):
        assert reopened.summary(december)['api_fees_inr'] == 1764
    assert list(reopened.store.meta(KEY)['api_fees']) == ['2026-10', '2026-11', '2026-12']
    # Future booked records are excluded when rendering a prior accounting month.
    assert reopened.summary(NOW)['api_fees_inr'] == 588


def test_fee_concurrency_and_provisioning_retry_preserve_withdrawals(tmp_path):
    capital = ledger(tmp_path)
    record = {'id': 'same-request', 'amount_inr': '100.10', 'effective_date': '2026-10-05'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: capital.record_withdrawal(record, NOW), range(4)))
    assert sum(r['status'] == 'RECORDED' for r in responses) == 1
    assert capital.initialize(100000, 588, '2026-10', NOW)['withdrawn_inr'] == 100.10
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: capital.summary(NOW.replace(month=11)), range(4)))
    assert all(r['api_fees_inr'] == 1176 for r in results)
    with pytest.raises(ValueError, match='CAPITAL_ALREADY_CONFIGURED'):
        capital.initialize(200000, 588, '2026-10', NOW)


def test_duplicate_after_restart_conflict_and_negative_remaining_capital(tmp_path):
    capital = ledger(tmp_path)
    record = {'id': 'withdrawal', 'amount_inr': '100005', 'effective_date': '2026-10-05'}
    capital.record_withdrawal(record, NOW)
    reopened = CapitalLedger(Store(tmp_path / 'journal.sqlite3'))
    assert reopened.record_withdrawal(record, NOW)['status'] == 'ALREADY_RECORDED'
    assert reopened.summary(NOW)['remaining_capital_inr'] == -593
    with pytest.raises(ValueError, match='WITHDRAWAL_ID_CONFLICT'):
        reopened.record_withdrawal(dict(record, amount_inr='50'), NOW)


@pytest.mark.parametrize('value', [True, -1, '1.235', float('nan'), 'Infinity', '1e999999999999', {}, ''])
def test_invalid_financial_amounts(value):
    with pytest.raises(ValueError):
        paise(value, positive=True)


def test_future_date_and_unconfigured_or_corrupt_ledger(tmp_path):
    capital = CapitalLedger(Store(tmp_path / 'journal.sqlite3'))
    assert capital.summary(NOW)['status'] == 'NOT_CONFIGURED'
    with pytest.raises(ValueError):
        capital.record_withdrawal({'id': 'a', 'amount_inr': '1', 'effective_date': '2026-10-06'}, NOW)
    capital.store.set_meta(KEY, {'private-malformed': 'not financial evidence'})
    assert capital.summary(NOW)['remaining_capital_inr'] is None
    assert capital.summary(NOW)['status'] == 'INVALID_LEDGER'


def test_cached_capital_is_not_recomputed_by_desktop_at_month_boundary(tmp_path):
    capital = ledger(tmp_path)
    current = capital.summary(NOW)
    assert revalidate(copy.deepcopy(current), NOW, cached=True)['status'] == 'CACHED'
    future = revalidate(current, NOW.replace(month=11))
    assert future['status'] == 'FEE_UPDATE_PENDING'
    assert future['invested_inr'] == 100000 and future['withdrawn_inr'] == 0
    assert future['api_fees_inr'] is None and future['remaining_capital_inr'] is None


def test_dashboard_and_visual_email_share_capital_without_changing_pnl(tmp_path):
    from pathlib import Path
    root = Path(__file__).parents[1]
    view = view_model(None, json.loads((root / 'config/owner_strategies.json').read_text(encoding='utf-8')), now=NOW)
    view['capital_summary'] = ledger(tmp_path).summary(NOW)
    old = copy.deepcopy(view)
    mail = build(view, '2026-10-05')['mail']['html']
    for label in ('Invested', 'Withdrawn', 'API fees', 'Remaining capital'):
        assert label in mail
    assert '₹99,412.00' in mail and view == old


def test_protected_http_accounting_records_not_broker_orders(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import nifty_engine.agent_engine.dashboard as dashboard
    monkeypatch.setattr(dashboard, 'datetime', SimpleNamespace(now=lambda tz: NOW.astimezone(tz)))
    state = DashboardState(tmp_path, offline=True)
    state.capital_ledger.initialize(100000, 588, '2026-10', NOW)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    url = f'http://127.0.0.1:{server.server_port}/api/capital/withdrawals'
    body = json.dumps({'id': 'browser-request', 'amount_inr': '25.50', 'effective_date': '2026-10-05'}).encode()
    def post(payload=body, token=state.token, origin=None):
        headers = {'X-Local-Token': token, 'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        return urlopen(Request(url, data=payload, headers=headers), timeout=5)
    try:
        with pytest.raises(HTTPError) as rejected: post(token='wrong')
        assert rejected.value.code == 403
        with pytest.raises(HTTPError) as rejected: post(origin='https://external.test')
        assert rejected.value.code == 403
        with pytest.raises(HTTPError) as rejected: post(payload=b'x' * 1025)
        assert rejected.value.code == 400
        with post() as response: result = json.load(response)
        assert result['status'] == 'RECORDED' and not result['money_moved']
        with post() as response: assert json.load(response)['status'] == 'ALREADY_RECORDED'
        conflict = json.dumps({'id': 'browser-request', 'amount_inr': '26', 'effective_date': '2026-10-05'}).encode()
        with pytest.raises(HTTPError) as rejected: post(payload=conflict)
        assert rejected.value.code == 409
        assert state.capital_ledger.summary(NOW)['withdrawn_inr'] == 25.50
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=3)


def test_ssh_fixed_accounting_command_has_no_arbitrary_shell_or_actions(tmp_path):
    from types import SimpleNamespace
    cfg = {'format': 'trading-oracle-viewer-v1', 'host': 'example.test', 'user': 'ubuntu',
           'identity_file': str((tmp_path / 'key').resolve()), 'python': '/opt/growing-trader/releases/' + 'a' * 40 + '/venv/bin/python', 'root': '/var/lib/trading-observer'}
    command = {'action': 'record_withdrawal', 'withdrawal': {'id': 'request', 'amount_inr': '1', 'effective_date': '2000-01-01'}}
    def run(args, **kwargs):
        assert json.loads(kwargs['input']) == command
        assert 'record_withdrawal' not in args[-1]
        return SimpleNamespace(returncode=0, stdout='{"status":"RECORDED","money_moved":false,"broker_writes":false}')
    assert request(cfg, command, run=run)['status'] == 'RECORDED'
    for invalid in ({'action': 'place_order'}, dict(command, shell='unsafe')):
        with pytest.raises(ValueError): request(cfg, invalid, run=run)


def test_oracle_accounting_command_dispatch_and_validation(tmp_path):
    from types import SimpleNamespace
    capital = ledger(tmp_path)
    runtime = Runtime.__new__(Runtime)
    runtime.state = SimpleNamespace(record_withdrawal=lambda body: capital.record_withdrawal(body, NOW))
    command = {'action': 'record_withdrawal', 'withdrawal': {'id': 'owner-request', 'amount_inr': '5.05', 'effective_date': '2026-10-05'}}
    assert runtime.command(command)['capital_summary']['withdrawn_inr'] == 5.05
    assert runtime.command(command)['status'] == 'ALREADY_RECORDED'
    bad = dict(command, withdrawal=dict(command['withdrawal'], amount_inr='-1'))
    assert runtime.command(bad)['status'] == 'INVALID_WITHDRAWAL'
    assert capital.summary(NOW)['withdrawn_inr'] == 5.05
    with pytest.raises(ValueError): runtime.command({'action': 'place_order'})
