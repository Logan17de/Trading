"""Owner-requested protective SELL GTT for one commissioning long.

Only an explicit owner action or its exact authorized test job may write.
References are durable before each request;
uncertain creates/cancels are reconciled, never retried to force an outcome.
"""
from __future__ import annotations

import copy
import uuid
from datetime import date

from .contracts import identity, stamp
from .execution import TERMINAL, fresh
from .execution_gate import ExecutionDenied
from .market_check import quote_summary

FORMAT = 'trading-broker-commissioning-plan-v2'
DIRECT_FORMAT = 'trading-broker-commissioning-plan-v3'
PROTECTED_FORMATS = (FORMAT, DIRECT_FORMAT)
FIELDS = {'stop_trigger_price', 'stop_limit_price'}


def entry_settled(record):
    if record['plan']['format'] == DIRECT_FORMAT:
        return record.get('child', {}).get('order', {}).get('order_status') in TERMINAL
    from .broker_commissioning import FINAL_PARENT
    return record.get('parent', {}).get('status') in FINAL_PARENT


def request(plan, reference, quantity):
    from .broker_commissioning import _request
    value = _request(plan, reference)
    value.update(quantity=quantity, trigger_direction='DOWN', trigger_price=plan['stop_trigger_price'])
    value['order'] = dict(order_type='LIMIT', transaction_type='SELL', price=plan['stop_limit_price'])
    return value


def child_request(stop):
    req = stop['request']
    return dict(trading_symbol=req['trading_symbol'], quantity=req['quantity'], segment='FNO',
                exchange=req['exchange'], product='NRML', order_type='LIMIT', transaction_type='SELL',
                validity='DAY', price=req['order']['price'])


def exit_orders(record):
    items = [i['order'] for i in record.get('closes', []) if i.get('order')]
    stop_child = record.get('stop', {}).get('child', {}).get('order')
    return items + ([stop_child] if stop_child else [])


def sold(record):
    return sum(i['filled_quantity'] for i in exit_orders(record))


def settled(record):
    from .broker_commissioning import FINAL_PARENT
    stop = record.get('stop')
    return not stop or (stop.get('parent', {}).get('status') in FINAL_PARENT and
                        (not stop.get('child') or stop['child']['order']['order_status'] in TERMINAL))


def capture(tool, record):
    """GET-only; includes cancel/trigger races and exact generated child reads."""
    from .broker_commissioning import _parent_matches, fail
    stop = record.get('stop')
    if not stop:
        return
    if stop.get('aborted_before_write'):
        if any(stop.get(k) for k in ('smart_id', 'create_receipt', 'parent', 'child')):
            fail('COMMISSIONING_ABORTED_STOP_HAS_BROKER_EVIDENCE')
        record.setdefault('aborted_stops', []).append(record.pop('stop'))
        return
    tool._recover_parent(stop)
    parent = _parent_matches(tool._read('get_smart_order', segment='FNO', smart_order_type='GTT',
                                       smart_order_id=stop['smart_id']), stop)
    stop['parent'] = dict(parent, observed_at=tool.clock().isoformat())
    triggered = parent.get('triggered_at') is not None or parent['status'] in ('TRIGGERED', 'COMPLETED', 'EXECUTED')
    if parent['status'] == 'ACTIVE' and not triggered:
        if stop.get('child'):
            fail('COMMISSIONING_STOP_STATE_REGRESSION')
        if date.fromisoformat(str(parent.get('expire_at', ''))[:10]) <= date.fromisoformat(record['plan']['contract']['expiry']):
            fail('COMMISSIONING_STOP_EXPIRY_UNPROVEN')
        fact = dict(at=tool.clock().isoformat(), session_id=tool.session_id, parent=parent,
                    request_hash=identity(stop['request']))
        if not stop['active'] or (tool.clock() - stamp(stop['active'][-1]['at'])).total_seconds() >= 5:
            stop['active'].append(fact)
            stop['active'] = stop['active'][:1] + stop['active'][1:][-7:]
        record['phase'] = 'STOP_ACTIVE_OBSERVED'
    elif triggered:
        req = child_request(stop)
        row = tool._read_order(req, stop['reference'], stop.get('child', {}).get('order', {}).get('groww_order_id'))
        if row['filled_quantity'] < stop.get('child', {}).get('order', {}).get('filled_quantity', 0):
            fail('COMMISSIONING_FILL_REGRESSION')
        stop['child'] = dict(order=row, at=tool.clock().isoformat(), request_hash=identity(req))
        record['phase'] = 'STOP_CHILD_OBSERVED'
    elif parent['status'] not in ('CANCELLED', 'EXPIRED', 'REJECTED') or stop.get('child'):
        fail('COMMISSIONING_STOP_STATE_UNKNOWN')


def attach(tool, plan_id, expected_hash):
    from .broker_commissioning import _decimal, _id, _command_result, fail, FINAL_PARENT
    tool.capture(plan_id)
    record = tool._load(plan_id)
    if expected_hash != record['plan_hash']:
        fail('EXACT_OWNER_PLAN_HASH_REQUIRED')
    if record.get('reason'):
        fail('COMMISSIONING_RECONCILIATION_REQUIRED')
    if record['plan']['format'] not in PROTECTED_FORMATS:
        fail('COMMISSIONING_PROTECTED_PLAN_REQUIRED')
    if record.get('stop'):
        return _command_result(tool.status(plan_id), 'protect', attempted=False, confirmed=False)
    child = record.get('child', {}).get('order', {})
    if child.get('order_status') != 'EXECUTED' or child.get('filled_quantity') != record['plan']['quantity'] or record['closes']:
        fail('COMMISSIONING_FULL_UNCLOSED_LONG_REQUIRED')
    if not entry_settled(record):
        fail('COMMISSIONING_INACTIVE_PARENT_REQUIRED')
    if _decimal(record['plan']['stop_trigger_price']) >= _decimal(child['average_fill_price']):
        fail('COMMISSIONING_STOP_MUST_BE_BELOW_ENTRY')
    c = record['plan']['contract']
    market_at = tool.clock().isoformat()
    quote = quote_summary(tool.market.quote(c['symbol'], segment='FNO', exchange=record['request']['exchange']), tool.clock())
    if quote['timestamp_status'] != 'RECENT_TRADE_ONLY' or quote.get('bid_price') is None:
        fail('COMMISSIONING_RECENT_OPTION_QUOTE_REQUIRED')
    if _decimal(quote['bid_price']) <= _decimal(record['plan']['stop_trigger_price']):
        fail('COMMISSIONING_STOP_MUST_START_BELOW_MARKET')
    obs = tool._observe(record['plan'], record=record)
    obs['received_at'] = min(market_at, obs['received_at'], key=stamp)
    if not fresh(obs['received_at'], tool.clock(), 10):
        fail('FRESH_COMMISSIONING_OBSERVATION_REQUIRED')
    if obs['quantity'] != child['filled_quantity']:
        fail('COMMISSIONING_POSITION_NOT_EXCLUSIVE')
    previous = copy.deepcopy(record)
    ref = 'CP' + uuid.uuid4().hex[:18]
    stop = dict(reference=ref, request=request(record['plan'], ref, obs['quantity']),
                created_at=tool.clock().isoformat(), active=[], observation=obs)
    record.update(stop=stop, phase='STOP_SUBMITTING')
    tool._save(record, previous); previous = copy.deepcopy(record)
    try:
        receipt = tool._write(record, obs, 'POST', '/v1/order-advance/create', stop['request'],
                              lambda: tool.transport.create_smart_order(timeout=5, **stop['request']))
        identifier = _id(receipt.get('smart_order_id'))
        stop.update(smart_id=identifier, create_receipt=dict(smart_order_id=identifier,
            at=tool.clock().isoformat(), request_hash=identity(stop['request']), binding=tool.binding))
    except ExecutionDenied:
        stop['aborted_before_write'] = True
    except Exception:
        record['phase'] = 'STOP_CREATE_UNCERTAIN'
    tool._save(record, previous)
    status = tool.capture(plan_id)
    current = tool._load(plan_id).get('stop', {})
    return _command_result(status, 'protect', attempted=not stop.get('aborted_before_write', False),
                           confirmed=bool(current.get('create_receipt')))


def cancel(tool, plan_id, expected_hash):
    from .broker_commissioning import _command_result, fail
    tool.capture(plan_id); record = tool._load(plan_id)
    if expected_hash != record['plan_hash']:
        fail('EXACT_OWNER_PLAN_HASH_REQUIRED')
    if record.get('reason'):
        fail('COMMISSIONING_RECONCILIATION_REQUIRED')
    stop = record.get('stop', {})
    child = stop.get('child', {}).get('order', {})
    if child and child['order_status'] not in TERMINAL:
        result = tool._cancel_linked_order(record, child, 'STOP_CHILD')
        return dict(result, command='cancel-stop')
    if settled(record) or stop.get('cancel_attempted'):
        return _command_result(tool.status(plan_id), 'cancel-stop', attempted=False, confirmed=False)
    if stop.get('parent', {}).get('status') != 'ACTIVE' or stop['parent'].get('triggered_at') is not None:
        fail('COMMISSIONING_ACTIVE_STOP_REQUIRED')
    obs = tool._observe(record['plan'], record=record)
    if obs['quantity'] != record['child']['order']['filled_quantity'] - sold(record):
        fail('COMMISSIONING_POSITION_NOT_EXCLUSIVE')
    previous = copy.deepcopy(record)
    stop['cancel_attempted'] = tool.clock().isoformat()
    tool._save(record, previous); previous = copy.deepcopy(record)
    aborted = False
    try:
        receipt = tool._write(record, obs, 'POST', '/v1/order-advance/cancel/FNO/GTT/' + stop['smart_id'], None,
            lambda: tool.transport.cancel_smart_order(segment='FNO', smart_order_type='GTT', smart_order_id=stop['smart_id'], timeout=5))
        if not isinstance(receipt, dict) or receipt.get('smart_order_id') != stop['smart_id']:
            fail('COMMISSIONING_CANCEL_RECEIPT_DIFFERS')
        stop['cancel_receipt'] = dict(smart_order_id=stop['smart_id'], at=tool.clock().isoformat())
    except ExecutionDenied:
        stop.setdefault('aborted_cancels', []).append(stop.pop('cancel_attempted'))
        aborted = True
    except Exception:
        record['phase'] = 'STOP_CANCEL_UNCERTAIN'
    tool._save(record, previous)
    status = tool.capture(plan_id)
    return _command_result(status, 'cancel-stop', attempted=not aborted, confirmed=bool(stop.get('cancel_receipt')))


def facts(record):
    stop = record.get('stop', {})
    receipt = stop.get('create_receipt', {})
    acknowledged = bool(stop and receipt.get('binding') == record['binding'] and
                        receipt.get('smart_order_id') == stop.get('smart_id') and
                        receipt.get('request_hash') == identity(stop['request']))
    active = stop.get('active', [])
    persistent = any(a['session_id'] != b['session_id'] and (stamp(b['at']) - stamp(a['at'])).total_seconds() >= 5
                     for a in active for b in active)
    return dict(stop_order_write=acknowledged, stop_persisted=persistent,
                stop_inactive=bool(stop and settled(record)))


def validate(record):
    from .broker_commissioning import _parent_matches, _order_matches, fail
    stop = record['stop']; plan = record['plan']
    created = stamp(record['created_at']); completed = stamp(record['completed_at'])
    if stop['request'] != request(plan, stop['reference'], plan['quantity']):
        fail('COMMISSIONING_STOP_REQUEST_CHANGED')
    obs = stop['observation']
    if (obs.get('complete') is not True or obs.get('quantity') != plan['quantity'] or
            not created <= stamp(obs['received_at']) <= stamp(stop['created_at']) <= completed):
        fail('COMMISSIONING_STOP_OWNERSHIP_CHANGED')
    receipt = stop['create_receipt']
    if not stamp(stop['created_at']) <= stamp(receipt['at']) <= completed:
        fail('COMMISSIONING_STOP_RECEIPT_TIME_CHANGED')
    _parent_matches(stop['parent'], stop)
    if not stamp(stop['created_at']) <= stamp(stop['parent']['observed_at']) <= completed:
        fail('COMMISSIONING_STOP_PARENT_TIME_CHANGED')
    for item in stop['active']:
        _parent_matches(item['parent'], stop)
        if (item['parent']['status'] != 'ACTIVE' or item['parent'].get('triggered_at') is not None or
                item['request_hash'] != identity(stop['request']) or
                not stamp(stop['created_at']) <= stamp(item['at']) <= completed or
                date.fromisoformat(str(item['parent']['expire_at'])[:10]) <= date.fromisoformat(plan['contract']['expiry'])):
            fail('COMMISSIONING_STOP_PERSISTENCE_CHANGED')
    if stop.get('child'):
        child = stop['child']; row = child['order']; req = child_request(stop)
        _order_matches(row, req, row['groww_order_id'], stop['reference'])
        if child['request_hash'] != identity(req) or not stamp(stop['created_at']) <= stamp(child['at']) <= completed:
            fail('COMMISSIONING_STOP_CHILD_CHANGED')
    elif stop['parent']['status'] not in ('CANCELLED', 'REJECTED', 'EXPIRED'):
        fail('COMMISSIONING_STOP_CHILD_MISSING')
    if not all(facts(record).values()):
        fail('COMMISSIONING_STOP_EVIDENCE_REQUIRED')
