"""Thirty-minute direction, provider parsing, stale/gapped inputs and RAM wiring."""
import copy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import intraday_trend as trend, normal_theta
from nifty_engine.agent_engine.normal_data import NormalData, connect_snapshot
from nifty_engine.agent_engine.live_buffers import OperationalStore
from nifty_engine.agent_engine.pc_control import PcJournal
from test_normal_theta import CFG, NOW, evidence, intraday_evidence
from test_report_data import snapshot


def window(index='NIFTY', start=23000, steps=(2, 2, 2, 2, 2, 2)):
    value=intraday_evidence(index,NOW)
    opened=start
    for bar,step in zip(value['bars'],steps):
        closed=opened+step
        bar.update(open=opened,high=max(opened,closed)+1,low=min(opened,closed)-1,close=closed)
        opened=closed
    return value,opened


@pytest.mark.parametrize('index,start,step',[('NIFTY',23000,2),('SENSEX',74000,5)])
@pytest.mark.parametrize('direction',[-1,1])
def test_small_sustained_moves_do_not_need_daily_average_crossing(index,start,step,direction):
    value,spot=window(index,start,[step*direction]*6)
    result=trend.assess(index,value,spot,NOW)
    assert result['status']=='CONFIRMED'
    assert result['regime']==('BEARISH' if direction<0 else 'BULLISH')
    assert abs(result['change_points'])<100
    assert result['bar_count']==6 and result['efficiency']==1
    assert not result['broker_writes']


def test_one_pullback_does_not_disqualify_a_sustained_decline():
    value,spot=window(steps=(-4,-4,1,-4,-4,-4))
    result=trend.assess('NIFTY',value,spot,NOW)
    assert result['regime']=='BEARISH' and result['agreeing_bars']==5


@pytest.mark.parametrize('steps',[(0,0,0,0,0,0),(10,-9,10,-9,10,-9),(40,0,0,0,0,0),(.1,.1,.1,.1,.1,.1)])
def test_sideways_noise_single_jump_and_tiny_drift_stay_wait(steps):
    value,spot=window(steps=steps)
    assert trend.assess('NIFTY',value,spot,NOW)['status']=='WAIT'


@pytest.mark.parametrize('fault',['stale_receipt','future_receipt','missing_bar','duplicate_bar',
    'future_bar','old_session','wrong_index','wrong_source','bad_ohlc','reversed_live','warmup'])
def test_unknown_and_reversal_cannot_authorize_direction(fault):
    value,spot=window()
    if fault=='stale_receipt':value['received_at']=(NOW-timedelta(seconds=91)).isoformat()
    if fault=='future_receipt':value['received_at']=(NOW+timedelta(seconds=1)).isoformat()
    if fault=='missing_bar':value['bars'][2]['at']=(NOW-timedelta(minutes=50)).isoformat()
    if fault=='duplicate_bar':value['bars'][2]['at']=value['bars'][1]['at']
    if fault=='future_bar':
        for bar in value['bars']:bar['at']=(trend.stamp(bar['at'])+timedelta(minutes=5)).isoformat()
    if fault=='old_session':
        for bar in value['bars']:bar['at']=(trend.stamp(bar['at'])-timedelta(days=1)).isoformat()
    if fault=='wrong_index':value['index']='SENSEX'
    if fault=='wrong_source':value['source']='VIEWER_ESTIMATE'
    if fault=='bad_ohlc':value['bars'][0]['low']=99999
    if fault=='reversed_live':spot=22990
    if fault=='warmup':value['bars']=value['bars'][:5]
    assert trend.assess('NIFTY',value,spot,NOW)['regime']=='UNKNOWN'


def payload(value):
    return dict(interval_in_minutes=5,candles=[[r['at'],r['open'],r['high'],r['low'],r['close'],0] for r in value['bars']])


def test_provider_parser_omits_incomplete_bars_rejects_duplicates_and_keeps_receipt():
    value,spot=window();raw=payload(value)
    raw['candles'].append([NOW.isoformat(),spot,spot+1,spot-1,spot,0])
    parsed=trend.history(raw,'NIFTY',NOW)
    assert len(parsed['bars'])==6 and parsed['received_at']==NOW.isoformat()
    assert trend.assess('NIFTY',parsed,spot,NOW)['regime']=='BULLISH'
    raw['candles'].append(raw['candles'][0])
    with pytest.raises(ValueError,match='DUPLICATE'):trend.history(raw,'NIFTY',NOW)
    with pytest.raises(ValueError):trend.history(dict(raw,interval_in_minutes=1),'NIFTY',NOW)


def test_opposing_daily_averages_and_missing_daily_adx_are_context_only():
    bundle=evidence()
    bundle['features']['ma20']['value']=26000
    bundle['features']['ma50']['value']=27000
    bundle['features'].pop('adx14')
    result=normal_theta.evaluate(CFG,'NIFTY',bundle,NOW)
    assert result['candidate'] and result['regime']=='BULLISH'
    assert result['trend_inputs']['adx14']==dict(status='UNKNOWN')


def test_real_reader_request_cache_and_snapshot_bridge_are_ram_only(tmp_path):
    journal=PcJournal(tmp_path/'private.sqlite3');store=OperationalStore(journal.store)
    value,spot=window();calls=[]
    def candles(**kwargs):calls.append(kwargs);return payload(value)
    market=SimpleNamespace(limiter=SimpleNamespace(wait=lambda:None),groww=SimpleNamespace(get_historical_candles=candles))
    clock=[NOW]
    reader=NormalData(market,SimpleNamespace(store=store),CFG,clock=lambda:clock[0])
    first=reader.intraday('NIFTY',NOW)
    reader.intraday('NIFTY',NOW+timedelta(seconds=10))
    assert len(calls)==1 and calls[0]['candle_interval']=='5minute'
    assert calls[0]['groww_symbol']=='NSE-NIFTY' and calls[0]['timeout']==5
    assert calls[0]['start_time'].endswith('09:15:00')
    connect_snapshot(store,snapshot(),CFG,NOW)
    assert store.meta('normal-theta-evidence-NIFTY')['intraday']==first
    assert journal.store.meta('normal-theta-intraday-NIFTY') is None
    assert OperationalStore(journal.store).meta('normal-theta-intraday-NIFTY') is None
    clock[0]=NOW+timedelta(seconds=16)
    reader.intraday('NIFTY',clock[0])
    assert len(calls)==2,'a minute-based worker must not skip a cycle on a 44-second-old cache'
    assert store.meta('normal-theta-intraday-NIFTY')['received_at']==clock[0].isoformat()
