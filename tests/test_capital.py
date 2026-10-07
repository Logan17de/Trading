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


@pytest.mark.parametrize("kind", ["withdrawal", "investment"])
def test_protected_http_accounting_records_not_broker_orders(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    import nifty_engine.agent_engine.dashboard as dashboard
    monkeypatch.setattr(dashboard, 'datetime', SimpleNamespace(now=lambda tz: NOW.astimezone(tz)))
    state = DashboardState(tmp_path, offline=True)
    state.capital_ledger.initialize(100000, 588, '2026-10', NOW)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    url = f'http://127.0.0.1:{server.server_port}/api/capital/{kind}s'
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
        assert state.capital_ledger.summary(NOW)['withdrawn_inr' if kind == 'withdrawal' else 'invested_inr'] == (25.50 if kind == 'withdrawal' else 100025.50)
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=3)


@pytest.mark.parametrize("kind", ["withdrawal", "investment"])
def test_ssh_fixed_accounting_command_has_no_arbitrary_shell_or_actions(tmp_path, kind):
    from types import SimpleNamespace
    cfg = {'format': 'trading-oracle-viewer-v1', 'host': 'example.test', 'user': 'ubuntu',
           'identity_file': str((tmp_path / 'key').resolve()), 'python': '/opt/growing-trader/releases/' + 'a' * 40 + '/venv/bin/python', 'root': '/var/lib/trading-observer'}
    command = {'action': 'record_' + kind, kind: {'id': 'request', 'amount_inr': '1', 'effective_date': '2000-01-01'}}
    def run(args, **kwargs):
        assert json.loads(kwargs['input']) == command
        assert 'record_' + kind not in args[-1]
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


def test_investment_concurrency_restart_and_provision_retry(tmp_path):
    capital = ledger(tmp_path)
    record = {'id': 'addition', 'amount_inr': '2500.25', 'effective_date': '2026-10-05'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: capital.record_investment(record, NOW), range(4)))
    assert sum(r['status'] == 'RECORDED' for r in responses) == 1
    assert all(r['money_moved'] is False and r['broker_writes'] is False for r in responses)
    capital.record_withdrawal({'id': 'out', 'amount_inr': '500', 'effective_date': '2026-10-04'}, NOW)
    reopened = CapitalLedger(Store(tmp_path / 'journal.sqlite3'))
    result = reopened.initialize(100000, 588, '2026-10', NOW)
    assert result['invested_inr'] == 102500.25
    assert result['remaining_capital_inr'] == 101412.25
    assert reopened.record_investment(record, NOW)['status'] == 'ALREADY_RECORDED'
    assert reopened.summary(NOW.replace(month=11))['remaining_capital_inr'] == 100824.25
    assert not capital.store.read('SELECT * FROM events')
    assert not capital.store.read('SELECT * FROM snapshots')


def test_investment_legacy_ledger_and_dated_summary(tmp_path):
    capital = ledger(tmp_path)
    legacy = capital.store.meta(KEY)
    legacy.pop('investments')
    capital.store.set_meta(KEY, legacy)
    assert capital.summary(NOW)['status'] == 'AVAILABLE'
    capital.record_investment({'id': 'dated', 'amount_inr': '125.50', 'effective_date': '2026-10-05'}, NOW)
    assert capital.summary(NOW)['invested_inr'] == 100125.50
    assert capital.summary(NOW - timedelta(days=1))['invested_inr'] == 100000
    assert capital.store.meta(KEY)['invested_paise'] == legacy['invested_paise']
    assert capital.store.meta(KEY)['api_fees'] == legacy['api_fees']
    # Email uses the same updated aggregate, without mutating the input.
    view = {'capital_summary': capital.summary(NOW)}
    original = copy.deepcopy(view)
    mail = build(view, '2026-10-05')['mail']['html']
    assert '₹100,125.50' in mail and '₹99,537.50' in mail and view == original


def test_investment_conflicting_ids_never_overwrite(tmp_path):
    capital = ledger(tmp_path)
    body = {'id': 'unique', 'amount_inr': '50', 'effective_date': '2026-10-05'}
    capital.record_investment(body, NOW)
    before = capital.store.meta(KEY)
    with pytest.raises(ValueError, match='INVESTMENT_ID_CONFLICT'):
        capital.record_investment(dict(body, amount_inr='51'), NOW)
    with pytest.raises(ValueError, match='CAPITAL_RECORD_ID_CONFLICT'):
        capital.record_withdrawal(body, NOW)
    assert capital.store.meta(KEY) == before


@pytest.mark.parametrize('changes', [
    {'amount_inr': '0'}, {'amount_inr': '-1'}, {'amount_inr': '1.001'},
    {'amount_inr': True}, {'effective_date': '2026-10-06'},
    {'effective_date': 'bad'}, {'id': 'bad/id'}, {'extra': 'not allowed'}])
def test_invalid_investments_leave_ledger_unchanged(tmp_path, changes):
    capital = ledger(tmp_path)
    before = capital.store.meta(KEY)
    with pytest.raises(ValueError):
        capital.record_investment(dict({'id': 'request', 'amount_inr': '1', 'effective_date': '2026-10-05'}, **changes), NOW)
    assert capital.store.meta(KEY) == before


def test_oracle_investment_command_exact_dispatch(tmp_path):
    from types import SimpleNamespace
    capital = ledger(tmp_path)
    runtime = Runtime.__new__(Runtime)
    runtime.state = SimpleNamespace(record_investment=lambda body: capital.record_investment(body, NOW))
    command = {'action': 'record_investment', 'investment': {'id': 'new', 'amount_inr': '15.05', 'effective_date': '2026-10-05'}}
    assert runtime.command(command)['capital_summary']['invested_inr'] == 100015.05
    assert runtime.command(command)['status'] == 'ALREADY_RECORDED'
    invalid = dict(command, investment=dict(command['investment'], amount_inr='-1'))
    assert runtime.command(invalid)['status'] == 'INVALID_INVESTMENT'
    with pytest.raises(ValueError): runtime.command(dict(command, order=True))


@pytest.mark.parametrize('changes', [{'record_id': 'other'}, {'money_moved': True}, {'broker_writes': True}, {'capital_summary': None}])
def test_viewer_rejects_unconfirmed_investment_receipts(monkeypatch, changes):
    from nifty_engine.agent_engine.oracle_link import RemoteViewer
    import nifty_engine.agent_engine.oracle_link as link
    result = dict({'status': 'RECORDED', 'record_id': 'request', 'money_moved': False,
                   'broker_writes': False, 'capital_summary': {'status': 'AVAILABLE'}}, **changes)
    monkeypatch.setattr(link, 'request', lambda *_: result)
    viewer = RemoteViewer.__new__(RemoteViewer)
    viewer.config = {}
    viewer.lock = threading.Lock()
    viewer.cache = {'capital_summary': {'status': 'CACHED'}}
    with pytest.raises(ConnectionError): viewer.record_investment({'id': 'request'})
    assert viewer.cache['capital_summary']['status'] == 'CACHED'


def test_capital_ui_retry_receipts_and_independence_from_market_freshness():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for dashboard accounting interaction check')
    source = Path(__file__).parents[1] / 'src/nifty_engine/agent_engine/dashboard_assets/dashboard.js'
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const elements=new Map(),storage=new Map();
function $(id){if(!elements.has(id))elements.set(id,{events:{},value:'',addEventListener(e,f){this.events[e]=f;},showModal(){this.open=true;},close(){this.open=false;},focus(){}});return elements.get(id);}
let calls=0;
const context=vm.createContext({$,data:{capital_summary:{status:'CACHED'},vm:{status:'RECONNECTING'}},demo:false,
  setAmount(){},inr(){return '';},dateTime(){return '';},load:async()=>{},
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  crypto:{randomUUID:()=> 'request-'+(++calls)},document:{querySelector:()=>({content:'local-token'})},
  fetch:async()=>{throw new Error('lost reply');}});
vm.runInContext(source.slice(source.indexOf('const capitalPending='),source.indexOf('function strategyReadinessHTML'))+
  source.slice(source.indexOf("for(const kind of ['withdrawal','investment']) {"),source.indexOf('$("refresh").addEventListener')),context);
(async()=>{
  vm.runInContext('renderCapital()',context);
  assert.equal($('record-investment').disabled,false);
  $('record-investment').events.click();
  assert.equal($('investment-dialog').open,true);
  $('investment-amount').value='125.50';$('investment-date').value='2026-10-05';
  const submit=()=> $('investment-form').events.submit({preventDefault(){}});
  await submit();
  const key='options-trader-pending-investment-v1',pending=storage.get(key);
  assert.equal(JSON.parse(pending).amount_inr,'125.50');
  assert.equal($('investment-amount').readOnly,true);
  $('record-investment').events.click();
  assert.equal($('investment-amount').value,'125.50');
  context.fetch=async(url,args)=>{
    assert.equal(url,'/api/capital/investments');assert.equal(args.headers['X-Local-Token'],'local-token');
    assert.equal(args.body,pending);
    return {ok:true,json:async()=>({status:'RECORDED',record_id:'wrong',money_moved:false,broker_writes:false})};
  };
  await submit();assert.equal(storage.get(key),pending);
  context.fetch=async(url,args)=>({ok:true,json:async()=>({status:'ALREADY_RECORDED',record_id:JSON.parse(args.body).id,money_moved:false,broker_writes:false})});
  await submit();assert.equal(storage.has(key),false);assert.equal(calls,1);
  assert.match($('investment-result').textContent,/Already recorded/);
  assert.equal($('investment-amount').readOnly,false);
  context.data.capital_summary.status='INVALID_LEDGER';vm.runInContext('renderCapital()',context);
  assert.equal($('record-investment').disabled,true);
  context.data.capital_summary.status='AVAILABLE';context.demo=true;vm.runInContext('renderCapital()',context);
  assert.equal($('record-investment').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', script, str(source)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
