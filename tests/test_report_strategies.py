import copy
from datetime import datetime,timedelta
from pathlib import Path
import pytest
from nifty_engine.agent_engine import report_strategies as r
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine.pc_control import JST,PcJournal
from nifty_engine.agent_engine.execution_gate import ExecutionGate
from nifty_engine.agent_engine.research_sync import SupabaseArchive,PROJECT_URL

ROOT=Path(__file__).parents[1]
CFG=r.load(ROOT)
NOW=datetime(2026,10,7,14,5,tzinfo=JST)


def bundle(index='NIFTY',regime='BULLISH'):
    values={'spot':25000,'ma20':24900,'ma50':24800,'adx14':30,'iv_percentile':80,'iv30':25,'rv30':15}
    if regime=='BEARISH':values.update(spot=24700,ma20=24800,ma50=24900)
    if regime=='RANGE':values.update(adx14=15)
    features={}
    for key,value in values.items():
        unit='index_points' if key in ('spot','ma20','ma50') else 'index_score' if key=='adx14' else 'percentile' if key=='iv_percentile' else 'annualized_percent'
        features[key]=dict(value=value,unit=unit,source='synthetic-fixture',observed_at=NOW.isoformat(),
            history_end_at=(NOW-timedelta(days=1)).isoformat(),sessions=252)
    return dict(index=index,received_at=NOW.isoformat(),features=features,positions_complete=True,protected_symbols=[],
        expiry_evidence=dict(status='CONFIRMED_CURRENT_MASTER',day_jst=NOW.date().isoformat(),expiries=['2026-11-10','2026-12-10']),
        contracts=[],margins={},funds=dict(received_at=NOW.isoformat(),option_buy_available_inr=50000,option_sell_available_inr=50000),
        event_calendar=dict(status='VERIFIED_CLEAR',source='synthetic-schedule',checked_at=NOW.isoformat(),
            coverage_start=NOW.isoformat(),coverage_through='2026-12-31'))


def option(index,strike,kind,premium,delta,expiry='2026-11-10'):
    return dict(symbol=f'{index}26N10{strike}{kind}',index=index,expiry=expiry,strike=strike,
        lot_size=65 if index=='NIFTY' else 20,tick_size=.05,bid=premium,ask=round(premium+.05,2),
        bid_quantity=1000,ask_quantity=1000,received_at=NOW.isoformat(),delta=delta,iv=25,iv_unit='annualized_percent')


def priced_bundle(index='NIFTY',regime='BULLISH'):
    b=bundle(index,regime);kind='CE' if regime=='BEARISH' else 'PE'
    short=option(index,25000,kind,40 if index=='NIFTY' else 85,.2 if kind=='CE' else -.2)
    hedge=option(index,25050 if kind=='CE' else 24950,kind,5 if index=='NIFTY' else 30,.1 if kind=='CE' else -.1)
    if index=='SENSEX':hedge['strike']=24900;hedge['symbol']=f'{index}26N1024900PE'
    b['contracts']=[short,hedge]
    for lots in (1,2):
        key=identity({'legs':[(hedge['symbol'],'BUY'),(short['symbol'],'SELL')],'quantity':short['lot_size']*lots})
        b['margins'][key]=dict(received_at=NOW.isoformat(),basket_requirement_inr=20000*lots,round_trip_charges_inr=10)
    return b


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
@pytest.mark.parametrize('regime,sid',[('BULLISH','bull_put'),('BEARISH','bear_call')])
def test_directional_selection_hedges_and_risk(index,regime,sid):
    if index=='SENSEX' and regime=='BEARISH':
        b=priced_bundle(index,'BULLISH')
        for c in b['contracts']:
            c['symbol']=c['symbol'][:-2]+'CE';c['delta']=abs(c['delta'])
        b['contracts'][1]['strike']=25100;b['contracts'][1]['symbol']='SENSEX26N1025100CE'
        b['features']['spot']['value']=24700;b['features']['ma20']['value']=24800;b['features']['ma50']['value']=24900;b['margins']={}
        key=identity({'legs':[(b['contracts'][1]['symbol'],'BUY'),(b['contracts'][0]['symbol'],'SELL')],'quantity':20})
        b['margins'][key]=dict(received_at=NOW.isoformat(),basket_requirement_inr=20000,round_trip_charges_inr=10)
    else:b=priced_bundle(index,regime)
    value=r.evaluate(CFG,index,b,NOW)
    candidate=value['selected']
    assert candidate['strategy']==sid and candidate['lots']==1
    assert candidate['worst_case_loss_inr']<=1000
    assert [leg['side'] for leg in candidate['legs']]==['BUY','SELL']
    assert not candidate['execution_enabled'] and not candidate['broker_writes']
    assert all(row['performance_status']=='NOT_BACKTESTED' for row in value['strategies'])


def test_range_condor_same_expiry_four_legs():
    b=bundle(regime='RANGE')
    pl=option('NIFTY',24850,'PE',5,-.1);ps=option('NIFTY',24900,'PE',25,-.2)
    cl=option('NIFTY',25150,'CE',5,.1);cs=option('NIFTY',25100,'CE',25,.2)
    b['contracts']=[pl,ps,cl,cs]
    key=identity({'legs':[(pl['symbol'],'BUY'),(ps['symbol'],'SELL'),(cl['symbol'],'BUY'),(cs['symbol'],'SELL')],'quantity':65})
    b['margins'][key]=dict(received_at=NOW.isoformat(),basket_requirement_inr=20000,round_trip_charges_inr=10)
    value=r.evaluate(CFG,'NIFTY',b,NOW)
    assert value['selected']['strategy']=='iron_condor'
    assert value['selected']['worst_case_loss_inr']==666.5
    assert len(value['selected']['legs'])==4


@pytest.mark.parametrize('fault',['future','missing','units','sessions','event','margin','manual','occupied','expiry'])
def test_unknown_stale_protected_and_unaffordable_never_select(fault):
    b=priced_bundle()
    if fault=='future':b['features']['spot']['observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    if fault=='missing':b['features'].pop('iv_percentile')
    if fault=='units':b['features']['iv30']['unit']='decimal'
    if fault=='sessions':b['features']['iv_percentile']['sessions']=251
    if fault=='event':b['event_calendar']['status']='UNKNOWN'
    if fault=='margin':b['margins']={}
    if fault=='manual':b['protected_symbols']=[b['contracts'][0]['symbol']]
    if fault=='expiry':
        for c in b['contracts']:c['expiry']=NOW.date().isoformat()
    value=r.evaluate(CFG,'NIFTY',b,NOW,occupied=fault=='occupied')
    assert value['selected'] is None
    assert not value['execution_enabled']


def test_calendar_is_only_a_model_candidate_not_claimed_debit_loss_cap():
    b=bundle();b['features']['iv_percentile']['value']=20
    short=option('NIFTY',25000,'CE',40,.5)
    long=option('NIFTY',25000,'CE',80,.5,expiry='2026-12-10');long['symbol']='NIFTY26D1025000CE';long['iv']=20
    b['contracts']=[short,long]
    row=r.evaluate(CFG,'NIFTY',b,NOW)['strategies'][3]
    assert row['status']=='RISK_MODEL_REQUIRED' and row['candidate']['worst_case_loss_inr'] is None
    assert not row['candidate']['risk_bound_verified']


def test_history_computation_rejects_lookahead_and_needs_complete_iv_history():
    history=[dict(day=(NOW-timedelta(days=252-i)).date().isoformat(),close=100+i,high=101+i,low=99+i,iv=20+i/100,iv_unit='annualized_percent') for i in range(252)]
    f=r.historical_features(history,30,NOW,source='synthetic-daily')
    assert f['iv_percentile']['value']==100 and f['adx14']['value']==100
    assert f['ma50']['sessions']==50 and f['rv30']['value']>0
    history[-1]['iv']=None
    assert 'iv_percentile' not in r.historical_features(history,30,NOW,source='synthetic-daily')
    history[-1]['day']=NOW.date().isoformat()
    with pytest.raises(ValueError):r.historical_features(history,30,NOW,source='synthetic-daily')


def test_research_exit_priority_and_manual_protection():
    position=dict(strategy='bull_put',ownership='ENGINE_VERIFIED',received_at=NOW.isoformat(),
        expiry='2026-11-10',entry_credit_inr=2000,net_max_expiry_profit_inr=1900,net_pnl_inr=-1000)
    assert r.review(CFG,position,NOW)['reason']=='LOSS_TRIGGER'
    position['net_pnl_inr']=950
    assert r.review(CFG,position,NOW)['reason']=='HALF_CREDIT_PROFIT'
    position['expiry']='2026-10-14'
    assert r.review(CFG,position,NOW)['reason']=='EXPIRY_GAMMA_WINDOW'
    position['ownership']='MANUAL_OR_UNKNOWN_PROTECTED'
    assert r.review(CFG,position,NOW)['action']=='WAIT'


def test_retirement_blocks_new_entries_even_with_other_gates_satisfied(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3')
    gate=ExecutionGate(j,tmp_path/'pause',mode='paper',release='a'*40,host='ORACLE',clock=lambda:NOW,policy_hash='fixture',entries_retired=True)
    assert 'RETIRED_STRATEGY_NEW_ENTRY_DISABLED' in gate.blockers(None,purpose='ENTRY')
    assert 'RETIRED_STRATEGY_NEW_ENTRY_DISABLED' in gate.blockers(None,purpose='ROLL')
    assert 'RETIRED_STRATEGY_NEW_ENTRY_DISABLED' not in gate.blockers(None,purpose='EXIT')
    assert 'RETIRED_STRATEGY_NEW_ENTRY_DISABLED' not in gate.blockers(None,purpose='PROTECT')


def test_private_evaluation_archive_dedup_and_no_performance_invention(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3')
    value=r.update(j.store,ROOT,NOW)
    assert len(value['indices'])==2 and all(i['selected'] is None for i in value['indices'].values())
    sent=[];archive=SupabaseArchive(j.store,url=PROJECT_URL,key='fixture',post=sent.extend,clock=lambda:NOW)
    archive.step();archive.step()
    rows=[row for row in sent if row['record_key'].startswith('algo_state:report-strategy-evaluations:')]
    assert len(rows)==1 and rows[0]['mode']=='MONITOR_ONLY'
