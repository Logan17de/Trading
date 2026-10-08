"""Protection commissioning uses synthetic brokers, never real order routes."""
from copy import deepcopy
from datetime import timedelta

import pytest

from test_broker_commissioning import Session
from nifty_engine.agent_engine.broker_commissioning import PREFIX, validated_evidence
from nifty_engine.agent_engine.commissioning_stop import FORMAT
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine import live_setup, owner_setup


def filled(tmp_path, index='NIFTY'):
    s = Session(tmp_path, index)
    s.plan.update(format=FORMAT, stop_trigger_price='7.00', stop_limit_price='6.95')
    s.active(); s.broker.trigger(); s.commission.capture(s.plan['id'])
    return s


def protected(s):
    result = s.commission.protect(s.plan['id'], s.plan_hash)
    assert result['status'] == 'STOP_ACTIVE_OBSERVED'
    s.now += timedelta(seconds=5); s.commission = s.new_process()
    result = s.commission.capture(s.plan['id'])
    assert result['facts']['stop_persisted']
    return s.store.meta(PREFIX + s.plan['id'])['stop']


def stop_trigger(s, *, quantity=None, linked=True):
    stop = s.store.meta(PREFIX + s.plan['id'])['stop']
    parent = s.broker.parents[stop['smart_id']]
    parent.update(status='TRIGGERED', triggered_at=s.now.isoformat())
    ref = stop['reference'] if linked else 'manual-stop'
    qty = parent['quantity'] if quantity is None else quantity
    req = parent['order']
    s.broker.orders['exit-' + ref] = dict(groww_order_id='exit-' + ref, order_reference_id=ref,
        segment='FNO', product='NRML', exchange=parent['exchange'], trading_symbol=parent['trading_symbol'],
        validity='DAY', quantity=parent['quantity'], transaction_type='SELL', order_type='LIMIT',
        price=float(req['price']), filled_quantity=qty, average_fill_price=float(req['price']) if qty else 0,
        order_status='EXECUTED' if qty == parent['quantity'] else 'OPEN')
    s.broker.quantity -= qty


@pytest.mark.parametrize('index', ['NIFTY', 'SENSEX'])
def test_protected_workflow_can_prepare_live_only_after_stop_and_exit_evidence(tmp_path, index):
    s = Session(tmp_path, index); proof = s.complete(protected=True)
    assert all(proof['facts'].values()) and len(s.broker.writes) == 4
    assert s.commission.verify_ready_to_arm(s.plan['id']) == proof
    s.store.set_meta(live_setup.KEY, live_setup.receipt(proof, s.now))
    assert live_setup.activation(s.store, s.binding['release'], s.binding['policy_hash']) == proof
    assert s.pause.exists() and not s.store.meta('premium-algo-intent')


def test_unprotected_legacy_evidence_is_preserved_but_cannot_arm(tmp_path):
    s = Session(tmp_path); proof = s.complete()
    s.store.set_meta(live_setup.KEY, live_setup.receipt(proof, s.now))
    assert live_setup.activation(s.store, s.binding['release'], s.binding['policy_hash']) is None
    with pytest.raises(ValueError, match='STOP_EVIDENCE_REQUIRED'):
        s.commission.verify_ready_to_arm(s.plan['id'])


def test_stop_blocks_separate_sell_until_confirmed_inactive(tmp_path):
    s = filled(tmp_path); protected(s)
    assert s.commission.status(s.plan['id'])['allowed_actions'] == ['cancel-stop']
    with pytest.raises(ValueError, match='CANCEL_STOP_BEFORE_CLOSE'):
        s.commission.close(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 2
    s.commission.cancel_stop(s.plan['id'], s.plan_hash)
    assert s.commission.status(s.plan['id'])['allowed_actions'] == ['close']
    s.commission.close(s.plan['id'], s.plan_hash)
    assert s.broker.quantity == 0


@pytest.mark.parametrize('index', ['NIFTY', 'SENSEX'])
def test_stop_trigger_child_is_exact_exit_and_never_double_sold(tmp_path, index):
    s = filled(tmp_path, index); protected(s); stop_trigger(s)
    s.commission.capture(s.plan['id']); s.now += timedelta(seconds=5)
    assert s.commission.capture(s.plan['id'])['status'] == 'COMPLETE'
    assert validated_evidence(s.store, s.plan['id'], s.binding)['facts']['standard_order_filled']
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 2 and s.broker.quantity == 0


def test_stop_cancel_trigger_race_captures_exit_before_any_new_sell(tmp_path):
    s = filled(tmp_path); protected(s)
    def race(**args):
        s.broker._write('POST', '/v1/order-advance/cancel/FNO/GTT/' + args['smart_order_id'], None)
        stop_trigger(s)
        return dict(smart_order_id=args['smart_order_id'], status='CANCELLED')
    s.broker.cancel_smart_order = race
    result = s.commission.cancel_stop(s.plan['id'], s.plan_hash)
    assert result['status'] == 'FLAT_AWAITING_SECOND_READ'
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)
    assert s.broker.quantity == 0 and len(s.broker.writes) == 3


def test_partial_stop_requires_exact_child_cancel_and_only_closes_remainder(tmp_path):
    s = filled(tmp_path); protected(s); stop_trigger(s, quantity=10)
    s.commission.capture(s.plan['id'])
    assert s.commission.status(s.plan['id'])['allowed_actions'] == ['cancel-stop']
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)
    s.commission.cancel_stop(s.plan['id'], s.plan_hash)
    s.commission.close(s.plan['id'], s.plan_hash)
    assert s.broker.writes[-1][2]['quantity'] == s.plan['quantity'] - 10
    s.now += timedelta(seconds=5)
    assert s.commission.capture(s.plan['id'])['status'] == 'COMPLETE'


def test_unknown_child_stops_cleanup_without_adopting_manual_order(tmp_path):
    s = filled(tmp_path); protected(s); stop_trigger(s, linked=False)
    result = s.commission.capture(s.plan['id'])
    assert result['status'] == 'RECONCILIATION_REQUIRED' and not result['allowed_actions']
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 2


def test_create_timeout_recovers_original_parent_after_restart_without_retry(tmp_path):
    s = filled(tmp_path); s.broker.timeout_gtt = True
    assert s.commission.protect(s.plan['id'], s.plan_hash)['facts']['stop_order_write']
    s.commission = s.new_process()
    s.commission.protect(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 2


def test_unknown_stop_cancel_is_never_retried_and_blocks_separate_sell(tmp_path):
    s = filled(tmp_path); protected(s)
    def lost(**args):
        s.broker._write('POST', '/v1/order-advance/cancel/FNO/GTT/' + args['smart_order_id'], None)
        raise TimeoutError('private transport message')
    s.broker.cancel_smart_order = lost
    result = s.commission.cancel_stop(s.plan['id'], s.plan_hash)
    assert result['broker_write_outcome'] == 'UNCERTAIN'
    s.commission = s.new_process()
    s.commission.cancel_stop(s.plan['id'], s.plan_hash)
    with pytest.raises(ValueError): s.commission.close(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 3 and s.broker.quantity == s.plan['quantity']


def test_stop_quote_must_still_be_fresh_at_capability_use(tmp_path):
    s = filled(tmp_path)
    original = s.commission._observe
    def slow(*a, **k):
        obs = original(*a, **k)
        s.now += timedelta(seconds=11)
        return obs
    s.commission._observe = slow
    with pytest.raises(ValueError): s.commission.protect(s.plan['id'], s.plan_hash)
    assert len(s.broker.writes) == 1


@pytest.mark.parametrize('fault', ['request', 'quantity', 'receipt', 'active', 'timestamp', 'child'])
def test_protection_evidence_cannot_be_fabricated_from_modified_saved_flags(tmp_path, fault):
    s = filled(tmp_path); protected(s); stop_trigger(s)
    s.commission.capture(s.plan['id']); s.now += timedelta(seconds=5); s.commission.capture(s.plan['id'])
    record = s.store.meta(PREFIX + s.plan['id']); stop = record['stop']
    if fault == 'request': stop['request']['trigger_direction'] = 'UP'
    if fault == 'quantity': stop['observation']['quantity'] = 999
    if fault == 'receipt': stop['create_receipt']['binding']['release'] = 'f' * 40
    if fault == 'active': stop['active'][0]['parent']['order']['transaction_type'] = 'BUY'
    if fault == 'timestamp': stop['created_at'] = (s.now + timedelta(days=1)).isoformat()
    if fault == 'child': stop['child']['order']['order_reference_id'] = 'manual'
    s.store.set_meta(PREFIX + s.plan['id'], record)
    with pytest.raises(ValueError): validated_evidence(s.store, s.plan['id'], s.binding)


def test_cancelled_old_release_reconciliation_retains_original_binding(tmp_path):
    s = Session(tmp_path); s.plan['id'] = 'a' * 32; s.submit()
    next(iter(s.broker.parents.values()))['status'] = 'CANCELLED'
    original = deepcopy(s.binding); s.binding['release'] = 'e' * 40; tool = s.new_process()
    first = owner_setup.retire_plan(s.store, tool, s.binding, s.plan['id'])
    assert first['status'] == 'FLAT_AWAITING_SECOND_READ'
    s.now += timedelta(seconds=5)
    result = owner_setup.retire_plan(s.store, tool, s.binding, s.plan['id'])
    assert result['status'] == 'PRIOR_TEST_RETIRED' and not result['provider_verified']
    record = s.store.meta(PREFIX + s.plan['id'])
    assert record['binding'] == original and record['plan_hash'] == identity(record['plan'])
    assert len(s.broker.writes) == 1 and record['phase'] == 'CANCELLED_FLAT'


def test_active_old_release_cannot_be_retired(tmp_path):
    s = Session(tmp_path); s.plan['id'] = 'a' * 32; s.submit()
    s.binding['release'] = 'e' * 40
    result = owner_setup.retire_plan(s.store, s.new_process(), s.binding, s.plan['id'])
    assert result['status'] == 'ACTIVE_PARENT_OBSERVED'
    assert not s.store.meta('broker-commissioning-retirement-' + s.plan['id'])
    assert len(s.broker.writes) == 1
