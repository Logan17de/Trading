import copy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import report_data as data, report_strategies as strategy, premium_strategy
from nifty_engine.agent_engine.execution_gate import ExecutionGate, ExecutionDenied
from nifty_engine.agent_engine.pc_control import PcJournal
from nifty_engine.agent_engine.oracle_runtime import Runtime
from test_report_strategies import NOW, CFG, priced_bundle


def snapshot():
    return dict(finished_at=NOW.isoformat(),positions_status='AVAILABLE',orders_status='AVAILABLE',
        probes={i+'_quote':dict(ok=True,received_at=NOW.isoformat(),value={'last_price':25000}) for i in CFG['indices']},
        funds=dict(received_at=(NOW-timedelta(seconds=20)).isoformat(),option_buy_available_inr=1234),
        execution_observation=dict(complete=True,positions=[dict(symbol='NIFTY26N1025000CE',ownership='MANUAL_OR_UNKNOWN_PROTECTED')]),
        ordered_options=[])


def test_snapshot_bridge_preserves_real_times_manual_protection_and_unknowns(tmp_path):
    j=PcJournal(tmp_path/'journal.sqlite3');raw=snapshot()
    data.connect_snapshot(j.store,raw,CFG,NOW)
    for index in CFG['indices']:
        b=j.store.meta('report-strategy-evidence-'+index)
        assert b['received_at']==raw['finished_at'] and b['positions_complete']
        assert b['funds']['received_at']==raw['funds']['received_at']
        assert b['protected_symbols']==['NIFTY26N1025000CE']
        assert b['event_calendar']['status']=='UNKNOWN'
        assert set(b['features'])=={'spot'}
        assert strategy.evaluate(CFG,index,b,NOW)['selected'] is None
    data.connect_snapshot(j.store,raw,CFG,NOW+timedelta(seconds=30))
    assert not j.store.meta('report-strategy-evidence-NIFTY')['input_connection']['current_snapshot']
    assert j.store.meta('report-strategy-evidence-NIFTY')['features']['spot']['observed_at']==NOW.isoformat()


def test_failed_reads_clear_spot_and_incomplete_protection_blocks(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3');raw=snapshot()
    data.connect_snapshot(j.store,raw,CFG,NOW)
    raw['probes']['NIFTY_quote']['ok']=False;raw['orders_status']='INCOMPLETE'
    data.connect_snapshot(j.store,raw,CFG,NOW)
    b=j.store.meta('report-strategy-evidence-NIFTY')
    assert 'spot' not in b['features'] and not b['positions_complete']


def test_worker_cache_keeps_external_iv_calendar_and_separate_greek_time(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3');b=priced_bundle()
    j.store.set_meta('report-strategy-evidence-NIFTY',b)
    c=copy.deepcopy(b['contracts']);c[0]['greeks_received_at']=(NOW-timedelta(seconds=16)).isoformat()
    j.store.set_meta('report-contracts-NIFTY',dict(contracts=c,margins=b['margins']))
    data.connect_snapshot(j.store,snapshot(),CFG,NOW)
    merged=j.store.meta('report-strategy-evidence-NIFTY')
    assert merged['features']['iv_percentile']==b['features']['iv_percentile']
    assert merged['event_calendar']==b['event_calendar']
    with pytest.raises(ValueError,match='CURRENT_SIGNED_GREEKS'):
        strategy._contract(merged['contracts'][0],'NIFTY',NOW)


def test_daily_history_is_prior_ohlc_not_iv_and_rejects_duplicates():
    yesterday=(NOW-timedelta(days=1)).date().isoformat()
    raw=dict(candle_interval='1day',candles=[[yesterday+'T09:15:00',100,102,99,101,0]])
    rows=data.daily_rows(raw,NOW)
    assert rows==[dict(day=yesterday,close=101,high=102,low=99)]
    raw['candles'].append([NOW.date().isoformat()+'T09:15:00',100,103,99,102,0])
    assert len(data.daily_rows(raw,NOW))==1
    raw['candles'].append(raw['candles'][0])
    with pytest.raises(ValueError,match='UNIQUE_PRIOR'):data.daily_rows(raw,NOW)
    with pytest.raises(ValueError,match='DAILY_EXCHANGE'):data.daily_rows(dict(raw,candle_interval='5minute'),NOW)


def test_expiry_intersection_never_certifies_unlisted_contracts(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3');calls=[]
    def expiries(**kw):
        calls.append(kw);return {'expiries':['2026-11-10','2026-12-10']}
    market=SimpleNamespace(groww=SimpleNamespace(get_expiries=expiries),limiter=SimpleNamespace(wait=lambda:None))
    master='trading_symbol,underlying_symbol,segment,exchange,expiry_date,lot_size,tick_size,strike_price\nNIFTY26N1025000CE,NIFTY,FNO,NSE,2026-11-10,65,.05,25000\n'
    reader=data.ReportData(market,j,CFG,clock=lambda:NOW,master=lambda:master)
    r=reader.expiries('NIFTY',NOW)
    assert r['expiries']==['2026-11-10'] and r['status']=='CONFIRMED_CURRENT_MASTER'
    assert calls[0]['timeout']==5


def test_daily_fetch_respects_real_provider_window_and_never_fabricates_iv(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3');requests=[]
    def candles(**kw):
        requests.append(kw)
        return dict(candle_interval='1day',candles=[[(NOW-timedelta(days=60-i)).date().isoformat()+'T09:15:00',100+i,102+i,99+i,101+i,0] for i in range(60)])
    market=SimpleNamespace(groww=SimpleNamespace(get_historical_candles=candles),limiter=SimpleNamespace(wait=lambda:None))
    reader=data.ReportData(market,j,CFG,clock=lambda:NOW)
    reader.history('NIFTY',NOW);reader.history('NIFTY',NOW)
    assert len(requests)==1
    start=Path(requests[0]['start_time'].split()[0]).name
    from datetime import date
    assert (date.fromisoformat(requests[0]['end_time'].split()[0])-date.fromisoformat(start)).days<=180
    features=j.store.meta('report-daily-features-NIFTY')
    assert set(features)=={'ma20','ma50','adx14','rv30'} and features['ma50']['sessions']==50


def test_stale_worker_performs_no_broker_calls_or_owner_changes(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3')
    reader=data.ReportData(SimpleNamespace(),j,CFG,clock=lambda:NOW)
    reader.check({})
    assert j.store.meta('report-data-status-NIFTY')['reason']=='CURRENT_COLLECTOR_SNAPSHOT_REQUIRED'
    assert not premium_strategy.intent(j.store)['enabled']


@pytest.mark.parametrize('purpose',['ENTRY','ROLL','EXIT','PROTECT'])
def test_off_blocks_all_order_purposes_even_with_valid_activation(tmp_path,purpose):
    j=PcJournal(tmp_path/'j.sqlite3');release='a'*40
    j.store.set_meta('premium-execution-activation',dict(format='trading-execution-activation-v1',release=release,
        policy_hash='fixture',owner_approved=True,replay_verified=True,static_ip_verified=True,
        broker_write_verified=True,persistent_gtt_verified=True,child_link_verified=True))
    gate=ExecutionGate(j,tmp_path/'absent-pause',mode='live',release=release,host='ORACLE',clock=lambda:NOW,policy_hash='fixture')
    obs=dict(complete=True,received_at=NOW.isoformat())
    assert gate.blockers(obs,purpose=purpose)==['OWNER_ALGO_OFF']
    with pytest.raises(ExecutionDenied,match='OWNER_ALGO_OFF'):
        with gate.authorize('POST','/v1/order/create',{},obs,purpose=purpose):pytest.fail('write capability must not be granted')


def test_owner_command_only_explicit_intent_survives_restart_no_orders(tmp_path):
    path=tmp_path/'j.sqlite3';j=PcJournal(path)
    runtime=Runtime.__new__(Runtime)
    runtime.state=SimpleNamespace(algo_set=lambda enabled:premium_strategy.set_intent(j.store,enabled,NOW))
    runtime.read=lambda:{'control':{'algo':{'execution_enabled':False,'broker_writes':False}}}
    assert not premium_strategy.intent(j.store)['enabled']
    with pytest.raises(ValueError):runtime.command({'action':'intent','enabled':'true'})
    assert not premium_strategy.intent(j.store)['enabled']
    result=runtime.command({'action':'intent','enabled':True})
    assert not result['execution_enabled'] and not result['broker_writes']
    assert premium_strategy.intent(PcJournal(path).store)['enabled']
    runtime.command({'action':'intent','enabled':False})
    assert not premium_strategy.intent(PcJournal(path).store)['enabled']
