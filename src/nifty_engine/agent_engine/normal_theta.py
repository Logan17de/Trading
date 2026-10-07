"""Normal defined-risk theta hypothesis, separate from historical-IV research.

Pure snapshot evaluation. Positive model theta is not an expected-return or
win-probability estimate. No API, owner intent, broker writes or activation here.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

from .contracts import identity, number, stamp
from .execution import fresh
from .pc_control import JST
from . import report_strategies as research

ID = 'normal_theta'
KEY = 'normal-theta-evaluations'


def load(root):
    path = Path(root) / 'config/normal_theta.json'
    if not path.exists(): return None
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    expected = dict(format='normal-theta-v1', indices=['NIFTY','SENSEX'],
        maximum_lots=2, risk_limit_inr=1000, entry_window_jst=['14:00','19:00'],
        short_dte=[14,45], close_dte=7, short_abs_delta=[.15,.25],
        trend_adx_min=25, take_profit_fraction=.5, stop_credit_multiple=1.5,
        maximum_relative_quote_spread=.10, historical_iv_required=False)
    if cfg != expected: raise ValueError('DECLARED_NORMAL_THETA_POLICY_REQUIRED')
    return cfg


def evaluate(cfg, index, bundle, now, *, occupied=False):
    result = dict(id=ID, index=index, at=now.isoformat(), config_sha256=identity(cfg),
        status='WAIT', reasons=[], candidate=None, execution_enabled=False,
        broker_writes=False, performance_status='NOT_BACKTESTED', regime='UNKNOWN',
        event_calendar=bundle.get('event_calendar', {'status':'UNKNOWN'}))
    reasons = result['reasons']; local = now.astimezone(JST)
    if index not in cfg['indices']: raise ValueError('SUPPORTED_INDEX_REQUIRED')
    if not (local.weekday()<5 and (14,0)<=(local.hour,local.minute)<(19,0)):
        reasons.append('OUTSIDE_1400_1900_JST')
    if occupied: reasons.append('SINGLE_BASKET_OCCUPIED')
    if bundle.get('index')!=index or bundle.get('positions_complete') is not True:
        reasons.append('COMPLETE_INDEX_POSITION_EVIDENCE_REQUIRED')
    if not isinstance(bundle.get('protected_symbols'),list): reasons.append('MANUAL_PROTECTION_EVIDENCE_REQUIRED')
    expiry=bundle.get('expiry_evidence',{})
    try:
        if (expiry['status']!='CONFIRMED_CURRENT_MASTER' or expiry['day_jst']!=local.date().isoformat()
                or not fresh(expiry['received_at'],now,3600)): raise ValueError()
        if not fresh(bundle['received_at'],now,10): raise ValueError()
    except (KeyError,ValueError,TypeError): reasons.append('CURRENT_SNAPSHOT_AND_API_MASTER_EXPIRY_REQUIRED')
    values={}
    for key,unit,daily,sessions in (('spot','index_points',False,0),('ma20','index_points',True,20),
            ('ma50','index_points',True,50),('adx14','index_score',True,28)):
        try: values[key]=research._feature(bundle,key,unit,now,daily=daily,sessions=sessions)
        except (KeyError,ValueError,TypeError): reasons.append('CURRENT_TREND_INPUT_REQUIRED:'+key)
    if len(values)==4:
        if 0<=values['adx14']<=100 and values['adx14']>=cfg['trend_adx_min']:
            if values['spot']>values['ma20']>values['ma50']: result['regime']='BULLISH'
            elif values['spot']<values['ma20']<values['ma50']: result['regime']='BEARISH'
        if result['regime']=='UNKNOWN': reasons.append('NO_CONFIRMED_DIRECTIONAL_TREND')
    try:
        funds=bundle['funds']
        if not fresh(funds['received_at'],now,10): raise ValueError()
        buy_cash=number(funds['option_buy_available_inr']); sell_cash=number(funds['option_sell_available_inr'])
        if min(buy_cash,sell_cash)<0: raise ValueError()
    except (KeyError,ValueError,TypeError): reasons.append('CURRENT_MARGIN_BALANCES_REQUIRED')
    if reasons:return result
    kind='PE' if result['regime']=='BULLISH' else 'CE'
    contracts=[]; seen=set()
    for raw in bundle.get('contracts',[])[:200]:
        try:
            row=research._contract(raw,index,now)
            if row['symbol'] in seen:
                result['reasons']=['DUPLICATE_CONTRACT_EVIDENCE']; return result
            seen.add(row['symbol'])
            theta=number(raw['theta'])
            if raw.get('theta_unit')!='groww_native' or theta>=0: continue
            dte=(date.fromisoformat(row['expiry'])-local.date()).days
            if (row['symbol'] in bundle['protected_symbols'] or row['expiry'] not in expiry['expiries']
                    or not cfg['short_dte'][0]<=dte<=cfg['short_dte'][1]):continue
            midpoint=(row['bid']+row['ask'])/2
            if midpoint<=0 or (row['ask']-row['bid'])/midpoint>cfg['maximum_relative_quote_spread']:continue
            contracts.append(dict(row,theta=theta,theta_unit=raw['theta_unit'],
                greeks_received_at=raw.get('greeks_received_at',raw['received_at'])))
        except (KeyError,ValueError,TypeError):continue
    candidates=[]
    for legs in research._pairs(contracts,kind,cfg):
        hedge,short=legs[0][0],legs[1][0]
        if not (short['strike']<values['spot'] if kind=='PE' else short['strike']>values['spot']):continue
        net_theta=hedge['theta']-short['theta']
        credit=short['bid']-hedge['ask']; width=abs(short['strike']-hedge['strike'])
        if net_theta<=0 or not 0<credit<width:continue
        for lots in range(1,cfg['maximum_lots']+1):
            quantity=short['lot_size']*lots
            if hedge['ask_quantity']<quantity or short['bid_quantity']<quantity:continue
            try:
                key,margin,cost=research._margin(bundle,legs,quantity,now)
                m=bundle['margins'][key]; hedge_margin=number(m['hedge_requirement_inr'])
                if hedge_margin<0:continue
            except (KeyError,ValueError,TypeError):continue
            profit=credit*quantity-cost; loss=(width-credit)*quantity+cost
            hedge_debit=hedge['ask']*quantity
            if (profit<=0 or not 0<loss<=cfg['risk_limit_inr'] or margin>sell_cash
                    or max(hedge_margin,hedge_debit)>buy_cash or hedge_debit+cost>cfg['risk_limit_inr']):continue
            candidates.append(dict(key=key,strategy=ID,spread_type='bull_put' if kind=='PE' else 'bear_call',
                index=index,expiry=short['expiry'],legs=[dict(r,side=s) for r,s in legs],
                lots=lots,quantity=quantity,basket_requirement_inr=margin,hedge_requirement_inr=hedge_margin,
                round_trip_charges_inr=cost,net_max_expiry_profit_inr=round(profit,2),
                worst_case_loss_inr=round(loss,2),entry_credit_inr=round(credit*quantity,2),
                take_profit_inr=round(profit*.5,2),stop_trigger_inr=round(min(1000,credit*quantity*1.5),2),
                close_at_dte=7,net_theta_model_units=round(net_theta*quantity,4),
                prepared_at=now.isoformat(),normal_policy_sha256=identity(cfg),
                execution_enabled=False,broker_writes=False))
    if candidates:
        result.update(status='CANDIDATE',candidate=max(candidates,key=lambda c:(
            c['net_max_expiry_profit_inr']/c['worst_case_loss_inr'],-c['basket_requirement_inr'],c['key'])))
    else:result.update(status='NO_TRADE',reasons=['POSITIVE_THETA_LIQUID_HEDGED_BASKET_WITHIN_RISK_REQUIRED'])
    return result


def update(store,cfg,now):
    occupied=bool(store.read("SELECT 1 FROM pc_orders WHERE state<>'CLOSED' LIMIT 1"))
    value=dict(at=now.isoformat(),policy=cfg,indices={i:evaluate(cfg,i,
        store.meta('normal-theta-evidence-'+i,{}),now,occupied=occupied) for i in cfg['indices']},
        execution_enabled=False,broker_writes=False)
    store.set_meta(KEY,value);return value
