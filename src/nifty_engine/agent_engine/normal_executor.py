"""Normal theta policy in the existing durable hedged-basket lifecycle."""
import copy

from .contracts import identity
from .report_executor import ReportExecution, signature, contract
from .execution import fresh
from . import normal_theta as policy, strategy_groups as groups
from .pc_control import JST
from datetime import date


class NormalExecution(ReportExecution):
    def _liquidation(self,state,obs):
        return sum(q*self.e._book(contract(next(r for r in state['candidate']['legs'] if r['symbol']==symbol)),obs,
            'SELL' if q>0 else 'BUY',abs(q))['bid' if q>0 else 'ask'] for symbol,q in self.e._net(state).items())

    def protection_stops(self,s,obs,active,qty,limit):
        if not getattr(self.e.protection,'ordinary_stop',False):
            return super().protection_stops(s,obs,active,qty,limit)
        from .premium_executor import cash_stop
        longs=sum(q*self.e._book(contract(next(r for r in s['candidate']['legs'] if r['symbol']==symbol)),obs,
            'SELL',q)['bid'] for symbol,q in self.e._net(s).items() if q>0)
        cash=self.e._cash(s)+longs
        return {r['symbol']:cash_stop(cash,qty,s['costs'],r['tick_size'],s.get('best_pnl',0),limit)
                for r in active}

    def trail(self,s,obs):
        if not getattr(self.e.protection,'ordinary_stop',False):return None
        e=self.e;qty=s['candidate']['quantity']
        pnl=e._cash(s)+self._liquidation(s,obs)-s['costs']
        if pnl>s.get('best_pnl',0):s['best_pnl']=pnl;e._save(s)
        active=[r for r in s['candidate']['legs'] if r['side']=='SELL' and e._net(s).get(r['symbol'],0)<0]
        limit=min(self.cfg['risk_limit_inr'],s['candidate']['entry_credit_inr']*self.cfg['stop_credit_multiple'])
        stops=self.protection_stops(s,obs,active,qty,limit)
        for row in active:
            key=s['protections'][row['symbol']]['key'];old=e.protection.record(key)
            if old.get('request',{}).get('order_type')!='SL':continue  # Preserve an existing legacy parent.
            stop=stops[row['symbol']]
            if stop['trigger_price']<=e._book(contract(row),obs,'BUY',qty)['ask']:
                s.update(phase='REPORT_EXIT_SHORTS',exit_reason='TRAILING_STOP');e._save(s)
                return e._status('REPORT_TRAILING_EXIT_QUEUED')
            if stop['trigger_price']<old['request']['trigger_price']:
                s['last_write_at']=e.clock().isoformat();e._save(s)
                return e._status('REPORT_TRAILING_SL_'+e.protection.tighten(key,stop,obs)['status'])
        return None

    def enabled(self,sid):return sid==policy.ID and groups.read(self.e.journal.store)['enabled'][policy.ID]
    def allowed(self,sid):return sid==policy.ID
    def matched_iv_required(self):return False
    def may_continue_entry(self,now,state=None):
        return (policy.entry_window(self.cfg,now) and
                (state is None or state['candidate']['index']==policy.scheduled_index(self.cfg,now)))
    def timed_exit_reason(self,now):
        return 'DAILY_1900_FLAT_DEADLINE' if policy.force_exit_due(self.cfg,now) else None
    def spread_type(self,c):
        if c.get('spread_type') not in ('bull_put','bear_call'):raise ValueError('NORMAL_VERTICAL_TYPE_REQUIRED')
        return c['spread_type']
    def input_bundle(self,c):return self.e.journal.store.meta('normal-theta-evidence-'+c['index'],{})

    def evidence(self,obs):
        result=copy.deepcopy(obs);result.setdefault('books',{})
        for index in self.cfg['indices']:
            for row in self.input_bundle({'index':index}).get('contracts',[]):
                if row['symbol'] not in result['books']:
                    result['books'][row['symbol']]={k:row[k] for k in ('bid','ask','bid_quantity','ask_quantity','received_at')}
        return result

    def preparation(self,obs,state=None):
        reasons=[];candidates=[]
        if not self.enabled(policy.ID):reasons.append('STRATEGY_SWITCH_OFF')
        for index in self.cfg['indices']:
            if index!=policy.scheduled_index(self.cfg,self.e.clock()):
                reasons.append(index+'_INDEX_NOT_SCHEDULED_TODAY')
                continue
            value=policy.evaluate(self.cfg,index,self.input_bundle({'index':index}),self.e.clock())
            candidate=value['candidate']
            if candidate and self.enabled(policy.ID) and (not state or signature(candidate)==signature(state['candidate'])):
                candidates.append(candidate)
            else:reasons.extend(value['reasons'])
        if state is None and self.e.journal.slot_status()['new_entry_blocked']:
            candidates=[];reasons.append('SINGLE_BASKET_OCCUPIED')
        selected=max(candidates,key=lambda c:(c['net_max_expiry_profit_inr'],
            -c['basket_requirement_inr'],-c['worst_case_loss_inr'],c['key'])) if candidates else None
        return dict(status='PREPARED' if selected else 'WAIT',selected=selected,
            reasons=list(dict.fromkeys(reasons)),broker_writes=False,
            reason='NORMAL_THETA_BASKET_PREPARED' if selected else 'NORMAL_THETA_INPUTS_REQUIRED')

    def validate(self,c,obs,state=None):
        # Re-evaluate current source evidence; a forged/stale prepared object may
        # not remove trend, net-theta, quote-width or actual margin requirements.
        if c.get('normal_policy_sha256')!=identity(self.cfg):raise ValueError('NORMAL_POLICY_CHANGED')
        if c.get('index')!=policy.scheduled_index(self.cfg,self.e.clock()):
            raise ValueError('INDEX_NOT_SCHEDULED_TODAY')
        if not policy.entry_window(self.cfg,self.e.clock()):
            raise ValueError('NORMAL_NEW_ENTRY_WINDOW_ENDED')
        current=self.preparation(obs,state)['selected']
        if not current or signature(current)!=signature(c) or current['spread_type']!=c.get('spread_type'):
            raise ValueError('CURRENT_NORMAL_THETA_CANDIDATE_REQUIRED')
        for key in ('basket_requirement_inr','hedge_requirement_inr','round_trip_charges_inr',
                'worst_case_loss_inr','net_theta_model_units','lots','take_profit_inr',
                'stop_trigger_inr','close_at_dte'):
            if current[key]!=c.get(key):raise ValueError('REPREPARE_CHANGED_NORMAL_EVIDENCE')
        fields=('symbol','side','index','expiry','strike','lot_size','tick_size','bid','ask',
            'bid_quantity','ask_quantity','delta','theta','theta_unit')
        if ([{k:r.get(k) for k in fields} for r in current['legs']]
                != [{k:r.get(k) for k in fields} for r in c['legs']]):
            raise ValueError('REPREPARE_CHANGED_NORMAL_LEGS')
        return super().validate(c,obs,state)

    def close_reason(self,state,obs):
        if self.timed_exit_reason(self.e.clock()):
            return dict(action='REVIEW_EXIT',reason='DAILY_1900_FLAT_DEADLINE',
                        net_pnl_inr=None,execution_enabled=False,broker_writes=False)
        e=self.e;c=state['candidate'];net=e._net(state)
        liquidation=sum(q*e._book(contract(next(r for r in c['legs'] if r['symbol']==symbol)),obs,
            'SELL' if q>0 else 'BUY',abs(q))['bid' if q>0 else 'ask'] for symbol,q in net.items())
        pnl=e._cash(state)+liquidation-state['costs']
        credit=state.get('entry_credit_inr',c['entry_credit_inr'])
        profit=state.get('profit_basis_inr',c['net_max_expiry_profit_inr'])
        reason=('LOSS_TRIGGER' if pnl<=-min(self.cfg['risk_limit_inr'],credit*self.cfg['stop_credit_multiple']) else
            'EXPIRY_GAMMA_WINDOW' if (date.fromisoformat(c['expiry'])-e.clock().astimezone(JST).date()).days<=self.cfg['close_dte'] else
            'HALF_CREDIT_PROFIT' if pnl>=profit*self.cfg['take_profit_fraction'] else None)
        return dict(action='REVIEW_EXIT' if reason else 'HOLD',reason=reason or 'NO_EXIT_SIGNAL',
            net_pnl_inr=round(pnl,2),execution_enabled=False,broker_writes=False)
