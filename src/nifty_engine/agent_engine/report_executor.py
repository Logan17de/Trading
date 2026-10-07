"""Report basket lifecycle inside the existing PremiumExecutor.

Uses its lock, journal, exact-reference gateway and persistent protection. There
is no broker/client/runtime here. Calendar remains a research model, not an order
route. Synthetic replay evidence is never a live activation receipt.
"""
from __future__ import annotations

import copy
import uuid
from datetime import date

from .contracts import identity, number, stamp
from .execution import fresh
from .pc_control import JST
from .premium_executor import cash_stop, FILLED
from . import premium_strategy, report_strategies as research, strategy_controls as controls

FIELDS = ('symbol', 'index', 'expiry', 'strike', 'lot_size', 'tick_size',
          'bid', 'ask', 'bid_quantity', 'ask_quantity', 'received_at')


def contract(row):
    return {key: row[key] for key in FIELDS}


def signature(candidate):
    return (candidate['strategy'], candidate['index'], candidate['expiry'], candidate['quantity'],
            tuple((r['symbol'], r['side']) for r in candidate['legs']))


class ReportExecution:
    def __init__(self, executor, cfg):
        self.e, self.cfg = executor, cfg

    def enabled(self, sid):
        if self.e.gate.normal_catalog:return False
        return controls.read(self.e.journal.store)['enabled'].get(sid, False)

    def allowed(self, sid):
        return sid in controls.EXECUTABLE

    def spread_type(self, c):
        return c['strategy']

    def input_bundle(self, c):
        return self.e.journal.store.meta('report-strategy-evidence-' + c['index'], {})

    def matched_iv_required(self):
        return self.e.gate.matched_iv_required

    def evidence(self, obs):
        """Add independently dated prospective books without redating either source."""
        result = copy.deepcopy(obs)
        result.setdefault('books', {})
        for index in self.cfg['indices']:
            bundle = self.e.journal.store.meta('report-strategy-evidence-' + index, {})
            for row in bundle.get('contracts', []):
                if row['symbol'] not in result['books']:
                    result['books'][row['symbol']] = {k: row[k] for k in
                        ('bid', 'ask', 'bid_quantity', 'ask_quantity', 'received_at')}
        return result

    def preparation(self, obs, state=None):
        enabled = controls.read(self.e.journal.store)['enabled']
        candidates, reasons = [], []
        for index in self.cfg['indices']:
            bundle = copy.deepcopy(self.e.journal.store.meta('report-strategy-evidence-' + index, {}))
            if self.e.gate.matched_iv_required:
                from .iv_history import features
                values=features(self.e.journal.store,index,self.e.clock())
                if not values:
                    reasons.append(index+'_REVIEWED_MATCHED_IV_HISTORY_AND_CURRENT_REQUIRED')
                    continue
                bundle.setdefault('features',{}).update(values)
            value = research.evaluate(self.cfg, index, bundle, self.e.clock(), occupied=False)
            for row in value['strategies']:
                if not enabled[row['id']]: continue
                if row['id'] == 'calendar':
                    reasons.append('CALENDAR_SETTLEMENT_PAYOFF_AND_MARGIN_MODEL_REQUIRED'); continue
                if row['status'] == 'RESEARCH_CANDIDATE':
                    c = copy.deepcopy(row['candidate'])
                    if self.e.gate.matched_iv_required:c['matched_iv_sha256']=identity(values)
                    c['prepared_at'] = value['at']
                    c['hedge_requirement_inr'] = bundle['margins'][c['key']].get('hedge_requirement_inr')
                    if state and signature(c) != signature(state['candidate']): continue
                    candidates.append(c)
                else: reasons.extend(row['reasons'])
        if not any(enabled[sid] for sid in controls.EXECUTABLE): reasons.append('ALL_EXECUTABLE_STRATEGIES_OFF')
        if state is None and self.e.journal.slot_status()['new_entry_blocked']:
            candidates=[]; reasons.append('SINGLE_BASKET_OCCUPIED')
        candidate = max(candidates, key=lambda c: c['net_max_expiry_profit_inr'] / c['worst_case_loss_inr']) if candidates else None
        return dict(status='PREPARED' if candidate else 'WAIT', selected=candidate,
                    reasons=list(dict.fromkeys(reasons)), broker_writes=False,
                    reason='REPORT_INPUTS_REQUIRED' if not candidate else 'REPORT_BASKET_PREPARED')

    def validate(self, c, obs, state=None):
        e, now = self.e, self.e.clock()
        if not self.allowed(c['strategy']): raise ValueError('MONITOR_ONLY_OR_RETIRED_STRATEGY')
        if not self.enabled(c['strategy']): raise ValueError('STRATEGY_SWITCH_OFF')
        if not fresh(c['prepared_at'], now, 10): raise ValueError('FRESH_REPORT_PREPARATION_REQUIRED')
        if type(c['lots']) is not int or not 1 <= c['lots'] <= self.cfg['maximum_lots']:
            raise ValueError('REPORT_LOT_LIMIT')
        spread = self.spread_type(c)
        legs = c['legs']; expected = 4 if spread == 'iron_condor' else 2
        if (len(legs) != expected or len({r['symbol'] for r in legs}) != expected
                or len({r['lot_size'] for r in legs}) != 1
                or any(r['expiry'] != c['expiry'] or r['index'] != c['index'] for r in legs)
                or c['quantity'] != legs[0]['lot_size'] * c['lots']):
            raise ValueError('EXACT_SAME_EXPIRY_BASKET_REQUIRED')
        for i in range(0, expected, 2):
            hedge, short = legs[i:i+2]; kind = short['symbol'][-2:]
            if (hedge['side'] != 'BUY' or short['side'] != 'SELL' or hedge['symbol'][-2:] != kind
                    or not (hedge['strike'] > short['strike'] if kind == 'CE' else hedge['strike'] < short['strike'])):
                raise ValueError('BOUGHT_EQUAL_QUANTITY_WING_REQUIRED')
        kinds = [r['symbol'][-2:] for r in legs if r['side'] == 'SELL']
        if kinds != (['PE', 'CE'] if expected == 4 else ['PE'] if spread == 'bull_put' else ['CE']):
            raise ValueError('STRATEGY_LEG_TYPES_REQUIRED')
        if expected == 4 and legs[1]['strike'] >= legs[3]['strike']: raise ValueError('CONDOR_STRIKE_ORDER_REQUIRED')
        dte = (date.fromisoformat(c['expiry']) - now.astimezone(JST).date()).days
        if not self.cfg['short_dte'][0] <= dte <= self.cfg['short_dte'][1]: raise ValueError('REPORT_DTE_REQUIRED')
        bundle = self.input_bundle(c)
        if self.matched_iv_required():
            from .iv_history import features
            values=features(e.journal.store,c['index'],now)
            if not values:
                raise ValueError('REVIEWED_MATCHED_IV_HISTORY_AND_CURRENT_REQUIRED')
            if c.get('matched_iv_sha256')!=identity(values):
                raise ValueError('REPREPARE_CHANGED_MATCHED_IV')
        exp = bundle.get('expiry_evidence', {})
        if (exp.get('status') != 'CONFIRMED_CURRENT_MASTER' or exp.get('day_jst') != now.astimezone(JST).date().isoformat()
                or c['expiry'] not in exp.get('expiries', [])):
            raise ValueError('CURRENT_API_AND_MASTER_EXPIRY_REQUIRED')
        funds = obs['funds']
        if not fresh(funds['received_at'], now, 10): raise ValueError('FRESH_MARGIN_REQUIRED')
        margin = number(c['basket_requirement_inr']); costs = number(c['round_trip_charges_inr'])
        if margin < 0 or costs < 0 or margin > number(funds['option_sell_available_inr']):
            raise ValueError('INSUFFICIENT_BASKET_MARGIN')
        net = e._net(state) if state else {}
        cash = e._cash(state) if state else 0
        hedge_cash = 0
        for r in legs:
            leg = contract(r); book = e._book(leg, obs, r['side'], c['quantity'])
            if (book['bid'], book['ask']) != (r['bid'], r['ask']): raise ValueError('REPREPARE_CHANGED_BOOK')
            if e.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?', (r['symbol'],)):
                raise ValueError('MANUAL_OR_MIXED_CONTRACT_PROTECTED')
            already = abs(net.get(r['symbol'], 0))
            cash += (c['quantity'] - already) * (book['bid'] if r['side'] == 'SELL' else -book['ask'])
            if r['side'] == 'BUY': hedge_cash += (c['quantity'] - already) * book['ask']
        if not state:
            hedge_margin = number(c['hedge_requirement_inr'])
            if hedge_margin < 0 or max(hedge_margin, hedge_cash) > number(funds['option_buy_available_inr']):
                raise ValueError('INSUFFICIENT_HEDGE_MARGIN')
            if hedge_cash + costs > self.cfg['risk_limit_inr']:
                raise ValueError('HEDGE_ONLY_DEBIT_EXCEEDS_RISK_LIMIT')
        costs = max(costs, state.get('costs', 0) if state else 0)
        width = max(abs(legs[i]['strike'] - legs[i+1]['strike']) for i in range(0, expected, 2))
        risk = width * c['quantity'] - cash + costs
        if not cash > costs or not 0 < risk <= self.cfg['risk_limit_inr']:
            raise ValueError('ACTUAL_FILL_AND_QUOTE_RISK_LIMIT')
        return costs, cash

    def start(self, c, obs):
        e = self.e; self.validate(c, obs)
        if any(r.get('quantity') and r['symbol'] in {l['symbol'] for l in c['legs']} for r in obs['positions']):
            raise ValueError('EXISTING_POSITION_NEVER_ADOPTED')
        if e.journal.slot_status()['new_entry_blocked']: raise ValueError('ONE_ACTIVE_ENGINE_BASKET_ONLY')
        e._save(dict(catalog='report-v1', slot='report-' + uuid.uuid4().hex, strategy=c['strategy'],
            phase='REPORT_ENTRY_HEDGES', candidate=copy.deepcopy(c), operations={}, all_operations=[],
            cycle=0, costs=c['round_trip_charges_inr'], best_pnl=0, protections={},
            started_at=e.clock().isoformat(), last_write_at=e.clock().isoformat()))
        return e._status('REPORT_BASKET_RESERVED')

    def tick(self, obs):
        e = self.e; obs = self.evidence(obs); state = e._state()
        try:
            e.gate.check(obs, purpose='PROTECT')
            if not state or state.get('phase') == 'CLOSED':
                prepared = self.preparation(obs)
                e.journal.store.set_meta('report-execution-preparation', prepared)
                c = prepared['selected']
                if not c: return e._status(prepared['reason'])
                e.gate.check(obs, purpose='ENTRY', strategy=c['strategy'])
                return self.start(c, obs)
            if state['candidate']['expiry'] < e.clock().astimezone(JST).date().isoformat():
                with e.gateway.scope(obs,'PROTECT',state['strategy']): return self.expired_flat(state,obs)
            e._exclusive(state, obs)
            if stamp(obs['received_at']) <= stamp(state['last_write_at']):
                return e._status('WAIT_FOR_POST_WRITE_POSITION_SNAPSHOT')
            if state['phase'] in ('REPORT_ENTRY_HEDGES','REPORT_ENTRY_SHORTS','REPORT_PROTECTION'):
                for symbol,p in state['protections'].items():
                    result=e.protection.reconcile(p['key'])
                    if result['status'] in ('CHILD_FILLED','CHILD_TERMINAL','VERIFIED_INACTIVE','NO_PROTECTION_ABORTED_BEFORE_WRITE'):
                        state.update(phase='REPORT_EXIT_SHORTS',exit_reason='ENTRY_PROTECTION_TERMINATED');e._save(state);break
                    if result['status']!='VERIFIED_ACTIVE' and not (result['status']=='NO_PROTECTION' and
                            state['phase']=='REPORT_PROTECTION' and symbol==state.get('protect_symbol')):
                        return e._status('REPORT_PROTECTION_'+result['status'])
            # New-entry switches do not orphan a held basket. An incomplete
            # entry is unwound; completed baskets retain protection and exits.
            entering = state['phase'] in ('REPORT_ENTRY_HEDGES', 'REPORT_ENTRY_SHORTS')
            enabled = self.enabled(state['strategy'])
            if state['phase'] in ('REPORT_ENTRY_HEDGES','REPORT_ENTRY_SHORTS','REPORT_PROTECTION') and e._net(state):
                review=self.close_reason(state,obs)
                if review.get('reason')=='LOSS_TRIGGER':
                    state.update(phase='REPORT_EXIT_SHORTS',exit_reason='LOSS_DURING_ENTRY');e._save(state)
            if entering and (not enabled or not premium_strategy.entry_window(e.clock())
                             or (e.clock() - stamp(state['started_at'])).total_seconds() > 180):
                state.update(phase='REPORT_EXIT_SHORTS', exit_reason='ENTRY_DISABLED_OR_TIMEOUT'); e._save(state)
            purpose = ('ENTRY' if state['phase'] in ('REPORT_ENTRY_HEDGES', 'REPORT_ENTRY_SHORTS') else
                       'EXIT' if state['phase'] in ('REPORT_EXIT_SHORTS', 'REPORT_EXIT_HEDGES') else 'PROTECT')
            e.gate.check(obs, purpose=purpose, strategy=state['strategy'])
            with e.gateway.scope(obs, purpose, state['strategy']): return self.step(state, obs)
        except (ValueError, KeyError, TypeError, PermissionError) as exc:
            reason = str(exc) if isinstance(exc, (ValueError, PermissionError)) and str(exc).replace('_', '').isalnum() else 'REPORT_EXECUTION_INPUT_UNAVAILABLE'
            return e._status(reason)
        except Exception: return e._status('EXECUTION_RECONCILIATION_REQUIRED')

    def expired_flat(self,s,obs):
        e=self.e;symbols={r['symbol'] for r in s['candidate']['legs']}
        if (any(r.get('quantity') and r['symbol'] in symbols for r in obs['positions'])
                or any(e.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?',(symbol,)) for symbol in symbols)):
            return e._status('EXPIRED_BASKET_POSITION_OR_MANUAL_CONFLICT')
        for key in s['all_operations']:
            if e.gateway.reconcile(key)['status'] not in FILLED | {'NO_BROKER_SUBMISSION'}:
                return e._status('EXPIRED_ORDER_TERMINAL_REQUIRED')
        for p in s['protections'].values():
            result=e.protection.cancel_expired(p['key'],obs)
            if result['status'] not in ('VERIFIED_INACTIVE','CHILD_FILLED','CHILD_TERMINAL','NO_PROTECTION_ABORTED_BEFORE_WRITE'):
                return e._status('EXPIRED_PROTECTION_'+result['status'])
        if not s.get('expired_flat_at'):
            s['expired_flat_at']=obs['received_at'];e._save(s)
            return e._status('EXPIRED_FLAT_SECOND_SNAPSHOT_REQUIRED')
        if (stamp(obs['received_at'])-stamp(s['expired_flat_at'])).total_seconds()<5:
            return e._status('EXPIRED_FLAT_SECOND_SNAPSHOT_REQUIRED')
        with e.journal.store.transaction() as db:
            db.execute("UPDATE pc_orders SET state='CLOSED' WHERE slot=?",(s['slot'],))
        s.update(phase='CLOSED',closed_at=e.clock().isoformat(),exit_reason='EXPIRED_BROKER_FLAT',settlement_pnl='UNKNOWN')
        e._save(s);return e._status('EXPIRED_BROKER_FLAT_CONFIRMED_NO_SETTLEMENT_PNL_ASSUMED')

    def close_reason(self, state, obs):
        e = self.e; c = state['candidate']; net = e._net(state)
        liquidation = sum(q * e._book(contract(next(r for r in c['legs'] if r['symbol'] == symbol)), obs,
                           'SELL' if q > 0 else 'BUY', abs(q))['bid' if q > 0 else 'ask'] for symbol, q in net.items())
        pnl = e._cash(state) + liquidation - state['costs']
        review = research.review(self.cfg, dict(strategy=state['strategy'], ownership='ENGINE_VERIFIED',
            received_at=obs['received_at'], expiry=c['expiry'], net_pnl_inr=pnl,
            entry_credit_inr=state.get('entry_credit_inr', c['entry_credit_inr']),
            net_max_expiry_profit_inr=state.get('profit_basis_inr', c['net_max_expiry_profit_inr'])), e.clock())
        return dict(review, net_pnl_inr=round(pnl, 2))

    def step(self, s, obs):
        e = self.e; c = s['candidate']; net = e._net(s); qty = c['quantity']; phase = s['phase']
        longs = [r for r in c['legs'] if r['side'] == 'BUY']; shorts = [r for r in c['legs'] if r['side'] == 'SELL']
        if phase in ('REPORT_ENTRY_HEDGES', 'REPORT_ENTRY_SHORTS'):
            rows = longs if phase == 'REPORT_ENTRY_HEDGES' else shorts
            for row in rows:
                signed = qty if row['side'] == 'BUY' else -qty
                if net.get(row['symbol']) == signed: continue
                prepared = self.preparation(obs, s); selected = prepared['selected']
                e.journal.store.set_meta('report-execution-preparation', prepared)
                if not selected: return e._status('REPORT_REPREPARATION_REQUIRED')
                s['costs'], projected_cash = self.validate(selected, obs, s)
                selected['entry_credit_inr'] = projected_cash
                selected['net_max_expiry_profit_inr'] = projected_cash - s['costs']
                s['candidate'] = copy.deepcopy(selected); e._save(s)
                if row['side'] == 'SELL' and any(net.get(r['symbol']) != qty for r in longs):
                    raise ValueError('ALL_HEDGES_FULL_READBACK_REQUIRED')
                result = e._op(s, 'entry-' + row['symbol'], contract(row), row['side'], qty, obs, 'ENTRY')
                if result['status'] in ('TERMINAL_PARTIAL', 'TERMINAL_EMPTY'):
                    s.update(phase='REPORT_EXIT_SHORTS', exit_reason='INCOMPLETE_ENTRY'); e._save(s)
                elif result['status'] == 'FULLY_FILLED' and row['side'] == 'SELL':
                    s.update(phase='REPORT_PROTECTION', protect_symbol=row['symbol']); e._save(s)
                return e._status('REPORT_ENTRY_' + result['status'])
            s['phase'] = 'REPORT_ENTRY_SHORTS' if phase == 'REPORT_ENTRY_HEDGES' else 'REPORT_MONITORING'
            if s['phase'] == 'REPORT_MONITORING':
                s['entry_credit_inr'] = e._cash(s); s['profit_basis_inr'] = e._cash(s) - s['costs']
            e._save(s); return e._status(s['phase'])
        if phase == 'REPORT_PROTECTION':
            row = next(r for r in shorts if r['symbol'] == s['protect_symbol']); symbol = row['symbol']
            amount = -net.get(symbol, 0)
            if amount != qty: raise ValueError('EXACT_SHORT_PROTECTION_QUANTITY_REQUIRED')
            active=[r for r in shorts if net.get(r['symbol'],0)<0]
            limit=min(self.cfg['risk_limit_inr'],c['entry_credit_inr']*self.cfg['stop_credit_multiple'])
            budget=(e._cash(s)+limit-s['costs'])/len(active)
            stops={r['symbol']:cash_stop(budget,qty,0,r['tick_size'],0,0) for r in active}
            if any(stops[r['symbol']]['trigger_price']<=e._book(contract(r),obs,'BUY',qty)['ask'] for r in active):
                s.update(phase='REPORT_EXIT_SHORTS',exit_reason='INSUFFICIENT_STOP_ROOM');e._save(s)
                return e._status('REPORT_EXIT_INSUFFICIENT_STOP_ROOM')
            # Base every stop on actual reconciled cash, including an incomplete
            # condor. Never count the proceeds of a not-yet-filled second short.
            for r in active:
                if r['symbol']==symbol or r['symbol'] not in s['protections']:continue
                prior=s['protections'][r['symbol']];record=e.protection.record(prior['key']) or {}
                if stops[r['symbol']]['trigger_price']<float(record['request']['trigger_price']):
                    s['last_write_at']=e.clock().isoformat();e._save(s)
                    return e._status('REPORT_REBALANCE_STOP_'+e.protection.tighten(prior['key'],stops[r['symbol']],obs)['status'])
            if symbol not in s['protections']:
                stop=stops[symbol]
                s['protections'][symbol] = dict(key=s['slot'] + '-gtt-' + symbol, stop=stop)
                e._save(s)
            p = s['protections'][symbol]
            s['last_write_at']=e.clock().isoformat();e._save(s)
            result = e.protection.create(p['key'], s['slot'], s['strategy'], contract(row), qty, p['stop'], obs)
            if result['status'] == 'VERIFIED_ACTIVE': s['phase'] = 'REPORT_ENTRY_SHORTS'; e._save(s)
            elif result['status'] in ('CHILD_FILLED', 'CHILD_TERMINAL', 'NO_PROTECTION_ABORTED_BEFORE_WRITE', 'VERIFIED_INACTIVE'):
                s.update(phase='REPORT_EXIT_SHORTS', exit_reason='PROTECTION_TRIGGERED'); e._save(s)
            return e._status('REPORT_PROTECTION_' + result['status'])
        if phase == 'REPORT_MONITORING':
            for p in s['protections'].values():
                result = e.protection.reconcile(p['key'])
                if result['status'] in ('CHILD_FILLED', 'CHILD_TERMINAL', 'VERIFIED_INACTIVE', 'NO_PROTECTION_ABORTED_BEFORE_WRITE'):
                    s.update(phase='REPORT_EXIT_SHORTS', exit_reason='PROTECTION_TRIGGERED'); e._save(s)
                    return e._status('REPORT_PROTECTIVE_CHILD_EXECUTED')
                if result['status'] != 'VERIFIED_ACTIVE': return e._status('REPORT_PROTECTION_' + result['status'])
            review = self.close_reason(s, obs)
            if review['action'] == 'REVIEW_EXIT':
                s.update(phase='REPORT_EXIT_SHORTS', exit_reason=review['reason']); e._save(s)
                return e._status('REPORT_EXIT_QUEUED')
            return e._status('REPORT_MONITORING_OWNED_BASKET')
        if phase == 'REPORT_EXIT_SHORTS':
            # A switch/window/timeout may stop an entry while its LIMIT is still
            # pending. Settle all entry operations before using net quantities.
            for name, key in s['operations'].items():
                if not name.startswith('entry-'): continue
                result = e.gateway.reconcile(key)
                if result['status'] in ('PENDING', 'PARTIAL'):
                    s['last_write_at'] = e.clock().isoformat(); e._save(s)
                    return e._status('REPORT_CANCEL_ENTRY_' + e.gateway.cancel(key, obs)['status'])
                if result['status'] not in FILLED | {'NO_BROKER_SUBMISSION'}:
                    return e._status('WAIT_FOR_ALL_ENTRY_TERMINALS')
            # Before placing a close, every protective child must be terminal.
            for p in s['protections'].values():
                result = e.protection.reconcile(p['key'])
                if result['status'] == 'VERIFIED_ACTIVE':
                    s['last_write_at'] = e.clock().isoformat(); e._save(s)
                    return e._status('REPORT_CANCEL_PROTECTION_' + e.protection.cancel(p['key'], obs)['status'])
                if result['status'] == 'CHILD_RECONCILIATION_REQUIRED':
                    record = e.protection.record(p['key'])
                    s['last_write_at']=e.clock().isoformat();e._save(s)
                    return e._status('REPORT_CANCEL_CHILD_' + e.gateway.cancel(record['child_key'], obs)['status'])
                if result['status'] not in ('VERIFIED_INACTIVE', 'CHILD_FILLED', 'CHILD_TERMINAL', 'NO_PROTECTION_ABORTED_BEFORE_WRITE'):
                    return e._status('WAIT_FOR_NO_OVERLAPPING_PROTECTIVE_BUY')
            for row in shorts:
                q = net.get(row['symbol'], 0)
                if q > 0: raise ValueError('UNEXPECTED_LONG_SHORT_CONTRACT')
                if q: return self.exit_leg(s, row, -q, 'BUY', obs)
            s['phase'] = 'REPORT_EXIT_HEDGES'; e._save(s); return e._status('REPORT_ALL_SHORTS_FLAT')
        if phase == 'REPORT_EXIT_HEDGES':
            if any(q < 0 for q in net.values()): raise ValueError('CLOSE_ALL_OWNED_SHORTS_FIRST')
            for row in longs:
                q = net.get(row['symbol'], 0)
                if q: return self.exit_leg(s, row, q, 'SELL', obs)
            for key in s['all_operations']:
                if e.gateway.reconcile(key)['status'] not in FILLED | {'NO_BROKER_SUBMISSION'}:
                    return e._status('WAIT_FOR_ALL_ORDER_TERMINALS')
            with e.journal.store.transaction() as db:
                db.execute("UPDATE pc_orders SET state='CLOSED' WHERE slot=?", (s['slot'],))
            s.update(phase='CLOSED', closed_at=e.clock().isoformat()); e._save(s)
            return e._status('REPORT_BASKET_CONFIRMED_CLOSED')
        raise ValueError('UNKNOWN_DURABLE_EXECUTION_PHASE')

    def exit_leg(self, s, row, qty, side, obs):
        name = 'exit-' + row['symbol']
        result = self.e._op(s, name, contract(row), side, qty, obs, 'EXIT')
        if result['status'] == 'TERMINAL_PARTIAL':
            s['operations'].pop(name); s['cycle'] += 1; self.e._save(s)
        return self.e._status('REPORT_EXIT_' + result['status'])
