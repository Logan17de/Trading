"""Commissioning tests use only synthetic SDK responses and isolated journals."""
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.broker_commissioning import (
    Commissioning, PREFIX, PLAN_FORMAT, account_fingerprint, public_status, validated_evidence,
)
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine.execution_gate import consume_write, ExecutionDenied
from nifty_engine.agent_engine.pc_control import PcJournal, JST
from nifty_engine.agent_engine.premium_strategy import set_intent


class Broker:
    """Imitates SDK parsing; writes still consume the exact HTTP capability."""
    simulated = True

    def __init__(self, session):
        self.session = session
        self.profile = dict(vendor_user_id='synthetic-account', ucc='synthetic-client', active_segments=['FNO'])
        self.orders = {}; self.parents = {}; self.quantity = 0; self.writes = []
        self.timeout_gtt = False; self.timeout_close = False; self.hide_reference = False
        self.fill_close = True; self.change_request = False; self.cancel_race = False

    def get_user_profile(self, **args): return copy.deepcopy(self.profile)
    def get_order_list(self, **args): return dict(order_list=copy.deepcopy(list(self.orders.values())) if args['page'] == 0 else [])
    def get_positions_for_user(self, **args):
        c = self.session.plan['contract']
        return dict(positions=[] if not self.quantity else [dict(trading_symbol=c['symbol'], quantity=self.quantity,
                     product='NRML', segment='FNO', exchange='BSE' if c['index'] == 'SENSEX' else 'NSE')])
    def get_available_margin_details(self, **args): return dict(fno_margin_details=dict(option_buy_balance_available=10000))
    def get_smart_order_list(self, **args):
        rows = [copy.deepcopy(r) for r in self.parents.values() if r['smart_order_type'] == args['smart_order_type']
                and (r['status'] == args['status'] or args['status'] == 'COMPLETED' and r['status'] == 'TRIGGERED')]
        if self.hide_reference:
            for r in rows: r.pop('reference_id', None)
        return dict(orders=rows if args['page'] == 0 else [])
    def get_smart_order(self, **args): return copy.deepcopy(self.parents[args['smart_order_id']])
    def get_order_status_by_reference(self, **args):
        return copy.deepcopy(next(r for r in self.orders.values() if r['order_reference_id'] == args['order_reference_id']))
    def get_order_detail(self, **args): return copy.deepcopy(self.orders[args['groww_order_id']])

    def _write(self, method, path, body):
        actual = copy.deepcopy(body)
        if self.change_request and actual:
            actual['quantity'] += 1
        if not consume_write(method, 'https://api.groww.in' + path, dict(json=actual)):
            raise PermissionError('synthetic denied request')
        # Single-use token cannot authorize a second request.
        assert not consume_write(method, 'https://api.groww.in' + path, dict(json=actual))
        self.writes.append((method, path, copy.deepcopy(body)))

    def create_smart_order(self, **args):
        body = {k: v for k, v in args.items() if k != 'timeout'}
        self._write('POST', '/v1/order-advance/create', body)
        identifier = 'gtt-' + args['reference_id']
        row = dict(body, smart_order_id=identifier, status='ACTIVE', triggered_at=None, expire_at='2027-10-07T10:00:00')
        self.parents[identifier] = row
        if self.timeout_gtt: raise TimeoutError('sensitive provider exception')
        return copy.deepcopy(row)

    def place_order(self, **args):
        body = {k: v for k, v in args.items() if k != 'timeout'}
        self._write('POST', '/v1/order/create', body)
        identifier = 'sell-' + args['order_reference_id']
        qty = args['quantity'] if self.fill_close else 0
        self.orders[identifier] = dict(body, groww_order_id=identifier, filled_quantity=qty,
                                      order_status='EXECUTED' if qty else 'OPEN', average_fill_price=args['price'] if qty else 0)
        self.quantity -= qty
        if self.timeout_close: raise TimeoutError('secret body')
        return dict(groww_order_id=identifier, order_reference_id=args['order_reference_id'])

    def cancel_smart_order(self, **args):
        self._write('POST', '/v1/order-advance/cancel/FNO/GTT/' + args['smart_order_id'], None)
        if self.cancel_race: self.trigger()
        else: self.parents[args['smart_order_id']]['status'] = 'CANCELLED'
        return dict(smart_order_id=args['smart_order_id'], status='CANCELLED')

    def trigger(self, *, linked=True, quantity=None):
        row = next(iter(self.parents.values()))
        row.update(status='TRIGGERED', triggered_at=self.session.now.isoformat())
        ref = row['reference_id'] if linked else 'unlinked-manual-reference'
        qty = row['quantity'] if quantity is None else quantity
        identifier = 'child-' + ref
        self.orders[identifier] = dict(groww_order_id=identifier, order_reference_id=ref, segment='FNO',
            product='NRML', exchange=row['exchange'], trading_symbol=row['trading_symbol'], validity='DAY',
            quantity=row['quantity'], transaction_type='BUY', order_type='LIMIT', price=float(row['order']['price']),
            filled_quantity=qty, average_fill_price=float(row['order']['price']) if qty else 0,
            order_status='EXECUTED' if qty == row['quantity'] else 'OPEN')
        self.quantity += qty


class Session:
    def __init__(self, tmp_path, index='NIFTY'):
        self.now = datetime(2026, 10, 7, 14, 30, tzinfo=JST)
        self.journal = PcJournal(tmp_path / 'journal.sqlite3'); self.store = self.journal.store
        self.pause = tmp_path / '.trader-paused'; self.pause.write_text('owner pause')
        self.broker = Broker(self)
        lot = 65 if index == 'NIFTY' else 20
        self.binding = dict(host='ORACLE', release='a'*40, policy_hash='b'*64, sdk_version='1.5.0',
                            account_fingerprint=account_fingerprint(self.broker.profile), egress_ip='8.8.4.4')
        self.plan = dict(format=PLAN_FORMAT, id='synthetic-test', **{k:v for k,v in self.binding.items() if k!='host'},
            contract=dict(symbol=index+'26O2925000CE', index=index, expiry='2026-10-29', lot_size=lot, tick_size=.05),
            quantity=lot, trigger_price='9.00', buy_limit_price='10.00', sell_limit_price='9.50',
            valid_until=(self.now + timedelta(hours=2)).isoformat())
        self.market = SimpleNamespace(groww=self.broker, limiter=SimpleNamespace(wait=lambda: None),
             quote=lambda *args, **kwargs: dict(last_price=8.0, bid_price=7.95, offer_price=8.0, bid_quantity=100,
                                                offer_quantity=100, last_trade_time=self.now.timestamp()))
        self.commission = self.new_process()

    def master(self):
        c = self.plan['contract']
        return ('trading_symbol,underlying_symbol,segment,exchange,expiry_date,lot_size,tick_size,strike_price\n'
                + f"{c['symbol']},{c['index']},FNO,{'BSE' if c['index']=='SENSEX' else 'NSE'},{c['expiry']},{c['lot_size']},{c['tick_size']},25000\n")

    def new_process(self):
        return Commissioning(self.store, self.market, binding=self.binding, pause_file=self.pause, mode='paper',
                             clock=lambda: self.now, master=self.master)

    def submit(self):
        preview = self.commission.preview(self.plan)
        self.plan_hash = preview['plan_hash']
        return self.commission.submit(self.plan, self.plan_hash)

    def active(self):
        self.submit(); self.now += timedelta(seconds=5); self.commission = self.new_process()
        return self.commission.capture(self.plan['id'])

    def complete(self):
        self.active(); self.broker.trigger()
        self.commission.capture(self.plan['id'])
        self.commission.close(self.plan['id'], self.plan_hash)
        self.now += timedelta(seconds=5)
        result = self.commission.capture(self.plan['id'])
        assert result['status'] == 'COMPLETE', result
        return validated_evidence(self.store, self.plan['id'], self.binding)


@pytest.mark.parametrize('index', ['NIFTY', 'SENSEX'])
def test_complete_exact_owner_workflow_is_separate_from_production(tmp_path, index):
    s = Session(tmp_path, index); proof = s.complete()
    assert all(proof['facts'].values())
    assert len(s.broker.writes) == 2
    assert s.store.read('SELECT * FROM pc_orders') == []
    assert s.store.meta('premium-execution-activation') is None
    assert s.pause.exists() and s.store.meta('premium-algo-intent') is None
    assert s.broker.quantity == 0
    public = public_status(s.store, s.binding['release'], s.binding['policy_hash'])
    assert public['provider_verified']
    assert not any(v in str(public) for v in (s.plan['id'], s.binding['egress_ip'], s.binding['account_fingerprint']))


def test_preview_does_not_write_journal_or_broker(tmp_path):
    s = Session(tmp_path); before = s.store.read('SELECT * FROM meta')
    assert s.commission.preview(s.plan)['status'] == 'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'
    assert s.store.read('SELECT * FROM meta') == before and s.broker.writes == []


@pytest.mark.parametrize('fault', ['hash', 'live', 'unpaused', 'on', 'manual_position', 'open_order', 'wrong_account', 'master', 'budget', 'quantity', 'tick'])
def test_submission_rejects_unreviewed_or_unsafe_plan(tmp_path, fault):
    s = Session(tmp_path); value = s.commission.preview(s.plan)
    plan_hash = value['plan_hash']
    if fault == 'hash': plan_hash = 'c'*64
    elif fault == 'live': s.commission.mode = 'live'
    elif fault == 'unpaused': s.pause.unlink()
    elif fault == 'on': set_intent(s.store, True, s.now)
    elif fault == 'manual_position': s.broker.quantity = 1
    elif fault == 'open_order': s.broker.orders['manual'] = dict(groww_order_id='manual', trading_symbol=s.plan['contract']['symbol'], order_status='OPEN')
    elif fault == 'wrong_account': s.broker.profile['ucc'] = 'other-private-account'
    elif fault == 'master': s.commission.master = lambda: 'bad,csv\n'
    elif fault == 'budget': s.plan['buy_limit_price'] = '20.00'
    elif fault == 'quantity': s.plan['quantity'] += 1
    elif fault == 'tick': s.plan['buy_limit_price'] = '10.01'
    with pytest.raises((ValueError, PermissionError)):
        s.commission.submit(s.plan, plan_hash)
    assert s.broker.writes == []


def test_http_capability_rejects_changed_request(tmp_path):
    s = Session(tmp_path); s.broker.change_request = True
    status = s.submit()
    assert status['status'] == 'RECONCILIATION_REQUIRED'
    assert not s.broker.writes


def test_timeout_is_recovered_only_by_original_reference_and_never_resubmitted(tmp_path):
    s = Session(tmp_path); s.broker.timeout_gtt = True
    result = s.submit()
    assert result['status'] == 'ACTIVE_PARENT_OBSERVED'
    s.commission.submit(s.plan, s.plan_hash)
    assert len(s.broker.writes) == 1
    s.now += timedelta(seconds=5); s.commission = s.new_process(); s.commission.capture(s.plan['id'])
    s.broker.trigger(); s.broker.timeout_close = True
    s.commission.close(s.plan['id'], s.plan_hash)
    s.now += timedelta(seconds=5)
    assert s.commission.capture(s.plan['id'])['status'] == 'COMPLETE'
    assert len(s.broker.writes) == 2


def test_uncertain_parent_without_original_reference_stays_unverified(tmp_path):
    s = Session(tmp_path); s.broker.timeout_gtt = True; s.broker.hide_reference = True
    assert s.submit()['status'] == 'RECONCILIATION_REQUIRED'
    for _ in range(2): s.commission.submit(s.plan, s.plan_hash)
    assert len(s.broker.writes) == 1
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)


def test_persistence_requires_different_process_and_five_seconds(tmp_path):
    s = Session(tmp_path); s.submit(); s.now += timedelta(seconds=5)
    assert not s.commission.capture(s.plan['id'])['facts']['persistent_gtt']
    s.commission = s.new_process()
    assert not s.commission.capture(s.plan['id'])['facts']['persistent_gtt']  # same time not a new observation
    s.now += timedelta(seconds=5)
    assert s.commission.capture(s.plan['id'])['facts']['persistent_gtt']


@pytest.mark.parametrize('fault', ['missing_link', 'wrong_child_contract', 'changed_quantity', 'external_fill', 'partial_child', 'active_expiry'])
def test_unknown_or_mixed_child_never_authorizes_close(tmp_path, fault):
    s = Session(tmp_path); s.active()
    if fault == 'active_expiry':
        next(iter(s.broker.parents.values()))['expire_at'] = s.plan['contract']['expiry']
    else:
        s.broker.trigger(linked=fault != 'missing_link', quantity=1 if fault == 'partial_child' else None)
        row = next(iter(s.broker.orders.values()))
        if fault == 'wrong_child_contract': row['trading_symbol'] = 'NIFTYOTHERCE'
        elif fault == 'changed_quantity': row['quantity'] += 1
        elif fault == 'external_fill':
            s.broker.orders['manual'] = dict(groww_order_id='manual', trading_symbol=s.plan['contract']['symbol'], order_status='EXECUTED')
    s.commission.capture(s.plan['id'])
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 1
    with pytest.raises(ValueError): validated_evidence(s.store, s.plan['id'])


def test_cancel_requires_terminal_flat_reads_and_cannot_certify_protection_fill(tmp_path):
    s = Session(tmp_path); s.active()
    s.commission.cancel(s.plan['id'], s.plan_hash)
    s.now += timedelta(seconds=5)
    assert s.commission.capture(s.plan['id'])['status'] == 'CANCELLED_FLAT'
    with pytest.raises(ValueError): validated_evidence(s.store, s.plan['id'])
    assert len(s.broker.writes) == 2


def test_cancel_trigger_race_captures_child_without_second_buy(tmp_path):
    s = Session(tmp_path); s.active(); s.broker.cancel_race = True
    result = s.commission.cancel(s.plan['id'], s.plan_hash)
    assert result['status'] == 'CHILD_FILLED'
    assert s.broker.quantity == s.plan['quantity']
    assert len(s.broker.writes) == 2


def test_pending_close_is_never_submitted_twice(tmp_path):
    s = Session(tmp_path); s.active(); s.broker.trigger(); s.broker.fill_close = False
    s.commission.close(s.plan['id'], s.plan_hash)
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 2


def test_cleanup_after_plan_deadline_and_completed_evidence_stable(tmp_path):
    s = Session(tmp_path); s.active(); s.broker.trigger()
    s.now += timedelta(hours=3)
    s.commission.close(s.plan['id'], s.plan_hash); s.now += timedelta(seconds=5)
    assert s.commission.capture(s.plan['id'])['status'] == 'COMPLETE'
    evidence = validated_evidence(s.store, s.plan['id'], s.binding)
    s.now += timedelta(days=1)
    assert s.commission.capture(s.plan['id'])['status'] == 'COMPLETE'
    assert validated_evidence(s.store, s.plan['id'], s.binding) == evidence


@pytest.mark.parametrize('field', ['release', 'policy_hash', 'sdk_version', 'account_fingerprint', 'egress_ip'])
def test_evidence_binding_mismatch_rejected(tmp_path, field):
    s = Session(tmp_path); s.complete(); binding = dict(s.binding)
    binding[field] = 'different'
    with pytest.raises(ValueError): validated_evidence(s.store, s.plan['id'], binding)


@pytest.mark.parametrize('fault', ['plan', 'parent', 'child', 'close', 'flat', 'receipt', 'create_receipt', 'final_parent', 'receipt_time'])
def test_completed_evidence_rederives_facts_and_rejects_corruption(tmp_path, fault):
    s = Session(tmp_path); s.complete(); record = s.store.meta(PREFIX + s.plan['id'])
    if fault == 'plan': record['plan']['quantity'] += 1
    elif fault == 'parent': record['active'][0]['parent']['quantity'] += 1
    elif fault == 'child': record['child']['order']['order_reference_id'] = 'other-ref'
    elif fault == 'close': record['closes'][0]['request']['transaction_type'] = 'BUY'
    elif fault == 'flat': record['flat'][0]['observation']['quantity'] = 1
    elif fault == 'receipt': record['closes'][0]['receipt']['binding']['release'] = 'c'*40
    elif fault == 'create_receipt': record['create_receipt']['request_hash'] = 'd'*64
    elif fault == 'final_parent': record['parent']['quantity'] += 1
    elif fault == 'receipt_time': record['closes'][0]['receipt']['at'] = (s.now+timedelta(days=1)).isoformat()
    s.store.set_meta(PREFIX + s.plan['id'], record)
    with pytest.raises(ValueError): validated_evidence(s.store, s.plan['id'], s.binding)


def test_restart_and_other_plan_cannot_steal_active_test(tmp_path):
    s = Session(tmp_path); s.submit(); other = copy.deepcopy(s.plan); other['id'] = 'another-plan'
    with pytest.raises(ValueError): s.new_process().submit(other, identity(other))
    assert len(s.broker.writes) == 1


def test_idle_production_is_allowed_only_without_unsettled_reservations(tmp_path):
    s = Session(tmp_path); s.store.set_meta('premium-executor-v1', {'phase': 'IDLE'})
    s.commission.preview(s.plan)
    s.journal.reserve('old-slot', s.plan['contract']['symbol'], 'BUY', s.plan['quantity'])
    with pytest.raises(ValueError, match='NO_ENGINE_BASKET'):
        s.commission.preview(s.plan)
    assert not s.broker.writes


def test_provider_uppercase_exception_cannot_enter_public_status(tmp_path):
    s = Session(tmp_path); s.submit()
    def unavailable(**args): raise ValueError('PRIVATE_ACCOUNT_SECRET')
    s.broker.get_smart_order = unavailable
    result = s.commission.capture(s.plan['id'])
    assert result['reason'] == 'COMMISSIONING_PROVIDER_READ_UNVERIFIED'
    assert 'PRIVATE_ACCOUNT_SECRET' not in str(result)


def test_slow_historical_scan_precedes_final_price_and_money_reads(tmp_path):
    s = Session(tmp_path); calls = []
    original_list = s.broker.get_smart_order_list
    def slow_list(**args):
        calls.append('smart'); s.now += timedelta(seconds=1)
        return original_list(**args)
    original_quote = s.market.quote
    def quote(*args, **kwargs):
        calls.append('quote'); return original_quote(*args, **kwargs)
    s.broker.get_smart_order_list = slow_list; s.market.quote = quote
    preview = s.commission.preview(s.plan)
    assert preview['status'] == 'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'
    assert calls.index('quote') == 28
    assert not s.broker.writes


def test_slow_final_positions_read_does_not_retime_old_funds_and_quotes(tmp_path):
    s = Session(tmp_path); original = s.broker.get_positions_for_user
    def slow_positions(**args):
        s.now += timedelta(seconds=11)
        return original(**args)
    s.broker.get_positions_for_user = slow_positions
    with pytest.raises(ValueError, match='FRESH_COMMISSIONING_OBSERVATION_REQUIRED'):
        s.commission.preview(s.plan)
    assert not s.broker.writes


def test_arm_readiness_checks_fresh_settlement_without_rewriting_proof(tmp_path):
    s = Session(tmp_path); proof = s.complete(); calls = len(s.broker.writes)
    assert s.commission.verify_ready_to_arm(s.plan['id']) == proof
    assert len(s.broker.writes) == calls
    assert validated_evidence(s.store, s.plan['id']) == proof


@pytest.mark.parametrize('fault', ['later_plan', 'manual_position', 'external_order', 'parent_changed', 'parent_active', 'child_changed', 'close_changed'])
def test_historical_completion_cannot_bypass_current_arm_failures(tmp_path, fault):
    s = Session(tmp_path); s.complete()
    if fault == 'later_plan': s.store.set_meta('broker-commissioning-latest-id', 'later-owner-plan')
    elif fault == 'manual_position': s.broker.quantity = 1
    elif fault == 'external_order':
        s.broker.orders['new-manual'] = dict(groww_order_id='new-manual', trading_symbol=s.plan['contract']['symbol'], order_status='EXECUTED')
    elif fault == 'parent_changed': next(iter(s.broker.parents.values()))['quantity'] += 1
    elif fault == 'parent_active': next(iter(s.broker.parents.values()))['status'] = 'ACTIVE'
    elif fault == 'child_changed': next(r for r in s.broker.orders.values() if r['transaction_type'] == 'BUY')['filled_quantity'] -= 1
    elif fault == 'close_changed': next(r for r in s.broker.orders.values() if r['transaction_type'] == 'SELL')['filled_quantity'] -= 1
    with pytest.raises(ValueError): s.commission.verify_ready_to_arm(s.plan['id'])
    assert len(s.broker.writes) == 2


@pytest.mark.parametrize('malformed', [None, [], {'phase': 'COMPLETE'}, {'phase': 'COMPLETE', 'plan': []}])
def test_malformed_evidence_is_unverified_without_runtime_exception(tmp_path, malformed):
    s = Session(tmp_path)
    s.store.set_meta('broker-commissioning-latest-id', s.plan['id'])
    s.store.set_meta(PREFIX + s.plan['id'], malformed)
    assert public_status(s.store, s.binding['release'], s.binding['policy_hash'])['provider_verified'] is False
    with pytest.raises(ValueError): validated_evidence(s.store, s.plan['id'])


def test_explicit_write_results_distinguish_acceptance_from_read_only_status(tmp_path):
    s = Session(tmp_path); result = s.submit()
    assert (result['command'], result['broker_write_attempted'], result['broker_writes'], result['broker_write_outcome']) == ('submit', True, True, 'ACCEPTED')
    result = s.commission.submit(s.plan, s.plan_hash)
    assert result['command'] == 'submit' and result['broker_write_attempted'] is False and result['broker_writes'] is False
    s.broker.trigger()
    result = s.commission.close(s.plan['id'], s.plan_hash)
    assert result['command'] == 'close' and result['broker_write_attempted'] and result['broker_writes'] is True
    assert s.commission.capture(s.plan['id'])['broker_writes'] is False


def test_uncertain_submission_result_never_claims_zero_writes(tmp_path):
    s = Session(tmp_path); s.broker.timeout_gtt = True; s.broker.hide_reference = True
    result = s.submit()
    assert result['broker_write_attempted'] is True and result['broker_writes'] is None
    assert result['broker_write_outcome'] == 'UNCERTAIN'


def test_cancel_result_reports_actual_attempt_and_deduplicated_read(tmp_path):
    s = Session(tmp_path); s.submit()
    first = s.commission.cancel(s.plan['id'], s.plan_hash)
    assert first['command'] == 'cancel' and first['broker_writes'] is True and first['broker_write_attempted']
    second = s.commission.cancel(s.plan['id'], s.plan_hash)
    assert second['broker_writes'] is False and second['broker_write_attempted'] is False


def test_proven_gate_abort_never_retries_same_plan_and_allows_new_reviewed_plan(tmp_path):
    s = Session(tmp_path)
    def deny(*args, **kwargs): raise ExecutionDenied('SYNTHETIC_GATE_ABORT')
    s.commission.gate.check = deny
    result = s.submit()
    assert result['status'] == 'ABORTED_BEFORE_WRITE' and result['broker_writes'] is False
    assert result['broker_write_attempted'] is False
    current = s.new_process()
    assert current.submit(s.plan, s.plan_hash)['broker_write_attempted'] is False
    other = copy.deepcopy(s.plan); other['id'] = 'owner-new-plan'
    preview = current.preview(other)
    result = current.submit(other, preview['plan_hash'])
    assert result['broker_writes'] is True and len(s.broker.writes) == 1
