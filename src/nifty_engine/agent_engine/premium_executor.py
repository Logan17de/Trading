"""Persistent one-basket controller for the owner's two premium strategies.

One step per tick, exact owned net positions and durable operation keys. Entry,
roll and replacement never share a slot with another strategy. Broker limits,
GTT triggers and the basket stop cannot guarantee execution or a maximum loss.
"""
from __future__ import annotations

import copy
import json
import threading
import uuid
from decimal import Decimal, ROUND_FLOOR

from .contracts import EXCHANGES, identity, number, stamp
from .execution import fresh, protective_stop, leg
from . import premium_strategy as policy
from .pc_control import JST

KEY='premium-executor-v1'
FILLED={'FULLY_FILLED','TERMINAL_PARTIAL','TERMINAL_EMPTY'}


def observation(snapshot):
    value=copy.deepcopy((snapshot or {}).get('execution_observation',{}))
    if not value:
        return dict(complete=False,received_at=(snapshot or {}).get('finished_at'),positions=[],books={})
    return value


def trailing_stop(short_fill, hedge_fill, quantity, costs, tick, best_pnl, limit=2000):
    """Tighten against recorded net basket high water, conservative hedge zero."""
    initial=protective_stop(short_fill,hedge_fill,quantity,costs,tick,limit)
    step=Decimal(str(tick))
    trigger=(Decimal(str(short_fill))-Decimal(str(hedge_fill))+
             (Decimal(str(limit))-Decimal(str(max(0,best_pnl)))-Decimal(str(costs)))/quantity)
    trigger=(trigger/step).to_integral_value(rounding=ROUND_FLOOR)*step
    return dict(initial,trigger_price=float(trigger),price=float(trigger+step))


def cash_stop(cash,quantity,costs,tick,best_pnl,limit=2000):
    """Use all reconciled basket cash, including prior rolls and surplus hedges."""
    step=Decimal(str(tick))
    trigger=(Decimal(str(cash))+Decimal(str(limit))-Decimal(str(max(0,best_pnl)))-Decimal(str(costs)))/quantity
    trigger=(trigger/step).to_integral_value(rounding=ROUND_FLOOR)*step
    return dict(trigger_price=float(trigger),price=float(trigger+step),quantity=quantity,
                direction='UP',loss_cap_guaranteed=False,carry_protection_required=True)


class PremiumExecutor:
    def __init__(self,journal,gateway,protection,gate,cfg,*,clock):
        self.journal,self.gateway,self.protection,self.gate=journal,gateway,protection,gate
        self.cfg,self.clock=policy.validate(cfg),clock
        self.lock=threading.RLock()

    def _state(self): return self.journal.store.meta(KEY,{})

    def _save(self,state): self.journal.store.set_meta(KEY,state)

    def public(self,obs=None):
        state=self._state(); blockers=self.gate.blockers(obs,purpose='ENTRY')
        status=self.journal.store.meta('premium-executor-status',{})
        protection='NOT_ARMED'
        if state.get('protection_key'):
            record=self.protection.record(state['protection_key']) or {}
            protection=record.get('status','UNKNOWN')
            if protection=='VERIFIED_ACTIVE' and not fresh(record.get('checked_at'),self.clock(),30):
                protection='UNKNOWN_STALE_READBACK'
            if 'RECONCILIATION_REQUIRED' in protection or protection=='UNKNOWN_STALE_READBACK':
                blockers.append('BROKER_PROTECTION_RECONCILIATION_REQUIRED')
        return dict(code_implemented=True,status='BLOCKED' if blockers else 'READY',
            phase=state.get('phase','IDLE'),strategy=state.get('strategy'),blockers=blockers,
            reason=status.get('reason'),at=status.get('at'),execution_enabled=not blockers,
            protection=protection,loss_cap_guaranteed=False,
            provider_execution_verified=not bool('REVIEWED_LIVE_ACTIVATION_REQUIRED' in blockers),
            position_review=self.position_review(obs))

    def position_review(self,obs):
        """Read-only carried-basket review, even when entries/activation are blocked."""
        result=dict(action='WAIT',reason='OWNED_FRESH_POSITION_REQUIRED',broker_writes=False)
        s=self._state()
        if s.get('phase')!='MONITORING' or s.get('strategy')!='EVERYDAY': return result
        try:
            if not obs or obs.get('complete') is not True or not fresh(obs.get('received_at'),self.clock(),10):
                return result
            self._exclusive(s,obs)
            c=s['candidate'];qty=s['short_quantity']
            sb=self._book(c['short'],obs,'BUY',qty)
            liquidation=-sb['ask']*qty
            for symbol,q in self._net(s).items():
                if q>0:
                    contract=c['hedge'] if symbol==c['hedge']['symbol'] else s['roll_candidate']['hedge']
                    liquidation+=self._book(contract,obs,'SELL',q)['bid']*q
            pnl=self._cash(s)+liquidation-s['costs']
            position=dict(ownership='ENGINE_VERIFIED',received_at=obs['received_at'],
                short_entry=s['short_fill'],short_premium=sb['ask'],net_pnl_inr=pnl)
            review=policy.everyday_review(self.cfg,position,self.clock())
            if pnl<=max(-self.cfg['loss_stop_inr'],s.get('best_pnl',0)-self.cfg['loss_stop_inr']):
                review.update(action='REVIEW_OWNED_EXIT',reason='BASKET_STOP_OR_TRAIL',exit_sequence='CLOSE_SHORT_THEN_HEDGE')
            return dict(review,net_pnl_inr=round(pnl,2),execution_enabled=False)
        except (ValueError,KeyError,TypeError):
            return result

    def _status(self,reason,**extra):
        value=dict(reason=reason,at=self.clock().isoformat(),**extra)
        self.journal.store.set_meta('premium-executor-status',value)
        return value

    def reconcile_before_collection(self):
        """Link generated children before the collector classifies raw orders."""
        with self.lock:
            state=self._state()
            if not state or state.get('phase')=='CLOSED': return
            for key in set(state.get('all_operations',[]))|set(state.get('operations',{}).values()): self.gateway.reconcile(key)
            if state.get('protection_key'): self.protection.reconcile(state['protection_key'])

    def _net(self,state):
        rows=self.journal.store.read('SELECT symbol,side,filled FROM pc_orders WHERE slot=? AND broker_hash IS NOT NULL',(state['slot'],))
        result={}
        for r in rows: result[r['symbol']]=result.get(r['symbol'],0)+r['filled']*(1 if r['side']=='BUY' else -1)
        return {s:q for s,q in result.items() if q}

    def _exclusive(self,state,obs):
        symbols={r['symbol'] for r in self.journal.store.read('SELECT DISTINCT symbol FROM pc_orders WHERE slot=?',(state['slot'],))}
        net=self._net(state)
        for symbol in symbols:
            if self.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?',(symbol,)):
                raise ValueError('MANUAL_OR_MIXED_CONTRACT_PROTECTED')
            rows=[r for r in obs.get('positions',[]) if r.get('symbol')==symbol and r.get('quantity')]
            if net.get(symbol,0):
                if (len(rows)!=1 or rows[0].get('quantity')!=net[symbol] or rows[0].get('ownership')!='ENGINE_VERIFIED'
                        or rows[0].get('product')!='NRML'):
                    raise ValueError('WAIT_FOR_EXACT_OWNED_POSITION_READBACK')
            elif rows: raise ValueError('UNEXPECTED_POSITION_PROTECTED')

    def _book(self,contract,obs,side,quantity):
        value=dict(contract,**obs.get('books',{}).get(contract['symbol'],{}))
        value=leg(value)
        if (not fresh(value['received_at'],self.clock(),10)
                or value['ask_quantity' if side=='BUY' else 'bid_quantity']<quantity):
            raise ValueError('FRESH_EXECUTABLE_DEPTH_REQUIRED')
        return value

    def _order(self,contract,side,quantity,obs):
        book=self._book(contract,obs,side,quantity)
        return dict(trading_symbol=contract['symbol'],exchange=EXCHANGES[contract['index']],product='NRML',
                    transaction_type=side,quantity=quantity,order_type='LIMIT',price=book['ask' if side=='BUY' else 'bid'])

    def _op(self,state,name,contract,side,quantity,obs,purpose):
        # Once reserved, original price/quantity remain immutable across retries.
        key=state['operations'].get(name)
        if key:
            result=self.gateway.reconcile(key)
            if result['status']=='NO_BROKER_SUBMISSION':
                record=self.journal.store.meta('prepared-order-'+identity({'operation':key}))
                if record:
                    state['cycle']+=1;state['operations'].pop(name);self._save(state)
                    return {'status':'ABORTED_BEFORE_WRITE'}
                # The controller committed its step but crashed before the
                # gateway reservation. There was no broker call to repeat.
                self.gateway.submit(key,state['slot'],state['strategy'],self._order(contract,side,quantity,obs),obs)
                return self.gateway.reconcile(key)
            if result['status'] in ('PENDING','PARTIAL'):
                record=self.journal.store.meta('prepared-order-'+identity({'operation':key}))
                if (self.clock()-stamp(record.get('submitted_at',state['last_write_at']))).total_seconds()>=15:
                    result=self.gateway.cancel(key,obs)
            return result
        order=self._order(contract,side,quantity,obs)
        key=state['slot']+'-'+name+'-'+str(state.get('cycle',0))
        state['operations'][name]=key; state['last_write_at']=self.clock().isoformat()
        state.setdefault('all_operations',[]).append(key)
        self._save(state)
        self.gateway.submit(key,state['slot'],state['strategy'],order,obs)
        return self.gateway.reconcile(key)

    def _verify_candidate(self,selected,obs):
        now=self.clock()
        if (selected.get('product')!='NRML' or selected.get('strategy') not in ('EVERYDAY','LATE_SESSION')
                or not fresh(selected['prepared_at'],now,15) or type(selected['lots']) is not int
                or not 1<=selected['lots']<=self.cfg['maximum_lots']):
            raise ValueError('FRESH_APPROVED_CANDIDATE_REQUIRED')
        short,hedge=leg(selected['short']),leg(selected['hedge']); qty=selected['quantity']
        kind=short['symbol'][-2:]
        if (short['index']!=hedge['index'] or short['expiry']!=hedge['expiry'] or kind!=hedge['symbol'][-2:]
                or short['lot_size']!=hedge['lot_size'] or qty!=selected['lots']*short['lot_size']
                or not (hedge['strike']>short['strike'] if kind=='CE' else hedge['strike']<short['strike'])):
            raise ValueError('EQUAL_QUANTITY_SAME_EXPIRY_CREDIT_HEDGE_REQUIRED')
        if short['expiry']<now.astimezone(JST).date().isoformat(): raise ValueError('EXPIRED_CONTRACT')
        day=now.astimezone(JST).date().isoformat()
        evidence=obs.get('expiry_evidence',{}).get(short['index'],{})
        if (evidence.get('status')!='CONFIRMED_CURRENT_MASTER' or evidence.get('day_jst')!=day
                or short['expiry'] not in evidence.get('expiries',[])):
            raise ValueError('CURRENT_API_AND_MASTER_EXPIRY_REQUIRED')
        if short['index']!=policy.preferred_index(now): raise ValueError('OWNER_WEEKDAY_INDEX_ROUTING_REQUIRED')
        if selected['strategy']=='EVERYDAY':
            if day in evidence['expiries'] or kind!='CE': raise ValueError('EVERYDAY_CALL_NON_EXPIRY_REQUIRED')
        else:
            proposed=policy.expiry_short(self.cfg,short['index'],selected.get('spot'),selected.get('listed_strikes',[]),
                selected.get('trend'),actual_expiry=short['expiry']==day)
            if (now.astimezone(JST).hour<18 or short['strike']!=proposed.get('short_strike')
                    or kind!=proposed.get('option_type')):
                raise ValueError('ACTUAL_EXPIRY_1800_MAPPED_THREE_STRIKES_REQUIRED')
        for c,side in ((short,'SELL'),(hedge,'BUY')):
            if self.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?',(c['symbol'],)):
                raise ValueError('MANUAL_CONTRACT_PROTECTED')
            book=self._book(c,obs,side,qty)
            if (book['bid'],book['ask'])!=(c['bid'],c['ask']):
                raise ValueError('CALCULATED_BOOK_PRICES_CHANGED')
        funds=obs.get('funds',{})
        if (not fresh(funds['received_at'],now,15) or number(selected['basket_requirement_inr'])>number(funds['option_sell_available_inr'])
                or (not selected.get('held_hedge') and hedge['ask']*qty>number(funds['option_buy_available_inr']))
                or number(selected['round_trip_charges_inr'])<0):
            raise ValueError('CURRENT_BROKER_MARGIN_AND_COSTS_REQUIRED')
        return selected

    def _start(self,selected,obs):
        self._verify_candidate(selected,obs)
        active=[r for r in obs['positions'] if r.get('quantity')]
        exact={(r['symbol'],'BUY' if r['quantity']>0 else 'SELL',abs(r['quantity']),r.get('product')) for r in active}
        signatures={(selected['short']['symbol'],'SELL',selected['quantity'],'NRML'),
                    (selected['hedge']['symbol'],'BUY',selected['quantity'],'NRML')}
        if signatures<=exact: return self._status('MATCHING_POSITION_ALREADY_EXISTS')
        if any(r['symbol'] in {selected['short']['symbol'],selected['hedge']['symbol']} for r in active):
            raise ValueError('EXISTING_CONTRACT_NEVER_ADOPTED')
        if self.journal.slot_status()['new_entry_blocked']: raise ValueError('ENGINE_SLOT_OCCUPIED')
        state=dict(slot='premium-'+uuid.uuid4().hex,strategy=selected['strategy'],phase='ENTRY_HEDGE',
            candidate=copy.deepcopy(selected),operations={},cycle=0,best_pnl=0,realized_cash=0,
            costs=selected['round_trip_charges_inr'],created_at=self.clock().isoformat(),last_write_at=self.clock().isoformat())
        self._save(state)
        return self._status('ENTRY_QUEUED')

    def tick(self,obs,prepared):
        with self.lock:
            try:
                self.gate.check(obs,purpose='PROTECT')
                state=self._state()
                if not state or state.get('phase')=='CLOSED':
                    self.gate.check(obs,purpose='ENTRY')
                    if prepared.get('status')!='PREPARED' or not prepared.get('selected'):
                        return self._status(prepared.get('reason','FRESH_PREPARATION_REQUIRED'))
                    return self._start(prepared['selected'],obs)
                if state['candidate']['expiry']<self.clock().astimezone(JST).date().isoformat():
                    return self._expired_flat(state,obs)
                self._exclusive(state,obs)
                if stamp(obs['received_at'])<=stamp(state['last_write_at']):
                    return self._status('WAIT_FOR_POST_WRITE_POSITION_SNAPSHOT')
                if (state['strategy']=='EVERYDAY' and state['candidate']['expiry']==self.clock().astimezone(JST).date().isoformat()
                        and ((state['phase']=='ENTRY_SHORT' and 'entry-short' not in state['operations'])
                             or state['phase']=='ROLL_REHEDGE')):
                    state.update(phase='EXIT_SHORT',exit_reason='EVERYDAY_ACTUAL_EXPIRY_ENTRY_SKIPPED');self._save(state)
                purpose='PROTECT'
                if state['phase']=='ENTRY_HEDGE' and 'entry-hedge' not in state['operations']: purpose='ENTRY'
                if state['phase']=='ENTRY_SHORT' and 'entry-short' not in state['operations']: purpose='ENTRY'
                if state['phase']=='ROLL_REHEDGE': purpose='ROLL'
                if purpose=='ENTRY' and not policy.entry_window(self.clock()):
                    state.update(phase='EXIT_SHORT',exit_reason='ENTRY_WINDOW_ENDED');self._save(state);purpose='EXIT'
                self.gate.check(obs,purpose=purpose)
                with self.gateway.scope(obs,purpose): return self._step(state,obs,prepared)
            except (ValueError,KeyError,TypeError,PermissionError) as exc:
                # Only application-owned fixed codes reach UI. Provider exception
                # text and responses remain excluded from notifications/reports.
                reason=str(exc) if isinstance(exc,(ValueError,PermissionError)) and str(exc).replace('_','').isalnum() else 'EXECUTION_INPUT_UNAVAILABLE'
                return self._status(reason)
            except Exception: return self._status('EXECUTION_RECONCILIATION_REQUIRED')

    def _expired_flat(self,s,obs):
        """Release an expired basket only after complete flat/terminal evidence.

        This records risk flat, never an invented settlement fill or net P&L.
        Manual conflicts or unlinked GTT children still require reconciliation.
        """
        symbols={r['symbol'] for r in self.journal.store.read('SELECT symbol FROM pc_orders WHERE slot=?',(s['slot'],))}
        if (any(r.get('symbol') in symbols and r.get('quantity') for r in obs['positions'])
                or any(self.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?',(symbol,)) for symbol in symbols)):
            return self._status('EXPIRED_BASKET_POSITION_OR_MANUAL_CONFLICT')
        for key in s.get('all_operations',[]):
            if self.gateway.reconcile(key)['status'] not in FILLED|{'NO_BROKER_SUBMISSION'}:
                return self._status('EXPIRED_ORDER_TERMINAL_REQUIRED')
        if s.get('protection_key'):
            with self.gateway.scope(obs,'PROTECT'):
                result=self.protection.cancel_expired(s['protection_key'],obs)
            if result['status']!='VERIFIED_INACTIVE': return self._status('EXPIRED_PROTECTION_'+result['status'])
        first=s.get('expired_flat_at')
        if not first:
            s['expired_flat_at']=obs['received_at'];self._save(s)
            return self._status('EXPIRED_FLAT_SECOND_SNAPSHOT_REQUIRED')
        if (stamp(obs['received_at'])-stamp(first)).total_seconds()<5:
            return self._status('EXPIRED_FLAT_SECOND_SNAPSHOT_REQUIRED')
        with self.journal.store.transaction() as db:
            db.execute("UPDATE pc_orders SET state='CLOSED' WHERE slot=?",(s['slot'],))
        s.update(phase='CLOSED',closed_at=self.clock().isoformat(),exit_reason='EXPIRED_BROKER_FLAT',settlement_pnl='UNKNOWN')
        self._save(s)
        return self._status('EXPIRED_BROKER_FLAT_CONFIRMED_NO_SETTLEMENT_PNL_ASSUMED')

    def _step(self,s,obs,prepared):
        c=s['candidate']; short,hedge=c['short'],c['hedge']; qty=c['quantity']; phase=s['phase']
        net=self._net(s)
        if phase=='ENTRY_HEDGE':
            result=self._op(s,'entry-hedge',hedge,'BUY',qty,obs,'ENTRY')
            if result['status']=='FULLY_FILLED' and net.get(hedge['symbol'])==qty:
                s.update(phase='ENTRY_SHORT',hedge_fill=result['average_fill_price']);self._save(s)
            elif result['status'] in ('TERMINAL_PARTIAL','TERMINAL_EMPTY'):
                s.update(phase='EXIT_SHORT',exit_reason='HEDGE_INCOMPLETE'); self._save(s)
            return self._status('HEDGE_'+result['status'])
        if phase=='ENTRY_SHORT':
            if net.get(hedge['symbol'])!=qty: raise ValueError('FULL_HEDGE_READBACK_REQUIRED')
            if 'entry-short' not in s['operations']:
                selected=prepared.get('selected')
                if (prepared.get('status')!='PREPARED_OWNED_ENTRY' or not selected
                        or (selected['short']['symbol'],selected['hedge']['symbol'],selected['quantity'])!=
                           (short['symbol'],hedge['symbol'],qty)):
                    return self._status('CURRENT_MARGIN_REQUIRED_AFTER_HEDGE_FILL')
                self._verify_candidate(selected,obs)
                s['candidate']=copy.deepcopy(selected);c=s['candidate'];short,hedge=c['short'],c['hedge'];self._save(s)
            result=self._op(s,'entry-short',short,'SELL',qty,obs,'ENTRY')
            if result['status'] in ('FULLY_FILLED','TERMINAL_PARTIAL'):
                sold=result['filled_quantity']
                if net.get(short['symbol'])==-sold:
                    s.update(phase='PROTECTION',short_fill=result['average_fill_price'],short_quantity=sold)
                    self._save(s)
            elif result['status']=='TERMINAL_EMPTY':
                s.update(phase='EXIT_SHORT',exit_reason='SHORT_REJECTED'); self._save(s)
            return self._status('SHORT_'+result['status'])
        if phase=='PROTECTION':
            if not s.get('protection_key'):
                stop=cash_stop(self._cash(s),s['short_quantity'],s['costs'],short['tick_size'],s['best_pnl'])
                if stop['trigger_price']<=self._book(short,obs,'BUY',s['short_quantity'])['ask']:
                    s.update(phase='EXIT_SHORT',exit_reason='INSUFFICIENT_STOP_ROOM');self._save(s)
                    return self._status('EXIT_INSUFFICIENT_STOP_ROOM')
                s['protection_key']=s['slot']+'-protection-'+str(s['cycle']);s['initial_stop']=stop; self._save(s)
            result=self.protection.create(s['protection_key'],s['slot'],s['strategy'],short,s['short_quantity'],s['initial_stop'],obs)
            if result['status']=='VERIFIED_ACTIVE': s['phase']='MONITORING';self._save(s)
            elif result['status']=='NO_PROTECTION_ABORTED_BEFORE_WRITE':
                s.pop('protection_key');s.pop('initial_stop');s['cycle']+=1;self._save(s)
            elif result['status'] in ('CHILD_FILLED','CHILD_TERMINAL'): s.update(phase='EXIT_SHORT',exit_reason='PROTECTION_TRIGGERED');self._save(s)
            return self._status('PROTECTION_'+result['status'],protection=result['status'])
        if phase=='MONITORING': return self._monitor(s,obs,prepared)
        if phase in ('EXIT_SHORT','ROLL_CLOSE_SHORT'):
            protection=self.protection.reconcile(s['protection_key']) if s.get('protection_key') else {'status':'NO_PROTECTION'}
            if protection['status']=='VERIFIED_ACTIVE':
                s['last_write_at']=self.clock().isoformat();self._save(s)
                return self._status('PROTECTION_'+self.protection.cancel(s['protection_key'],obs)['status'])
            if protection['status']=='CHILD_RECONCILIATION_REQUIRED':
                record=self.protection.record(s['protection_key'])
                child=self.gateway.cancel(record['child_key'],obs)
                return self._status('GENERATED_CHILD_'+child['status'])
            if protection['status'] not in ('NO_PROTECTION','VERIFIED_INACTIVE','CHILD_FILLED','CHILD_TERMINAL'):
                return self._status('WAIT_FOR_NO_OVERLAPPING_PROTECTIVE_BUY')
            remaining=-net.get(short['symbol'],0)
            if remaining<0: raise ValueError('UNEXPECTED_LONG_SHORT_CONTRACT')
            if remaining:
                result=self._op(s,'close-short',short,'BUY',remaining,obs,'EXIT')
                if result['status']=='TERMINAL_PARTIAL':
                    s['operations'].pop('close-short');s['cycle']+=1; self._save(s)
                elif result['status']=='TERMINAL_EMPTY': return self._status('SHORT_EXIT_REJECTED_REVIEW_REQUIRED')
                return self._status('CLOSE_SHORT_'+result['status'])
            # Record exact cash across all fills in this slot; unrealized and
            # realized totals survive short rolls/restarts rather than resetting.
            if phase=='ROLL_CLOSE_SHORT':
                s['phase']='ROLL_REHEDGE';self._save(s)
            else: s['phase']='EXIT_HEDGE';self._save(s)
            return self._status('SHORT_CONFIRMED_FLAT')
        if phase=='EXIT_HEDGE':
            # Close every owned remaining bought hedge (including replacement
            # surplus), never a manual/unknown netted position.
            longs=[(symbol,q) for symbol,q in net.items() if q>0]
            if any(q<0 for q in net.values()): raise ValueError('CLOSE_ALL_OWNED_SHORTS_FIRST')
            if longs:
                symbol,q=longs[0]; contracts={hedge['symbol']:hedge}
                if s.get('roll_candidate'): contracts[s['roll_candidate']['hedge']['symbol']]=s['roll_candidate']['hedge']
                contract=contracts[symbol]; name='exit-hedge-'+symbol
                result=self._op(s,name,contract,'SELL',q,obs,'EXIT')
                if result['status']=='TERMINAL_PARTIAL': s['operations'].pop(name);s['cycle']+=1;self._save(s)
                return self._status('EXIT_HEDGE_'+result['status'])
            # All records must be terminal, not merely a temporarily flat net.
            for key in set(s.get('all_operations',[]))|set(s['operations'].values()):
                if self.gateway.reconcile(key)['status'] not in FILLED|{'NO_BROKER_SUBMISSION'}:
                    return self._status('WAIT_FOR_ALL_ORDER_TERMINALS')
            with self.journal.store.transaction() as db:
                db.execute("UPDATE pc_orders SET state='CLOSED' WHERE slot=?",(s['slot'],))
            s['phase']='CLOSED'; s['closed_at']=self.clock().isoformat();self._save(s)
            self.journal.store.event('PREMIUM_BASKET_CLOSED',dict(strategy=s['strategy'],reason=s.get('exit_reason')),self.clock())
            return self._status('BASKET_CONFIRMED_CLOSED')
        if phase=='ROLL_REHEDGE': return self._roll(s,obs,prepared)
        raise ValueError('UNKNOWN_DURABLE_EXECUTION_PHASE')

    def _cash(self,s):
        result=0
        for row in self.journal.store.read("SELECT body FROM meta WHERE key LIKE 'prepared-order-%'"):
            r=json.loads(row['body'])
            if r.get('slot')==s['slot'] and r.get('filled_quantity'):
                result+=r['filled_quantity']*r['average_fill_price']*(1 if r['order']['transaction_type']=='SELL' else -1)
        return result

    def _monitor(self,s,obs,prepared):
        c=s['candidate']; qty=s['short_quantity']; hedge=c['hedge']; short=c['short']
        protection=self.protection.reconcile(s['protection_key'])
        if protection['status'] in ('CHILD_FILLED','CHILD_TERMINAL'):
            s.update(phase='EXIT_SHORT',exit_reason='PROTECTION_TRIGGERED');self._save(s)
            return self._status('PROTECTIVE_CHILD_EXECUTED',protection=protection['status'])
        if protection['status']!='VERIFIED_ACTIVE': return self._status('PROTECTION_'+protection['status'],protection=protection['status'])
        sb=self._book(short,obs,'BUY',qty); net=self._net(s)
        liquidation=-sb['ask']*qty
        for symbol,q in net.items():
            if q>0:
                hc=hedge if symbol==hedge['symbol'] else s['roll_candidate']['hedge']
                liquidation+=self._book(hc,obs,'SELL',q)['bid']*q
        pnl=self._cash(s)+liquidation-s['costs']
        s['best_pnl']=max(s['best_pnl'],pnl); s['net_pnl']=round(pnl,2);self._save(s)
        if pnl<=max(-self.cfg['loss_stop_inr'],s['best_pnl']-self.cfg['loss_stop_inr']):
            s.update(phase='EXIT_SHORT',exit_reason='BASKET_STOP_OR_TRAIL');self._save(s)
            return self._status('BASKET_STOP_EXIT_QUEUED')
        # Persistent protection is tightened first, never loosened on drawdown.
        stop=cash_stop(self._cash(s),qty,s['costs'],short['tick_size'],s['best_pnl'])
        if stop['trigger_price']<=sb['ask']:
            s.update(phase='EXIT_SHORT',exit_reason='CONSERVATIVE_TRAIL_REACHED');self._save(s)
            return self._status('CONSERVATIVE_TRAIL_EXIT_QUEUED')
        if stop['trigger_price']<protection['trigger_price']:
            s['last_write_at']=self.clock().isoformat(); self._save(s)
            return self._status('TRAIL_'+self.protection.tighten(s['protection_key'],stop,obs)['status'])
        now=self.clock(); day=now.astimezone(JST).date().isoformat(); local=now.astimezone(JST)
        selected=prepared.get('selected')
        if (selected and selected['strategy']=='LATE_SESSION' and s['strategy']!='LATE_SESSION'
                and prepared.get('status')=='REVIEW_OWNED_REPLACEMENT' and policy.entry_window(now)):
            signatures={(r['symbol'],'BUY' if r['quantity']>0 else 'SELL',abs(r['quantity']),r.get('product')) for r in obs['positions']}
            wanted={(selected['short']['symbol'],'SELL',selected['quantity'],'NRML'),(selected['hedge']['symbol'],'BUY',selected['quantity'],'NRML')}
            if wanted<=signatures: return self._status('MATCHING_POSITION_ALREADY_EXISTS')
            self._verify_candidate(selected,obs)
            s.update(phase='EXIT_SHORT',exit_reason='LATE_SESSION_REPLACEMENT');self._save(s)
            return self._status('OWNED_EXPIRY_REPLACEMENT_EXIT_QUEUED')
        if local.hour>=19 and s.get('eod_checked_day')!=day:
            s['eod_checked_day']=day
            if sb['ask']<=s['short_fill']-5: s['queued_roll']='INDEX_TARGET'
            self._save(s)
        roll=(sb['ask']<8 or s.get('queued_roll')) and s['strategy']=='EVERYDAY'
        if roll and policy.entry_window(now):
            if prepared.get('status')!='PREPARED_ROLL' or not selected:
                return self._status('ROLL_WAIT_FOR_CURRENT_MARGIN_BOOKS')
            self._verify_candidate(selected,obs)
            if selected['short']['symbol']==short['symbol']: return self._status('NEXT_DISTINCT_SHORT_REQUIRED')
            s.update(phase='ROLL_CLOSE_SHORT',roll_candidate=copy.deepcopy(selected),roll_reason='INDEX_TARGET' if s.get('queued_roll') else 'BELOW_8')
            self._save(s); return self._status('OWNED_SHORT_ROLL_QUEUED')
        return self._status('MONITORING_OWNED_BASKET',protection='VERIFIED_ACTIVE')

    def _roll(self,s,obs,prepared):
        """Revalidate after old short is flat; buy a new hedge before selling old."""
        selected=prepared.get('selected')
        if prepared.get('status')!='PREPARED_ROLL' or not selected: return self._status('ROLL_REFRESH_REQUIRED_AFTER_CLOSE')
        if selected['quantity']!=s['short_quantity']: raise ValueError('ROLL_CANNOT_INCREASE_POSITION_SIZE')
        self._verify_candidate(selected,obs)
        old=s['candidate']['hedge']; new=selected['hedge']; qty=s['short_quantity']; net=self._net(s)
        if new['symbol']!=old['symbol']:
            if net.get(new['symbol'],0)<qty:
                improvement=number(selected.get('hedge_improvement_after_costs_inr'))
                if improvement<=100: raise ValueError('KEEP_HEDGE_UNLESS_NET_IMPROVEMENT_GT_100')
                s['roll_candidate']=copy.deepcopy(selected);self._save(s)
            if net.get(new['symbol'],0)<qty:
                result=self._op(s,'roll-buy-hedge',new,'BUY',qty,obs,'ROLL')
                if result['status'] in ('TERMINAL_PARTIAL','TERMINAL_EMPTY'):
                    s.update(phase='EXIT_SHORT',exit_reason='ROLL_HEDGE_INCOMPLETE');self._save(s)
                return self._status('ROLL_HEDGE_'+result['status'])
            if net.get(old['symbol'],0):
                result=self._op(s,'roll-sell-old-hedge',old,'SELL',net[old['symbol']],obs,'ROLL')
                if result['status']=='TERMINAL_PARTIAL':
                    s['operations'].pop('roll-sell-old-hedge');s['cycle']+=1;self._save(s)
                return self._status('ROLL_OLD_HEDGE_'+result['status'])
            s['hedge_fill']=self.gateway.reconcile(s['operations']['roll-buy-hedge'])['average_fill_price']
        s['candidate']=copy.deepcopy(selected);s['phase']='ENTRY_SHORT';s['cycle']+=1
        s['operations'].pop('entry-short',None);s['operations'].pop('close-short',None)
        s.pop('protection_key',None);s.pop('initial_stop',None);s.pop('queued_roll',None)
        s['costs']+=number(selected.get('incremental_costs_inr',selected['round_trip_charges_inr']))
        self._save(s); return self._status('ROLL_SHORT_QUEUED_WITH_CONFIRMED_HEDGE')
