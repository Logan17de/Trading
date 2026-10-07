"""Synthetic broker replay; never proves real Groww acceptance or child linkage."""
import copy
from datetime import timedelta

import pytest

from test_premium_execution import Session, CFG
from test_report_execution import ReportSession
from test_normal_theta import NormalSession
from nifty_engine.agent_engine import premium_strategy as policy
from nifty_engine.agent_engine.pc_control import PcJournal
from nifty_engine.agent_engine.premium_executor import PremiumExecutor


def snapshot(s, *, flat=False, partial=False):
    obs = s.observation()
    obs.update(orders_complete=True, orders_received_at=s.now.isoformat(),
               orders=copy.deepcopy(list(s.broker.orders.values())))
    if flat: obs['positions'] = []
    elif partial:
        obs['positions'] = [r for r in obs['positions'] if r['quantity'] > 0]
    return obs


def advance(s):
    s.now += timedelta(seconds=5)
    for b in s.books.values(): b['received_at'] = s.now.isoformat()


def manually_close(s, *, partial=False):
    for symbol, qty in s.executor._net(s.executor._state()).items():
        if partial and qty > 0: continue
        ref = 'self-' + symbol
        s.broker.orders[ref] = dict(groww_order_id=ref, order_reference_id=ref, trading_symbol=symbol,
            exchange='BSE' if symbol.startswith('SENSEX') else 'NSE', product='NRML', segment='FNO',
            transaction_type='SELL' if qty > 0 else 'BUY', quantity=abs(qty), filled_quantity=abs(qty), order_status='EXECUTED')


def setup(s):
    s.monitoring()
    s.executor.tick(snapshot(s), {})
    assert s.executor._state()['external_order_baseline'] == {}


@pytest.mark.parametrize('index', ['NIFTY', 'SENSEX'])
@pytest.mark.parametrize('factory', [ReportSession, NormalSession])
def test_report_self_close_cancels_only_owned_parent_releases_slot_and_keeps_manual_protection(tmp_path, index, factory):
    s = factory(tmp_path, index=index); setup(s)
    before = len(s.broker.writes); manually_close(s)
    obs = snapshot(s, flat=True)
    s.journal.ownership(obs['orders'], [], complete=True)
    assert s.executor.tick(obs, {})['reason'] == 'SELF_CLOSE_SECOND_FLAT_READ_REQUIRED'
    assert len(s.broker.writes) == before
    advance(s); result = s.executor.tick(snapshot(s, flat=True), {})
    assert result['reason'] == 'SELF_CLOSED_CONFIRMED'
    assert s.executor._state()['phase'] == 'CLOSED'
    assert not s.journal.slot_status()['new_entry_blocked']
    assert all(k == 'CANCEL_GTT' for k, _ in s.broker.writes[before:])
    assert s.journal.store.read('SELECT symbol FROM pc_protected')
    assert not any(r['reference'].startswith('self-') for r in s.journal.store.read('SELECT reference FROM pc_orders'))
    public = s.executor.public(snapshot(s, flat=True))
    assert public['last_external_close']['actor'] == 'SELF'
    assert public['last_external_close']['pnl_status'] == 'UNKNOWN'
    assert 'groww_order_id' not in str(public['last_external_close'])
    from nifty_engine.agent_engine.visual_report import build
    from test_premium_runtime import model
    view = model(); view['execution_controller'] = public
    assert 'Closed outside algo (Self)' in build(view, s.now.date().isoformat())['mail']['html']


@pytest.mark.parametrize('blocker', ['off', 'paper', 'pause', 'proof'])
def test_self_close_bookkeeping_does_not_bypass_write_gates(tmp_path, blocker):
    s = Session(tmp_path); setup(s); manually_close(s)
    if blocker == 'off': policy.set_intent(s.journal.store, False, s.now)
    if blocker == 'paper': s.gate.mode = 'paper'
    if blocker == 'pause': s.pause.touch()
    if blocker == 'proof': s.journal.store.set_meta('premium-execution-activation', {})
    before = list(s.broker.writes)
    s.executor.tick(snapshot(s, flat=True), {}); advance(s)
    result = s.executor.tick(snapshot(s, flat=True), {})
    assert result['reason'] == 'SELF_CLOSED_OWNED_PROTECTION_CLEANUP_PENDING'
    assert s.executor._state()['external_close']['status'] == 'CONFIRMED_FLAT'
    assert s.journal.slot_status()['new_entry_blocked']
    assert s.broker.writes == before
    # Owner cancels the GTT externally; read-only closure now completes while Off/paper.
    next(iter(s.broker.smart.values()))['status'] = 'CANCELLED'
    advance(s)
    assert s.executor.tick(snapshot(s, flat=True), {})['reason'] == 'SELF_CLOSED_CONFIRMED'
    assert s.broker.writes == before


def test_partial_manual_close_freezes_management_and_never_reopens_short(tmp_path):
    s = Session(tmp_path); setup(s); manually_close(s, partial=True)
    before = list(s.broker.writes)
    for _ in range(3):
        advance(s)
        assert s.executor.tick(snapshot(s, partial=True), {})['reason'] == 'SELF_POSITION_CHANGE_MANAGEMENT_STOPPED'
    assert s.executor._state()['external_close']['status'] == 'PARTIAL_OR_MIXED'
    assert not s.executor.public(snapshot(s, partial=True))['management_enabled']
    assert 'SELF_CLOSE_RECONCILIATION_REQUIRED' in s.executor.public(snapshot(s, partial=True))['blockers']
    assert s.journal.slot_status()['new_entry_blocked'] and s.broker.writes == before


@pytest.mark.parametrize('fault', ['incomplete', 'stale', 'duplicate', 'pending', 'wrong_product', 'reserved_reference'])
def test_invalid_external_close_evidence_cannot_release_or_cancel(tmp_path, fault):
    s = Session(tmp_path); setup(s); manually_close(s); before = list(s.broker.writes)
    for _ in range(2):
        advance(s); obs = snapshot(s, flat=True)
        external = next(r for r in obs['orders'] if r['order_reference_id'].startswith('self-'))
        if fault == 'incomplete': obs['orders_complete'] = False
        if fault == 'stale': obs['orders_received_at'] = (s.now - timedelta(seconds=11)).isoformat()
        if fault == 'duplicate': obs['orders'].append(copy.deepcopy(external))
        if fault == 'pending': external['order_status'] = 'OPEN'
        if fault == 'wrong_product': external['product'] = 'MIS'
        if fault == 'reserved_reference': external['order_reference_id'] = next(iter(s.broker.smart.values()))['reference_id']
        s.executor.tick(obs, {})
    assert s.executor._state()['phase'] != 'CLOSED'
    assert s.broker.writes == before


def test_same_snapshot_is_not_two_reads_and_restart_preserves_confirmation(tmp_path):
    s = Session(tmp_path); setup(s); manually_close(s)
    obs = snapshot(s, flat=True); before = len(s.broker.writes)
    for _ in range(3):
        assert s.executor.tick(obs, {})['reason'] == 'SELF_CLOSE_SECOND_FLAT_READ_REQUIRED'
    assert len(s.broker.writes) == before
    s.executor = PremiumExecutor(PcJournal(tmp_path/'journal.sqlite3'), s.gateway, s.protection, s.gate, CFG, clock=lambda:s.now)
    advance(s)
    assert s.executor.tick(snapshot(s, flat=True), {})['reason'] == 'SELF_CLOSED_CONFIRMED'


def test_cancel_trigger_race_does_not_release_flat_slot_or_place_another_close(tmp_path):
    s = Session(tmp_path); setup(s); manually_close(s); s.broker.cancel_race = True
    s.executor.tick(snapshot(s, flat=True), {}); advance(s)
    before = len(s.broker.writes)
    assert s.executor.tick(snapshot(s, flat=True), {})['reason'] == 'SELF_CLOSE_POST_PROTECTION_POSITION_READ_REQUIRED'
    assert s.journal.slot_status()['new_entry_blocked']
    assert [k for k, _ in s.broker.writes[before:]] == ['CANCEL_GTT']


def test_direct_orphan_cancel_without_durable_flat_proof_is_rejected(tmp_path):
    s = Session(tmp_path); setup(s); before = list(s.broker.writes)
    with pytest.raises(ValueError, match='EXTERNAL_CLOSE_CONFIRMED_FLAT_REQUIRED'):
        with s.gateway.scope(snapshot(s, flat=True), 'PROTECT'):
            s.protection.cancel_flat(s.executor._state()['protection_key'], snapshot(s, flat=True))
    assert s.broker.writes == before


def test_old_flat_basket_is_not_guessed_as_self_closure_without_baseline(tmp_path):
    s = Session(tmp_path); s.monitoring(); manually_close(s)
    before = list(s.broker.writes)
    for _ in range(2): advance(s); s.executor.tick(snapshot(s, flat=True), {})
    assert 'external_close' not in s.executor._state()
    assert s.journal.slot_status()['new_entry_blocked'] and s.broker.writes == before


def test_cancel_acceptance_or_timeout_without_inactive_readback_never_repeats_post(tmp_path):
    s = Session(tmp_path); setup(s); manually_close(s)
    original = s.broker.cancel_smart_order
    def uncertain(**args):
        s.broker.writes.append(('CANCEL_GTT', args['smart_order_id']))
        raise TimeoutError('private provider body')
    s.broker.cancel_smart_order = uncertain
    s.executor.tick(snapshot(s, flat=True), {}); advance(s)
    before = len(s.broker.writes)
    result = s.executor.tick(snapshot(s, flat=True), {})
    assert result['reason'] == 'SELF_CLOSE_PROTECTION_CANCEL_RECONCILIATION_REQUIRED'
    for _ in range(3): advance(s); s.executor.tick(snapshot(s, flat=True), {})
    assert [k for k, _ in s.broker.writes[before:]] == ['CANCEL_GTT']
    assert s.journal.slot_status()['new_entry_blocked']
    s.broker.cancel_smart_order = original
    next(iter(s.broker.smart.values()))['status'] = 'CANCELLED'
    advance(s)
    assert s.executor.tick(snapshot(s, flat=True), {})['reason'] == 'SELF_CLOSED_CONFIRMED'


def test_unlinked_generated_child_is_not_labelled_as_a_personal_close(tmp_path):
    s = Session(tmp_path); setup(s)
    s.broker.trigger(next(iter(s.broker.smart.values())), linked=False)
    manually_close(s, partial=False)
    # The unlinked child and external close together are deliberately ambiguous.
    before = list(s.broker.writes)
    for _ in range(2): advance(s); s.executor.tick(snapshot(s, flat=True), {})
    assert s.executor._state()['external_close']['actor'] == 'UNKNOWN'
    assert s.broker.writes == before and s.journal.slot_status()['new_entry_blocked']
