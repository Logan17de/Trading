"""Write-ahead, persistent GTT BUY protection for an exactly owned short.

Parent acceptance is not protection readback or a child fill. Missing reference
linkage never permits adopting a generated/manual order or a second close.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal

from .contracts import IST, dumps, identity, number, stamp
from .execution import TERMINAL
from .execution_gate import ExecutionDenied


def decimal_price(value):
    return format(Decimal(str(number(value))), 'f')


def broker_price(value):
    if isinstance(value,str):
        if not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,8})?',value): raise ValueError('INVALID_BROKER_DECIMAL')
        value=float(value)
    value=number(value)
    if not 0<value<1e8: raise ValueError('POSITIVE_BROKER_PRICE_REQUIRED')
    return value


class PersistentProtection:
    def __init__(self, journal, transport, gateway):
        self.journal, self.transport, self.gateway = journal, transport, gateway

    @staticmethod
    def key(operation): return 'premium-gtt-'+identity({'operation':operation})

    def record(self, operation): return self.journal.store.meta(self.key(operation))

    def _save(self, operation, record): self.journal.store.set_meta(self.key(operation), record)

    def create(self, operation, slot, strategy, short, quantity, stop, observation):
        symbol = short['symbol']
        request = dict(smart_order_type='GTT', segment='FNO', trading_symbol=symbol, quantity=quantity,
            product_type='NRML', exchange='BSE' if short['index']=='SENSEX' else 'NSE', duration='DAY',
            trigger_price=decimal_price(stop['trigger_price']), trigger_direction='UP',
            order=dict(order_type='LIMIT', price=decimal_price(stop['price']), transaction_type='BUY'))
        old = self.record(operation)
        if old:
            if old['initial_fingerprint'] != identity(request): raise ValueError('PROTECTION_OPERATION_CONFLICT')
            return self.reconcile(operation)
        self._exclusive(symbol, -quantity, observation)
        self.gateway._gate(observation)
        # Reservation is committed with the intent, before parent creation. The
        # reserved reference is for the generated BUY, not a fill of the parent.
        with self.journal.store.transaction() as db:
            if db.execute('SELECT 1 FROM meta WHERE key=?', (self.key(operation),)).fetchone():
                return {'status':'RECONCILIATION_REQUIRED'}
            if db.execute("SELECT 1 FROM pc_orders WHERE slot<>? AND state<>'CLOSED'", (slot,)).fetchone():
                raise ValueError('ONE_ACTIVE_ENGINE_BASKET_ONLY')
            owner = db.execute('SELECT strategy FROM pc_slots WHERE slot=?', (slot,)).fetchone()
            if not owner or owner[0] != strategy: raise ValueError('EXACT_SLOT_STRATEGY_REQUIRED')
            reference = 'GT'+uuid.uuid4().hex[:18]
            db.execute('INSERT INTO pc_orders(reference,slot,symbol,side,quantity) VALUES(?,?,?,?,?)',
                       (reference,slot,symbol,'BUY',quantity))
            record = dict(status='SUBMITTING', reference=reference, slot=slot, request=request,
                          initial_fingerprint=identity(request), expiry=short['expiry'], child_key='gtt-child-'+reference,
                          created_at=self.gateway.clock().isoformat())
            db.execute('INSERT INTO meta VALUES(?,?)', (self.key(operation),dumps(record)))
        try:
            self.gateway._gate(observation)
            receipt = self.transport.create_smart_order(reference_id=reference, timeout=5, **request)
            identifier = receipt.get('smart_order_id')
            if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identifier):
                raise ValueError('EXACT_SMART_ID_REQUIRED')
            record.update(smart_id=identifier,status='PARENT_ACCEPTED')
        except ExecutionDenied:
            record['status']='ABORTED_BEFORE_WRITE'
            with self.journal.store.transaction() as db:
                db.execute("UPDATE pc_orders SET state='CLOSED' WHERE reference=?",(record['reference'],))
        except Exception: record['status']='CREATE_RECONCILIATION_REQUIRED'
        self._save(operation,record)
        return self.reconcile(operation)

    def _exclusive(self, symbol, quantity, observation):
        rows = [r for r in observation.get('positions',[]) if r.get('symbol')==symbol]
        if (len(rows)!=1 or rows[0].get('ownership')!='ENGINE_VERIFIED'
                or rows[0].get('quantity') != quantity or rows[0].get('product')!='NRML'
                or self.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?',(symbol,))):
            raise ValueError('EXCLUSIVE_OWNED_POSITION_REQUIRED')

    def _full(self, record):
        full = self.transport.get_smart_order(segment='FNO', smart_order_type='GTT',
                                            smart_order_id=record['smart_id'], timeout=5)
        request = record['request']
        if (full.get('smart_order_id')!=record['smart_id'] or full.get('smart_order_type')!='GTT'
                or ('reference_id' in full and full['reference_id']!=record['reference'])
                or any(full.get(k)!=request[k] for k in ('trading_symbol','quantity','product_type','exchange','duration','trigger_direction'))
                or full.get('segment','FNO')!='FNO'
                or broker_price(full.get('trigger_price'))!=broker_price(request['trigger_price'])
                or not isinstance(full.get('order'),dict)
                or any(full['order'].get(k)!=request['order'][k] for k in ('order_type','transaction_type'))
                or broker_price(full['order'].get('price'))!=broker_price(request['order']['price'])):
            raise ValueError('SMART_PROTECTION_READBACK_DIFFERS')
        return full

    def reconcile(self, operation):
        record = self.record(operation)
        if not record: return {'status':'NO_PROTECTION'}
        if record['status']=='ABORTED_BEFORE_WRITE': return {'status':'NO_PROTECTION_ABORTED_BEFORE_WRITE'}
        if not record.get('smart_id'):
            # Never repeat an uncertain POST. Only an exact provider-returned
            # original reference in list + full detail can recover the parent.
            try:
                found=[]
                # Default lists only cover today. An uncertain carry parent
                # must be looked up in its original creation day, not guessed
                # from matching contract fields or resent after a restart.
                created=stamp(record['created_at']).astimezone(IST)
                if created>self.gateway.clock().astimezone(IST):raise ValueError('FUTURE_PARENT_CREATION')
                start=created.replace(hour=0,minute=0,second=0,microsecond=0,tzinfo=None)
                window=dict(start_date_time=start.isoformat(),end_date_time=(start+timedelta(days=1)).isoformat())
                # Collect all states before accepting uniqueness. Default
                # ACTIVE misses a parent triggered during uncertain creation.
                for state in ('ACTIVE','COMPLETED','CANCELLED'):
                    for page in range(4):
                        body=self.transport.get_smart_order_list(segment='FNO',smart_order_type='GTT',status=state,
                            page=page,page_size=50,timeout=5,**window)
                        rows=body['orders']
                        if not isinstance(rows,list) or len(rows)>50 or any(not isinstance(r,dict) for r in rows):
                            raise ValueError('SMART_LIST_INCOMPLETE')
                        found.extend(r for r in rows if r.get('reference_id')==record['reference'])
                        if len(rows)<50: break
                    else: raise ValueError('SMART_LIST_INCOMPLETE')
                if len(found)!=1: raise ValueError('ORIGINAL_SMART_REFERENCE_NOT_RETURNED')
                identifier=found[0]['smart_order_id']
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identifier): raise ValueError('SMART_ID_INVALID')
                record['smart_id']=identifier
                self._full(record)
                self._save(operation,record)
            except Exception: return {'status':'CREATE_RECONCILIATION_REQUIRED'}
        try:
            full=self._full(record); status=full.get('status')
            if status in ('TRIGGERED','EXECUTED','COMPLETED') or full.get('triggered_at') is not None:
                # A trigger can race an ACTIVE/cancel read. Never mark the
                # reserved BUY closed or submit a second close while unknown.
                record['status']='CHILD_RECONCILIATION_REQUIRED'
            elif status=='ACTIVE':
                # Even a naive date is sufficient when expiry is *after* the
                # contract's last day. No timezone is guessed for a near expiry.
                expires=date.fromisoformat(str(full.get('expire_at',''))[:10])
                if expires<=date.fromisoformat(record['expiry']): raise ValueError('PERSISTENT_VALIDITY_REQUIRED')
                record['status']='CANCEL_RECONCILIATION_REQUIRED' if record.get('cancel_attempted') else 'VERIFIED_ACTIVE'
            elif status in ('CANCELLED','EXPIRED','REJECTED'):
                record['status']='VERIFIED_INACTIVE'
                with self.journal.store.transaction() as db:
                    db.execute("UPDATE pc_orders SET state='CLOSED' WHERE reference=? AND filled=0",(record['reference'],))
            else: raise ValueError('UNKNOWN_SMART_STATUS')
            record['checked_at']=self.gateway.clock().isoformat()
            self._save(operation,record)
            if record['status']=='CHILD_RECONCILIATION_REQUIRED':
                child=self._child(record)
                if child['status']=='FULLY_FILLED': record['status']='CHILD_FILLED'
                elif child['status'] in ('TERMINAL_PARTIAL','TERMINAL_EMPTY'): record['status']='CHILD_TERMINAL'
                self._save(operation,record)
                return dict(status=record['status'],child=child,quantity=record['request']['quantity'])
            return dict(status=record['status'], quantity=record['request']['quantity'],
                        trigger_price=broker_price(record['request']['trigger_price']), price=broker_price(record['request']['order']['price']))
        except Exception:
            record['status']='PROTECTION_RECONCILIATION_REQUIRED'
            self._save(operation,record)
            return {'status':'PROTECTION_RECONCILIATION_REQUIRED'}

    def _child(self, record):
        """Prove the child's original reserved reference and every order field.

        Groww's published schema omits child linkage. This assumption must be
        confirmed at controlled activation; otherwise no child is acknowledged.
        """
        request=record['request']; key=record['child_key']
        operation_key='prepared-order-'+identity({'operation':key})
        try:
            response=self.transport.get_order_status_by_reference(segment='FNO',order_reference_id=record['reference'],timeout=5)
            identifier=response['groww_order_id']
            row=self.transport.get_order_detail(segment='FNO',groww_order_id=identifier,timeout=5)
            order=dict(trading_symbol=request['trading_symbol'],exchange=request['exchange'],product='NRML',
                transaction_type='BUY',quantity=request['quantity'],order_type='LIMIT',price=broker_price(request['order']['price']))
            if (row.get('order_reference_id')!=record['reference'] or row.get('groww_order_id')!=identifier
                    or row.get('segment')!='FNO' or row.get('validity')!='DAY'
                    or any(row.get(k)!=v for k,v in order.items())):
                raise ValueError('GENERATED_CHILD_LINK_UNPROVEN')
            old=self.journal.store.meta(operation_key)
            if old and old.get('broker_id')!=identifier: raise ValueError('GENERATED_CHILD_CHANGED')
            if not old:
                self.journal.store.set_meta(operation_key,dict(status='ACKNOWLEDGED',reference=record['reference'],
                    fingerprint=identity(order),order=order,slot=record['slot'],broker_id=identifier,operation_key=operation_key))
            return self.gateway.reconcile(key)
        except Exception: return {'status':'RECONCILIATION_REQUIRED'}

    def cancel(self, operation, observation):
        result=self.reconcile(operation); record=self.record(operation)
        if result['status']!='VERIFIED_ACTIVE': return result
        self._exclusive(record['request']['trading_symbol'],-record['request']['quantity'],observation)
        self.gateway._gate(observation)
        record['cancel_attempted']=True; self._save(operation,record)
        try:
            self.transport.cancel_smart_order(segment='FNO',smart_order_type='GTT',smart_order_id=record['smart_id'],timeout=5)
        except ExecutionDenied:
            record['cancel_attempted']=False;self._save(operation,record)
            raise
        except Exception: pass
        return self.reconcile(operation)

    def tighten(self, operation, stop, observation):
        result=self.reconcile(operation); record=self.record(operation)
        if result['status']!='VERIFIED_ACTIVE': return result
        if number(stop['trigger_price'])>=broker_price(record['request']['trigger_price']): return result
        self._exclusive(record['request']['trading_symbol'],-record['request']['quantity'],observation)
        self.gateway._gate(observation)
        # Commit the desired tighter values before PUT. An unchanged broker
        # readback after timeout remains uncertain, never authorizes another PUT.
        import copy
        previous=copy.deepcopy(record)
        record['request']['trigger_price']=decimal_price(stop['trigger_price'])
        record['request']['order']['price']=decimal_price(stop['price'])
        record['status']='MODIFY_RECONCILIATION_REQUIRED'; self._save(operation,record)
        try:
            self.transport.modify_smart_order(smart_order_id=record['smart_id'],smart_order_type='GTT',segment='FNO',
                quantity=record['request']['quantity'],trigger_price=record['request']['trigger_price'],
                trigger_direction='UP',order=record['request']['order'],timeout=5)
        except ExecutionDenied:
            self._save(operation,previous)
            raise
        except Exception: pass
        return self.reconcile(operation)

    def cancel_expired(self,operation,observation):
        result=self.reconcile(operation); record=self.record(operation)
        if result['status']!='VERIFIED_ACTIVE': return result
        symbol=record['request']['trading_symbol']
        from .pc_control import JST
        if (record['expiry']>=self.gateway.clock().astimezone(JST).date().isoformat()
                or any(r.get('symbol')==symbol and r.get('quantity') for r in observation.get('positions',[]))
                or self.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?',(symbol,))):
            raise ValueError('EXPIRED_EXCLUSIVE_FLAT_REQUIRED')
        rows=self.journal.store.read('SELECT side,filled FROM pc_orders WHERE slot=? AND symbol=? AND broker_hash IS NOT NULL',
                                     (record['slot'],symbol))
        if sum(r['filled']*(1 if r['side']=='BUY' else -1) for r in rows)!=-record['request']['quantity']:
            raise ValueError('ORIGINAL_OWNED_SHORT_PROOF_REQUIRED')
        self.gateway._gate(observation)
        record['cancel_attempted']=True;self._save(operation,record)
        try:
            self.transport.cancel_smart_order(segment='FNO',smart_order_type='GTT',smart_order_id=record['smart_id'],timeout=5)
        except ExecutionDenied:
            record['cancel_attempted']=False;self._save(operation,record)
            raise
        except Exception: pass
        return self.reconcile(operation)

    def cancel_flat(self, operation, observation):
        """Cancel only our orphan parent after independently verified Self closure.

        A manual symbol flag stays protected. No ordinary/manual order is
        cancelled, modified or adopted. Off/pause/paper still prohibit this POST.
        """
        result = self.reconcile(operation); record = self.record(operation)
        if result['status'] != 'VERIFIED_ACTIVE': return result
        from .external_close import proof
        state = self.journal.store.meta('premium-executor-v1', {})
        checked = proof(self.journal, state, observation, self.gateway.clock())
        symbol = record['request']['trading_symbol']
        keys = {p['key'] for p in state.get('protections', {}).values()}
        if state.get('protection_key'): keys.add(state['protection_key'])
        if (record['slot'] != state['slot'] or operation not in keys or symbol not in checked['symbols']):
            raise ValueError('EXACT_OWNED_ORPHAN_PARENT_REQUIRED')
        rows = self.journal.store.read('SELECT side,filled FROM pc_orders WHERE slot=? AND symbol=? AND broker_hash IS NOT NULL',
                                       (record['slot'], symbol))
        if sum(r['filled'] * (1 if r['side'] == 'BUY' else -1) for r in rows) != -record['request']['quantity']:
            raise ValueError('ORIGINAL_OWNED_SHORT_PROOF_REQUIRED')
        self.gateway._gate(observation)
        record['cancel_attempted'] = True; self._save(operation, record)
        try:
            self.transport.cancel_smart_order(segment='FNO', smart_order_type='GTT', smart_order_id=record['smart_id'], timeout=5)
        except ExecutionDenied:
            record['cancel_attempted'] = False; self._save(operation, record)
            raise
        except Exception: pass
        return self.reconcile(operation)
