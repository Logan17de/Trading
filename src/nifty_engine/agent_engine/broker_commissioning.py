"""Explicit owner-run broker commissioning; never imported by the trading loop.

One predeclared one-lot GTT BUY and its exact SELL close exercise the provider
contract. Production remains Off/paper/paused throughout. A missing/uncertain
receipt is read back by its original reference, never submitted again. Facts
live separately from production ownership, and no existing position is adopted.
"""
from __future__ import annotations

import contextlib
import copy
import ipaddress
import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from .contracts import EXCHANGES, IST, dumps, identity, stamp
from .execution import TERMINAL, fresh
from .execution_gate import ExecutionGate, ExecutionDenied
from .market_check import SDK_VERSION, quote_summary
from .oracle_orders import GrowwOrderTransport

PREFIX = 'broker-commissioning-'
LATEST = PREFIX + 'latest-id'
PLAN_FORMAT = 'trading-broker-commissioning-plan-v1'
EVIDENCE_FORMAT = 'trading-broker-evidence-v1'
BINDING_KEYS = {'release', 'policy_hash', 'sdk_version', 'account_fingerprint', 'egress_ip', 'host'}
FINAL_PARENT = {'COMPLETED', 'EXECUTED', 'TRIGGERED', 'CANCELLED', 'EXPIRED', 'REJECTED'}


class CommissioningError(ValueError):
    """Only these code-owned messages may enter public status."""


def fail(reason):
    raise CommissioningError(reason)


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        fail('COMMISSIONING_ID_INVALID')
    return value


def account_fingerprint(profile):
    """Private binding only; public status never returns this or broker IDs."""
    if not isinstance(profile, dict) or any(not isinstance(profile.get(k), str) or not profile[k]
                                           for k in ('vendor_user_id', 'ucc')):
        fail('COMMISSIONING_ACCOUNT_IDENTITY_REQUIRED')
    return identity({k: profile[k] for k in ('vendor_user_id', 'ucc')})


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        fail('COMMISSIONING_PRICE_INVALID')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not 0 < result < 1000000:
            fail('COMMISSIONING_PRICE_INVALID')
    except ArithmeticError:
        fail('COMMISSIONING_PRICE_INVALID')
    return result


def _binding(binding):
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS or binding.get('host') != 'ORACLE':
        fail('COMMISSIONING_ORACLE_BINDING_REQUIRED')
    if (not re.fullmatch('[a-f0-9]{40}', str(binding['release']))
            or any(not re.fullmatch('[a-f0-9]{64}', str(binding[k])) for k in ('policy_hash', 'account_fingerprint'))
            or binding['sdk_version'] != SDK_VERSION):
        fail('COMMISSIONING_BUILD_BINDING_INVALID')
    try:
        if not ipaddress.ip_address(binding['egress_ip']).is_global:
            fail('COMMISSIONING_PUBLIC_EGRESS_REQUIRED')
    except (ValueError, TypeError):
        fail('COMMISSIONING_PUBLIC_EGRESS_REQUIRED')
    return copy.deepcopy(binding)


def validate_plan(plan, binding, now, *, expired=False):
    required = {'format', 'id', 'release', 'policy_hash', 'sdk_version', 'account_fingerprint',
                'egress_ip', 'contract', 'quantity', 'trigger_price', 'buy_limit_price',
                'sell_limit_price', 'valid_until'}
    if not isinstance(plan, dict) or set(plan) != required or plan['format'] != PLAN_FORMAT:
        fail('EXACT_COMMISSIONING_PLAN_REQUIRED')
    value = copy.deepcopy(plan)
    _id(value['id']); expected = _binding(binding)
    if value['id'] == 'latest-id' or value['id'].startswith('plan-'):
        fail('COMMISSIONING_RESERVED_PLAN_ID')
    if any(value[k] != expected[k] for k in BINDING_KEYS - {'host'}):
        fail('COMMISSIONING_BINDING_CHANGED')
    c = value['contract']
    if (not isinstance(c, dict) or set(c) != {'symbol', 'index', 'expiry', 'lot_size', 'tick_size'}
            or c['index'] not in ('NIFTY', 'SENSEX') or not isinstance(c['symbol'], str)
            or not re.fullmatch(r'[A-Z0-9]{8,50}(?:CE|PE)', c['symbol'])
            or not c['symbol'].startswith(c['index'])):
        fail('EXACT_COMMISSIONING_OPTION_REQUIRED')
    try:
        expiry = date.fromisoformat(c['expiry'])
        deadline = stamp(value['valid_until'])
    except (ValueError, TypeError):
        fail('COMMISSIONING_DATES_INVALID')
    if not expired and (expiry <= now.astimezone(IST).date() or not 0 < (deadline - now).total_seconds() <= 86400):
        fail('COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG')
    if type(c['lot_size']) is not int or not 1 <= c['lot_size'] <= 100000 or value['quantity'] != c['lot_size'] or type(value['quantity']) is not int:
        fail('COMMISSIONING_EXACT_ONE_LOT_REQUIRED')
    tick = _decimal(c['tick_size'])
    c['tick_size'] = float(tick)
    for name in ('trigger_price', 'buy_limit_price', 'sell_limit_price'):
        price = _decimal(value[name])
        if price % tick:
            fail('COMMISSIONING_PRICE_NOT_ON_TICK')
        value[name] = format(price, 'f')
    if _decimal(value['buy_limit_price']) < _decimal(value['trigger_price']):
        fail('COMMISSIONING_BUY_LIMIT_BELOW_TRIGGER')
    if _decimal(value['buy_limit_price']) * value['quantity'] > 1000:
        fail('COMMISSIONING_PURCHASE_DEBIT_EXCEEDS_1000')
    return value


def _request(plan, reference):
    c = plan['contract']
    return dict(reference_id=reference, smart_order_type='GTT', segment='FNO', trading_symbol=c['symbol'],
                quantity=plan['quantity'], product_type='NRML', exchange=EXCHANGES[c['index']], duration='DAY',
                trigger_price=plan['trigger_price'], trigger_direction='UP',
                order=dict(order_type='LIMIT', price=plan['buy_limit_price'], transaction_type='BUY'))


def _order_matches(row, request, identifier, reference):
    if (not isinstance(row, dict) or row.get('groww_order_id') != identifier
            or row.get('order_reference_id') != reference
            or any(row.get(k) != request[k] for k in ('trading_symbol', 'quantity', 'segment', 'exchange',
                                                     'product', 'order_type', 'transaction_type', 'validity'))
            or _decimal(row.get('price')) != _decimal(request['price'])):
        fail('COMMISSIONING_EXACT_ORDER_READBACK_REQUIRED')
    quantity = row.get('filled_quantity')
    if type(quantity) is not int or not 0 <= quantity <= request['quantity']:
        fail('COMMISSIONING_FILL_QUANTITY_INVALID')
    if row.get('order_status') not in TERMINAL | {'OPEN', 'TRIGGER_PENDING', 'PARTIAL_FILL', 'ACKED', 'PENDING'}:
        fail('COMMISSIONING_ORDER_STATUS_UNKNOWN')
    if quantity:
        average = _decimal(row.get('average_fill_price')); limit = _decimal(request['price'])
        if (request['transaction_type'] == 'BUY' and average > limit
                or request['transaction_type'] == 'SELL' and average < limit):
            fail('COMMISSIONING_FILL_VIOLATES_LIMIT')
    return {k: row.get(k) for k in (*request.keys(), 'groww_order_id', 'order_reference_id',
                                  'filled_quantity', 'average_fill_price', 'order_status')}


def _parent_matches(row, record):
    expected = record['request']
    if (not isinstance(row, dict) or row.get('smart_order_id') != record.get('smart_id')
            or row.get('smart_order_type') != 'GTT' or row.get('segment', 'FNO') != 'FNO'
            or ('reference_id' in row and row['reference_id'] != record['reference'])
            or any(row.get(k) != expected[k] for k in ('trading_symbol', 'quantity', 'exchange', 'product_type',
                                                     'duration', 'trigger_direction'))
            or _decimal(row.get('trigger_price')) != _decimal(expected['trigger_price'])
            or not isinstance(row.get('order'), dict)
            or any(row['order'].get(k) != expected['order'][k] for k in ('order_type', 'transaction_type'))
            or _decimal(row['order'].get('price')) != _decimal(expected['order']['price'])):
        fail('COMMISSIONING_PARENT_READBACK_DIFFERS')
    return {k: copy.deepcopy(row.get(k)) for k in ('smart_order_id', 'smart_order_type', 'status',
            'trading_symbol', 'quantity', 'exchange', 'product_type', 'duration', 'trigger_direction',
            'trigger_price', 'order', 'expire_at', 'triggered_at')}


class CommissioningGate(ExecutionGate):
    """Reuse the normal single-request HTTP capability for one declared command."""
    def __init__(self, owner):
        self.owner = owner
        super().__init__(SimpleNamespace(store=owner.store), owner.pause_file, mode='paper',
                         release=owner.binding['release'], host='ORACLE', clock=owner.clock,
                         policy_hash=owner.binding['policy_hash'])
        self.expected = None

    def check(self, observation, *, purpose='PROTECT', strategy=None):
        try:
            self.owner._guard()
        except CommissioningError as exc:
            raise ExecutionDenied(str(exc)) from None
        item = self.expected
        if not item or purpose != 'PROTECT' or observation is not item['observation']:
            raise ExecutionDenied('EXPLICIT_COMMISSIONING_COMMAND_REQUIRED')
        if not fresh(observation.get('received_at'), self.clock(), 10) or observation.get('complete') is not True:
            raise ExecutionDenied('FRESH_COMMISSIONING_OBSERVATION_REQUIRED')
        current = self.owner._load(item['plan_id'])
        if identity(current) != item['record_hash']:
            raise ExecutionDenied('COMMISSIONING_STATE_CHANGED_BEFORE_WRITE')

    @contextlib.contextmanager
    def authorize(self, method, path, body, observation, *, purpose):
        expected = self.expected
        if not expected or (method, path, identity(body)) != expected['request']:
            raise ExecutionDenied('EXACT_COMMISSIONING_REQUEST_REQUIRED')
        with super().authorize(method, path, body, observation, purpose=purpose):
            yield


class Commissioning:
    def __init__(self, store, market, *, binding, pause_file, mode, clock, master=None):
        self.store, self.market, self.binding = store, market, _binding(binding)
        self.pause_file, self.mode, self.clock = Path(pause_file), mode, clock
        self.session_id = uuid.uuid4().hex
        if master is None:
            from .dashboard import download_instrument_text
            master = download_instrument_text
        self.master = master
        self.gate = CommissioningGate(self)
        self.transport = GrowwOrderTransport(market, self.gate)

    def _guard(self):
        from .premium_strategy import intent
        if self.mode != 'paper' or not (self.pause_file.exists() or self.pause_file.is_symlink()):
            fail('COMMISSIONING_REQUIRES_PRODUCTION_PAPER_AND_PAUSED')
        if intent(self.store)['enabled']:
            fail('COMMISSIONING_REQUIRES_ALGO_OFF')
        basket = self.store.meta('premium-executor-v1', {})
        if (basket and basket.get('phase') not in ('IDLE', 'CLOSED')
                or self.store.read("SELECT 1 FROM pc_orders WHERE state<>'CLOSED' LIMIT 1")):
            fail('COMMISSIONING_REQUIRES_NO_ENGINE_BASKET')

    def _load(self, plan_id):
        value = self.store.meta(PREFIX + _id(plan_id))
        if not isinstance(value, dict):
            fail('COMMISSIONING_RECORD_MISSING')
        if value.get('binding') != self.binding or value.get('plan_hash') != identity(value.get('plan')):
            fail('COMMISSIONING_RECORD_BINDING_CHANGED')
        if value['plan'].get('id') != plan_id:
            fail('COMMISSIONING_RECORD_ID_CHANGED')
        validate_plan(value['plan'], self.binding, self.clock(), expired=True)
        return value

    def _save(self, record, previous=None):
        key = PREFIX + record['plan']['id']
        with self.store.transaction() as db:
            old = db.execute('SELECT body FROM meta WHERE key=?', (key,)).fetchone()
            if (old is None) != (previous is None) or (old and identity(json.loads(old[0])) != identity(previous)):
                fail('COMMISSIONING_CONCURRENT_ACTION')
            latest = db.execute('SELECT body FROM meta WHERE key=?', (LATEST,)).fetchone()
            if latest and json.loads(latest[0]) != record['plan']['id']:
                other = db.execute('SELECT body FROM meta WHERE key=?', (PREFIX + json.loads(latest[0]),)).fetchone()
                if not other or json.loads(other[0]).get('phase') not in ('COMPLETE', 'CANCELLED_FLAT', 'ABORTED_BEFORE_WRITE'):
                    fail('OTHER_COMMISSIONING_PLAN_UNSETTLED')
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, dumps(record)))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (LATEST, dumps(record['plan']['id'])))

    def _read(self, name, **args):
        self.market.limiter.wait()
        return getattr(self.market.groww, name)(timeout=5, **args)

    def _profile(self):
        value = self._read('get_user_profile')
        if account_fingerprint(value) != self.binding['account_fingerprint']:
            fail('COMMISSIONING_ACCOUNT_CHANGED')
        if 'FNO' not in value.get('active_segments', []):
            fail('COMMISSIONING_FNO_NOT_ENABLED')
        return value

    def _orders(self):
        rows = []
        for page in range(10):
            body = self._read('get_order_list', segment='FNO', page=page, page_size=100)
            items = body.get('order_list')
            if not isinstance(items, list) or len(items) > 100 or any(not isinstance(r, dict) for r in items):
                fail('COMMISSIONING_ORDER_LIST_INCOMPLETE')
            rows.extend(items)
            if len(items) < 100:
                if len({r.get('groww_order_id') for r in rows}) != len(rows):
                    fail('COMMISSIONING_ORDER_LIST_AMBIGUOUS')
                return rows
        fail('COMMISSIONING_ORDER_LIST_INCOMPLETE')

    def _smart_active(self, plan):
        """Scan full automatic GTT validity, not only the default current day."""
        end = self.clock().astimezone(IST).replace(tzinfo=None)
        found = {}
        for kind in ('GTT', 'OCO'):
            for offset in range(0, 392, 28):
                stop = end - timedelta(days=offset)
                start = stop - timedelta(days=28)
                for page in range(4):
                    body = self._read('get_smart_order_list', segment='FNO', smart_order_type=kind,
                                      status='ACTIVE', page=page, page_size=50,
                                      start_date_time=start.isoformat(), end_date_time=stop.isoformat())
                    rows = body.get('orders')
                    if not isinstance(rows, list) or len(rows) > 50 or any(not isinstance(r, dict) for r in rows):
                        fail('COMMISSIONING_SMART_LIST_INCOMPLETE')
                    for item in rows:
                        key = _id(item.get('smart_order_id'))
                        if key not in found:
                            if len(found) >= 100:
                                fail('COMMISSIONING_SMART_LIST_TOO_LARGE')
                            full = self._read('get_smart_order', segment='FNO', smart_order_type=kind, smart_order_id=key)
                            if full.get('smart_order_id') != key or not full.get('trading_symbol'):
                                fail('COMMISSIONING_SMART_IDENTITY_UNKNOWN')
                            found[key] = full
                    if len(rows) < 50:
                        break
                else:
                    fail('COMMISSIONING_SMART_LIST_INCOMPLETE')
        return [r for r in found.values() if r['trading_symbol'] == plan['contract']['symbol']
                and r.get('status') == 'ACTIVE']

    def _observe(self, plan, *, record=None, initial=False, scan_smart=True):
        self._guard(); self._profile()
        smart = self._smart_active(plan) if initial and scan_smart else []
        if initial and smart:
            fail('COMMISSIONING_EXISTING_SMART_ORDER_PROTECTED')
        started = self.clock()
        orders = self._orders()
        positions = self._read('get_positions_for_user', segment='FNO').get('positions')
        if not isinstance(positions, list) or any(not isinstance(r, dict) for r in positions):
            fail('COMMISSIONING_POSITION_LIST_INCOMPLETE')
        symbol = plan['contract']['symbol']; exchange = EXCHANGES[plan['contract']['index']]
        selected = [r for r in positions if r.get('trading_symbol') == symbol and r.get('quantity')]
        if len(selected) > 1 or any(r.get('segment') != 'FNO' or r.get('exchange') != exchange
                                   or r.get('product') != 'NRML' or type(r.get('quantity')) is not int for r in selected):
            fail('COMMISSIONING_EXACT_POSITION_REQUIRED')
        own_ids = set()
        if record:
            if record.get('child'): own_ids.add(record['child']['order']['groww_order_id'])
            own_ids.update(r['broker_id'] for r in record.get('closes', []) if r.get('broker_id'))
        baseline = set(record.get('baseline_orders', [])) if record else set()
        same = [r for r in orders if r.get('trading_symbol') == symbol]
        if any(r.get('order_status') not in TERMINAL and r.get('groww_order_id') not in own_ids for r in same):
            fail('COMMISSIONING_OPEN_ORDER_CONFLICT')
        if record and any(r.get('groww_order_id') not in own_ids | baseline for r in same):
            fail('COMMISSIONING_EXTERNAL_ORDER_CONFLICT')
        quantity = selected[0]['quantity'] if selected else 0
        if initial and quantity:
            fail('COMMISSIONING_EXISTING_POSITION_PROTECTED')
        if not fresh(started.isoformat(), self.clock(), 10):
            fail('FRESH_COMMISSIONING_OBSERVATION_REQUIRED')
        return dict(complete=True, received_at=started.isoformat(), observed_at=self.clock().isoformat(),
                    quantity=quantity, orders=same, baseline_orders=[r['groww_order_id'] for r in same])

    def _verify_contract_and_buy(self, plan):
        from .execution_data import metadata
        c = plan['contract']; row = metadata(self.master(), c['index'], c['expiry']).get(c['symbol'])
        if not row or any(row[k] != c[k] for k in c):
            fail('COMMISSIONING_CURRENT_MASTER_MISMATCH')
        observed_at = self.clock().isoformat()
        raw = self.market.quote(c['symbol'], segment='FNO', exchange=EXCHANGES[c['index']])
        quote = quote_summary(raw, self.clock())
        if quote['timestamp_status'] != 'RECENT_TRADE_ONLY' or quote.get('offer_price') is None:
            fail('COMMISSIONING_RECENT_OPTION_QUOTE_REQUIRED')
        if _decimal(quote['offer_price']) >= _decimal(plan['trigger_price']):
            fail('COMMISSIONING_TRIGGER_MUST_START_ABOVE_MARKET')
        funds = self._read('get_available_margin_details').get('fno_margin_details', {})
        if _decimal(funds.get('option_buy_balance_available')) < _decimal(plan['buy_limit_price']) * plan['quantity']:
            fail('COMMISSIONING_INSUFFICIENT_PURCHASE_BALANCE')
        return observed_at

    def _initial(self, plan):
        # The year-long active-parent scan can be slow. Perform it before the
        # final dated quote/money/ordinary-order/position reads, never redate them.
        self._guard(); self._profile()
        if self._smart_active(plan):
            fail('COMMISSIONING_EXISTING_SMART_ORDER_PROTECTED')
        market_at = self._verify_contract_and_buy(plan)
        obs = self._observe(plan, initial=True, scan_smart=False)
        obs['received_at'] = min(market_at, obs['received_at'], key=stamp)
        if not fresh(obs['received_at'], self.clock(), 10):
            fail('FRESH_COMMISSIONING_OBSERVATION_REQUIRED')
        return obs

    def preview(self, plan):
        value = validate_plan(plan, self.binding, self.clock())
        self._initial(value)
        return dict(status='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT', plan_hash=identity(value), broker_writes=False)

    def _write(self, record, obs, method, path, body, callback):
        self.gate.expected = dict(plan_id=record['plan']['id'], record_hash=identity(record),
                                  observation=obs, request=(method, path, identity(body)))
        try:
            with self.transport.scope(obs, 'PROTECT'):
                return callback()
        finally:
            self.gate.expected = None

    def submit(self, plan, expected_plan_hash):
        value = validate_plan(plan, self.binding, self.clock())
        if expected_plan_hash != identity(value):
            fail('EXACT_OWNER_PLAN_HASH_REQUIRED')
        if self.store.meta(PREFIX + value['id']) is not None:
            old = self._load(value['id'])
            if old['plan_hash'] != expected_plan_hash:
                fail('COMMISSIONING_PLAN_ID_CONFLICT')
            return _command_result(self.capture(value['id']), 'submit', attempted=False, confirmed=False)
        obs = self._initial(value)
        reference = 'CM' + uuid.uuid4().hex[:18]
        record = dict(format='trading-broker-commissioning-record-v1', plan=value, plan_hash=identity(value),
                      binding=self.binding, phase='GTT_SUBMITTING', reference=reference, request=_request(value, reference),
                      created_at=self.clock().isoformat(), baseline_orders=obs['baseline_orders'], active=[], closes=[], flat=[],
                      submit_session=self.session_id, submit_observation=obs)
        self._save(record)
        prior = copy.deepcopy(record)
        try:
            receipt = self._write(record, obs, 'POST', '/v1/order-advance/create', record['request'],
                                  lambda: self.transport.create_smart_order(timeout=5, **record['request']))
            identifier = _id(receipt.get('smart_order_id'))
            record.update(smart_id=identifier, create_receipt=dict(smart_order_id=identifier,
                          at=self.clock().isoformat(), request_hash=identity(record['request']), binding=self.binding),
                          phase='GTT_ACCEPTED')
        except ExecutionDenied:
            record.update(phase='ABORTED_BEFORE_WRITE', reason='COMMISSIONING_GATE_DENIED')
        except Exception:
            record.update(phase='GTT_CREATE_UNCERTAIN', reason='COMMISSIONING_CREATE_RECONCILIATION_REQUIRED')
        self._save(record, prior)
        status = self.capture(value['id'])
        current = self._load(value['id'])
        return _command_result(status, 'submit', attempted=current['phase'] != 'ABORTED_BEFORE_WRITE',
                               confirmed=bool(current.get('create_receipt')))

    def _recover_parent(self, record):
        if record.get('smart_id'):
            return
        created = stamp(record['created_at']).astimezone(IST).replace(tzinfo=None)
        start = created.replace(hour=0, minute=0, second=0, microsecond=0)
        found = {}
        for state in ('ACTIVE', 'COMPLETED', 'CANCELLED'):
            for page in range(4):
                body = self._read('get_smart_order_list', segment='FNO', smart_order_type='GTT', status=state,
                                  page=page, page_size=50, start_date_time=start.isoformat(),
                                  end_date_time=(start + timedelta(days=1)).isoformat())
                rows = body.get('orders')
                if not isinstance(rows, list) or len(rows) > 50:
                    fail('COMMISSIONING_SMART_LIST_INCOMPLETE')
                for row in rows:
                    if row.get('reference_id') == record['reference']:
                        found[_id(row.get('smart_order_id'))] = row
                if len(rows) < 50: break
            else: fail('COMMISSIONING_SMART_LIST_INCOMPLETE')
        if len(found) != 1:
            fail('COMMISSIONING_ORIGINAL_PARENT_REFERENCE_UNPROVEN')
        record['smart_id'] = next(iter(found))
        # A prior attempted POST plus exact unique original reference readback
        # is recovery evidence; it never permits repeating the submission.
        record['create_receipt'] = dict(smart_order_id=record['smart_id'], at=self.clock().isoformat(),
            request_hash=identity(record['request']), binding=self.binding, source='ORIGINAL_REFERENCE_READBACK')

    def _read_order(self, request, reference, identifier=None):
        by_ref = self.transport.get_order_status_by_reference(segment='FNO', order_reference_id=reference, timeout=5)
        found = _id(by_ref.get('groww_order_id'))
        if identifier is not None and identifier != found:
            fail('COMMISSIONING_ORDER_ID_CHANGED')
        row = self.transport.get_order_detail(segment='FNO', groww_order_id=found, timeout=5)
        return _order_matches(row, request, found, reference)

    def _capture_close(self, item):
        row = self._read_order(item['request'], item['reference'], item.get('broker_id'))
        if row['filled_quantity'] < item.get('observed_filled', 0):
            fail('COMMISSIONING_FILL_REGRESSION')
        item.update(broker_id=row['groww_order_id'], order=row, observed_filled=row['filled_quantity'],
                    checked_at=self.clock().isoformat())
        if not item.get('receipt') and item.get('status') != 'ABORTED_BEFORE_WRITE':
            item['receipt'] = dict(groww_order_id=row['groww_order_id'], order_reference_id=item['reference'],
                request_hash=identity(item['request']), binding=self.binding, at=self.clock().isoformat(),
                source='ORIGINAL_REFERENCE_READBACK')

    def capture(self, plan_id):
        record = self._load(plan_id)
        # Completed evidence is immutable; viewing it never rewrites its digest.
        if record['phase'] == 'COMPLETE':
            validated_evidence(self.store, plan_id, self.binding)
            return self.status(plan_id)
        self._guard(); previous = copy.deepcopy(record)
        if record['phase'] == 'ABORTED_BEFORE_WRITE':
            return self.status(plan_id)
        try:
            self._profile(); self._recover_parent(record)
            parent = self._read('get_smart_order', segment='FNO', smart_order_type='GTT', smart_order_id=record['smart_id'])
            parent = _parent_matches(parent, record)
            record['parent'] = dict(parent, observed_at=self.clock().isoformat())
            triggered = parent.get('triggered_at') is not None or parent['status'] in ('TRIGGERED', 'EXECUTED', 'COMPLETED')
            if parent['status'] == 'ACTIVE' and not triggered:
                try:
                    expiry = date.fromisoformat(str(parent.get('expire_at', ''))[:10])
                except ValueError:
                    fail('COMMISSIONING_PERSISTENT_EXPIRY_UNPROVEN')
                if expiry <= date.fromisoformat(record['plan']['contract']['expiry']):
                    fail('COMMISSIONING_PERSISTENT_EXPIRY_UNPROVEN')
                fact = dict(at=self.clock().isoformat(), session_id=self.session_id, parent=parent,
                            request_hash=identity(record['request']))
                if not record['active'] or (self.clock() - stamp(record['active'][-1]['at'])).total_seconds() >= 5:
                    record['active'].append(fact)
                    record['active'] = record['active'][:1] + record['active'][1:][-7:]
                record['phase'] = 'ACTIVE_PARENT_OBSERVED'
            elif triggered:
                req = dict(trading_symbol=record['request']['trading_symbol'], quantity=record['request']['quantity'],
                           segment='FNO', exchange=record['request']['exchange'], product='NRML',
                           order_type='LIMIT', transaction_type='BUY', validity='DAY', price=record['plan']['buy_limit_price'])
                row = self._read_order(req, record['reference'], (record.get('child') or {}).get('order', {}).get('groww_order_id'))
                if row['filled_quantity'] < (record.get('child') or {}).get('order', {}).get('filled_quantity', 0):
                    fail('COMMISSIONING_FILL_REGRESSION')
                record['child'] = dict(order=row, at=self.clock().isoformat(), request_hash=identity(req))
                record['phase'] = 'CHILD_FILLED' if row['order_status'] == 'EXECUTED' and row['filled_quantity'] == req['quantity'] else 'CHILD_PENDING_OR_PARTIAL'
            elif parent['status'] not in FINAL_PARENT:
                fail('COMMISSIONING_PARENT_STATE_UNKNOWN')
            for item in record['closes']:
                self._capture_close(item)
            obs = self._observe(record['plan'], record=record)
            record['last_observation'] = obs
            acquired = (record.get('child') or {}).get('order', {}).get('filled_quantity', 0)
            sold = sum(item['order']['filled_quantity'] for item in record['closes'])
            if obs['quantity'] != acquired - sold or sold > acquired:
                fail('COMMISSIONING_POSITION_NOT_EXCLUSIVE')
            child_terminal = not triggered or (record.get('child') or {}).get('order', {}).get('order_status') in TERMINAL
            close_terminal = all(item['order']['order_status'] in TERMINAL for item in record['closes'])
            if not obs['quantity'] and parent['status'] in FINAL_PARENT and child_terminal and close_terminal:
                if not record['flat'] or (stamp(obs['received_at']) - stamp(record['flat'][-1]['at'])).total_seconds() >= 5:
                    record['flat'].append(dict(at=obs['received_at'], session_id=self.session_id,
                                               observation_hash=identity(obs), observation=obs))
                    record['flat'] = record['flat'][-2:]
                record['phase'] = 'FLAT_AWAITING_SECOND_READ'
                if len(record['flat']) >= 2:
                    record['phase'] = 'COMPLETE' if _facts(record)['all_complete'] else 'CANCELLED_FLAT'
                    record['completed_at'] = self.clock().isoformat()
            else:
                record['flat'] = []
            record.pop('reason', None)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, CommissioningError) else 'COMMISSIONING_PROVIDER_READ_UNVERIFIED'
            record.update(phase='RECONCILIATION_REQUIRED', reason=reason)
        record['checked_at'] = self.clock().isoformat()
        self._save(record, previous)
        return self.status(plan_id)

    def close(self, plan_id, expected_plan_hash):
        self.capture(plan_id); record = self._load(plan_id)
        if expected_plan_hash != record['plan_hash']:
            fail('EXACT_OWNER_PLAN_HASH_REQUIRED')
        if record.get('reason'):
            fail('COMMISSIONING_RECONCILIATION_REQUIRED')
        child = (record.get('child') or {}).get('order', {})
        if child.get('order_status') not in TERMINAL or not child.get('filled_quantity'):
            fail('COMMISSIONING_TERMINAL_ACQUIRED_LONG_REQUIRED')
        if any(item['order']['order_status'] not in TERMINAL for item in record['closes']):
            fail('COMMISSIONING_CLOSE_ALREADY_PENDING')
        parent = record['parent']
        if parent['status'] not in FINAL_PARENT:
            fail('COMMISSIONING_INACTIVE_PARENT_REQUIRED')
        obs = self._observe(record['plan'], record=record)
        quantity = child['filled_quantity'] - sum(item['order']['filled_quantity'] for item in record['closes'])
        if quantity <= 0 or obs['quantity'] != quantity:
            fail('COMMISSIONING_EXCLUSIVE_ACQUIRED_LONG_REQUIRED')
        previous = copy.deepcopy(record); reference = 'CS' + uuid.uuid4().hex[:18]
        request = dict(trading_symbol=record['plan']['contract']['symbol'], quantity=quantity, segment='FNO',
                       exchange=record['request']['exchange'], product='NRML', order_type='LIMIT',
                       transaction_type='SELL', validity='DAY', price=float(record['plan']['sell_limit_price']),
                       order_reference_id=reference, trigger_price=None)
        item = dict(reference=reference, request=request, at=self.clock().isoformat(), status='SUBMITTING')
        record['closes'].append(item); record['phase'] = 'CLOSE_SUBMITTING'
        self._save(record, previous); previous = copy.deepcopy(record)
        try:
            receipt = self._write(record, obs, 'POST', '/v1/order/create', request,
                                  lambda: self.transport.place_order(timeout=5, **request))
            identifier = _id(receipt.get('groww_order_id'))
            if receipt.get('order_reference_id') != reference:
                fail('COMMISSIONING_CLOSE_RECEIPT_REFERENCE_DIFFERS')
            item.update(broker_id=identifier, receipt=dict(groww_order_id=identifier, order_reference_id=reference,
                        request_hash=identity(request), binding=self.binding, at=self.clock().isoformat()), status='ACKNOWLEDGED')
        except ExecutionDenied:
            item['status'] = 'ABORTED_BEFORE_WRITE'
        except Exception:
            item['status'] = 'RECONCILIATION_REQUIRED'
        self._save(record, previous)
        status = self.capture(plan_id)
        current = self._load(plan_id)['closes'][-1]
        return _command_result(status, 'close', attempted=current['status'] != 'ABORTED_BEFORE_WRITE',
                               confirmed=bool(current.get('receipt')))

    def cancel(self, plan_id, expected_plan_hash):
        self.capture(plan_id); record = self._load(plan_id)
        if expected_plan_hash != record['plan_hash']:
            fail('EXACT_OWNER_PLAN_HASH_REQUIRED')
        if record.get('reason'):
            fail('COMMISSIONING_RECONCILIATION_REQUIRED')
        if record.get('cancel_attempted'):
            return _command_result(self.status(plan_id), 'cancel', attempted=False, confirmed=False)
        parent = record['parent']
        if parent['status'] != 'ACTIVE' or parent.get('triggered_at') is not None:
            fail('COMMISSIONING_ONLY_EXACT_ACTIVE_PARENT_CANCEL')
        obs = self._observe(record['plan'], record=record)
        if obs['quantity']:
            fail('COMMISSIONING_CANCEL_REQUIRES_EMPTY_TEST_SYMBOL')
        previous = copy.deepcopy(record); record['cancel_attempted'] = self.clock().isoformat()
        self._save(record, previous); previous = copy.deepcopy(record)
        try:
            receipt = self._write(record, obs, 'POST', '/v1/order-advance/cancel/FNO/GTT/' + record['smart_id'], None,
                                 lambda: self.transport.cancel_smart_order(segment='FNO', smart_order_type='GTT',
                                                                          smart_order_id=record['smart_id'], timeout=5))
            if not isinstance(receipt, dict) or receipt.get('smart_order_id') != record['smart_id']:
                fail('COMMISSIONING_CANCEL_RECEIPT_DIFFERS')
            record['cancel_receipt'] = dict(smart_order_id=record['smart_id'], at=self.clock().isoformat())
        except ExecutionDenied:
            record['cancel_aborted_before_write'] = True
        except Exception:
            record['reason'] = 'COMMISSIONING_CANCEL_RECONCILIATION_REQUIRED'
        self._save(record, previous)
        status = self.capture(plan_id)
        current = self._load(plan_id)
        return _command_result(status, 'cancel', attempted=not current.get('cancel_aborted_before_write', False),
                               confirmed=bool(current.get('cancel_receipt')))

    def status(self, plan_id):
        record = self._load(plan_id)
        return _public(record)

    def verify_ready_to_arm(self, plan_id):
        """Fresh GET-only settlement check; historical evidence stays immutable."""
        self._guard()
        if self.store.meta(LATEST) != plan_id:
            fail('LATEST_COMPLETED_COMMISSIONING_PLAN_REQUIRED')
        proof = validated_evidence(self.store, plan_id, self.binding)
        record = self._load(plan_id)
        started = self.clock().isoformat()
        self._profile()
        parent = self._read('get_smart_order', segment='FNO', smart_order_type='GTT', smart_order_id=record['smart_id'])
        parent = _parent_matches(parent, record)
        if parent['status'] not in FINAL_PARENT:
            fail('COMMISSIONING_CURRENT_INACTIVE_PARENT_REQUIRED')
        child = record['child']['order']
        request = dict(trading_symbol=record['request']['trading_symbol'], quantity=record['plan']['quantity'],
                       segment='FNO', exchange=record['request']['exchange'], product='NRML', order_type='LIMIT',
                       transaction_type='BUY', validity='DAY', price=record['plan']['buy_limit_price'])
        latest_child = self._read_order(request, record['reference'], child['groww_order_id'])
        if (latest_child['order_status'] != 'EXECUTED' or latest_child['filled_quantity'] != record['plan']['quantity']
                or _decimal(latest_child['average_fill_price']) != _decimal(child['average_fill_price'])):
            fail('COMMISSIONING_CURRENT_CHILD_FILL_CHANGED')
        for item in record['closes']:
            row = self._read_order(item['request'], item['reference'], item['broker_id'])
            previous = item['order']
            if (row['order_status'] not in TERMINAL or row['filled_quantity'] != previous['filled_quantity']
                    or row['filled_quantity'] and _decimal(row['average_fill_price']) != _decimal(previous['average_fill_price'])):
                fail('COMMISSIONING_CURRENT_CLOSE_FILL_CHANGED')
        obs = self._observe(record['plan'], record=record)
        if obs['quantity'] or any(row.get('order_status') not in TERMINAL for row in obs['orders']):
            fail('COMMISSIONING_CURRENT_FLAT_TERMINAL_STATE_REQUIRED')
        if not fresh(started, self.clock(), 10):
            fail('FRESH_COMMISSIONING_OBSERVATION_REQUIRED')
        return proof


def _facts(record):
    active = record.get('active', [])
    persistent = any(a['session_id'] != b['session_id'] and (stamp(b['at']) - stamp(a['at'])).total_seconds() >= 5
                     for a in active for b in active)
    child = (record.get('child') or {}).get('order', {})
    qty = record['plan']['quantity']
    linked = bool(child and child.get('order_reference_id') == record['reference'])
    closes = record.get('closes', [])
    acknowledged = bool(closes) and all(i.get('receipt') and i['receipt'].get('binding') == record['binding']
        and i['receipt'].get('request_hash') == identity(i['request']) and i.get('broker_id') == i['receipt'].get('groww_order_id') for i in closes)
    filled = bool(closes) and all(i.get('order', {}).get('order_status') in TERMINAL for i in closes) and sum(
        i.get('order', {}).get('filled_quantity', 0) for i in closes) == qty
    flat = record.get('flat', [])
    creation = record.get('create_receipt') or {}
    facts = dict(oracle_order_write=bool(creation.get('binding') == record['binding']
                    and creation.get('smart_order_id') == record.get('smart_id')
                    and creation.get('request_hash') == identity(record['request'])),
        persistent_gtt=persistent, exact_child_link=linked,
        child_filled=linked and child.get('order_status') == 'EXECUTED' and child.get('filled_quantity') == qty,
        standard_order_write=acknowledged, standard_order_filled=filled,
        flat_confirmed=len(flat) == 2 and (stamp(flat[-1]['at']) - stamp(flat[0]['at'])).total_seconds() >= 5,
        inactive_parent=record.get('parent', {}).get('status') in FINAL_PARENT)
    return dict(facts, all_complete=all(facts.values()))


def _public(record):
    facts = _facts(record)
    return dict(status=record['phase'], reason=record.get('reason'), checked_at=record.get('checked_at'),
                completed_at=record.get('completed_at'), facts={k: v for k, v in facts.items() if k != 'all_complete'},
                broker_writes=False, production_activation_changed=False)


def _command_result(status, command, *, attempted, confirmed):
    return dict(status, command=command, broker_write_attempted=attempted,
                broker_writes=bool(confirmed) if confirmed or not attempted else None,
                broker_write_outcome='ACCEPTED' if confirmed else 'UNCERTAIN' if attempted else 'NOT_SUBMITTED')


def _validated_evidence(store, plan_id, binding=None):
    record = store.meta(PREFIX + _id(plan_id))
    if not isinstance(record, dict) or record.get('phase') != 'COMPLETE' or record.get('reason'):
        fail('COMMISSIONING_COMPLETED_PROVIDER_EVIDENCE_REQUIRED')
    if record.get('plan', {}).get('id') != plan_id:
        fail('COMMISSIONING_RECORD_ID_CHANGED')
    original = _binding(record['binding'])
    if binding is not None and original != _binding(binding):
        fail('COMMISSIONING_EVIDENCE_BINDING_CHANGED')
    if record.get('plan_hash') != identity(record['plan']):
        fail('COMMISSIONING_EVIDENCE_PLAN_CHANGED')
    validate_plan(record['plan'], original, stamp(record['completed_at']), expired=True)
    if record['request'] != _request(record['plan'], record['reference']):
        fail('COMMISSIONING_EVIDENCE_REQUEST_CHANGED')
    created = stamp(record['created_at']); completed = stamp(record['completed_at'])
    receipt = record.get('create_receipt') or {}
    if (receipt.get('binding') != original or receipt.get('smart_order_id') != record.get('smart_id')
            or receipt.get('request_hash') != identity(record['request'])
            or not created <= stamp(receipt.get('at')) <= completed):
        fail('COMMISSIONING_EVIDENCE_CREATE_RECEIPT_CHANGED')
    _parent_matches(record['parent'], record)
    if not created <= stamp(record['parent']['observed_at']) <= completed:
        fail('COMMISSIONING_EVIDENCE_PARENT_TIME_CHANGED')
    for item in record['active']:
        _parent_matches(item['parent'], record)
        if item['parent']['status'] != 'ACTIVE' or item['parent'].get('triggered_at') is not None:
            fail('COMMISSIONING_EVIDENCE_ACTIVE_CHANGED')
        if date.fromisoformat(str(item['parent']['expire_at'])[:10]) <= date.fromisoformat(record['plan']['contract']['expiry']):
            fail('COMMISSIONING_EVIDENCE_EXPIRY_CHANGED')
        if item.get('request_hash') != identity(record['request']) or not created <= stamp(item['at']) <= completed:
            fail('COMMISSIONING_EVIDENCE_ACTIVE_RECEIPT_CHANGED')
    child = record['child']['order']
    expected = dict(trading_symbol=record['request']['trading_symbol'], quantity=record['plan']['quantity'],
                    segment='FNO', exchange=record['request']['exchange'], product='NRML', order_type='LIMIT',
                    transaction_type='BUY', validity='DAY', price=record['plan']['buy_limit_price'])
    _order_matches(child, expected, child['groww_order_id'], record['reference'])
    if record['child'].get('request_hash') != identity(expected) or not created <= stamp(record['child']['at']) <= completed:
        fail('COMMISSIONING_EVIDENCE_CHILD_CHANGED')
    for item in record['closes']:
        _order_matches(item['order'], item['request'], item['broker_id'], item['reference'])
        expected_sell = dict(expected, transaction_type='SELL', price=float(record['plan']['sell_limit_price']),
                             quantity=item['request']['quantity'], order_reference_id=item['reference'], trigger_price=None)
        if item['request'] != expected_sell or not 0 < item['request']['quantity'] <= record['plan']['quantity']:
            fail('COMMISSIONING_EVIDENCE_CLOSE_CHANGED')
        receipt = item.get('receipt') or {}
        if (receipt.get('binding') != original or receipt.get('groww_order_id') != item['broker_id']
                or receipt.get('order_reference_id') != item['reference']
                or receipt.get('request_hash') != identity(item['request'])
                or not created <= stamp(item['at']) <= stamp(receipt.get('at')) <= completed
                or not created <= stamp(item['checked_at']) <= completed):
            fail('COMMISSIONING_EVIDENCE_CLOSE_RECEIPT_CHANGED')
    for item in record['flat']:
        obs = item['observation']
        if (identity(obs) != item['observation_hash'] or obs.get('quantity') != 0
                or obs.get('complete') is not True or obs.get('received_at') != item['at']
                or not created <= stamp(item['at']) <= completed
                or any(r.get('order_status') not in TERMINAL for r in obs.get('orders', []))):
            fail('COMMISSIONING_EVIDENCE_FLAT_CHANGED')
    facts = _facts(record)
    if not facts.pop('all_complete'):
        fail('COMMISSIONING_PROVIDER_FACTS_INCOMPLETE')
    return dict(format=EVIDENCE_FORMAT, plan_id=plan_id, binding=original, facts=facts,
                completed_at=record['completed_at'], record_sha256=identity(record))


def validated_evidence(store, plan_id, binding=None):
    try:
        return _validated_evidence(store, plan_id, binding)
    except CommissioningError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, ArithmeticError):
        fail('COMMISSIONING_EVIDENCE_MALFORMED')


def public_status(store, release, policy_hash):
    """Read-only projection without plan, account, IP or broker identifiers."""
    try:
        plan_id = store.meta(LATEST)
        if not plan_id:
            return dict(status='NOT_STARTED', provider_verified=False, broker_writes=False)
        record = store.meta(PREFIX + _id(plan_id))
        value = _public(record)
        current = record.get('binding', {}).get('release') == release and record.get('binding', {}).get('policy_hash') == policy_hash
        verified = False
        if current and record.get('phase') == 'COMPLETE':
            validated_evidence(store, plan_id); verified = True
        return dict(value, provider_verified=verified, binding_current=current)
    except Exception:
        return dict(status='UNVERIFIED', provider_verified=False, broker_writes=False)
