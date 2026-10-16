"""Exact owned DAY stop-loss orders. No GTT or generated-child assumption.

Create/modify/cancel intents survive restart. An uncertain write is read back,
never repeated. Existing journal ownership and the Oracle write gate apply.
"""
from .contracts import EXCHANGES, identity, number
from .execution_gate import ExecutionDenied


class OrdinaryStopProtection:
    ordinary_stop = True

    def __init__(self, journal, transport, gateway):
        self.journal, self.transport, self.gateway = journal, transport, gateway

    @staticmethod
    def key(operation): return 'ordinary-protection-' + identity({'operation': operation})

    def record(self, operation): return self.journal.store.meta(self.key(operation))

    def _save(self, operation, value): self.journal.store.set_meta(self.key(operation), value)

    def _order_record(self, record):
        return self.journal.store.meta('prepared-order-' + identity({'operation': record['child_key']}))

    def _exclusive(self, symbol, quantity, observation):
        rows = [r for r in observation.get('positions', []) if r.get('symbol') == symbol and r.get('quantity')]
        if (len(rows) != 1 or rows[0].get('ownership') != 'ENGINE_VERIFIED'
                or rows[0].get('quantity') != -quantity or rows[0].get('product') != 'NRML'
                or self.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?', (symbol,))):
            raise ValueError('EXCLUSIVE_OWNED_SHORT_REQUIRED')

    def create(self, operation, slot, strategy, short, quantity, stop, observation):
        request = dict(trading_symbol=short['symbol'], exchange=EXCHANGES[short['index']],
            product='NRML', transaction_type='BUY', quantity=quantity, order_type='SL',
            price=number(stop['price']), trigger_price=number(stop['trigger_price']))
        if not 0 < request['trigger_price'] < request['price']:
            raise ValueError('BUY_STOP_LIMIT_MUST_COVER_TRIGGER')
        old = self.record(operation)
        if old and old['initial_fingerprint'] != identity(request):
            raise ValueError('PROTECTION_OPERATION_CONFLICT')
        if not old:
            self._exclusive(short['symbol'], quantity, observation)
            self.gateway._gate(observation)
            old = dict(status='RESERVED', slot=slot, strategy=strategy,
                request=request, initial_fingerprint=identity(request), expiry=short['expiry'],
                child_key='ordinary-stop-' + operation, created_at=self.gateway.clock().isoformat())
            self._save(operation, old)
        # No gateway reservation means no network call could have occurred.
        if not self._order_record(old):
            self._exclusive(short['symbol'], quantity, observation)
            self.gateway.submit(old['child_key'], slot, strategy, request, observation)
        return self.reconcile(operation)

    def reconcile(self, operation):
        record = self.record(operation)
        if not record: return {'status': 'NO_PROTECTION'}
        prepared = self._order_record(record)
        if not prepared: return {'status': 'NO_PROTECTION'}
        if record.get('modify_pending'):
            try:
                row = self.transport.get_order_detail(segment='FNO', groww_order_id=prepared['broker_id'], timeout=5)
                desired = dict(prepared['order'], **record['modify_pending'])
                if (row.get('groww_order_id') != prepared['broker_id']
                        or row.get('order_reference_id') != prepared['reference']
                        or row.get('segment') != 'FNO' or row.get('validity') != 'DAY'
                        or any(row.get(k) != v for k, v in desired.items())):
                    return {'status': 'PROTECTION_RECONCILIATION_REQUIRED'}
                prepared.update(order=desired, fingerprint=identity(desired))
                self.journal.store.set_meta(prepared['operation_key'], prepared)
                record['request'] = desired
                record.pop('modify_pending')
            except Exception:
                return {'status': 'PROTECTION_RECONCILIATION_REQUIRED'}
        result = self.gateway.reconcile(record['child_key'])
        prepared = self._order_record(record)
        states = {'NO_BROKER_SUBMISSION': 'NO_PROTECTION_ABORTED_BEFORE_WRITE',
            'FULLY_FILLED': 'CHILD_FILLED', 'TERMINAL_PARTIAL': 'CHILD_TERMINAL',
            'TERMINAL_EMPTY': 'VERIFIED_INACTIVE', 'PARTIAL': 'CHILD_RECONCILIATION_REQUIRED'}
        status = states.get(result['status'], 'PROTECTION_RECONCILIATION_REQUIRED')
        if result['status'] == 'PENDING':
            status = ('CANCEL_RECONCILIATION_REQUIRED' if prepared.get('cancel_attempted') else
                'VERIFIED_ACTIVE' if prepared.get('broker_status') in ('TRIGGER_PENDING', 'PENDING') else
                'CHILD_RECONCILIATION_REQUIRED')
        record.update(status=status, reference=prepared['reference'],
            checked_at=self.gateway.clock().isoformat())
        self._save(operation, record)
        return dict(status=status, quantity=record['request']['quantity'],
            trigger_price=record['request']['trigger_price'], price=record['request']['price'],
            child=result, ordinary_stop=True)

    def cancel(self, operation, observation):
        result = self.reconcile(operation); record = self.record(operation)
        if result['status'] not in ('VERIFIED_ACTIVE', 'CHILD_RECONCILIATION_REQUIRED', 'CANCEL_RECONCILIATION_REQUIRED'):
            return result
        # A partial stop has already reduced the short; verify the remaining net.
        remaining = record['request']['quantity'] - self._order_record(record).get('filled_quantity', 0)
        if remaining: self._exclusive(record['request']['trading_symbol'], remaining, observation)
        self.gateway.cancel(record['child_key'], observation)
        return self.reconcile(operation)

    def tighten(self, operation, stop, observation):
        result = self.reconcile(operation); record = self.record(operation)
        if result['status'] != 'VERIFIED_ACTIVE': return result
        trigger, price = number(stop['trigger_price']), number(stop['price'])
        if (not 0 < trigger < record['request']['trigger_price'] or price <= trigger
                or price > record['request']['price']):
            return result
        self._exclusive(record['request']['trading_symbol'], record['request']['quantity'], observation)
        self.gateway._gate(observation)
        prepared = self._order_record(record)
        record['modify_pending'] = dict(trigger_price=trigger, price=price)
        self._save(operation, record)
        try:
            self.transport.modify_order(segment='FNO', groww_order_id=prepared['broker_id'],
                quantity=record['request']['quantity'], order_type='SL',
                trigger_price=trigger, price=price, timeout=5)
        except ExecutionDenied:
            record.pop('modify_pending'); self._save(operation, record); raise
        except Exception: pass  # Read the exact original order; never repeat POST.
        return self.reconcile(operation)

    def cancel_flat(self, operation, observation):
        record = self.record(operation)
        if not record: return {'status': 'NO_PROTECTION'}
        symbol = record['request']['trading_symbol']
        if (any(r.get('symbol') == symbol and r.get('quantity') for r in observation.get('positions', []))
                or self.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?', (symbol,))):
            raise ValueError('EXCLUSIVE_FLAT_POSITION_REQUIRED')
        result = self.reconcile(operation)
        if result['status'] in ('VERIFIED_ACTIVE', 'CHILD_RECONCILIATION_REQUIRED'):
            self.gateway.cancel(record['child_key'], observation)
            return self.reconcile(operation)
        return result

    def cancel_expired(self, operation, observation): return self.cancel_flat(operation, observation)


class ProtectionRouter:
    """New Normal orders use DAY SL; existing legacy protections keep exact IDs."""
    ordinary_stop = True

    def __init__(self, journal, transport, gateway):
        from .oracle_protection import PersistentProtection
        self.standard = OrdinaryStopProtection(journal, transport, gateway)
        self.legacy = PersistentProtection(journal, transport, gateway)

    def target(self, operation):
        return self.legacy if self.legacy.record(operation) else self.standard

    def record(self, operation): return self.target(operation).record(operation)
    def reconcile(self, operation): return self.target(operation).reconcile(operation)
    def cancel(self, operation, observation): return self.target(operation).cancel(operation, observation)
    def tighten(self, operation, stop, observation): return self.target(operation).tighten(operation, stop, observation)
    def cancel_flat(self, operation, observation): return self.target(operation).cancel_flat(operation, observation)
    def cancel_expired(self, operation, observation): return self.target(operation).cancel_expired(operation, observation)

    def create(self, operation, slot, strategy, short, quantity, stop, observation):
        if self.legacy.record(operation):
            return self.legacy.create(operation, slot, strategy, short, quantity, stop, observation)
        return self.standard.create(operation, slot, strategy, short, quantity, stop, observation)
