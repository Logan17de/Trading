"""Read-only attribution of a basket closed outside the journal-owned executor.

Self means outside this engine, as in the existing P&L split. Broker order data
does not prove which person/device submitted an external order. No external fill
is adopted into pc_orders and no manual protection flag is removed.
"""
from .contracts import EXCHANGES, identity, stamp
from .execution import TERMINAL, fresh
from .pc_control import digest

TERMINAL_OPERATIONS = {'FULLY_FILLED', 'TERMINAL_PARTIAL', 'TERMINAL_EMPTY', 'NO_BROKER_SUBMISSION'}
INACTIVE_PROTECTION = {'VERIFIED_INACTIVE', 'CHILD_FILLED', 'CHILD_TERMINAL', 'NO_PROTECTION_ABORTED_BEFORE_WRITE'}


def rows(journal, state, obs):
    """Bounded, exact external fills; IDs are hashed before private persistence."""
    owned = journal.store.read('SELECT * FROM pc_orders')
    symbols = {r['symbol'] for r in owned if r['slot'] == state['slot']}
    refs = {r['reference'] for r in owned}
    brokers = {r['broker_hash'] for r in owned if r['broker_hash']}
    orders = obs.get('orders')
    if (obs.get('orders_complete') is not True or not isinstance(orders, list)
            or len(orders) > 400):
        raise ValueError('EXTERNAL_CLOSE_ORDER_EVIDENCE_REQUIRED')
    result = {}
    for order in orders:
        if not isinstance(order, dict): raise ValueError('EXTERNAL_CLOSE_ORDER_EVIDENCE_REQUIRED')
        symbol = order.get('trading_symbol')
        if symbol not in symbols: continue
        broker = order.get('groww_order_id')
        if not isinstance(broker, str) or not 1 <= len(broker) <= 128:
            raise ValueError('EXTERNAL_CLOSE_ORDER_ID_REQUIRED')
        key = digest(broker)
        if key in brokers: continue
        # An unlinked protective child is never reclassified as a Self fill.
        if order.get('order_reference_id') in refs:
            raise ValueError('EXTERNAL_CLOSE_OWNED_REFERENCE_UNRECONCILED')
        qty, filled = order.get('quantity'), order.get('filled_quantity')
        index = state['candidate']['index']
        if (order.get('segment') != 'FNO' or order.get('product') != 'NRML'
                or order.get('exchange') != EXCHANGES[index]
                or order.get('transaction_type') not in ('BUY', 'SELL')
                or type(qty) is not int or type(filled) is not int or not 0 <= filled <= qty
                or qty <= 0 or order.get('order_status') not in TERMINAL
                or order.get('order_status') == 'EXECUTED' and filled != qty
                or key in result):
            raise ValueError('EXTERNAL_CLOSE_TERMINAL_EXACT_ORDERS_REQUIRED')
        result[key] = dict(symbol=symbol, side=order['transaction_type'], quantity=qty, filled=filled)
    return result


def baseline(executor, state, obs):
    """Save only while positions match; never bootstrap from an already flat basket."""
    try:
        executor._exclusive(state, obs)
        return rows(executor.journal, state, obs)
    except (ValueError, KeyError, TypeError):
        return None


def proof(journal, state, obs, now):
    """Check durable two-read proof again immediately before orphan-GTT cleanup."""
    record = state.get('external_close', {})
    if (record.get('status') != 'CONFIRMED_FLAT' or obs.get('complete') is not True
            or not fresh(obs.get('received_at'), now, 10)
            or not fresh(obs.get('orders_received_at'), now, 10)
            or record.get('last_at') != obs.get('received_at')
            or (stamp(record['last_at']) - stamp(record['first_at'])).total_seconds() < 5
            or record.get('evidence') != identity(rows(journal, state, obs))
            or any(r.get('quantity') and r.get('symbol') in record['symbols'] for r in obs.get('positions', []))):
        raise ValueError('EXTERNAL_CLOSE_CONFIRMED_FLAT_REQUIRED')
    return record


def reconcile(executor, state, obs):
    """Runs before write gates: bookkeeping remains available when Algo is Off."""
    e = executor
    if (not state or state.get('phase') == 'CLOSED' or not obs
            or obs.get('complete') is not True or obs.get('orders_complete') is not True
            or not fresh(obs.get('received_at'), e.clock(), 10)
            or not fresh(obs.get('orders_received_at'), e.clock(), 10)):
        return None
    net = e._net(state)
    if not net: return None
    if state.get('external_order_baseline') is None:
        initial = baseline(e, state, obs)
        if initial is not None:
            state['external_order_baseline'] = initial; e._save(state)
        return None
    try:
        current = rows(e.journal, state, obs)
        changes = {}
        for key, row in current.items():
            old = state['external_order_baseline'].get(key)
            if old and (any(old[k] != row[k] for k in ('symbol', 'side', 'quantity')) or row['filled'] < old['filled']):
                raise ValueError('EXTERNAL_CLOSE_FILL_EVIDENCE_CHANGED')
            delta = row['filled'] - (old['filled'] if old else 0)
            changes[row['symbol']] = changes.get(row['symbol'], 0) + delta * (1 if row['side'] == 'BUY' else -1)
        changes = {s: q for s, q in changes.items() if q}
        if not changes:
            if state.pop('external_close', None): e._save(state)
            return None
        protections = [p['key'] for p in state.get('protections', {}).values()]
        if state.get('protection_key'): protections.append(state['protection_key'])
        for key in protections:
            result = e.protection.reconcile(key)
            if result['status'] not in INACTIVE_PROTECTION | {'VERIFIED_ACTIVE', 'CANCEL_RECONCILIATION_REQUIRED'}:
                state['external_close'] = dict(status='UNKNOWN', actor='UNKNOWN', label='Closure source unverified')
                e._save(state)
                return e._status('SELF_CLOSE_PROTECTION_' + result['status'])
        if e._net(state) != net:
            state.pop('external_close', None); e._save(state)
            return e._status('SELF_CLOSE_POST_PROTECTION_POSITION_READ_REQUIRED')
        if changes != {s: -q for s, q in net.items()}:
            state['external_close'] = dict(status='PARTIAL_OR_MIXED', actor='SELF', label='Changed outside algo (Self)')
            e._save(state); return e._status('SELF_POSITION_CHANGE_MANAGEMENT_STOPPED')
        symbols = {r['symbol'] for r in e.journal.store.read('SELECT symbol FROM pc_orders WHERE slot=?', (state['slot'],))}
        if any(r.get('quantity') and r.get('symbol') in symbols for r in obs.get('positions', [])):
            return e._status('SELF_CLOSE_WAITING_FOR_BROKER_FLAT')
        evidence = identity(current)
        previous = state.get('external_close', {})
        first = previous.get('first_at') if previous.get('evidence') == evidence else obs['received_at']
        state['external_close'] = dict(status='WAITING_SECOND_READ', actor='SELF', label='Closed outside algo (Self)',
            first_at=first, last_at=obs['received_at'], evidence=evidence, symbols=sorted(symbols))
        e._save(state)
        if (stamp(obs['received_at']) - stamp(first)).total_seconds() < 5:
            return e._status('SELF_CLOSE_SECOND_FLAT_READ_REQUIRED')
        # No active/uncertain owned standard order may refill the flattened basket.
        for key in set(state.get('all_operations', [])) | set(state.get('operations', {}).values()):
            if e.gateway.reconcile(key)['status'] not in TERMINAL_OPERATIONS:
                return e._status('SELF_CLOSE_OWNED_ORDER_TERMINAL_REQUIRED')
        state['external_close']['status'] = 'CONFIRMED_FLAT'; e._save(state)
        for key in protections:
            result = e.protection.reconcile(key)
            if result['status'] == 'VERIFIED_ACTIVE':
                blockers = e.gate.blockers(obs, purpose='PROTECT')
                if blockers:
                    return e._status('SELF_CLOSED_OWNED_PROTECTION_CLEANUP_PENDING', blockers=blockers)
                with e.gateway.scope(obs, 'PROTECT', state['strategy']):
                    result = e.protection.cancel_flat(key, obs)
            if result['status'] not in INACTIVE_PROTECTION:
                return e._status('SELF_CLOSE_PROTECTION_' + result['status'])
        if e._net(state) != net:
            state.pop('external_close', None); e._save(state)
            return e._status('SELF_CLOSE_POST_PROTECTION_POSITION_READ_REQUIRED')
        proof(e.journal, state, obs, e.clock())
        # Commit slot release and the closure receipt together. Never fabricate
        # engine exit fills, final strategy P&L or personal/device attribution.
        state.update(phase='CLOSED', closed_at=e.clock().isoformat(), exit_reason='SELF_CLOSED', closure_actor='SELF',
                     settlement_pnl='UNKNOWN')
        state['external_close']['status'] = 'CLOSED'
        state['external_close']['closed_at'] = state['closed_at']
        from .contracts import dumps
        receipt = dict(actor='SELF', label='Closed outside algo (Self)', strategy=state['strategy'],
                       closed_at=state['closed_at'], protection_cleanup='VERIFIED', pnl_status='UNKNOWN')
        with e.journal.store.transaction() as db:
            db.execute("UPDATE pc_orders SET state='CLOSED' WHERE slot=?", (state['slot'],))
            db.execute("INSERT OR REPLACE INTO meta VALUES('premium-executor-v1',?)", (dumps(state),))
            db.execute("INSERT OR REPLACE INTO meta VALUES('premium-last-external-close',?)", (dumps(receipt),))
        return e._status('SELF_CLOSED_CONFIRMED')
    except (ValueError, KeyError, TypeError, PermissionError):
        return e._status('SELF_CLOSE_EVIDENCE_OR_PROTECTION_RECONCILIATION_REQUIRED')
