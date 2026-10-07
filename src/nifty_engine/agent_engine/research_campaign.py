"""Owner-requested 38-calendar-day observation period, not trading activation."""
from __future__ import annotations
from datetime import timedelta
from .contracts import dumps, stamp
from .execution import fresh
from .pc_control import JST

KEY='report-research-campaign-v1'


def start(store,now):
    with store.transaction() as db:
        old=db.execute('SELECT body FROM meta WHERE key=?',(KEY,)).fetchone()
        if old:return store.meta(KEY)
        value=dict(format=KEY,status='COLLECTING',started_at=now.isoformat(),
            ends_at=(now+timedelta(days=38)).isoformat(),calendar_days=38,
            indices=['NIFTY','SENSEX'],strategies=['bull_put','bear_call','iron_condor','calendar'],
            days={},broker_writes=False,execution_authorized=False)
        db.execute('INSERT INTO meta VALUES(?,?)',(KEY,dumps(value)))
    return value


def observe(store,index,bundle,now):
    value=store.meta(KEY)
    if not value or value['status']!='COLLECTING' or now>=stamp(value['ends_at']):return
    if index not in value['indices']:raise ValueError('CAMPAIGN_INDEX_REQUIRED')
    try:
        if not fresh(bundle['received_at'],now,15):return
        day=stamp(bundle['received_at']).astimezone(JST).date().isoformat()
        if stamp(bundle['received_at'])<stamp(value['started_at']):return
    except (KeyError,TypeError,ValueError):return
    contracts=bundle.get('contracts',[]);current=[]
    for r in contracts:
        try:
            if fresh(r['received_at'],now,15) and fresh(r['greeks_received_at'],now,15):current.append(r)
        except (KeyError,TypeError,ValueError):pass
    key=day+':'+index
    old=value['days'].get(key,{})
    row=dict(index=index,day=day,last_snapshot_at=bundle['received_at'],
        maximum_fresh_contracts=max(old.get('maximum_fresh_contracts',0),len(current)),
        last_calendar_status=bundle.get('event_calendar',{}).get('status','UNKNOWN'),
        positions_complete=bundle.get('positions_complete') is True,
        has_actual_greek_books=bool(current) or old.get('has_actual_greek_books',False))
    # Preserve the collection horizon and owner decisions; this worker never
    # changes strategy/global switches, IV/session requirements or activation.
    with store.transaction() as db:
        import json
        value=json.loads(db.execute('SELECT body FROM meta WHERE key=?',(KEY,)).fetchone()[0])
        if value['status']!='COLLECTING':return
        value['days'][key]=row
        db.execute('UPDATE meta SET body=? WHERE key=?',(dumps(value),KEY))


def public(store,now):
    value=store.meta(KEY,{})
    if not value:return dict(status='NOT_CONFIGURED',broker_writes=False)
    finished=now>=stamp(value['ends_at'])
    days=value.get('days',{})
    return dict(status='COLLECTION_ENDED_REVIEW_REQUIRED' if finished else 'COLLECTING',
        started_at=value['started_at'],ends_at=value['ends_at'],calendar_days=38,
        elapsed_calendar_days=min(38,max(0,int((now-stamp(value['started_at'])).total_seconds()/86400))),
        observed_market_days={i:sum(r.get('has_actual_greek_books') is True for r in days.values() if r['index']==i) for i in value['indices']},
        strategy_validation='NOT_PROVEN',iv_history_requirement=252,
        broker_writes=False,execution_authorized=False)
