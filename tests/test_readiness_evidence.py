import copy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import event_calendar as events,iv_observations as iv,broker_readiness as broker
from nifty_engine.agent_engine.store import Store
from nifty_engine.agent_engine.research_sync import SupabaseArchive,PROJECT_URL

NOW=datetime(2026,10,7,4,35,tzinfo=timezone.utc)


def fed_html():
    months=['January','March','April','June','July','September','October','December']
    pairs=['27-28','17-18','28-29','16-17','28-29','15-16','27-28','8-9']
    return ('2026 FOMC Meetings'+''.join(f'<div class="fomc-meeting__month"><strong>{m}</strong></div><div class="fomc-meeting__date">{d}*</div>' for m,d in zip(months,pairs))+'2025 FOMC Meetings').encode()


def rbi_html():
    return b'Dates of meetings of Monetary Policy Committee for 2026-27 April 6, 7 and 8, 2026 June 3, 4 and 5, 2026 August 3, 4 and 5, 2026 October 5, 6 and 7, 2026 December 2, 3 and 4, 2026 February 3, 4 and 5, 2027 Chief General Manager'


def test_official_meetings_dates_timezone_and_incomplete_sources_rejected():
    rows,end=events.fed_events(fed_html(),2026)
    assert len(rows)==16 and str(end)=='2026-12-31'
    october=[r for r in rows if r['start_at'].startswith('2026-10')]
    assert len(october)==2 and october[0]['start_at'].endswith('-04:00')
    assert all(r['time_known'] is False for r in rows)
    rows,end=events.rbi_events(rbi_html())
    assert len(rows)==18 and str(end)=='2027-03-31'
    assert any(r['start_at']=='2026-10-07T00:00:00+05:30' for r in rows)
    with pytest.raises(ValueError):events.fed_events(b'bot challenge',2026)
    with pytest.raises(ValueError):events.rbi_events(b'press release list')
    with pytest.raises(ValueError):events.mospi_events(b'%PDF-unreviewed',b'release calendar')


def test_mospi_latest_document_revision_is_checked(monkeypatch):
    import hashlib,json
    raw=b'%PDF-fixture-reviewed';monkeypatch.setattr(events,'MOSPI_HASH',hashlib.sha256(raw).hexdigest())
    latest=dict(status='success',data={'url':events.MOSPI.split('.gov.in/',1)[1].replace('%20',' ')})
    rows,end=events.mospi_events(raw,json.dumps(latest).encode())
    assert len(rows)==15 and str(end)=='2027-03-31'
    assert sum(r['kind']=='INDIA_CPI' for r in rows)==6
    assert any(r['kind']=='INDIA_GDP' and r['start_at'].startswith('2026-11-30') for r in rows)
    latest['data']['url']='uploads/revised-calendar.pdf'
    with pytest.raises(ValueError):events.mospi_events(raw,json.dumps(latest).encode())


def sources(now=NOW):
    return {name:dict(status='AVAILABLE',checked_at=now.isoformat(),coverage_through='2026-12-31',events=[]) for name in ('FED','RBI','MOSPI')}


def test_event_risk_and_source_failures_never_become_clear_or_refresh_receipts():
    data=sources()
    data['FED']['events']=events.fed_events(fed_html(),2026)[0]
    out=events.assessment(data,NOW)
    assert out['status']=='EVENT_RISK' and len(out['events'])==2
    data['MOSPI']['status']='UNKNOWN'
    out=events.assessment(data,NOW+timedelta(minutes=30))
    assert out['status']=='UNKNOWN' and out['unknown_sources']==['MOSPI']
    assert out['events'] and out['checked_at']==NOW.isoformat()
    assert events.assessment(data,NOW+timedelta(hours=2))['status']=='UNKNOWN'
    data=sources();data['FED']['coverage_through']='2026-10-08'
    assert events.assessment(data,NOW)['status']=='UNKNOWN'


def test_calendar_fetch_cache_restart_and_failures_are_durable(tmp_path):
    store=Store(tmp_path/'j.sqlite3');calls=[]
    def fetch(url):
        calls.append(url)
        if url==events.FED:return fed_html()
        if url==events.RBI:return rbi_html()
        raise TimeoutError('private raw failure must not escape')
    worker=events.OfficialCalendar(store,fetcher=fetch)
    value=worker.check(NOW)
    assert value['status']=='UNKNOWN' and value['unknown_sources']==['MOSPI']
    assert len(calls)==3 and value['sources']['FED']['status']=='AVAILABLE'
    assert worker.check(NOW+timedelta(minutes=10))==value and len(calls)==3
    assert events.OfficialCalendar(Store(store.path),fetcher=fetch).check(NOW+timedelta(minutes=20))==value
    worker.check(NOW+timedelta(minutes=31));assert len(calls)==6
    assert 'private raw' not in str(store.meta(events.KEY))


def chain(index,expiry,strike=25000,a=20,b=22):
    symbols=[index+expiry.replace('-','')+str(strike)+kind for kind in ('CE','PE')]
    master={s:dict(index=index,expiry=expiry,strike=strike) for s in symbols}
    payload={'strikes':{str(strike):{kind:dict(trading_symbol=s,greeks={'iv':v}) for kind,s,v in zip(('CE','PE'),symbols,(a,b))}}}
    return payload,master


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
def test_iv_variance_bracket_exact_contracts_and_missing_side(index):
    terms=[]
    for expiry,vol in [('2026-10-28',20),('2026-11-25',30)]:
        raw,master=chain(index,expiry,a=vol,b=vol)
        terms.append(iv.term(index,expiry,25000,raw,master,NOW.isoformat(),NOW))
    point=iv.interpolate(index,terms,NOW)
    a,b=terms
    expected=(a['variance']*a['days']*(b['days']-30)+b['variance']*b['days']*(30-a['days']))/(b['days']-a['days'])/30
    assert point['value']==pytest.approx(expected**.5) and point['execution_feature'] is False
    with pytest.raises(ValueError):iv.interpolate(index,[terms[1]],NOW)
    with pytest.raises(ValueError):iv.interpolate(index,terms,NOW+timedelta(seconds=16))
    with pytest.raises(ValueError):iv.interpolate(index,[dict(terms[0],index='WRONG'),terms[1]],NOW)
    raw['strikes']['25000'].pop('PE')
    with pytest.raises(ValueError):iv.term(index,expiry,25000,raw,master,NOW.isoformat(),NOW)


def point(at,index='NIFTY'):
    return dict(method=iv.METHOD,index=index,value=25,observed_at=at.isoformat(),computed_at=at.isoformat(),
        unit='annualized_percent',execution_feature=False,broker_writes=False,mode='MONITOR_ONLY')


def test_iv_completed_days_no_intraday_or_downtime_fill_restart_and_archive(tmp_path):
    store=Store(tmp_path/'j.sqlite3');at=NOW
    assert iv.save(store,point(at),at)['completed_observed_days']==0
    assert store.read('SELECT * FROM report_iv30_days')==[]
    close=NOW.replace(hour=9,minute=57)  # 15:27 IST
    iv.save(store,point(close),close)
    restored=Store(store.path);iv.save(restored,point(close),close)
    assert len(restored.read('SELECT * FROM report_iv30_days'))==1
    next_day=close+timedelta(days=1)
    result=iv.save(restored,point(next_day),next_day)
    assert result['completed_observed_days']==1 and result['history_complete'] is False
    assert result['execution_feature'] is False
    assert iv.public(store,'NIFTY',next_day+timedelta(seconds=16))['value'] is None
    uploaded=[]
    archive=SupabaseArchive(store,url=PROJECT_URL,key='fixture',post=lambda rows:uploaded.extend(rows),clock=lambda:next_day)
    archive.step()
    assert len([r for r in uploaded if r['category']=='iv30_research_day'])==2
    archive.step();assert len(uploaded)==3


def test_empty_gtt_readback_does_not_prove_protection_or_child(tmp_path):
    calls=[]
    def read(**kw):calls.append(kw);return {'orders':[]}
    result=broker.inspect_gtt(read,NOW)
    assert result['gtt_read_api']=='AVAILABLE' and len(calls)==3
    assert result['reason']=='NO_RETURNED_GTT_OR_CHILD_EVIDENCE'
    assert not result['broker_writes'] and not result['generated_child_verified'] and not result['persistent_owned_gtt_verified']
    store=Store(tmp_path/'j.sqlite3');store.set_meta(broker.KEY,result)
    assert broker.summary(store,NOW)['read_api']=='AVAILABLE'
    assert broker.summary(store,NOW+timedelta(hours=2))['read_api']=='UNKNOWN_OR_STALE'
    assert broker.inspect_gtt(lambda **kw:{'orders':[{}]*50},NOW)['gtt_read_api']=='UNKNOWN'


@pytest.mark.parametrize('strategy',['bull_put','bear_call','iron_condor'])
def test_report_owned_orders_join_private_archive_not_manual(tmp_path,strategy):
    from nifty_engine.agent_engine.pc_control import PcJournal
    journal=PcJournal(tmp_path/'j.sqlite3');journal.reserve('owned','NIFTY26N1025000CE','SELL',65,strategy=strategy)
    uploaded=[];archive=SupabaseArchive(journal.store,url=PROJECT_URL,key='fixture',post=lambda rows:uploaded.extend(rows))
    archive.step()
    assert len(uploaded)==1 and uploaded[0]['category']=='algo_order'
    assert uploaded[0]['body']['strategy']==strategy


def test_campaign_38_days_restart_no_auto_activation_or_backfilled_days(tmp_path):
    from nifty_engine.agent_engine import research_campaign as campaign
    from nifty_engine.agent_engine import premium_strategy as intent
    store=Store(tmp_path/'j.sqlite3')
    first=campaign.start(store,NOW)
    assert campaign.start(Store(store.path),NOW+timedelta(days=5))==first
    assert first['ends_at']==(NOW+timedelta(days=38)).isoformat()
    bundle=dict(received_at=NOW.isoformat(),positions_complete=True,event_calendar={'status':'EVENT_RISK'},
        contracts=[dict(received_at=NOW.isoformat(),greeks_received_at=NOW.isoformat())])
    campaign.observe(store,'NIFTY',bundle,NOW)
    campaign.observe(store,'NIFTY',bundle,NOW)
    assert campaign.public(store,NOW)['observed_market_days']=={'NIFTY':1,'SENSEX':0}
    campaign.observe(store,'SENSEX',bundle,NOW+timedelta(days=2))
    assert campaign.public(store,NOW+timedelta(days=2))['observed_market_days']['SENSEX']==0
    complete=campaign.public(store,NOW+timedelta(days=38))
    assert complete['status']=='COLLECTION_ENDED_REVIEW_REQUIRED'
    assert complete['strategy_validation']=='NOT_PROVEN' and not complete['execution_authorized']
    assert not intent.intent(store)['enabled'] and store.meta('premium-execution-activation') is None
    assert complete['iv_history_requirement']==252
