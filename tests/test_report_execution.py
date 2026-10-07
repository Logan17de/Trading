"""Isolated exact-reference broker replay, never an actual account connection."""
from datetime import timedelta

import pytest

from test_premium_execution import Session, CFG as LEGACY
from test_report_strategies import CFG, NOW, priced_bundle, bundle, option
from nifty_engine.agent_engine import strategy_controls as controls, premium_strategy as policy
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine.execution_gate import ExecutionDenied, consume_write
from nifty_engine.agent_engine.premium_executor import PremiumExecutor
from nifty_engine.agent_engine.pc_control import PcJournal


class ReportSession(Session):
    def __init__(self, path, index='NIFTY', regime='BULLISH'):
        super().__init__(path); self.now = NOW
        self.gate.strategy_switches_required = True
        self.data = priced_bundle(index, regime)
        if index=='SENSEX' and regime=='BEARISH':
            hedge=self.data['contracts'][1]
            hedge.update(strike=25100,symbol='SENSEX26N1025100CE',delta=.1)
            self.data['margins']={}
            for lots in (1,2):
                key=identity({'legs':[(hedge['symbol'],'BUY'),(self.data['contracts'][0]['symbol'],'SELL')],'quantity':20*lots})
                self.data['margins'][key]=dict(received_at=NOW.isoformat(),basket_requirement_inr=20000*lots,round_trip_charges_inr=10)
        if regime == 'RANGE':
            self.data = bundle(index, regime)
            legs = [option(index,24850,'PE',5,-.1), option(index,24900,'PE',25,-.2),
                    option(index,25150,'CE',5,.1), option(index,25100,'CE',25,.2)]
            self.data['contracts'] = legs
            if index=='SENSEX':
                legs=[option(index,24800,'PE',5,-.1),option(index,24900,'PE',35,-.2),
                      option(index,25200,'CE',5,.1),option(index,25100,'CE',35,.2)]
                self.data['contracts']=legs
            key=identity({'legs':[(r['symbol'],'BUY' if i%2==0 else 'SELL') for i,r in enumerate(legs)],'quantity':legs[0]['lot_size']})
            self.data['margins'][key]=dict(received_at=NOW.isoformat(),basket_requirement_inr=20000,round_trip_charges_inr=10)
        for m in self.data['margins'].values(): m['hedge_requirement_inr']=1000
        self.executor=PremiumExecutor(self.journal,self.gateway,self.protection,self.gate,LEGACY,
            clock=lambda:self.now,report_cfg=CFG)
        for sid in controls.IDS:
            controls.set_switch(self.journal.store,dict(id=sid,enabled=True,revision=controls.read(self.journal.store)['revision']),self.now)
        self.books={r['symbol']:{k:r[k] for k in ('bid','ask','bid_quantity','ask_quantity','received_at')} for r in self.data['contracts']}
        self.selected={'expiry':self.data['contracts'][0]['expiry']}

    def tick(self):
        self.now+=timedelta(seconds=5)
        for book in self.books.values(): book['received_at']=self.now.isoformat()
        self.data['received_at']=self.now.isoformat();self.data['funds']['received_at']=self.now.isoformat()
        self.data['expiry_evidence']['day_jst']=self.now.date().isoformat()
        if 'event_calendar' in self.data:self.data['event_calendar']['checked_at']=self.now.isoformat()
        for row in self.data['features'].values(): row['observed_at']=self.now.isoformat()
        for row in self.data['contracts']: row.update(self.books[row['symbol']])
        for margin in self.data['margins'].values(): margin['received_at']=self.now.isoformat()
        self.journal.store.set_meta('report-strategy-evidence-'+self.data['index'],self.data)
        obs=self.observation()
        if self.data['index']=='SENSEX': obs['expiry_evidence']['SENSEX']=self.data['expiry_evidence']
        return self.executor.tick(obs,{})

    def monitoring(self):
        for _ in range(30):
            self.tick()
            if self.executor._state().get('phase')=='REPORT_MONITORING': return
        pytest.fail(str(self.journal.store.meta('premium-executor-status')))

    def close(self):
        for _ in range(35):
            self.tick()
            if self.executor._state().get('phase')=='CLOSED': return
        pytest.fail(str(self.journal.store.meta('premium-executor-status')))


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
@pytest.mark.parametrize('regime,sid',[('BULLISH','bull_put'),('BEARISH','bear_call'),('RANGE','iron_condor')])
def test_entry_hedges_protection_restart_and_profit_exit(tmp_path,index,regime,sid):
    s=ReportSession(tmp_path,index,regime);s.monitoring()
    state=s.executor._state();assert state['strategy']==sid
    writes=[r for kind,r in s.broker.writes if kind=='ORDER']
    n=2 if sid=='iron_condor' else 1
    assert all(r['transaction_type']=='BUY' for r in writes[:n])
    assert all(r['transaction_type']=='SELL' for r in writes[n:])
    assert len(s.broker.smart)==n
    assert all(s.protection.reconcile(p['key'])['status']=='VERIFIED_ACTIVE' for p in state['protections'].values())
    s.executor=PremiumExecutor(PcJournal(tmp_path/'journal.sqlite3'),s.gateway,s.protection,s.gate,LEGACY,
        clock=lambda:s.now,report_cfg=CFG)
    for row in state['candidate']['legs']:
        if row['side']=='SELL':s.books[row['symbol']].update(bid=4.95,ask=5)
    s.close()
    writes=[r for kind,r in s.broker.writes if kind=='ORDER']
    assert len(writes)==4*n
    assert all(r['transaction_type']=='BUY' for r in writes[2*n:3*n])
    assert all(r['transaction_type']=='SELL' for r in writes[3*n:])
    assert s.executor._state()['exit_reason']=='HALF_CREDIT_PROFIT'
    assert not s.journal.slot_status()['new_entry_blocked']


def test_switch_off_retains_completed_basket_protection_and_loss_exit(tmp_path):
    s=ReportSession(tmp_path);s.monitoring();sid=s.executor._state()['strategy']
    controls.set_switch(s.journal.store,dict(id=sid,enabled=False,revision=4),s.now)
    before=len(s.broker.writes);s.tick()
    assert s.executor._state()['phase']=='REPORT_MONITORING' and len(s.broker.writes)==before
    row=next(r for r in s.executor._state()['candidate']['legs'] if r['side']=='SELL')
    s.books[row['symbol']].update(bid=80,ask=80.05);s.close()
    assert s.executor._state()['exit_reason']=='LOSS_TRIGGER'
    s.tick();assert s.executor._state()['phase']=='CLOSED'


def test_turn_off_partial_pending_hedge_cancels_before_unwind(tmp_path):
    s=ReportSession(tmp_path);s.broker.autofill=False;s.tick();s.tick()
    row=next(iter(s.broker.orders.values()));row.update(filled_quantity=30,average_fill_price=5)
    controls.set_switch(s.journal.store,dict(id='bull_put',enabled=False,revision=4),s.now)
    s.tick();assert any(kind=='CANCEL' for kind,_ in s.broker.writes)
    s.broker.autofill=True;s.close()
    orders=[r for kind,r in s.broker.writes if kind=='ORDER']
    assert len(orders)==2 and [r['transaction_type'] for r in orders]==['BUY','SELL']
    assert orders[-1]['quantity']==30


@pytest.mark.parametrize('fault',['pause','paper','proof','owner_off','switches_off','iv','events','hedge_margin','risk','manual'])
def test_no_entry_on_missing_data_switch_gate_or_manual(tmp_path,fault):
    s=ReportSession(tmp_path)
    if fault=='pause':s.pause.touch()
    if fault=='paper':s.gate.mode='paper'
    if fault=='proof':s.journal.store.set_meta('premium-execution-activation',{})
    if fault=='owner_off':policy.set_intent(s.journal.store,False,s.now)
    if fault=='switches_off':
        controls.set_switch(s.journal.store,dict(id='bull_put',enabled=False,revision=4),s.now)
    if fault=='iv':s.data['features'].pop('iv_percentile')
    if fault=='events':s.data.pop('event_calendar')
    if fault=='hedge_margin':
        for m in s.data['margins'].values():m.pop('hedge_requirement_inr')
    if fault=='risk':
        for row in s.data['contracts']:
            if row['symbol'].endswith('PE') and row['strike']==24950:
                row['strike']=24900
    if fault=='manual':
        with s.journal.store.transaction() as db:db.execute('INSERT INTO pc_protected(symbol) VALUES(?)',(s.data['contracts'][0]['symbol'],))
    for _ in range(3):s.tick()
    assert not s.broker.writes


def test_off_after_capability_before_actual_network_is_denied(tmp_path):
    s=ReportSession(tmp_path);s.tick();obs=s.observation()
    with s.gate.strategy_scope('bull_put'),s.gate.authorize('POST','/v1/order/create',{'fixture':1},obs,purpose='ENTRY'):
        controls.set_switch(s.journal.store,dict(id='bull_put',enabled=False,revision=4),s.now)
        with pytest.raises(ExecutionDenied,match='STRATEGY_SWITCH_OFF'):
            consume_write('POST','https://api.groww.in/v1/order/create',{'json':{'fixture':1}})


def test_calendar_cannot_be_sent_through_oracle_gateway(tmp_path):
    s=ReportSession(tmp_path);obs=s.observation()
    with pytest.raises(ExecutionDenied,match='MONITOR_ONLY'):
        s.gate.check(obs,purpose='ENTRY',strategy='calendar')


def test_control_restart_idempotency_revision_conflict_and_default_off(tmp_path):
    journal=PcJournal(tmp_path/'private.sqlite3'); assert not any(controls.read(journal.store)['enabled'].values())
    body=dict(id='iron_condor',enabled=True,revision=0)
    assert controls.set_switch(journal.store,body,NOW)['status']=='SAVED'
    assert controls.set_switch(journal.store,body,NOW)['status']=='REVISION_CONFLICT'
    body['revision']=1
    assert controls.set_switch(journal.store,body,NOW)['status']=='UNCHANGED'
    assert controls.read(PcJournal(tmp_path/'private.sqlite3').store)['enabled']['iron_condor']
    for field,value in [('enabled',1),('revision',True),('id','EVERYDAY')]:
        bad=dict(body);bad[field]=value
        with pytest.raises(ValueError): controls.set_switch(journal.store,bad,NOW)


def test_cancel_gtt_race_uses_exact_child_and_never_buys_twice(tmp_path):
    s=ReportSession(tmp_path);s.monitoring();s.broker.cancel_race=True
    short=next(r for r in s.executor._state()['candidate']['legs'] if r['side']=='SELL')
    s.books[short['symbol']].update(bid=80,ask=80.05);s.close()
    standard=[r for kind,r in s.broker.writes if kind=='ORDER']
    assert len(standard)==3
    assert not any(r['trading_symbol']==short['symbol'] and r['transaction_type']=='BUY' for r in standard)


def test_first_condor_stop_uses_only_confirmed_cash_and_rebalances_after_second_fill(tmp_path):
    s=ReportSession(tmp_path,regime='RANGE')
    for _ in range(20):
        s.tick()
        state=s.executor._state()
        if len(s.broker.smart)==1:break
    assert len(s.broker.smart)==1
    stop=next(iter(s.broker.smart.values()))
    assert float(stop['trigger_price'])*65 <= s.executor._cash(state)+1000-state['costs']
    s.monitoring()
    assert len(s.broker.smart)==2
    assert any(kind=='MODIFY' for kind,_ in s.broker.writes)
    assert sum(float(r['trigger_price'])*r['quantity'] for r in s.broker.smart.values()) <= s.executor._cash(s.executor._state())+1000-state['costs']


def test_first_condor_child_filled_during_entry_never_sells_second_short(tmp_path):
    s=ReportSession(tmp_path,regime='RANGE')
    for _ in range(20):
        s.tick()
        if s.broker.smart:break
    s.broker.trigger(next(iter(s.broker.smart.values())));s.close()
    sells=[r for kind,r in s.broker.writes if kind=='ORDER' and r['transaction_type']=='SELL']
    assert sum(r['trading_symbol'].endswith('PE') for r in sells)>=1
    assert not any(r['trading_symbol']=='NIFTY26N1025100CE' for r in sells)


def test_expired_multi_short_basket_needs_two_flat_reads_and_no_settlement_guess(tmp_path):
    s=ReportSession(tmp_path,regime='RANGE');s.monitoring()
    s.now=s.now.replace(month=11,day=11,hour=13)
    obs=s.observation();obs.update(positions=[],received_at=s.now.isoformat())
    assert s.executor.tick(obs,{})['reason'].startswith('EXPIRED_PROTECTION_') or s.executor._state().get('expired_flat_at')
    for _ in range(4):
        s.now+=timedelta(seconds=5);obs['received_at']=s.now.isoformat();s.executor.tick(obs,{})
        if s.executor._state()['phase']=='CLOSED':break
    assert s.executor._state()['phase']=='CLOSED'
    assert s.executor._state()['settlement_pnl']=='UNKNOWN'
    assert sum(kind=='ORDER' for kind,_ in s.broker.writes)==4


def test_local_switch_endpoint_requires_owner_token_and_rejects_order_payloads(tmp_path):
    import json,threading
    from types import SimpleNamespace
    from http.server import ThreadingHTTPServer
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from nifty_engine.agent_engine.dashboard import handler
    j=PcJournal(tmp_path/'controls.sqlite3')
    state=SimpleNamespace(token='fixture-token',strategy_switch=lambda b:controls.set_switch(j.store,b,NOW))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(state));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/api/strategies/switch'
    body=json.dumps(dict(id='bear_call',enabled=True,revision=0)).encode()
    try:
        for headers in ({'Content-Type':'application/json'},{'Content-Type':'application/json','X-Local-Token':state.token,'Origin':'https://example.com'}):
            with pytest.raises(HTTPError) as exc:urlopen(Request(url,data=body,headers=headers))
            assert exc.value.code==403
        headers={'Content-Type':'application/json','X-Local-Token':state.token}
        with urlopen(Request(url,data=body,headers=headers)) as response:assert json.load(response)['status']=='SAVED'
        with pytest.raises(HTTPError) as exc:urlopen(Request(url,data=body,headers=headers))
        assert exc.value.code==409
        with pytest.raises(HTTPError) as exc:urlopen(Request(url,data=b'{"id":"bear_call","enabled":true,"revision":1,"order":{}}',headers=headers))
        assert exc.value.code==400
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_email_distinguishes_selection_readiness_and_permitted_writes():
    from nifty_engine.agent_engine.visual_report import build
    from nifty_engine.agent_engine.dashboard import view_model
    from test_dashboard import protocol
    view=view_model(None,protocol(),now=NOW)
    view['strategies']=[dict(name='Calendar',mode='MONITOR_ONLY',desired_enabled=True)]
    view['control']={'algo':dict(desired_enabled=True,execution_enabled=False)}
    view['execution_controller']={'execution_enabled':False}
    html=build(view,NOW.date().isoformat())['mail']['html']
    assert 'ON · blocked' in html and 'Broker writes OFF' in html and 'Monitor selected' in html
    view['control']['algo']['execution_enabled']=True
    assert 'Broker writes OFF' in build(view,NOW.date().isoformat())['mail']['html']
    view['execution_controller']['execution_enabled']=True
    html=build(view,NOW.date().isoformat())['mail']['html']
    assert 'ON · ready' in html and 'Broker writes permitted' in html
