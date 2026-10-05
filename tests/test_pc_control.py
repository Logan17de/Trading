import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.dashboard import DashboardCollector, DashboardState, view_model
from nifty_engine.agent_engine.pc_control import (JST, PcJournal, PcMonitor, candidate_levels,
    collection_window, in_window, settings, trail_update, validate_analysis)
from nifty_engine.agent_engine.oracle_trailing import OracleTrailingUpdater

NOW = datetime(2026,10,5,13,30,tzinfo=JST)
SYMBOL = 'NIFTY26O0823000CE'
ROOT = Path(__file__).parents[1]


def config():
    return json.loads((ROOT/'config/pc_app.example.json').read_text())


def owner_protocol():
    return json.loads((ROOT/'config/owner_strategies.json').read_text())


def create_root(tmp_path):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/pc_app.example.json').write_text(json.dumps(config()))
    (tmp_path/'config/owner_strategies.json').write_text(json.dumps(owner_protocol()))
    (tmp_path/'.trader-paused').touch()
    return tmp_path


def order(ref,broker,*,side='BUY',quantity=65,filled=65,status='EXECUTED'):
    return dict(trading_symbol=SYMBOL,segment='FNO',exchange='NSE',quantity=quantity,
        filled_quantity=filled,transaction_type=side,order_status=status,
        order_reference_id=ref,groww_order_id=broker)


def register_owned(journal):
    entry=journal.reserve('only-slot',SYMBOL,'BUY',65)
    journal.acknowledge(entry,'private-entry-id')
    sl=journal.reserve('only-slot',SYMBOL,'SELL',65)
    journal.acknowledge(sl,'private-sl-id')
    rows=[order(entry,'private-entry-id'),order(sl,'private-sl-id',side='SELL',filled=0,status='TRIGGER_PENDING')]
    ownership=journal.ownership(rows,[dict(trading_symbol=SYMBOL,quantity=65)],complete=True)
    assert ownership[SYMBOL]=='ENGINE_VERIFIED'
    return entry,sl,rows


@pytest.mark.parametrize('hhmm,expected,research',[
    ('12:54',False,True),('12:55',True,True),('12:59',False,False),('13:00',True,False),
    ('18:14',True,False),('18:15',False,False),('18:16',False,True)])
def test_japan_window_is_exact_and_timezone_independent(hhmm,expected,research):
    h,m=map(int,hhmm.split(':'))
    now=NOW.replace(hour=h,minute=m)
    assert in_window(now,research=research) is expected
    assert in_window(now.astimezone(timezone.utc),research=research) is expected
    assert not in_window(now.replace(day=10),research=research)  # Saturday
    assert collection_window(NOW.replace(hour=12,minute=50))


def test_owner_pc_policy_and_strict_nonsecret_config():
    assert settings(config())['expiry_rule']=='SKIP_ACTUAL_EXPIRY_DAY'
    for field,value in [('lots',2),('one_active_slot',False),('action_window_jst',['13:00','19:00']),
                        ('hedge_reference','ABOVE_SHORT_CALL'),('api_key','SECRET')]:
        bad=config();bad[field]=value
        with pytest.raises(ValueError):settings(bad)


def test_manual_prefix_cannot_claim_engine_ownership_and_protection_survives_restart(tmp_path):
    journal=PcJournal(tmp_path/'journal.sqlite3')
    rows=[order('GT123456789012345678','manual-id')]
    assert journal.ownership(rows,[dict(trading_symbol=SYMBOL,quantity=65)],complete=True)[SYMBOL]=='MANUAL_OR_UNKNOWN_PROTECTED'
    ref=journal.reserve('only-slot',SYMBOL,'BUY',65);journal.acknowledge(ref,'engine-id')
    other=PcJournal(tmp_path/'journal.sqlite3')
    assert other.ownership([order(ref,'engine-id')],[dict(trading_symbol=SYMBOL,quantity=65)],complete=True)[SYMBOL]=='MANUAL_OR_UNKNOWN_PROTECTED'


def test_manual_roundtrip_on_same_contract_freezes_engine_even_when_net_quantity_matches(tmp_path):
    journal=PcJournal(tmp_path/'journal.sqlite3');entry,sl,rows=register_owned(journal)
    rows += [order('manual-a','m-a'),order('manual-b','m-b',side='SELL')]
    assert journal.ownership(rows,[dict(trading_symbol=SYMBOL,quantity=65)],complete=True)[SYMBOL]=='MANUAL_OR_UNKNOWN_PROTECTED'
    with pytest.raises(ValueError):journal.require_owned(entry,SYMBOL,'BUY',65)


def test_one_slot_and_pending_orders_are_not_fills(tmp_path):
    journal=PcJournal(tmp_path/'journal.sqlite3')
    ref=journal.reserve('only-slot',SYMBOL,'BUY',65);journal.acknowledge(ref,'engine-id')
    with pytest.raises(ValueError):journal.reserve('second-slot','SENSEX26O0875000CE','SELL',20)
    owned=journal.ownership([order(ref,'engine-id',filled=0,status='OPEN')],[],complete=True)
    assert owned[SYMBOL]=='ENGINE_PENDING_VERIFIED'
    with pytest.raises(ValueError):journal.require_owned(ref,SYMBOL,'BUY',1)


def test_incomplete_read_blocks_temporarily_but_never_adopts_unexplained_quantity(tmp_path):
    journal=PcJournal(tmp_path/'journal.sqlite3');entry,sl,rows=register_owned(journal)
    pos=[dict(trading_symbol=SYMBOL,quantity=65)]
    assert journal.ownership(rows,pos,complete=False)[SYMBOL]=='MANUAL_OR_UNKNOWN_PROTECTED'
    assert journal.ownership(rows,pos,complete=True)[SYMBOL]=='ENGINE_VERIFIED'
    assert journal.ownership(rows,[dict(trading_symbol=SYMBOL,quantity=130)],complete=True)[SYMBOL]=='MANUAL_OR_UNKNOWN_PROTECTED'


def params(side='BUY'):
    return dict(symbol=SYMBOL,position_side=side,quantity=65,current_trigger=95 if side=='BUY' else 105,
        current_limit=94.9 if side=='BUY' else 105.1,best_premium=100,distance=5,step=1,
        tick_size=.05,limit_gap=.1,target=130 if side=='BUY' else 70)


@pytest.mark.parametrize('side,price,trigger,limit',[('BUY',110,105,104.9),('SELL',90,95,95.1)])
def test_trailing_moves_only_in_protective_direction_with_valid_tick_prices(side,price,trigger,limit):
    p=params(side);plan=trail_update(p,price)
    assert plan['status']=='SL_UPDATE_PROPOSAL' and plan['trigger_price']==trigger and plan['price']==limit
    assert plan['transaction_type']==('SELL' if side=='BUY' else 'BUY')
    assert not plan['broker_write']
    assert trail_update(p,100)['status']=='UNCHANGED'
    assert trail_update(p,90 if side=='BUY' else 110)['status']=='STOP_OR_TARGET_RECONCILIATION_REQUIRED'
    assert trail_update(p,140 if side=='BUY' else 60)['status']=='STOP_OR_TARGET_RECONCILIATION_REQUIRED'
    for field,value in [('step',0),('distance',-1),('tick_size',float('nan')),('quantity',True)]:
        bad=dict(p);bad[field]=value
        with pytest.raises(ValueError):trail_update(bad,price)


def updater_fixture(tmp_path):
    journal=PcJournal(tmp_path/'journal.sqlite3');entry,sl,rows=register_owned(journal)
    rule=dict(params(),entry_reference=entry,sl_reference=sl)
    observation=dict(complete=True,ownership='ENGINE_VERIFIED',received_at=NOW.isoformat(),symbol=SYMBOL,
        position_quantity=65,sl_reference=sl,sl_broker_id='private-sl-id')
    detail=dict(rows[1],order_type='SL',price=94.9,trigger_price=95)
    calls=[]
    def get(**kwargs):return copy.deepcopy(detail)
    def modify(**kwargs):
        calls.append(kwargs);detail.update(price=kwargs['price'],trigger_price=kwargs['trigger_price'])
        return dict(groww_order_id='private-sl-id',order_status='TRIGGER_PENDING')
    broker=SimpleNamespace(get_order_detail=get,modify_order=modify)
    return journal,rule,observation,broker,calls


def test_oracle_sl_update_confirms_provider_state_and_advances_only_after_readback(tmp_path):
    journal,rule,obs,broker,calls=updater_fixture(tmp_path)
    updater=OracleTrailingUpdater(journal,broker,tmp_path/'paused',deployed=True,clock=lambda:NOW)
    out=updater.update(rule,obs,110,NOW)
    assert out['status']=='BROKER_SL_UPDATE_CONFIRMED' and out['provider_accepted'] and not out['filled']
    assert len(calls)==1 and journal.store.meta('trailing-'+rule['sl_reference'])['trigger_price']==105
    assert updater.update(rule,obs,109,NOW)['status']=='UNCHANGED' and len(calls)==1
    assert updater.update(rule,obs,112,NOW)['trigger_price']==107 and len(calls)==2


@pytest.mark.parametrize('block',['paused','undeployed','manual','stale','outside','different-id','partial'])
def test_sl_updater_never_writes_for_blocked_or_manual_observations(tmp_path,block):
    journal,rule,obs,broker,calls=updater_fixture(tmp_path)
    pause=tmp_path/'paused'
    if block=='paused':pause.touch()
    if block=='manual':obs['ownership']='MANUAL_OR_UNKNOWN_PROTECTED'
    if block=='stale':obs['received_at']=(NOW-timedelta(seconds=11)).isoformat()
    if block=='different-id':obs['sl_broker_id']='manual-other-id'
    if block=='partial':obs['position_quantity']=1
    updater=OracleTrailingUpdater(journal,broker,pause,deployed=block!='undeployed',clock=lambda:NOW)
    with pytest.raises(ValueError):updater.update(rule,obs,110,NOW.replace(hour=19) if block=='outside' else NOW)
    assert calls==[]


def test_timeout_never_retries_a_broker_write_and_does_not_advance_stop(tmp_path):
    journal,rule,obs,broker,calls=updater_fixture(tmp_path)
    def timeout(**kwargs):calls.append(kwargs);raise TimeoutError('PRIVATE error')
    broker.modify_order=timeout
    updater=OracleTrailingUpdater(journal,broker,tmp_path/'paused',deployed=True,clock=lambda:NOW)
    out=updater.update(rule,obs,110,NOW)
    assert out['status']=='BROKER_SL_RECONCILIATION_REQUIRED'
    assert journal.store.meta('trailing-'+rule['sl_reference']) is None
    assert updater.update(rule,obs,110,NOW)['broker_write'] is False and len(calls)==1
    assert updater.update(rule,obs,112,NOW)['broker_write'] is False and len(calls)==1
    assert 'PRIVATE' not in json.dumps(out)


def test_provider_acceptance_without_matching_readback_never_advances_stop(tmp_path):
    journal,rule,obs,broker,calls=updater_fixture(tmp_path)
    def accept_only(**kwargs):
        calls.append(kwargs)
        return dict(groww_order_id='private-sl-id',order_status='TRIGGER_PENDING')
    broker.modify_order=accept_only
    result=OracleTrailingUpdater(journal,broker,tmp_path/'paused',deployed=True,clock=lambda:NOW).update(rule,obs,110,NOW)
    assert result['status']=='BROKER_SL_RECONCILIATION_REQUIRED' and len(calls)==1
    assert journal.store.meta('trailing-'+rule['sl_reference']) is None


def test_no_sl_write_when_broker_read_crosses_action_cutoff(tmp_path):
    journal,rule,obs,broker,calls=updater_fixture(tmp_path)
    start=NOW.replace(hour=18,minute=14,second=58)
    obs['received_at']=start.isoformat()
    updater=OracleTrailingUpdater(journal,broker,tmp_path/'paused',deployed=True,
        clock=lambda:start.replace(minute=15,second=0))
    result=updater.update(rule,obs,110,start)
    assert calls==[] and result['broker_write_may_have_occurred'] is False
    assert journal.store.meta('trailing-'+rule['sl_reference']) is None


def snapshot(expiry_status='CONFIRMED_CURRENT_MASTER',expiry_day='2026-10-08'):
    values=[100,110,130,105,100,90,80,95,100,110]
    bars=[dict(at=(NOW-timedelta(minutes=(len(values)-i)*5)).isoformat(),open=v,high=v+1,low=v-1,close=v) for i,v in enumerate(values)]
    return dict(finished_at=NOW.isoformat(),status='READ_ONLY_DATA_AVAILABLE',orders_status='AVAILABLE',
        probes={i+'_quote':dict(ok=True,value={'last_price':100},received_at=NOW.isoformat()) for i in ('NIFTY','SENSEX')},
        charts={i:dict(candles=bars,context_candles=bars) for i in ('NIFTY','SENSEX')},
        expiry_evidence={i:dict(day_jst='2026-10-05',status=expiry_status,expiries=[expiry_day]) for i in ('NIFTY','SENSEX')})


def test_expiry_uses_actual_holiday_shifted_date_and_unknown_blocks_entry(tmp_path):
    monitor=PcMonitor(create_root(tmp_path))
    out=monitor.tick(snapshot(expiry_day='2026-10-05'),owner_protocol(),NOW)
    assert out['expiry_check']['NIFTY']['is_expiry_day'] is True  # Monday, no weekday assumption
    assert out['everyday']['status']=='SKIP_EXPIRY_DAY'
    assert out['everyday']['hedge_reference']=='INDEX_ATM'
    out=monitor.tick(snapshot(expiry_status='UNKNOWN_BLOCKED'),owner_protocol(),NOW)
    assert out['everyday']['status']=='EXPIRY_EVIDENCE_REQUIRED'
    out=monitor.tick(snapshot(),owner_protocol(),NOW)
    assert out['expiry_check']['NIFTY']['is_expiry_day'] is False
    assert out['everyday']['minimum_short_call_strike']==500
    assert not out['execution_enabled'] and 'ATM_HEDGE_PAYOFF_REVIEW_REQUIRED' in out['blockers']


def test_everyday_entry_keeps_1315_boundary_inside_barrier_window(tmp_path):
    monitor=PcMonitor(create_root(tmp_path))
    for minute,status in [(14,'BEFORE_EVERYDAY_WINDOW'),(15,'ATM_HEDGE_REVIEW_ONLY')]:
        at=NOW.replace(minute=minute);value=snapshot();value['finished_at']=at.isoformat()
        for probe in value['probes'].values():probe['received_at']=at.isoformat()
        result=monitor.tick(value,owner_protocol(),at)
        assert result['window_open'] and result['everyday']['status']==status


def result_for(request):
    return dict(request_id=request['request_id'],snapshot_id=request['snapshot_id'],decision='PROPOSE_REVIEW',
        support=[79],resistance=[131],rule=None,reason='LEVELS_IDENTIFIED')


def test_daily_codex_requests_deduplicate_across_restart_and_touch_is_separate(tmp_path):
    root=create_root(tmp_path);monitor=PcMonitor(root)
    monitor.tick(snapshot(),owner_protocol(),NOW)
    assert len(monitor.journal.store.read('SELECT * FROM pc_requests'))==2
    monitor=PcMonitor(root);monitor.tick(snapshot(),owner_protocol(),NOW)
    assert len(monitor.journal.store.read('SELECT * FROM pc_requests'))==2
    assert monitor.analyze_once(result_for,NOW,clock=lambda:NOW)=='ANALYZED'
    monitor.tick(snapshot(),owner_protocol(),NOW)
    updated=snapshot();updated['finished_at']=(NOW+timedelta(seconds=5)).isoformat()
    updated['probes']['NIFTY_quote']['value']['last_price']=132
    monitor.tick(updated,owner_protocol(),NOW+timedelta(seconds=5))
    assert len(monitor.journal.store.read("SELECT * FROM pc_requests WHERE id LIKE 'touch-%'"))==1
    assert monitor.analyze_once(result_for,NOW.replace(hour=19),clock=lambda:NOW)=='OUTSIDE_WINDOW'


@pytest.mark.parametrize('problem',['blocked','stale-snapshot','stale-quotes','future-quotes'])
def test_monitor_never_enqueues_analysis_from_failed_or_stale_observations(tmp_path,problem):
    monitor=PcMonitor(create_root(tmp_path));value=snapshot()
    if problem=='blocked':value['status']='BLOCKED'
    if problem=='stale-snapshot':value['finished_at']=(NOW-timedelta(seconds=16)).isoformat()
    if problem in ('stale-quotes','future-quotes'):
        for probe in value['probes'].values():
            probe['received_at']=(NOW+timedelta(seconds=-16 if problem=='stale-quotes' else 1)).isoformat()
    monitor.tick(value,owner_protocol(),NOW)
    assert monitor.journal.store.read('SELECT * FROM pc_requests')==[]


def test_resident_monitor_recovers_after_invalid_local_configuration(tmp_path,monkeypatch):
    state=DashboardState(create_root(tmp_path),background=True)
    monkeypatch.setattr(state,'refresh',lambda:None)
    path=tmp_path/'config/pc_app.example.json';path.write_text('{invalid')
    class StopAfterRepair:
        waits=0
        def is_set(self):return self.waits==2
        def wait(self,seconds):
            self.waits+=1
            path.write_text(json.dumps(config()))
    state.stop_event=StopAfterRepair()
    state._monitor_loop()
    assert state.stop_event.waits==2 and state.analysis_error is None


def test_authentication_failure_backoff_prevents_repeated_reader_starts(tmp_path):
    state=DashboardState(create_root(tmp_path),background=True)
    state.retry_after=float('inf')
    assert state.refresh()=='RETRY_BACKOFF' and not state.refreshing


def test_analysis_rejects_hallucinated_levels_identity_stale_result_and_unapproved_rule(tmp_path):
    journal=PcJournal(tmp_path/'journal.sqlite3')
    body=dict(index='NIFTY',candidate_levels=[79,131],approved_parameters={k:None for k in
        ('trailing_distance_rupees','trailing_step_rupees','target_premium_rupees')})
    journal.enqueue('test',body,NOW);request=journal.claim(NOW);result=result_for(request)
    validate_analysis(result,request,NOW)
    for key,val in [('support',[73200]),('request_id','other'),('resistance',[float('nan')])]:
        bad=copy.deepcopy(result);bad[key]=val
        with pytest.raises(ValueError):validate_analysis(bad,request,NOW)
    rule=dict(index='NIFTY',comparison='BELOW',level=79,action='REVIEW_LONG_PUT',lots=1,
        valid_from=NOW.replace(hour=13,minute=0).isoformat(),expires_at=NOW.replace(hour=18,minute=15).isoformat(),
        trailing_distance_rupees=None,trailing_step_rupees=None,target_premium_rupees=None)
    validate_analysis(dict(result,rule=rule),request,NOW)
    for key,val in [('expires_at',NOW.replace(hour=19).isoformat()),('lots',2),('trailing_distance_rupees',5)]:
        bad=copy.deepcopy(rule);bad[key]=val
        with pytest.raises(ValueError):validate_analysis(dict(result,rule=bad),request,NOW)


@pytest.mark.parametrize('near_close',[False,True])
def test_result_freshness_and_action_window_use_time_after_codex_finishes(tmp_path,near_close):
    monitor=PcMonitor(create_root(tmp_path))
    start=NOW.replace(hour=18,minute=14) if near_close else NOW
    end=start+timedelta(minutes=2) if near_close else start+timedelta(seconds=301)
    body=dict(index='NIFTY',candidate_levels=[79,131],approved_parameters={k:None for k in
        ('trailing_distance_rupees','trailing_step_rupees','target_premium_rupees')})
    monitor.journal.enqueue('late-result',body,start)
    events=[]
    def runner(request):events.append('runner');return result_for(request)
    def finished():events.append('finished');return end
    assert monitor.analyze_once(runner,start,clock=finished)=='FAILED'
    assert events==['runner','finished'] and monitor.journal.store.meta('levels-NIFTY') is None


def test_collector_marks_manual_positions_and_checks_expiries_without_weekday_inference(tmp_path):
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':100},
        get_order_list=lambda **kwargs:{'order_list':[]},get_positions_for_user=lambda **kwargs:{'positions':[dict(
            trading_symbol=SYMBOL,exchange='NSE',segment='FNO',quantity=65)]},
        get_expiries=lambda **kwargs:{'expiries':['2026-10-05'] if kwargs['underlying_symbol']=='NIFTY' else ['2026-10-08']},
        get_historical_candles=lambda **kwargs:{'interval_in_minutes':5,'candles':[]})
    market=SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None))
    collector=DashboardCollector(market,clock=lambda:NOW,background_history=False,
        metadata_loader=lambda req:{},journal=PcJournal(tmp_path/'journal.sqlite3'),
        calendar_loader=lambda:{'NIFTY':['2026-10-05'],'SENSEX':['2026-10-08']})
    out=collector.sample()
    assert out['expiry_evidence']['NIFTY']['expiries']==['2026-10-05']
    assert out['ordered_options'][0]['ownership']=='MANUAL_OR_UNKNOWN_PROTECTED'
    browser=view_model(out,owner_protocol(),now=NOW)
    assert browser['chart_style']=='LINE' and not browser['order_capability']
    assert 'private-' not in json.dumps(browser)


@pytest.mark.parametrize('extra,expected',[("2031-06-24","CONFIRMED_CURRENT_MASTER"),("2026-10-06","UNKNOWN_BLOCKED")])
def test_calendar_ignores_other_years_and_rejects_current_month_disagreement(tmp_path,extra,expected):
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':100},
        get_order_list=lambda **kwargs:{'order_list':[]},get_positions_for_user=lambda **kwargs:{'positions':[]},
        get_expiries=lambda **kwargs:{'expiries':['2026-10-05']},
        get_historical_candles=lambda **kwargs:{'interval_in_minutes':5,'candles':[]})
    collector=DashboardCollector(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),
        clock=lambda:NOW,background_history=False,journal=PcJournal(tmp_path/'journal.sqlite3'),
        calendar_loader=lambda:{index:['2026-10-05',extra] for index in ('NIFTY','SENSEX')})
    evidence=collector.sample()['expiry_evidence']['NIFTY']
    assert evidence['status']==expected
    assert evidence['expiries']==(['2026-10-05'] if expected=='CONFIRMED_CURRENT_MASTER' else [])


def test_calendar_distant_horizons_do_not_certify_unconfirmed_future_dates(tmp_path):
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':100},
        get_order_list=lambda **kwargs:{'order_list':[]},get_positions_for_user=lambda **kwargs:{'positions':[]},
        get_expiries=lambda **kwargs:{'expiries':['2026-10-08','2026-11-05']},
        get_historical_candles=lambda **kwargs:{'interval_in_minutes':5,'candles':[]})
    collector=DashboardCollector(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),
        clock=lambda:NOW,background_history=False,journal=PcJournal(tmp_path/'journal.sqlite3'),
        calendar_loader=lambda:{index:['2026-10-08','2026-11-05','2026-11-12','2031-06-26'] for index in ('NIFTY','SENSEX')})
    evidence=collector.sample()['expiry_evidence']['SENSEX']
    assert evidence['status']=='CONFIRMED_CURRENT_MASTER' and evidence['expiries']==['2026-10-08']
    assert evidence['comparison_scope']=='CURRENT_MONTH_AND_NEAREST_LISTED_EXPIRY'


def test_calendar_verifies_next_year_when_current_year_has_no_remaining_expiry(tmp_path):
    years=[]
    def expiries(**kwargs):
        years.append(kwargs['year'])
        return {'expiries':[] if kwargs['year']==2026 else ['2027-01-05']}
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':100},get_expiries=expiries,
        get_order_list=lambda **kwargs:{'order_list':[]},get_positions_for_user=lambda **kwargs:{'positions':[]},
        get_historical_candles=lambda **kwargs:{'interval_in_minutes':5,'candles':[]})
    collector=DashboardCollector(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),
        clock=lambda:NOW.replace(month=12,day=31),background_history=False,journal=PcJournal(tmp_path/'journal.sqlite3'),
        calendar_loader=lambda:{index:['2027-01-05','2031-06-26'] for index in ('NIFTY','SENSEX')})
    evidence=collector.sample()['expiry_evidence']['NIFTY']
    assert years==[2026,2027,2026,2027]
    assert evidence['status']=='CONFIRMED_CURRENT_MASTER' and evidence['expiries']==['2027-01-05']
