"""Synthetic policy and controller replay; never a real account or activation."""
import copy
from pathlib import Path
from datetime import timedelta

import pytest

from nifty_engine.agent_engine import normal_theta as theta, strategy_groups as groups
from nifty_engine.agent_engine import strategy_controls, report_data, premium_strategy
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine.premium_executor import PremiumExecutor
from nifty_engine.agent_engine.pc_control import PcJournal
from test_report_strategies import NOW, CFG as RESEARCH, priced_bundle
from test_report_execution import ReportSession, LEGACY

CFG=theta.load(Path(__file__).parents[1])


def evidence(index='NIFTY',regime='BULLISH'):
    b=priced_bundle(index,regime)
    b['features']['spot']['value']=25100 if regime=='BULLISH' else 24700
    # No historical-IV features and no event-free horizon are invented.
    b['features'].pop('iv30');b['features'].pop('iv_percentile');b['features'].pop('rv30')
    b['event_calendar']={'status':'UNKNOWN'}
    b['expiry_evidence']['received_at']=NOW.isoformat()
    for i,row in enumerate(b['contracts']):row.update(theta=-5 if i==0 else -2,theta_unit='groww_native')
    for m in b['margins'].values():m['hedge_requirement_inr']=500
    if index=='SENSEX' and regime=='BEARISH':
        hedge=b['contracts'][1];hedge.update(strike=25100,symbol='SENSEX26N1025100CE')
        b['margins']={identity({'legs':[(hedge['symbol'],'BUY'),(b['contracts'][0]['symbol'],'SELL')],
            'quantity':20}):dict(received_at=NOW.isoformat(),basket_requirement_inr=20000,
                hedge_requirement_inr=500,round_trip_charges_inr=10)}
    return b


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
@pytest.mark.parametrize('regime,kind',[('BULLISH','PE'),('BEARISH','CE')])
def test_both_indices_direction_risk_and_theta_without_iv_history(index,regime,kind):
    c=theta.evaluate(CFG,index,evidence(index,regime),NOW)['candidate']
    assert c and c['strategy']=='normal_theta' and c['legs'][1]['symbol'].endswith(kind)
    assert [r['side'] for r in c['legs']]==['BUY','SELL']
    assert c['net_theta_model_units']>0 and c['worst_case_loss_inr']<=1000
    assert not c['execution_enabled'] and not c['broker_writes']


@pytest.mark.parametrize('fault',['stale_greeks','theta','units','spread','funds','positions',
    'expiry','manual','occupied','trend','costs','margin','hedge_cash','depth','duplicate','window','hedge_only'])
def test_missing_adverse_or_unaffordable_evidence_does_not_select(fault):
    b=evidence();now=NOW;occupied=False
    if fault=='stale_greeks':b['contracts'][0]['greeks_received_at']=(NOW-timedelta(seconds=30)).isoformat()
    if fault=='theta':b['contracts'][1]['theta']=-6
    if fault=='units':b['contracts'][0]['theta_unit']='unknown'
    if fault=='spread':b['contracts'][0]['ask']=70
    if fault=='funds':b['funds']['received_at']=(NOW-timedelta(seconds=30)).isoformat()
    if fault=='positions':b['positions_complete']=False
    if fault=='expiry':b['expiry_evidence']['day_jst']='2026-10-06'
    if fault=='manual':b['protected_symbols']=[b['contracts'][0]['symbol']]
    if fault=='occupied':occupied=True
    if fault=='trend':b['features']['adx14']['value']=15
    if fault=='costs':
        for m in b['margins'].values():m['round_trip_charges_inr']=1001
    if fault=='margin':
        for m in b['margins'].values():m['basket_requirement_inr']=100001
    if fault=='hedge_cash':b['funds']['option_buy_available_inr']=1
    if fault=='depth':b['contracts'][0]['bid_quantity']=1
    if fault=='duplicate':b['contracts'].append(copy.deepcopy(b['contracts'][0]))
    if fault=='window':now=NOW.replace(hour=19)
    if fault=='hedge_only':
        for m in b['margins'].values():m['hedge_requirement_inr']=100001
    assert theta.evaluate(CFG,'NIFTY',b,now,occupied=occupied)['candidate'] is None


def test_separate_group_switches_restart_revision_and_research_cannot_enter(tmp_path):
    j=PcJournal(tmp_path/'journal.sqlite3');assert not any(groups.read(j.store)['enabled'].values())
    groups.set_switch(j.store,dict(id='research',enabled=True,revision=0),NOW)
    reopened=PcJournal(tmp_path/'journal.sqlite3')
    assert groups.read(reopened.store)['enabled']==dict(normal_theta=False,research=True)
    assert groups.set_switch(reopened.store,dict(id='normal_theta',enabled=True,revision=0),NOW)['status']=='REVISION_CONFLICT'
    assert not any(strategy_controls.read(j.store)['enabled'].values())
    assert groups.EXECUTABLE==('normal_theta',)


class NormalSession(ReportSession):
    def __init__(self,path,index='NIFTY',regime='BULLISH'):
        super().__init__(path,index,regime);self.data=evidence(index,regime)
        self.gate.normal_catalog=True
        self.executor=PremiumExecutor(self.journal,self.gateway,self.protection,self.gate,LEGACY,
            clock=lambda:self.now,report_cfg=RESEARCH,normal_cfg=CFG)
        groups.set_switch(self.journal.store,dict(id='normal_theta',enabled=True,revision=0),self.now)
        self.books={r['symbol']:{k:r[k] for k in ('bid','ask','bid_quantity','ask_quantity','received_at')} for r in self.data['contracts']}

    def tick(self):
        self.now+=timedelta(seconds=5)
        for book in self.books.values():book['received_at']=self.now.isoformat()
        self.data['received_at']=self.now.isoformat();self.data['funds']['received_at']=self.now.isoformat()
        self.data['expiry_evidence'].update(day_jst=self.now.date().isoformat(),received_at=self.now.isoformat())
        for row in self.data['features'].values():row['observed_at']=self.now.isoformat()
        for row in self.data['contracts']:row.update(self.books[row['symbol']],greeks_received_at=self.now.isoformat())
        for m in self.data['margins'].values():m['received_at']=self.now.isoformat()
        self.journal.store.set_meta('normal-theta-evidence-'+self.data['index'],self.data)
        return self.executor.tick(self.observation(),{})


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
@pytest.mark.parametrize('regime',['BULLISH','BEARISH'])
def test_existing_executor_hedge_first_protection_restart_and_profit_exit(tmp_path,index,regime):
    s=NormalSession(tmp_path,index,regime);s.monitoring()
    assert s.executor._state()['strategy']=='normal_theta'
    orders=[r for kind,r in s.broker.writes if kind=='ORDER']
    assert [r['transaction_type'] for r in orders]==['BUY','SELL']
    s.executor=PremiumExecutor(PcJournal(tmp_path/'journal.sqlite3'),s.gateway,s.protection,s.gate,LEGACY,
        clock=lambda:s.now,report_cfg=RESEARCH,normal_cfg=CFG)
    short=next(r for r in s.executor._state()['candidate']['legs'] if r['side']=='SELL')
    s.books[short['symbol']].update(bid=4.95,ask=5);s.close()
    assert s.executor._state()['exit_reason']=='HALF_CREDIT_PROFIT'
    orders=[r for kind,r in s.broker.writes if kind=='ORDER']
    assert [r['transaction_type'] for r in orders]==['BUY','SELL','BUY','SELL']


@pytest.mark.parametrize('fault',['off','pause','paper','proof','switch','research_only','manual'])
def test_start_cannot_bypass_gates_or_adopt_manual(tmp_path,fault):
    s=NormalSession(tmp_path)
    if fault=='off':premium_strategy.set_intent(s.journal.store,False,s.now)
    if fault=='pause':s.pause.touch()
    if fault=='paper':s.gate.mode='paper'
    if fault=='proof':s.journal.store.set_meta('premium-execution-activation',{})
    if fault in ('switch','research_only'):
        groups.set_switch(s.journal.store,dict(id='normal_theta',enabled=False,revision=1),s.now)
        if fault=='research_only':groups.set_switch(s.journal.store,dict(id='research',enabled=True,revision=2),s.now)
    if fault=='manual':s.data['protected_symbols']=[s.data['contracts'][0]['symbol']]
    for _ in range(3):s.tick()
    assert not s.broker.writes and not s.executor._state()


def test_snapshot_connects_normal_cache_without_redating_or_iv_substitution(tmp_path):
    from test_report_data import snapshot
    j=PcJournal(tmp_path/'j.sqlite3');b=evidence()
    j.store.set_meta('normal-theta-contracts-NIFTY',dict(contracts=b['contracts'],margins=b['margins']))
    report_data.connect_snapshot(j.store,snapshot(),RESEARCH,NOW,CFG)
    normal=j.store.meta('normal-theta-evidence-NIFTY')
    assert normal['contracts']==b['contracts'] and normal['margins']==b['margins']
    assert 'iv_percentile' not in normal['features']


def test_switch_off_and_pre_entry_window_keep_owned_loss_exit(tmp_path):
    s=NormalSession(tmp_path);s.monitoring()
    groups.set_switch(s.journal.store,dict(id='normal_theta',enabled=False,revision=1),s.now)
    s.now=(s.now+timedelta(days=1)).replace(hour=13)
    short=next(r for r in s.executor._state()['candidate']['legs'] if r['side']=='SELL')
    s.books[short['symbol']].update(bid=80,ask=80.05);s.close()
    assert s.executor._state()['exit_reason']=='LOSS_TRIGGER'


def test_research_catalog_on_never_grants_entry_in_normal_runtime(tmp_path):
    s=NormalSession(tmp_path)
    assert all(strategy_controls.read(s.journal.store)['enabled'].values())
    for sid in strategy_controls.EXECUTABLE:
        assert 'MONITOR_ONLY_OR_RETIRED_STRATEGY' in s.gate.blockers(s.observation(),strategy=sid)


def test_prepared_object_cannot_replace_hedge_or_risk_model(tmp_path):
    s=NormalSession(tmp_path);s.tick()
    c=s.executor.normal.preparation(s.observation())['selected']
    assert c
    for field,value in [('close_at_dte',0),('worst_case_loss_inr',1),('net_theta_model_units',1)]:
        changed=copy.deepcopy(c);changed[field]=value
        with pytest.raises(ValueError):s.executor.normal.validate(changed,s.observation())
    changed=copy.deepcopy(c);changed['legs'][0]['strike']-=50
    with pytest.raises(ValueError):s.executor.normal.validate(changed,s.observation())


def test_runtime_two_groups_fixed_transport_and_no_implicit_activation(tmp_path):
    import shutil
    from nifty_engine.agent_engine.oracle_runtime import Runtime
    from nifty_engine.agent_engine.oracle_link import request
    root=Path(__file__).parents[1];(tmp_path/'config').mkdir()
    for name in ('premium_strategy.json','report_strategies.json','normal_theta.json','owner_strategies.json','pc_app.example.json'):
        shutil.copyfile(root/'config'/name,tmp_path/'config'/name)
    (tmp_path/'.trader-paused').touch()
    runtime=Runtime(tmp_path)
    view=runtime.read()
    assert [r['id'] for r in view['strategies']]==['normal_theta','research']
    assert not any(view['control']['strategy_controls']['enabled'].values())
    assert view['research_studies'] and runtime.gate.mode=='paper'
    assert 'MATCHED_IV' not in str(view['strategies'][0]['readiness'])
    from types import SimpleNamespace
    config=dict(format='trading-oracle-viewer-v1',host='example.test',user='ubuntu',
        identity_file=str(tmp_path/'key'),python='/opt/growing-trader/releases/'+('a'*40)+'/venv/bin/python',root='/var/lib/trading-observer')
    import json
    def local_transport(args,**kwargs):
        return SimpleNamespace(returncode=0,stdout=json.dumps(runtime.command(json.loads(kwargs['input']))))
    result=request(config,dict(action='strategy_switch',switch=dict(id='research',enabled=True,revision=0)),run=local_transport)
    assert result['controls']['enabled']==dict(normal_theta=False,research=True)
    assert not premium_strategy.intent(runtime.state.pnl_lines.store)['enabled']
    assert runtime.state.pnl_lines.store.meta('premium-execution-activation') is None
    assert (tmp_path/'.trader-paused').exists()
