"""Owner-requested report adaptation. Pure research proposals, no broker transport.

Numbers are declared hypotheses, not fitted parameters or SPX performance copied
to India. All feature inputs require point-in-time evidence. Archive/UI output
never constitutes execution authorization.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from .contracts import identity, number, stamp
from .execution import fresh, leg
from .pc_control import JST

CATALOG='report_strategies.json'
KEY='report-strategy-evaluations'
IDS=('bull_put','bear_call','iron_condor','calendar')


def historical_features(daily,iv_now,now,*,source):
    """Derive daily indicators from prior completed OHLC/IV rows only.

    IV percentile compares current supplied IV with 252 prior-session IV values;
    realized volatility uses 30 close-to-close returns. Wilder ADX uses OHLC,
    never LTP, option prices or a guessed spot volume/VWAP.
    """
    rows=sorted(daily,key=lambda r:r['day'])
    days=[date.fromisoformat(r['day']) for r in rows]
    if not source or len(set(days))!=len(days) or any(d>=now.astimezone(JST).date() for d in days):
        raise ValueError('UNIQUE_PRIOR_COMPLETED_SESSIONS_REQUIRED')
    for r in rows:
        for k in ('close','high','low'):number(r[k])
        if not 0<r['low']<=r['close']<=r['high']:raise ValueError('VALID_DAILY_OHLC_REQUIRED')
    result={}
    if not rows:return result
    end=datetime.combine(days[-1],datetime.min.time(),JST).replace(hour=19).isoformat()
    def add(key,value,unit,n):
        result[key]=dict(value=value,unit=unit,source=source,observed_at=now.isoformat(),history_end_at=end,sessions=n)
    for n in (20,50):
        if len(rows)>=n:add('ma'+str(n),sum(r['close'] for r in rows[-n:])/n,'index_points',n)
    if len(rows)>=31:
        returns=[math.log(b['close']/a['close']) for a,b in zip(rows[-31:-1],rows[-30:])]
        mean=sum(returns)/30
        add('rv30',math.sqrt(sum((r-mean)**2 for r in returns)/29*252)*100,'annualized_percent',30)
    ivs=[r['iv'] for r in rows[-252:] if r.get('iv') is not None and r.get('iv_unit')=='annualized_percent' and number(r['iv'])>0]
    if len(ivs)>=252 and number(iv_now)>0:
        add('iv_percentile',sum(r<=iv_now for r in ivs[-252:])/252*100,'percentile',252)
    if len(rows)>=28:
        changes=[]
        for a,b in zip(rows,rows[1:]):
            up=b['high']-a['high'];down=a['low']-b['low']
            changes.append((max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])),
                            up if up>down and up>0 else 0,down if down>up and down>0 else 0))
        tr,plus,minus=[sum(r[i] for r in changes[:14]) for i in range(3)]
        dx=[]
        for offset in range(13,len(changes)):
            if offset>13:
                tr=tr-tr/14+changes[offset][0];plus=plus-plus/14+changes[offset][1];minus=minus-minus/14+changes[offset][2]
            dx.append(100*abs(plus-minus)/(plus+minus) if plus+minus>0 else 0)
        adx=sum(dx[:14])/14
        for value in dx[14:]:adx=(adx*13+value)/14
        add('adx14',adx,'index_score',len(rows))
    return result


def load(root):
    path=Path(root)/'config'/CATALOG
    if not path.exists():return None
    if path.stat().st_size>16384:raise ValueError('RESEARCH_CONFIG_TOO_LARGE')
    cfg=json.loads(path.read_text(encoding='utf-8'))
    if (cfg.get('format')!='trading-report-research-v1' or cfg.get('mode')!='MONITOR_ONLY'
            or cfg.get('trading_enabled') is not False or cfg.get('indices')!=['NIFTY','SENSEX']
            or cfg.get('retired_entries')!=['EVERYDAY','LATE_SESSION']
            or tuple(r['id'] for r in cfg.get('strategies',[]))!=IDS
            or cfg.get('maximum_lots')!=2 or cfg.get('risk_limit_inr')!=1000
            or cfg.get('entry_window_jst')!=['14:00','19:00']):
        raise ValueError('RESEARCH_ONLY_BOUNDED_CATALOG_REQUIRED')
    expected={'short_dte':[30,45],'calendar_long_dte':[60,90],'close_dte':7,
        'iv_percentile_min':70,'iv_percentile_low':30,'iv_history_sessions':252,
        'vrp_min_vol_points':5,'trend_adx_min':25,'range_adx_max':20,
        'short_abs_delta':[.15,.25],'calendar_abs_delta':[.45,.55],
        'take_profit_fraction':.5,'stop_credit_multiple':1.5}
    if any(cfg.get(k)!=v for k,v in expected.items()):raise ValueError('DECLARED_RESEARCH_HYPOTHESES_REQUIRED')
    return cfg


def _feature(bundle,key,unit,now,*,daily=False,sessions=0):
    evidence=bundle.get('features',{}).get(key,{})
    value=number(evidence['value'])
    if evidence.get('unit')!=unit or not evidence.get('source'):
        raise ValueError('FEATURE_UNITS_AND_SOURCE_REQUIRED')
    if not fresh(evidence['observed_at'],now,259200 if daily else 15):
        raise ValueError('FEATURE_STALE')
    if not 0<=value:raise ValueError('FEATURE_NEGATIVE')
    if daily:
        end=stamp(evidence['history_end_at'])
        if (end>stamp(evidence['observed_at']) or not fresh(end.isoformat(),now,259200)
                or end.astimezone(JST).date()>=now.astimezone(JST).date()
                or type(evidence.get('sessions')) is not int or evidence['sessions']<sessions):
            raise ValueError('PRIOR_COMPLETED_HISTORY_REQUIRED')
    return value


def _regime(cfg,bundle,now):
    values={};unknown=[]
    fields=(('spot','index_points',False,0),('ma20','index_points',True,20),
        ('ma50','index_points',True,50),('adx14','index_score',True,28),
        ('iv_percentile','percentile',True,cfg['iv_history_sessions']),
        ('iv30','annualized_percent',False,0),('rv30','annualized_percent',True,30))
    for key,unit,daily,sessions in fields:
        try:values[key]=_feature(bundle,key,unit,now,daily=daily,sessions=sessions)
        except (KeyError,ValueError,TypeError):unknown.append(key)
    if unknown:return values,'UNKNOWN',unknown
    if values['iv_percentile']>100 or values['adx14']>100:return values,'UNKNOWN',['indicator_range']
    if values['adx14']>=cfg['trend_adx_min']:
        if values['spot']>values['ma20']>values['ma50']:return values,'BULLISH',[]
        if values['spot']<values['ma20']<values['ma50']:return values,'BEARISH',[]
    if values['adx14']<cfg['range_adx_max']:return values,'RANGE',[]
    return values,'TRANSITION',[]


def _contract(row,index,now):
    normalized={k:row[k] for k in ('symbol','index','expiry','strike','lot_size','tick_size',
        'bid','ask','bid_quantity','ask_quantity','received_at')}
    leg(normalized)
    if row['index']!=index or not fresh(row['received_at'],now):raise ValueError('CURRENT_INDEX_BOOK_REQUIRED')
    delta=number(row['delta'])
    kind=row['symbol'][-2:]
    if not (0<delta<1 if kind=='CE' else -1<delta<0):raise ValueError('SIGNED_DELTA_REQUIRED')
    iv=row.get('iv')
    if iv is not None and (row.get('iv_unit')!='annualized_percent' or number(iv)<=0):iv=None
    return dict(normalized,delta=delta,iv=iv)


def _margin(bundle,legs,quantity,now):
    key=identity({'legs':[(r['symbol'],side) for r,side in legs],'quantity':quantity})
    m=bundle.get('margins',{}).get(key,{})
    if not fresh(m['received_at'],now):raise ValueError('CURRENT_EXACT_BASKET_MARGIN_REQUIRED')
    requirement=number(m['basket_requirement_inr']);cost=number(m['round_trip_charges_inr'])
    if requirement<0 or cost<0:raise ValueError('NONNEGATIVE_COSTS_AND_MARGIN_REQUIRED')
    return key,requirement,cost


def _pairs(rows,kind,cfg):
    a,b=cfg['short_abs_delta']
    shorts=[r for r in rows if r['symbol'].endswith(kind) and a<=abs(r['delta'])<=b]
    for short in shorts:
        for hedge in rows:
            if (hedge['symbol'].endswith(kind) and hedge['expiry']==short['expiry']
                    and hedge['lot_size']==short['lot_size']
                    and (hedge['strike']>short['strike'] if kind=='CE' else hedge['strike']<short['strike'])):
                yield [(hedge,'BUY'),(short,'SELL')]


def evaluate(cfg,index,bundle,now,*,occupied=False):
    """Evaluate one snapshot; unknown inputs prevent candidate construction."""
    if index not in cfg['indices']:raise ValueError('SUPPORTED_INDEX_REQUIRED')
    rows=[dict(r,index=index,status='WAIT',reasons=[],candidate=None,execution_enabled=False,
        broker_writes=False,mode='MONITOR_ONLY',performance_status='NOT_BACKTESTED') for r in cfg['strategies']]
    result=dict(index=index,at=now.isoformat(),config_sha256=identity(cfg),mode='MONITOR_ONLY',
        broker_writes=False,execution_enabled=False,regime='UNKNOWN',strategies=rows,selected=None)
    reasons=[]
    local=now.astimezone(JST)
    if not(local.weekday()<5 and (14,0)<=(local.hour,local.minute)<(19,0)):reasons.append('OUTSIDE_RESEARCH_ENTRY_WINDOW')
    if occupied:reasons.append('SINGLE_BASKET_OCCUPIED')
    if bundle.get('index')!=index or bundle.get('positions_complete') is not True:
        reasons.append('COMPLETE_INDEX_POSITION_EVIDENCE_REQUIRED')
    if not isinstance(bundle.get('protected_symbols'),list):reasons.append('MANUAL_PROTECTION_EVIDENCE_REQUIRED')
    expiry=bundle.get('expiry_evidence',{})
    if expiry.get('status')!='CONFIRMED_CURRENT_MASTER' or expiry.get('day_jst')!=local.date().isoformat():
        reasons.append('CURRENT_API_AND_MASTER_EXPIRY_REQUIRED')
    try:
        if not fresh(bundle['received_at'],now):raise ValueError('STALE')
    except (KeyError,ValueError,TypeError):reasons.append('CURRENT_RESEARCH_SNAPSHOT_REQUIRED')
    values,regime,unknown=_regime(cfg,bundle,now);result['regime']=regime
    result['feature_values']=values
    if unknown:reasons.append('UNKNOWN_FEATURES:'+','.join(unknown))
    try:
        events=bundle['event_calendar']
        if (events['status']!='VERIFIED_CLEAR' or not events['source'] or not fresh(events['checked_at'],now,3600)
                or stamp(events['coverage_start'])>now
                or (date.fromisoformat(events['coverage_through'])-local.date()).days<cfg['short_dte'][1]-cfg['close_dte']):
            raise ValueError('EVENT_COVERAGE_REQUIRED')
    except (KeyError,ValueError,TypeError):reasons.append('EVENT_CALENDAR_UNKNOWN_OR_EVENT_RISK')
    if reasons:
        for row in rows:row['reasons']=reasons
        return result
    rich=values['iv_percentile']>=cfg['iv_percentile_min'] and values['iv30']-values['rv30']>=cfg['vrp_min_vol_points']
    eligible={'bull_put':rich and regime=='BULLISH','bear_call':rich and regime=='BEARISH',
        'iron_condor':rich and regime=='RANGE','calendar':values['iv_percentile']<=cfg['iv_percentile_low']}
    contracts=[];bad=0
    for raw in bundle.get('contracts',[])[:200]:
        try:
            c=_contract(raw,index,now)
            if c['symbol'] not in bundle['protected_symbols'] and c['expiry'] in expiry.get('expiries',[]):contracts.append(c)
        except (KeyError,ValueError,TypeError):bad+=1
    # Duplicate symbols are ambiguous rather than opportunities to repeat a leg.
    if len({r['symbol'] for r in contracts})!=len(contracts):
        for row in rows:row['reasons']=['DUPLICATE_CONTRACT_EVIDENCE']
        return result
    front=[r for r in contracts if cfg['short_dte'][0]<=(date.fromisoformat(r['expiry'])-local.date()).days<=cfg['short_dte'][1]]
    try:
        funds=bundle['funds']
        if not fresh(funds['received_at'],now):raise ValueError('STALE_FUNDS')
        cash=min(number(funds['option_buy_available_inr']),number(funds['option_sell_available_inr']))
        if cash<0:raise ValueError('NEGATIVE_FUNDS')
    except (KeyError,ValueError,TypeError):
        for row in rows:row['reasons']=['CURRENT_MARGIN_BALANCES_REQUIRED']
        return result
    for row in rows:
        sid=row['id']
        if not eligible[sid]:row.update(status='NO_TRADE',reasons=['REGIME_OR_VOLATILITY_FILTER']);continue
        candidates=[]
        if sid=='calendar':
            # Multi-expiry cash settlement needs a scenario model; never assert
            # that entry debit caps loss or that an inverted surface is an edge.
            a,b=cfg['calendar_abs_delta']
            for short in front:
                if not a<=abs(short['delta'])<=b:continue
                for long in contracts:
                    dte=(date.fromisoformat(long['expiry'])-local.date()).days
                    if (long['strike']!=short['strike'] or long['symbol'][-2:]!=short['symbol'][-2:]
                            or long['lot_size']!=short['lot_size'] or not cfg['calendar_long_dte'][0]<=dte<=cfg['calendar_long_dte'][1]):continue
                    try:
                        front_iv=number(short['iv']);back_iv=number(long['iv'])
                        if front_iv<=back_iv:continue
                        debit=long['ask']-short['bid']
                        if debit<=0:continue
                        candidates.append(dict(legs=[dict(long,side='BUY'),dict(short,side='SELL')],
                            lots=1,quantity=short['lot_size'],entry_debit_inr=round(debit*short['lot_size'],2),
                            worst_case_loss_inr=None,risk_bound_verified=False))
                    except (KeyError,ValueError,TypeError):continue
            row.update(status='RISK_MODEL_REQUIRED' if candidates else 'WAIT',
                reasons=['CALENDAR_SETTLEMENT_PAYOFF_AND_MARGIN_MODEL_REQUIRED'],candidate=candidates[0] if candidates else None)
            continue
        combinations=_pairs(front,'PE' if sid=='bull_put' else 'CE',cfg)
        if sid=='iron_condor':
            combinations=(p+c for p,c in itertools.product(list(_pairs(front,'PE',cfg)),list(_pairs(front,'CE',cfg)))
                if p[1][0]['strike']<c[1][0]['strike'] and p[0][0]['expiry']==c[0][0]['expiry'])
        for legs in itertools.islice(combinations,500):
            if len({r['symbol'] for r,_ in legs})!=len(legs) or len({r['lot_size'] for r,_ in legs})!=1:continue
            credit=sum(r['bid'] if side=='SELL' else -r['ask'] for r,side in legs)
            width=max(abs(legs[i][0]['strike']-legs[i+1][0]['strike']) for i in range(0,len(legs),2))
            if not 0<credit<width:continue
            for lots in range(1,cfg['maximum_lots']+1):
                qty=legs[0][0]['lot_size']*lots
                if any((r['ask_quantity'] if side=='BUY' else r['bid_quantity'])<qty for r,side in legs):continue
                try:key,margin,cost=_margin(bundle,legs,qty,now)
                except (KeyError,ValueError,TypeError):continue
                profit=credit*qty-cost;loss=(width-credit)*qty+cost
                if margin>cash or profit<=0 or not 0<loss<=cfg['risk_limit_inr']:continue
                candidates.append(dict(key=key,strategy=sid,index=index,expiry=legs[0][0]['expiry'],
                    legs=[dict(r,side=side) for r,side in legs],lots=lots,quantity=qty,
                    net_max_expiry_profit_inr=round(profit,2),worst_case_loss_inr=round(loss,2),
                    basket_requirement_inr=margin,round_trip_charges_inr=cost,
                    entry_credit_inr=round(credit*qty,2),take_profit_inr=round(profit*cfg['take_profit_fraction'],2),
                    stop_trigger_inr=round(min(cfg['risk_limit_inr'],credit*qty*cfg['stop_credit_multiple']),2),
                    close_at_dte=cfg['close_dte'],candidate_scope='MAX_200_CONTRACTS_500_COMBINATIONS',
                    execution_enabled=False,broker_writes=False))
        if candidates:
            candidates.sort(key=lambda c:(-c['net_max_expiry_profit_inr']/c['worst_case_loss_inr'],c['basket_requirement_inr'],c['key']))
            row.update(status='RESEARCH_CANDIDATE',candidate=candidates[0],reasons=['OUT_OF_SAMPLE_AND_EXECUTOR_VALIDATION_REQUIRED'])
        else:row['reasons']=['CURRENT_HEDGED_BOOKS_EXACT_MARGIN_AND_RISK_REQUIRED']
    proposals=[r['candidate'] for r in rows if r['status']=='RESEARCH_CANDIDATE']
    if proposals:result['selected']=max(proposals,key=lambda c:c['net_max_expiry_profit_inr']/c['worst_case_loss_inr'])
    result['invalid_contracts']=bad
    return result


def review(cfg,position,now):
    """Research exit decisions; no state mutation or order submission."""
    result=dict(action='WAIT',broker_writes=False,execution_enabled=False)
    try:
        if (position['strategy'] not in IDS[:-1] or position['ownership']!='ENGINE_VERIFIED'
                or not fresh(position['received_at'],now)):return result
        pnl=number(position['net_pnl_inr']);credit=number(position['entry_credit_inr'])
        if pnl<=-min(cfg['risk_limit_inr'],credit*cfg['stop_credit_multiple']):return dict(result,action='REVIEW_EXIT',reason='LOSS_TRIGGER')
        if (date.fromisoformat(position['expiry'])-now.astimezone(JST).date()).days<=cfg['close_dte']:
            return dict(result,action='REVIEW_EXIT',reason='EXPIRY_GAMMA_WINDOW')
        if pnl>=number(position['net_max_expiry_profit_inr'])*cfg['take_profit_fraction']:
            return dict(result,action='REVIEW_EXIT',reason='HALF_CREDIT_PROFIT')
        return dict(result,action='HOLD',reason='NO_EXIT_SIGNAL')
    except (KeyError,ValueError,TypeError):return result


def update(store,root,now):
    cfg=load(root)
    if not cfg:return None
    occupied=bool(store.read("SELECT 1 FROM pc_orders WHERE state<>'CLOSED' LIMIT 1"))
    value=dict(format=cfg['format'],at=now.isoformat(),config_sha256=identity(cfg),catalog=cfg,
        indices={i:evaluate(cfg,i,store.meta('report-strategy-evidence-'+i,{}),now,occupied=occupied) for i in cfg['indices']},
        mode='MONITOR_ONLY',broker_writes=False,execution_enabled=False)
    proposals=[v['selected'] for v in value['indices'].values() if v['selected']]
    value['selected']=max(proposals,key=lambda c:c['net_max_expiry_profit_inr']/c['worst_case_loss_inr']) if proposals else None
    store.set_meta(KEY,value)
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--features',type=Path,required=True)
    parser.add_argument('--at',required=True,help='Replay evaluation timestamp; never broker execution')
    parser.add_argument('--database',type=Path,help='Explicit private journal for research evidence only; no broker writes')
    args=parser.parse_args();cfg=load(args.root)
    if args.features.stat().st_size>1_000_000:raise ValueError('RESEARCH_EVIDENCE_TOO_LARGE')
    bundle=json.loads(args.features.read_text(encoding='utf-8-sig'))
    at=stamp(args.at);value=evaluate(cfg,bundle['index'],bundle,at)
    if args.database:
        # Explicit private-file import, not a cloud command or live order path.
        from .pc_control import PcJournal
        accepted={key:bundle[key] for key in ('index','received_at','positions_complete','protected_symbols',
            'features','expiry_evidence','funds','event_calendar','contracts','margins') if key in bundle}
        from .research_sync import scrub
        store=PcJournal(args.database).store
        store.set_meta('report-strategy-evidence-'+bundle['index'],scrub(accepted))
        update(store,args.root,at)
    print(json.dumps(value,allow_nan=False))


if __name__=='__main__':main()
