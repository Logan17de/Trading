"""Versioned ATM variance interpolation for study, never an invented IV history.

This proxy is neither India VIX nor model-free implied variance. It is not promoted
to execution features. Only actual near-close observations count as prior days.
"""
from __future__ import annotations
import json
import math
from datetime import date, datetime, time, timedelta

from .contracts import IST, dumps, number, stamp
from .execution import fresh

METHOD='GROWW_ATM_VARIANCE30_RESEARCH_V1'


def term(index,expiry,spot,chain,master,at,now):
    if not fresh(at,now,15) or number(spot)<=0:raise ValueError('CURRENT_CHAIN_AND_SPOT_REQUIRED')
    rows=[]
    for strike,sides in chain.get('strikes',{}).items():
        try:
            k=number(float(strike));values=[];symbols=[]
            for kind in ('CE','PE'):
                row=sides[kind];symbol=row['trading_symbol'];m=master[symbol]
                if (m['index']!=index or m['expiry']!=expiry or m['strike']!=k or not symbol.endswith(kind)):
                    raise ValueError('EXACT_CHAIN_MASTER_REQUIRED')
                iv=number(row['greeks']['iv'])
                if not 0<iv<500:raise ValueError('PERCENTAGE_IV_REQUIRED')
                values.append(iv);symbols.append(symbol)
            rows.append(dict(strike=k,variance=sum(v*v for v in values)/2,ivs=values,symbols=symbols))
        except (KeyError,ValueError,TypeError):continue
    if not rows:raise ValueError('PAIRED_ATM_IV_REQUIRED')
    # ATM means nearest listed strike to independently observed spot; tie goes
    # to the lower strike. Do not pick whichever IV would improve a strategy.
    row=min(rows,key=lambda r:(abs(r['strike']-spot),r['strike']))
    expires=datetime.combine(date.fromisoformat(expiry),time(15,30),IST)
    days=(expires-now).total_seconds()/86400
    if days<=0:raise ValueError('UNEXPIRED_TERM_REQUIRED')
    return dict(row,index=index,expiry=expiry,days=days,received_at=at)


def interpolate(index,terms,now):
    rows=sorted(terms,key=lambda r:r['days'])
    lower=[r for r in rows if r['days']<=30];upper=[r for r in rows if r['days']>=30]
    if not lower or not upper:raise ValueError('ACTUAL_EXPIRIES_MUST_BRACKET_30_DAYS')
    a,b=lower[-1],upper[0]
    if any(r['index']!=index or not fresh(r['received_at'],now,15) for r in (a,b)):
        raise ValueError('FRESH_MATCHING_TERMS_REQUIRED')
    if abs((stamp(a['received_at'])-stamp(b['received_at'])).total_seconds())>10:
        raise ValueError('CONTEMPORANEOUS_TERMS_REQUIRED')
    variance=a['variance'] if a['days']==b['days'] else (
        a['variance']*a['days']*(b['days']-30)+b['variance']*b['days']*(30-a['days']))/(b['days']-a['days'])/30
    return dict(method=METHOD,index=index,value=math.sqrt(variance),unit='annualized_percent',
        observed_at=min(a['received_at'],b['received_at']),computed_at=now.isoformat(),terms=[a,b],
        mode='MONITOR_ONLY',execution_feature=False,broker_writes=False)


def save(store,point,now):
    if (point.get('method')!=METHOD or point.get('execution_feature') is not False
            or point.get('index') not in ('NIFTY','SENSEX') or not fresh(point['observed_at'],now,15)):
        raise ValueError('ACTUAL_RESEARCH_IV_POINT_REQUIRED')
    index=point['index'];at=stamp(point['observed_at']);local=at.astimezone(IST);day=local.date().isoformat()
    # An intraday receipt is not an EOD history observation. Holidays and missed
    # sessions are never filled forward. A later real near-close point replaces
    # only that day's earlier near-close point; restart is idempotent.
    near_close=local.weekday()<5 and time(15,25)<=local.time().replace(tzinfo=None)<time(15,30)
    with store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS report_iv30_days(index_name TEXT,day TEXT,at TEXT,body TEXT,PRIMARY KEY(index_name,day))')
        if near_close:
            db.execute('INSERT INTO report_iv30_days VALUES(?,?,?,?) ON CONFLICT(index_name,day) DO UPDATE SET at=excluded.at,body=excluded.body WHERE excluded.at>report_iv30_days.at',
                (index,day,at.isoformat(),dumps(point)))
    days=store.read('SELECT day FROM report_iv30_days WHERE index_name=? AND day<? ORDER BY day',(index,now.astimezone(IST).date().isoformat()))
    status=dict(status='COLLECTING_MATCHED_RESEARCH_HISTORY',method=METHOD,current=point,
        completed_observed_days=len(days),required_prior_sessions=252,history_complete=False,
        missing_session_validation=True,execution_feature=False,broker_writes=False)
    store.set_meta('report-iv30-monitor-'+index,status)
    return status


def public(store,index,now):
    value=store.meta('report-iv30-monitor-'+index,{})
    point=value.get('current',{})
    try:current=bool(point and fresh(point['observed_at'],now,15))
    except (ValueError,TypeError,KeyError):current=False
    return dict(status='CURRENT_RESEARCH_PROXY' if current else 'UNKNOWN_OR_STALE',
        method=METHOD,value=point.get('value') if current else None,
        observed_at=point.get('observed_at'),completed_observed_days=value.get('completed_observed_days',0),
        required_prior_sessions=252,execution_feature=False,broker_writes=False)
