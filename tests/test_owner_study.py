import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.contracts import IST
from nifty_engine.agent_engine.market_check import allowed_request, call_samples, quote_summary, readonly_transport
from nifty_engine.agent_engine.owner_study import (
    FORMAT, collect_history, evaluate_history, normalize_candles, probability, record_market, value_at,
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
