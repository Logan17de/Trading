import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.contracts import IST
from nifty_engine.agent_engine.market_check import allowed_request, call_samples, quote_summary, readonly_transport
from nifty_engine.agent_engine.owner_study import (
    FORMAT, collect_history, evaluate_history, everyday_direction_review, everyday_reference,
    normalize_candles, probability, record_market, review_everyday, validate_protocol, value_at,
)
from nifty_engine.agent_engine.spread_review import compare


def protocol():
    return json.loads((Path(__file__).parents[1]/'config/owner_strategies.json').read_text())


def dataset():
    bars=[]
    expiries=[]
    for n in range(10):
        day=date(2026,8,3)+timedelta(days=n)
        expiries.append(str(day))
        # Synthetic fixture only; both calendar and prices are invented.
        for hhmm,price in [('09:29',75000),('14:14',75000),('15:14',75000),
                           *[(f'15:{minute}',75600) for minute in range(20,30)],('15:35',75100)]:
            at=datetime.fromisoformat(f'{day}T{hhmm}:00').replace(tzinfo=IST)
            bars.append({'at':at.isoformat(),'open':price,'high':price,'low':price,'close':price})
    return {'format':FORMAT,'index':'SENSEX','synthetic':True,'value_kind':'BROKER_INDEX_CANDLE_UNCLASSIFIED',
        'timestamp_semantics_verified':False,'expiries':expiries,'expiries_complete':True,'candles':bars,'failures':[]}


def test_no_probability_from_zero_or_unverified_evidence():
    assert probability(0,0)['rate'] is None
    interval=probability(9,10)
    assert .59 < interval['wilson_95'][0] < .60
    assert interval['wilson_95'][1] > .98
    with pytest.raises(ValueError): probability(11,10)
    result=evaluate_history(dataset(),protocol())
    assert result['partitions']['HOLDOUT']['candidate_series_events']['trials']==3
    assert result['strategy_reversal_probability'] is None
    assert result['probability_of_net_option_profit'] is None
    assert 'INDICATIVE_AUCTION_SERIES_NOT_VERIFIED' in result['blockers']
    assert 'SYNTHETIC_DATA' in result['blockers']
    assert result['execution_enabled'] is False


def test_japan_times_and_no_future_minute_close():
    raw=dataset()
    day=date(2026,8,3)
    rows={r['at']:r for r in raw['candles'] if r['at'].startswith(str(day))}
    assert value_at(rows,day,'15:15')==75000
    assert value_at(rows,day,'15:20') is None
    event=evaluate_history(raw,protocol())['expiry_events'][0]
    assert event['signal_available_at'].endswith('15:21:00+05:30')
    first=datetime.fromisoformat(event['signal_available_at'])
    from nifty_engine.agent_engine.owner_study import JST
    assert first.astimezone(JST).strftime('%H:%M')=='18:51'
    missing=deepcopy(raw)
    missing['candles']=[r for r in missing['candles'] if not r['at'].endswith('15:27:00+05:30')]
    result=evaluate_history(missing,protocol())
    assert result['coverage']['excluded']['EXPIRY_WINDOW_INCOMPLETE']==10
    assert not result['expiry_events']


def test_corrupt_duplicate_history_and_protocol_drift_rejected():
    value=dataset()
    value['candles'].append(value['candles'][-1])
    with pytest.raises(ValueError,match='chronological'): evaluate_history(value,protocol())
    wrong=protocol()
    wrong['expiry_reversal']['minimum_upward_jump_points']=50
    with pytest.raises(ValueError,match='threshold'): evaluate_history(dataset(),wrong)
    with pytest.raises(ValueError,match='OHLC'):
        normalize_candles({'interval_in_minutes':1,'candles':[['2026-09-01T09:15:00',100,99,90,100]]})


def test_history_is_bounded_and_partial_failures_remain_visible():
    calls=[]
    def read(**kwargs):
        calls.append(kwargs)
        if len(calls)==2: raise TimeoutError('PRIVATE_TOKEN')
        return {'interval_in_minutes':1,'candles':[[kwargs['start_time'].replace(' ','T'),1,1,1,1]]}
    market=SimpleNamespace(groww=SimpleNamespace(get_expiries=lambda **k:{'expiries':['2026-08-06']},
                           get_historical_candles=read),limiter=SimpleNamespace(wait=lambda:None))
    result=collect_history(market,'SENSEX',date(2026,8,3),date(2026,8,16))
    assert len(calls)==2 and len(result['candles'])==1 and len(result['failures'])==1
    assert 'PRIVATE_TOKEN' not in json.dumps(result)
    assert not allowed_request('GET','https://api.groww.in/v1/historical/candles')
    assert allowed_request('GET','https://api.groww.in/v1/historical/candles',history=True)
    assert not allowed_request('POST','https://api.groww.in/v1/order/create',history=True)


def test_call_sampling_is_fixed_bounded_and_sanitized():
    chain={'underlying_ltp':75000,'strikes':{str(75000+n*100):{'CE':{
        'trading_symbol':f'SENSEX26O0175{n:03}CE','greeks':{'delta':.4,'theta':-1,'secret':'PRIVATE'}}} for n in range(25)}}
    calls=call_samples(chain)
    assert [r['listed_strike_offset'] for r in calls]==[0,10,20]
    assert 'PRIVATE' not in json.dumps(calls)
    assert all(r['greeks_at'] is None for r in calls)


def test_observed_depth_and_second_timestamps_preserve_unknown_book_time():
    now=datetime(2026,9,30,15,6,tzinfo=IST)
    result=quote_summary({'last_trade_time':now.timestamp()-4,'bid_price':None,'offer_price':None,
        'depth':{'buy':[{'price':200.7,'quantity':180},{'price':200.75,'quantity':80}],
                 'sell':[{'price':201.4,'quantity':200},{'price':201.35,'quantity':180}]}},now)
    assert result['bid_price']==200.75 and result['offer_price']==201.35
    assert result['last_trade_age_seconds']==4 and result['trade_timestamp_unit']=='SECONDS'
    assert result['book_at'] is None and result['greeks_at'] is None
    assert result['book_sources']=={'buy':'DEPTH','sell':'DEPTH'}
    assert result['crossed_book'] is False


def test_recorder_keeps_pause_independent_and_stops_without_orders(tmp_path,monkeypatch):
    now=datetime(2026,9,30,15,20,tzinfo=IST)
    directory=tmp_path/'recording'
    def sample(market,clock):
        (directory/'STOP').write_text('owner stop')
        return {'status':'READ_ONLY_DATA_AVAILABLE','probes':{}}
    monkeypatch.setattr('nifty_engine.agent_engine.owner_study.run_checks',sample)
    result=record_market(object(),directory,now+timedelta(minutes=2),clock=lambda:now,sleep=lambda n:None)
    assert result['snapshots']==1 and result['status']=='STOP_FILE'
    assert json.loads((directory/'snapshot-0000.json').read_text())['in_cas_window'] is True
    assert result['execution_enabled'] is False
    with pytest.raises(ValueError):record_market(object(),tmp_path/'bad',now+timedelta(hours=3),clock=lambda:now)


def test_recording_deadline_blocks_further_network_calls(monkeypatch):
    import requests
    def unexpected(*args,**kwargs):
        pytest.fail('expired recorder contacted broker')
    monkeypatch.setattr(requests.sessions.Session,'request',unexpected)
    with readonly_transport([],deadline=datetime.now(timezone.utc)-timedelta(seconds=1)):
        with pytest.raises(TimeoutError):requests.get('https://api.groww.in/v1/live-data/quote')


def spread():
    return {'instrument':'SENSEX','exchange':'BSE','expiry':'2026-10-01','short_strike':75000,
        'short_bid':150,'hedges':[{'strike':77000,'ask':50,'broker_margin_inr':100000},
                               {'strike':76800,'ask':70,'broker_margin_inr':80000}],
        'lot_size':10,'quantity':10,'round_trip_cost_inr':100}


def test_spread_risk_and_margin_are_separate_no_profit_probability():
    result=compare(spread())
    wide,narrow=result['comparisons']
    assert wide['gross_credit_inr']==1000 and wide['net_max_expiry_loss_inr']==19100
    assert narrow['gross_credit_inr']==800 and narrow['net_max_expiry_loss_inr']==17300
    assert wide['broker_margin_inr'] != wide['net_max_expiry_loss_inr']
    assert result['selected_hedge'] is None and not wide['executable']
    value=spread();value['round_trip_cost_inr']=None
    assert compare(value)['comparisons'][0]['net_max_profit_inr'] is None
    for bad in (0,True,11):
        value=spread();value['quantity']=bad
        with pytest.raises(ValueError):compare(value)
    value=spread();value['hedges'][0]['strike']=74900
    with pytest.raises(ValueError):compare(value)


def test_everyday_time_and_listed_strike_use_owner_offsets():
    before=datetime(2026,10,5,9,44,tzinfo=IST)
    at=before+timedelta(minutes=1)
    assert everyday_reference(protocol(),'NIFTY',25425.5,before)['status']=='BEFORE_EVERYDAY_WINDOW'
    result=everyday_reference(protocol(),'NIFTY',25425.5,at,[25900,25800,25850])
    assert result['minimum_short_call_strike']==25825.5
    assert result['listed_short_call_strike']==25850
    assert result['hedge_selected'] is None and result['lot_size_verified'] is False
    assert result['probability_of_net_option_profit'] is None
    assert result['execution_enabled'] is False
    assert everyday_reference(protocol(),'NIFTY',25425.5,at,[25800])['listed_short_call_strike'] is None
    sensex=everyday_reference(protocol(),'SENSEX',75425,datetime(2026,10,1,13,15,tzinfo=timezone(timedelta(hours=9))),[76200,76300])
    assert sensex['minimum_short_call_strike']==76225 and sensex['listed_short_call_strike']==76300
    with pytest.raises(ValueError,match='offset-aware'):
        everyday_reference(protocol(),'NIFTY',25425.5,at.replace(tzinfo=None))


@pytest.mark.parametrize('day,index',[(5,'NIFTY'),(6,'NIFTY'),(7,'SENSEX'),(8,'SENSEX'),(9,'NIFTY')])
def test_everyday_weekday_preference(day,index):
    at=datetime(2026,10,day,9,45,tzinfo=IST)
    assert everyday_reference(protocol(),index,75000,at)['status']=='EVERYDAY_REFERENCE_ONLY'
    other='SENSEX' if index=='NIFTY' else 'NIFTY'
    result=everyday_reference(protocol(),other,75000,at)
    assert result['status']=='NOT_PREFERRED_INDEX_TODAY' and result['preferred_index']==index
    assert everyday_reference(protocol(),'BANKNIFTY',75000,at)['status']=='NOT_APPLICABLE'


def test_everyday_history_uses_completed_minute_and_preserves_v1():
    raw=dataset()
    for day in raw['expiries']:
        for hhmm,price in [('09:44',75000),('09:45',76000)]:
            raw['candles'].append({'at':f'{day}T{hhmm}:00+05:30','open':price,'high':price,'low':price,'close':price})
    raw['candles'].sort(key=lambda r:r['at'])
    result=evaluate_history(raw,protocol())
    observations=result['everyday_spot_observations']
    assert [r['day'] for r in observations]==['2026-08-05','2026-08-06','2026-08-12']
    assert all(r['reference_spot']==75000 and r['minimum_short_call_strike']==75800 for r in observations)
    assert all(r['observed_at'].endswith('09:45:00+05:30') for r in observations)
    excluded=result['coverage']['excluded']
    assert excluded['EVERYDAY_NOT_PREFERRED_INDEX_TODAY']==5
    assert excluded['EVERYDAY_ORDINARY_WEEKDAY_PLAN_NOT_APPLICABLE']==2
    assert 'EVERYDAY_REFERENCE_MINUTE_MISSING' not in excluded
    assert result['everyday_strategy']['unresolved']==['margin_budget_inr','expiry_rule']
    assert result['strategy_count']==4 and result['probability_of_net_option_profit'] is None
    missing=deepcopy(raw)
    missing['candles']=[r for r in missing['candles'] if r['at']!='2026-08-06T09:44:00+05:30']
    absent=evaluate_history(missing,protocol())
    assert absent['coverage']['excluded']['EVERYDAY_REFERENCE_MINUTE_MISSING']==1
    assert len(absent['everyday_spot_observations'])==2
    old=protocol();old.pop('everyday');old['format']='owner-strategy-hypotheses-v1'
    previous=evaluate_history(raw,old)
    assert previous['format']=='owner-study-result-v1' and 'everyday_strategy' not in previous
    assert previous['partitions']==result['partitions'] and previous['expiry_events']==result['expiry_events']


def test_everyday_protocol_has_no_required_loss_cap_and_rejects_drift():
    rule=protocol()
    validate_protocol(rule)
    assert rule['everyday']['max_spread_loss_inr'] is None
    for field,value in [('short_call_offset_points',{'SENSEX':800,'NIFTY':350}),
                        ('sensex_weekdays',['TUESDAY','THURSDAY']),
                        ('allow_position_size_increase',True),
                        ('margin_budget_inr',float('inf'))]:
        wrong=deepcopy(rule);wrong['everyday'][field]=value
        with pytest.raises(ValueError):validate_protocol(wrong)


@pytest.mark.parametrize('spot,assessment,review',[(25000,'FAVORABLE','HOLD_REVIEW'),
                                                 (24999,'FAVORABLE','HOLD_REVIEW'),
                                                 (25000.01,'ADVERSE','EXIT_REVIEW')])
def test_everyday_direction_uses_index_relative_to_entry(spot,assessment,review):
    result=everyday_direction_review(protocol(),25000,spot)
    assert result['assessment']==assessment and result['review']==review
    assert result['spot_move_points']==spot-25000
    assert result['spread_profit_verified'] is False and result['order_submitted'] is False
    assert result['execution_enabled'] is False


def test_existing_everyday_position_is_reviewed_outside_new_entry_preference():
    # Wednesday morning: NIFTY remains reviewable even though SENSEX is preferred
    # for new entries, and 13:15 JST has not yet arrived.
    value={'index':'NIFTY','spot':25001,'entry_spot':25000,'observed_at':'2026-10-07T13:00:00+09:00'}
    result=review_everyday(value,protocol())
    assert result['reference']['status']=='NOT_PREFERRED_INDEX_TODAY'
    assert result['position_review']['review']=='EXIT_REVIEW'
    value['observed_at']='2026-10-06T13:00:00+09:00'
    assert review_everyday(value,protocol())['reference']['status']=='BEFORE_EVERYDAY_WINDOW'
    value['spot']=None
    unknown=review_everyday(value,protocol())
    assert unknown['reference']['status']=='DATA_REQUIRED'
    assert unknown['position_review']['assessment']=='UNKNOWN'
    assert unknown['position_review']['review']=='DATA_REQUIRED'
    for bad in (True,0,-1,float('nan')):
        with pytest.raises(ValueError):everyday_direction_review(protocol(),bad,None)


def test_everyday_cli_is_offline_and_does_not_overwrite(tmp_path,monkeypatch):
    import sys
    import requests
    from nifty_engine.agent_engine.owner_study import main
    def unexpected(*args,**kwargs):
        pytest.fail('offline review contacted a network service')
    monkeypatch.setattr(requests.sessions.Session,'request',unexpected)
    source=tmp_path/'input.json';target=tmp_path/'review.json'
    source.write_text(json.dumps({'index':'NIFTY','spot':25001,'entry_spot':25000,
        'observed_at':'2026-10-05T13:15:00+09:00','listed_call_strikes':[25400,25450]}))
    monkeypatch.setattr(sys,'argv',['owner_study','everyday','--input',str(source),'--output',str(target),
        '--protocol',str(Path(__file__).parents[1]/'config/owner_strategies.json')])
    main()
    result=json.loads(target.read_text())
    assert result['reference']['listed_short_call_strike']==25450
    assert result['position_review']['review']=='EXIT_REVIEW' and result['order_submitted'] is False
    with pytest.raises(ValueError,match='new output'):main()


def test_spread_profit_ranking_respects_margin_and_fixed_quantity():
    value=spread();value['margin_budget_inr']=80000
    result=compare(value);wide,narrow=result['comparisons']
    assert wide['within_margin_budget'] is False and wide['profit_rank_within_margin_budget'] is None
    assert narrow['within_margin_budget'] is True and narrow['profit_rank_within_margin_budget']==1
    assert result['quantity']==10 and result['selected_hedge'] is None
    assert result['recommended_hedge_strike']==narrow['hedge_strike']
    assert result['maximum_loss_limit_applied'] is False
    assert narrow['net_max_expiry_loss_inr']==17300
    value['margin_budget_inr']=100000
    result=compare(value);wide,narrow=result['comparisons']
    assert wide['profit_rank_within_margin_budget']==1 and narrow['profit_rank_within_margin_budget']==2
    assert result['recommended_hedge_strike']==wide['hedge_strike']
    value.update(instrument='NIFTY',exchange='NSE',lot_size=65,quantity=65)
    assert compare(value)['quantity']==65
    value['quantity']=64
    with pytest.raises(ValueError,match='whole number'):compare(value)


def test_spread_unknown_budget_margin_or_cost_is_not_ranked():
    assert all(r['profit_rank_within_margin_budget'] is None for r in compare(spread())['comparisons'])
    value=spread();value['margin_budget_inr']=100000;value['hedges'][0]['broker_margin_inr']=None
    wide=compare(value)['comparisons'][0]
    assert wide['within_margin_budget'] is None and wide['profit_rank_within_margin_budget'] is None
    value['round_trip_cost_inr']=None
    assert all(r['profit_rank_within_margin_budget'] is None for r in compare(value)['comparisons'])
    for budget in (True,0,-1,float('nan'),float('inf')):
        value=spread();value['margin_budget_inr']=budget
        with pytest.raises(ValueError):compare(value)
