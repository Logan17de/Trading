"""Synthetic provider/calendar fixtures in isolated journals; never live proof."""
import copy
from datetime import datetime, timedelta

import pytest

from nifty_engine.agent_engine import iv_history as iv
from nifty_engine.agent_engine.contracts import IST, identity
from nifty_engine.agent_engine.store import Store
from nifty_engine.agent_engine import premium_strategy, strategy_controls

NOW=datetime(2026,10,7,11,tzinfo=IST)


def dataset(index='NIFTY'):
    days=[];day=NOW.date()-timedelta(days=1)
    while len(days)<252:
        if day.weekday()<5:days.append(day.isoformat())
        day-=timedelta(days=1)
    days.sort()
    return dict(format=iv.FORMAT,
        series=dict(provider='SYNTHETIC_TEST_ONLY',methodology='TEST_30D_V1',index=index,
                    tenor_days=30,unit='annualized_percent'),
        calendar=dict(exchange='NSE' if index=='NIFTY' else 'BSE',source='SYNTHETIC_TEST_CALENDAR',
            coverage_start=days[0],coverage_end=NOW.date().isoformat(),sessions=days+[NOW.date().isoformat()],
            session_closes={d:d+'T15:30:00+05:30' for d in days+[NOW.date().isoformat()]}),
        observations=[dict(day=d,value=10+i/10,observed_at=d+'T15:30:00+05:30',source_sha256='a'*64)
                      for i,d in enumerate(days)])


def install(store,data):
    return iv.install(store,data,NOW,reviewed_dataset_sha256=identity(data),
        reviewed_calendar_sha256=identity(data['calendar']))


def point(data,**changes):
    return dict(series=copy.deepcopy(data['series']),value=30,observed_at=NOW.isoformat(),
                source_sha256='b'*64,**changes)


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
def test_real_path_requires_exact_review_and_current_method_then_survives_restart(tmp_path,index):
    store=Store(tmp_path/'j.sqlite3');data=dataset(index)
    check=iv.validate(data,NOW)
    assert check['sessions']==252 and iv.features(store,index,NOW)=={}
    with pytest.raises(ValueError,match='EXACT_DATASET'):
        iv.install(store,data,NOW,reviewed_dataset_sha256='0'*64,reviewed_calendar_sha256=check['calendar_sha256'])
    assert store.meta(iv.HISTORY+index) is None
    install(store,data)
    assert not iv.public(store,index,NOW)['current_available']
    iv.current(store,point(data),NOW)
    features=iv.features(Store(store.path),index,NOW)
    assert features['iv_percentile']['value']==pytest.approx(201/252*100)
    assert features['iv30']['value']==30
    assert features['iv30']['observed_at']==NOW.isoformat()
    assert features['iv_percentile']['history_end_at']==data['observations'][-1]['observed_at']
    assert iv.features(store,index,NOW+timedelta(seconds=16))=={}
    assert store.meta('premium-execution-activation') is None
    assert not premium_strategy.intent(store)['enabled']
    assert not any(strategy_controls.read(store)['enabled'].values())


@pytest.mark.parametrize('fault',['missing','duplicate','future','unit','index','method','time','hash','calendar','stale'])
def test_bad_history_never_installs(tmp_path,fault):
    store=Store(tmp_path/'j.sqlite3');data=dataset()
    if fault=='missing':data['observations'].pop(100)
    if fault=='duplicate':data['observations'][100]=data['observations'][99]
    if fault=='future':data['observations'][-1]['day']=NOW.date().isoformat()
    if fault=='unit':data['series']['unit']='decimal_volatility'
    if fault=='index':data['series']['index']='SENSEX'
    if fault=='method':data['series']['methodology']='GROWW_ATM_VARIANCE30_RESEARCH_V1'
    if fault=='time':data['observations'][0]['observed_at']=data['observations'][0]['day']+'T09:30:00+05:30'
    if fault=='hash':data['observations'][0]['source_sha256']='missing'
    if fault=='calendar':data['calendar']['coverage_end']='2026-10-06'
    if fault=='stale':data['calendar']['sessions'].remove('2026-10-06');data['observations'].pop();data['calendar']['sessions'].insert(0,'2025-01-01');data['observations'].insert(0,dict(data['observations'][0],day='2025-01-01'))
    with pytest.raises(ValueError):install(store,data)
    assert store.meta(iv.HISTORY+'NIFTY') is None


@pytest.mark.parametrize('fault',['index','method','provider','stale','future','tampered_history'])
def test_current_must_match_exact_reviewed_series(tmp_path,fault):
    store=Store(tmp_path/'j.sqlite3');data=dataset();install(store,data);p=point(data)
    if fault=='index':p['series']['index']='SENSEX'
    if fault=='method':p['series']['methodology']='OTHER'
    if fault=='provider':p['series']['provider']='OTHER'
    if fault=='stale':p['observed_at']=(NOW-timedelta(seconds=16)).isoformat()
    if fault=='future':p['observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    if fault=='tampered_history':
        saved=store.meta(iv.HISTORY+'NIFTY');saved['data']['observations'][1]['value']=100
        store.set_meta(iv.HISTORY+'NIFTY',saved)
        with pytest.raises(ValueError,match='REVIEWED_HISTORY_CHANGED'):iv.current(store,p,NOW)
        assert iv.features(store,'NIFTY',NOW)=={}
        return
    with pytest.raises(ValueError):iv.current(store,p,NOW)
    assert iv.features(store,'NIFTY',NOW)=={}


def test_production_adapter_rejects_forged_bundle_iv_without_reviewed_history(tmp_path):
    from test_report_execution import ReportSession
    s=ReportSession(tmp_path);s.gate.matched_iv_required=True
    s.tick()
    assert not s.broker.writes and s.executor._state().get('phase','IDLE')=='IDLE'
    reasons=s.journal.store.meta('report-execution-preparation')['reasons']
    assert 'NIFTY_REVIEWED_MATCHED_IV_HISTORY_AND_CURRENT_REQUIRED' in reasons


@pytest.mark.parametrize('index,regime,sid',[('NIFTY','BULLISH','bull_put'),('SENSEX','BEARISH','bear_call'),('NIFTY','RANGE','iron_condor')])
def test_reviewed_matching_import_connects_to_all_three_guarded_routes(tmp_path,index,regime,sid):
    from test_report_execution import ReportSession
    s=ReportSession(tmp_path,index,regime);s.gate.matched_iv_required=True
    data=dataset(index)
    # Exact source hashes in isolated synthetic fixtures, not production proof.
    iv.install(s.journal.store,data,s.now,reviewed_dataset_sha256=identity(data),
               reviewed_calendar_sha256=identity(data['calendar']))
    p=point(data);p['observed_at']=s.now.isoformat();iv.current(s.journal.store,p,s.now)
    s.journal.store.set_meta('report-strategy-evidence-'+index,s.data)
    prepared=s.executor.report.preparation(s.observation())
    assert prepared['selected']['strategy']==sid
    assert s.executor.report.validate(prepared['selected'],s.observation())
    assert not s.broker.writes
    p['value']=31;iv.current(s.journal.store,p,s.now)
    with pytest.raises(ValueError,match='REPREPARE_CHANGED_MATCHED_IV'):
        s.executor.report.validate(prepared['selected'],s.observation())
    s.now+=timedelta(seconds=16)
    assert s.executor.report.preparation(s.observation())['selected'] is None


def test_readiness_distinguishes_three_installed_routes_and_calendar(tmp_path):
    from test_report_execution import ReportSession, CFG
    from nifty_engine.agent_engine.strategy_readiness import assess
    s=ReportSession(tmp_path)
    statuses=assess(s.journal.store,CFG,s.gate,s.observation(),s.now)
    assert all(statuses[sid]['code_implemented'] and not statuses[sid]['live_ready']
               for sid in ('bull_put','bear_call','iron_condor'))
    assert statuses['calendar']['status']=='RESEARCH_ONLY' and not statuses['calendar']['code_implemented']
    assert all('REVIEWED_MATCHED_IV_HISTORY_AND_CURRENT_REQUIRED' in r['blockers']
               for s in statuses.values() for r in s['indices'].values())


def test_exceptional_session_uses_reviewed_actual_close_not_regular_hours(tmp_path):
    store=Store(tmp_path/'j.sqlite3');data=dataset();day=data['observations'][0]['day']
    data['calendar']['session_closes'][day]=day+'T19:30:00+05:30'
    with pytest.raises(ValueError,match='DATED_CLOSE_IV'):install(store,data)
    data['observations'][0]['observed_at']=day+'T19:28:00+05:30'
    assert install(store,data)['status']=='REVIEWED_IMPORT'
