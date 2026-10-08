"""Owner-requested LIMIT test; durable references, truthful broker evidence.

This is not called by Algo Start. The independent owner test job advances only
an exact, explicitly authorized plan. Reads never create an authorization.
"""
from __future__ import annotations

import copy
import uuid

from .contracts import EXCHANGES, identity, stamp
from .execution import TERMINAL, fresh
from .execution_gate import ExecutionDenied
from .market_check import quote_summary
from . import commissioning_stop as protection

AUTH = 'owner-direct-test-authorization'
FINAL = {'COMPLETE', 'CANCELLED_FLAT', 'ABORTED_BEFORE_WRITE', 'CLOSED_UNVERIFIED'}


def request(plan, reference):
    return dict(trading_symbol=plan['contract']['symbol'], quantity=plan['quantity'], segment='FNO',
                exchange=EXCHANGES[plan['contract']['index']], product='NRML', order_type='LIMIT',
                transaction_type='BUY', validity='DAY', price=float(plan['buy_limit_price']),
                order_reference_id=reference, trigger_price=None)


def submit(tool, plan, expected_hash):
    from .broker_commissioning import PREFIX, validate_plan, fail, _id, _command_result
    value = validate_plan(plan, tool.binding, tool.clock())
    if expected_hash != identity(value): fail('EXACT_OWNER_PLAN_HASH_REQUIRED')
    if tool.store.meta(PREFIX + value['id']) is not None:
        if tool._load(value['id'])['plan_hash'] != expected_hash: fail('COMMISSIONING_PLAN_ID_CONFLICT')
        return _command_result(tool.capture(value['id']), 'submit', attempted=False, confirmed=False)
    obs = tool._initial(value)
    ref = 'CB' + uuid.uuid4().hex[:18]
    record = dict(format='trading-broker-direct-record-v1', plan=value, plan_hash=identity(value),
        binding=tool.binding, phase='BUY_SUBMITTING', reference=ref, request=request(value, ref),
        created_at=tool.clock().isoformat(), baseline_orders=obs['baseline_orders'], closes=[], flat=[],
        submit_session=tool.session_id, submit_observation=obs)
    tool._save(record); previous = copy.deepcopy(record)
    try:
        receipt = tool._write(record, obs, 'POST', '/v1/order/create', record['request'],
            lambda: tool.transport.place_order(timeout=5, **record['request']))
        identifier = _id(receipt.get('groww_order_id'))
        if receipt.get('order_reference_id') != ref: fail('COMMISSIONING_BUY_RECEIPT_REFERENCE_DIFFERS')
        record.update(broker_id=identifier, phase='BUY_ACCEPTED', create_receipt=dict(
            groww_order_id=identifier, order_reference_id=ref, request_hash=identity(record['request']),
            binding=tool.binding, at=tool.clock().isoformat()))
    except ExecutionDenied:
        record.update(phase='ABORTED_BEFORE_WRITE', reason='COMMISSIONING_GATE_DENIED')
    except Exception:
        record.update(phase='BUY_RESULT_UNKNOWN', reason='COMMISSIONING_CREATE_RECONCILIATION_REQUIRED')
    tool._save(record, previous)
    result = tool.capture(value['id']); current = tool._load(value['id'])
    return _command_result(result, 'submit', attempted=current['phase'] != 'ABORTED_BEFORE_WRITE',
                           confirmed=bool(current.get('create_receipt')))


def capture(tool, record):
    from .broker_commissioning import CommissioningError, _public, fail, validated_evidence
    if record['phase']=='COMPLETE':
        validated_evidence(tool.store,record['plan']['id'],tool.binding)
        return _public(record,tool.clock())
    if record['phase']=='ABORTED_BEFORE_WRITE':
        return _public(record, tool.clock())
    tool._guard(); previous = copy.deepcopy(record)
    try:
        started = tool.clock().isoformat(); tool._profile()
        row = tool._read_order(record['request'], record['reference'], record.get('broker_id'))
        if row['filled_quantity'] < record.get('child', {}).get('order', {}).get('filled_quantity', 0):
            fail('COMMISSIONING_FILL_REGRESSION')
        record['broker_id'] = row['groww_order_id']
        record['child'] = dict(kind='DIRECT_BUY', order=row, at=tool.clock().isoformat(),
                               request_hash=identity(record['request']))
        if not record.get('create_receipt'):
            record['create_receipt'] = dict(groww_order_id=row['groww_order_id'],
                order_reference_id=record['reference'], request_hash=identity(record['request']),
                binding=tool.binding, at=tool.clock().isoformat(), source='ORIGINAL_REFERENCE_READBACK')
        record['phase'] = 'CHILD_FILLED' if row['order_status'] == 'EXECUTED' and row['filled_quantity'] == record['plan']['quantity'] else 'BUY_PENDING_OR_PARTIAL'
        tool._archive_aborted_closes(record)
        for item in record['closes']: tool._capture_close(item)
        protection.capture(tool, record)
        if record.get('stop',{}).get('child'):
            record['phase']='STOP_CHILD_OBSERVED'
        elif protection.facts(record)['stop_order_write'] and not protection.settled(record):
            record['phase']='STOP_ACTIVE_OBSERVED'
        obs = tool._observe(record['plan'], record=record)
        obs['received_at'] = min(started, obs['received_at'], key=stamp)
        if not fresh(obs['received_at'], tool.clock(), 10): fail('FRESH_COMMISSIONING_OBSERVATION_REQUIRED')
        record['last_observation'] = obs
        acquired = row['filled_quantity']; sold = protection.sold(record)
        if obs['quantity'] != acquired - sold or sold > acquired: fail('COMMISSIONING_POSITION_NOT_EXCLUSIVE')
        if 0 < acquired < record['plan']['quantity']:
            fail('COMMISSIONING_PARTIAL_BUY_REQUIRES_REVIEW')
        exits_terminal = all(o['order_status'] in TERMINAL for o in protection.exit_orders(record))
        if not exits_terminal: record['phase'] = 'CLOSE_PENDING_OR_PARTIAL'
        elif row['order_status'] in TERMINAL and 0 < acquired < record['plan']['quantity']:
            record['phase'] = 'CHILD_TERMINAL_WITH_REMAINDER'
        if not obs['quantity'] and row['order_status'] in TERMINAL and exits_terminal and protection.settled(record):
            if not record['flat'] or (stamp(obs['received_at']) - stamp(record['flat'][-1]['at'])).total_seconds() >= 5:
                record['flat'].append(dict(at=obs['received_at'], session_id=tool.session_id,
                    observation_hash=identity(obs), observation=obs))
                record['flat'] = record['flat'][-2:]
            record['phase'] = 'FLAT_AWAITING_SECOND_READ'
            if len(record['flat']) == 2:
                record['phase'] = 'COMPLETE' if facts(record)['all_complete'] else 'CLOSED_UNVERIFIED' if acquired else 'CANCELLED_FLAT'
                record['completed_at'] = tool.clock().isoformat()
        else: record['flat'] = []
        record.pop('reason', None)
    except Exception as exc:
        record.update(phase='RECONCILIATION_REQUIRED', reason=str(exc) if isinstance(exc, CommissioningError)
                      else 'COMMISSIONING_PROVIDER_READ_UNVERIFIED')
    record['checked_at'] = tool.clock().isoformat()
    tool._save(record, previous)
    return _public(record, tool.clock())


def facts(record):
    qty = record['plan']['quantity']; buy = record.get('child', {}).get('order', {})
    receipt = record.get('create_receipt', {}); stop = record.get('stop', {})
    exit_child = stop.get('child', {}).get('order', {})
    stop_facts = protection.facts(record)
    flat = record.get('flat', [])
    exits = protection.exit_orders(record)
    close_ack = bool(record['closes']) and all(i.get('receipt', {}).get('binding') == record['binding'] and
        i['receipt'].get('request_hash') == identity(i['request']) for i in record['closes'])
    value = dict(oracle_order_write=bool(receipt.get('binding') == record['binding'] and
        receipt.get('groww_order_id') == record.get('broker_id') and receipt.get('order_reference_id') == record['reference'] and
        receipt.get('request_hash') == identity(record['request'])),
        buy_order_link=bool(buy and buy.get('order_reference_id') == record['reference']),
        child_filled=buy.get('order_status') == 'EXECUTED' and buy.get('filled_quantity') == qty,
        persistent_gtt=stop_facts['stop_persisted'],
        exact_child_link=bool(exit_child and exit_child.get('order_reference_id') == stop.get('reference')),
        standard_order_write=close_ack or bool(exit_child and stop_facts['stop_order_write']),
        standard_order_filled=bool(exits) and all(i['order_status'] in TERMINAL for i in exits) and protection.sold(record) == qty,
        flat_confirmed=len(flat) == 2 and (stamp(flat[-1]['at']) - stamp(flat[0]['at'])).total_seconds() >= 5,
        inactive_parent=protection.entry_settled(record) and protection.settled(record), **stop_facts)
    return dict(value, all_complete=all(value.values()))


def advance(tool, plan):
    """Only the explicit owner-authorized job may call this; never a GET handler."""
    from .broker_commissioning import PREFIX, fail
    auth = tool.store.meta(AUTH, {})
    if auth.get('plan_id') != plan['id'] or auth.get('plan_hash') != identity(plan) or auth.get('binding') != tool.binding:
        fail('EXACT_OWNER_TEST_AUTHORIZATION_REQUIRED')
    if auth.get('state') != 'RUNNING': return dict(status='OWNER_TEST_STOPPED', broker_writes=False)
    if not tool.store.meta(PREFIX + plan['id']):
        # Store the one submit attempt before invoking it. A process crash before
        # record creation requires review; it never silently retries the BUY.
        if auth.get('submit_attempted'): fail('COMMISSIONING_CREATE_RECONCILIATION_REQUIRED')
        auth['submit_attempted'] = tool.clock().isoformat(); tool.store.set_meta(AUTH, auth)
        result = tool.submit(plan, identity(plan))
    else: result = tool.capture(plan['id'])
    if result.get('reason') or result['status'] in FINAL:
        return result
    record = tool._load(plan['id']); child = record.get('child', {}).get('order', {})
    if child.get('order_status') == 'EXECUTED' and child.get('filled_quantity') == plan['quantity'] and not record.get('stop') and not record['closes']:
        # Persist an attempt separately from the broker record. Restart/repeated
        # polling cannot resend even a gate-denied/unknown protection request.
        if auth.get('protect_attempted'): fail('COMMISSIONING_STOP_REVIEW_REQUIRED')
        auth['protect_attempted'] = tool.clock().isoformat(); tool.store.set_meta(AUTH, auth)
        return tool.protect(plan['id'], identity(plan))
    # The target is an application-managed LIMIT exit. Never leave a second
    # active SELL alongside the stop or guess remaining quantity after a race.
    if (protection.facts(record)['stop_persisted'] and not auth.get('exit_requested') and
            not record['closes'] and record.get('last_observation', {}).get('quantity') == plan['quantity']):
        q = quote_summary(tool.market.quote(plan['contract']['symbol'], segment='FNO',
            exchange=record['request']['exchange']), tool.clock())
        if q.get('timestamp_status') == 'RECENT_TRADE_ONLY' and q.get('bid_price') is not None and float(q['bid_price']) >= float(plan['sell_limit_price']):
            auth['exit_requested'] = tool.clock().isoformat(); tool.store.set_meta(AUTH, auth)
    if auth.get('exit_requested') and record.get('last_observation', {}).get('quantity', 0) > 0:
        if not protection.settled(record):
            if auth.get('cancel_stop_attempted'): return result
            auth['cancel_stop_attempted'] = tool.clock().isoformat(); tool.store.set_meta(AUTH, auth)
            return tool.cancel_stop(plan['id'], identity(plan))
        if not record['closes']:
            if auth.get('close_attempted'): fail('COMMISSIONING_CLOSE_REVIEW_REQUIRED')
            auth['close_attempted'] = tool.clock().isoformat(); tool.store.set_meta(AUTH, auth)
            return tool.close(plan['id'], identity(plan))
    return result


def validate_evidence(record, plan_id, binding=None):
    from .broker_commissioning import validate_plan, _binding, _order_matches, fail, EVIDENCE_FORMAT
    if record.get('phase') != 'COMPLETE' or record.get('reason'): fail('COMMISSIONING_COMPLETED_PROVIDER_EVIDENCE_REQUIRED')
    original = _binding(record['binding'])
    if binding is not None and original != _binding(binding): fail('COMMISSIONING_EVIDENCE_BINDING_CHANGED')
    plan = record['plan']; created = stamp(record['created_at']); completed = stamp(record['completed_at'])
    validate_plan(plan, original, completed, expired=True)
    if plan['id'] != plan_id or record['plan_hash'] != identity(plan): fail('COMMISSIONING_EVIDENCE_PLAN_CHANGED')
    if record['request'] != request(plan, record['reference']): fail('COMMISSIONING_EVIDENCE_REQUEST_CHANGED')
    receipt = record['create_receipt']; child = record['child']; row = child['order']
    if (receipt.get('binding') != original or receipt.get('groww_order_id') != record['broker_id'] or
            receipt.get('order_reference_id') != record['reference'] or receipt.get('request_hash') != identity(record['request']) or
            not created <= stamp(receipt['at']) <= completed): fail('COMMISSIONING_EVIDENCE_CREATE_RECEIPT_CHANGED')
    _order_matches(row, record['request'], record['broker_id'], record['reference'])
    if child.get('kind') != 'DIRECT_BUY' or child['request_hash'] != identity(record['request']) or not created <= stamp(child['at']) <= completed:
        fail('COMMISSIONING_EVIDENCE_CHILD_CHANGED')
    for item in record['closes']:
        expected = dict(record['request'], transaction_type='SELL', price=float(plan['sell_limit_price']),
                        order_reference_id=item['reference'], quantity=item['request']['quantity'])
        if item['request'] != expected or not 0 < expected['quantity'] <= plan['quantity']: fail('COMMISSIONING_EVIDENCE_CLOSE_CHANGED')
        _order_matches(item['order'], expected, item['broker_id'], item['reference'])
        r = item['receipt']
        if (r.get('binding') != original or r.get('groww_order_id') != item['broker_id'] or
                r.get('order_reference_id') != item['reference'] or r.get('request_hash') != identity(expected) or
                not created <= stamp(item['at']) <= stamp(r['at']) <= completed or
                not created <= stamp(item['checked_at']) <= completed): fail('COMMISSIONING_EVIDENCE_CLOSE_RECEIPT_CHANGED')
    for item in record['flat']:
        obs = item['observation']
        if (identity(obs) != item['observation_hash'] or obs.get('quantity') != 0 or obs.get('complete') is not True or
                obs.get('received_at') != item['at'] or not created <= stamp(item['at']) <= completed or
                any(o.get('order_status') not in TERMINAL for o in obs.get('orders', []))): fail('COMMISSIONING_EVIDENCE_FLAT_CHANGED')
    protection.validate(record)
    value = facts(record)
    if not value.pop('all_complete'): fail('COMMISSIONING_PROVIDER_FACTS_INCOMPLETE')
    return dict(format=EVIDENCE_FORMAT, plan_id=plan_id, binding=original, facts=value,
                completed_at=record['completed_at'], record_sha256=identity(record))


def verify_current(tool, record, proof):
    from .broker_commissioning import fail
    started = tool.clock().isoformat(); tool._profile()
    latest = tool._read_order(record['request'], record['reference'], record['broker_id'])
    if latest != record['child']['order']: fail('COMMISSIONING_CURRENT_CHILD_FILL_CHANGED')
    current = copy.deepcopy(record); protection.capture(tool, current)
    if not protection.settled(current) or current['stop'].get('child') != record['stop'].get('child'):
        # The read observation time can advance; immutable order contents cannot.
        if (not protection.settled(current) or current['stop'].get('child', {}).get('order') !=
                record['stop'].get('child', {}).get('order')): fail('COMMISSIONING_CURRENT_STOP_CHANGED')
    for item in record['closes']:
        if tool._read_order(item['request'], item['reference'], item['broker_id']) != item['order']:
            fail('COMMISSIONING_CURRENT_CLOSE_FILL_CHANGED')
    obs = tool._observe(record['plan'], record=record)
    if obs['quantity'] or any(o['order_status'] not in TERMINAL for o in obs['orders']): fail('COMMISSIONING_CURRENT_FLAT_TERMINAL_STATE_REQUIRED')
    if not fresh(started, tool.clock(), 10): fail('FRESH_COMMISSIONING_OBSERVATION_REQUIRED')
    return proof
