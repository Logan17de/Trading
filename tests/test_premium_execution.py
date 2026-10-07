"""Execution tests use an isolated journal and a simulated broker only."""
import contextlib
import copy
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import premium_strategy as policy
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine.execution_gate import ExecutionGate,consume_write
from nifty_engine.agent_engine.oracle_orders import OracleOrderGateway,GrowwOrderTransport
from nifty_engine.agent_engine.oracle_protection import PersistentProtection
from nifty_engine.agent_engine.premium_executor import PremiumExecutor,cash_stop
from nifty_engine.agent_engine.pc_control import PcJournal,JST

CFG=policy.load(Path(__file__).parents[1])
NOW=datetime(2026,10,5,14,30,tzinfo=JST)


def contract(strike,bid,ask,kind='CE'):
    return dict(symbol=f'NIFTY26O06{strike}{kind}',index='NIFTY',expiry='2026-10-06',strike=strike,
        lot_size=65,tick_size=0.05,bid=bid,ask=ask,bid_quantity=1000,ask_quantity=1000,received_at=NOW.isoformat())


class SimulatedBroker:
    simulated=True
    def __init__(self):
        self.orders={};self.smart={};self.writes=[];self.autofill=True
        self.timeout_order=False;self.timeout_smart=False;self.timeout_modify=False;self.cancel_race=False
        self.sdk_off=None

    @contextlib.contextmanager
    def scope(self,*args): yield

    def place_order(self,**args):
        ref=args['order_reference_id'];value={k:v for k,v in args.items() if k!='timeout'}
        value.update(groww_order_id='broker-'+ref,filled_quantity=args['quantity'] if self.autofill else 0,
                     average_fill_price=args['price'] if self.autofill else 0,order_status='EXECUTED' if self.autofill else 'OPEN')
        self.orders[ref]=value;self.writes.append(('ORDER',copy.deepcopy(value)))
        if self.timeout_order: raise TimeoutError('private provider data')
        return dict(groww_order_id=value['groww_order_id'],order_reference_id=ref)

    def get_order_status_by_reference(self,**args): return self.orders[args['order_reference_id']]
    def get_order_detail(self,**args): return next(r for r in self.orders.values() if r['groww_order_id']==args['groww_order_id'])
    def cancel_order(self,**args):
        row=self.get_order_detail(**args);self.writes.append(('CANCEL',row['groww_order_id']))
        row['order_status']='CANCELLED'

    def create_smart_order(self,**args):
        identifier='gtt-'+args['reference_id']; value={k:v for k,v in args.items() if k!='timeout'}
        value.update(smart_order_id=identifier,status='ACTIVE',expire_at='2027-10-05T00:00:00',triggered_at=None)
        self.smart[identifier]=value;self.writes.append(('GTT',copy.deepcopy(value)))
        if self.timeout_smart: raise TimeoutError('private provider data')
        return value

    def get_smart_order(self,**args): return self.smart[args['smart_order_id']]
    def get_smart_order_list(self,**args):
        rows=list(self.smart.values()) if args.get('page',0)==0 else []
        status=args.get('status')
        if status:rows=[r for r in rows if r['status']==status or status=='COMPLETED' and r['status'] in ('TRIGGERED','EXECUTED')]
        return {'orders':rows}
    def modify_smart_order(self,**args):
        self.writes.append(('MODIFY',copy.deepcopy(args)))
        if self.timeout_modify: raise TimeoutError('private provider data')
        row=self.smart[args['smart_order_id']]
        row.update(trigger_price=args['trigger_price'],order=args['order'])
        return row

    def cancel_smart_order(self,**args):
        row=self.smart[args['smart_order_id']];self.writes.append(('CANCEL_GTT',args['smart_order_id']))
        if self.cancel_race: self.trigger(row)
        else: row['status']='CANCELLED'
        return row

    def trigger(self,row,*,linked=True,fill=True):
        row.update(status='TRIGGERED',triggered_at=NOW.isoformat())
        ref=row['reference_id'] if linked else 'external123'
        self.orders[ref]=dict(order_reference_id=ref,groww_order_id='child-'+ref,segment='FNO',validity='DAY',
            trading_symbol=row['trading_symbol'],exchange=row['exchange'],product='NRML',transaction_type='BUY',
            quantity=row['quantity'],price=float(row['order']['price']),order_type='LIMIT',
            filled_quantity=row['quantity'] if fill else 0,average_fill_price=float(row['order']['price']) if fill else 0,
            order_status='EXECUTED' if fill else 'OPEN')


class Session:
    def __init__(self,tmp_path):
        self.now=NOW;self.journal=PcJournal(tmp_path/'journal.sqlite3');self.broker=SimulatedBroker();self.pause=tmp_path/'.trader-paused'
        policy.set_intent(self.journal.store,True,NOW)
        self.gate=ExecutionGate(self.journal,self.pause,mode='live',release='a'*40,host='ORACLE',clock=lambda:self.now,policy_hash=identity(CFG))
        self.journal.store.set_meta('premium-execution-activation',dict(format='trading-execution-activation-v1',release='a'*40,
            policy_hash=identity(CFG),owner_approved=True,replay_verified=True,static_ip_verified=True,broker_write_verified=True,
            persistent_gtt_verified=True,child_link_verified=True))
        self.gateway=OracleOrderGateway(self.journal,self.broker,self.gate)
        self.protection=PersistentProtection(self.journal,self.broker,self.gateway)
        self.executor=PremiumExecutor(self.journal,self.gateway,self.protection,self.gate,CFG,clock=lambda:self.now)
        self.short=contract(25100,20,20.05);self.hedge=contract(25400,4.95,5)
        self.books={c['symbol']:c for c in (self.short,self.hedge)}
        self.selected=dict(short=self.short,hedge=self.hedge,index='NIFTY',expiry='2026-10-06',strategy='EVERYDAY',quantity=65,lots=1,
            product='NRML',net_max_expiry_profit_inr=900,basket_requirement_inr=20000,round_trip_charges_inr=100,prepared_at=NOW.isoformat())
        self.prepared=dict(status='PREPARED',selected=self.selected)

    def observation(self):
        self.executor.reconcile_before_collection()
        rows=self.journal.store.read('SELECT symbol,side,filled FROM pc_orders WHERE broker_hash IS NOT NULL')
        net={}
        for r in rows: net[r['symbol']]=net.get(r['symbol'],0)+r['filled']*(1 if r['side']=='BUY' else -1)
        return dict(complete=True,received_at=self.now.isoformat(),positions=[dict(symbol=s,quantity=q,product='NRML',ownership='ENGINE_VERIFIED')
            for s,q in net.items() if q],funds=dict(received_at=self.now.isoformat(),option_buy_available_inr=100000,option_sell_available_inr=100000),
            books=self.books,expiry_evidence={'NIFTY':dict(status='CONFIRMED_CURRENT_MASTER',
                day_jst=self.now.date().isoformat(),expiries=[self.selected['expiry']])})

    def tick(self):
        self.now+=timedelta(seconds=5)
        for c in self.books.values(): c['received_at']=self.now.isoformat()
        self.selected['prepared_at']=self.now.isoformat()
        state=self.executor._state()
        if state.get('phase')=='ENTRY_SHORT':
            self.prepared['status']='PREPARED_OWNED_ENTRY';self.selected['held_hedge']=True
        return self.executor.tick(self.observation(),self.prepared)

    def monitoring(self):
        for _ in range(15):
            self.tick()
            if self.executor._state().get('phase')=='MONITORING': return
        pytest.fail(str(self.journal.store.meta('premium-executor-status')))


def test_preentry_carried_review_is_fresh_owned_and_never_writes(tmp_path):
    s=Session(tmp_path);s.monitoring()
    s.now=s.now.replace(hour=13,minute=0)
    for c in s.books.values():c['received_at']=s.now.isoformat()
    s.short.update(bid=7.95,ask=8)
    writes=len(s.broker.writes)
    s.pause.touch()
    obs=s.observation()
    assert s.executor.public(obs)['position_review']['action']=='REVIEW_ROLL_SHORT'
    s.short.update(bid=60,ask=60.05)
    assert s.executor.public(obs)['position_review']['action']=='REVIEW_OWNED_EXIT'
    obs['positions'][0]['ownership']='MANUAL_OR_UNKNOWN_PROTECTED'
    assert s.executor.public(obs)['position_review']['action']=='WAIT'
    assert len(s.broker.writes)==writes


def test_paused_expiry_handoff_public_review_never_places_orders(tmp_path):
    s=Session(tmp_path);s.monitoring();s.pause.touch()
    s.now=s.now.replace(day=6,hour=19,minute=0)
    for book in s.books.values():book['received_at']=s.now.isoformat()
    obs=s.observation();obs['expiry_evidence']['SENSEX']=dict(status='CONFIRMED_CURRENT_MASTER',day_jst='2026-10-06',expiries=['2026-10-08'])
    before=list(s.broker.writes)
    review=s.executor.public(obs)['position_review']
    assert review['reason']=='EXPIRY_1900_HANDOFF' and review['successor']['index']=='SENSEX'
    obs['positions'][0]['ownership']='MANUAL_OR_UNKNOWN_PROTECTED'
    assert s.executor.public(obs)['position_review']['action']=='WAIT'
    assert s.broker.writes==before


def test_full_hedge_first_entry_persistent_protection_stop_short_first_and_restart(tmp_path):
    s=Session(tmp_path);s.monitoring()
    assert [r[0] for r in s.broker.writes]==['ORDER','ORDER','GTT']
    assert s.broker.writes[0][1]['transaction_type']=='BUY'
    assert s.broker.writes[1][1]['transaction_type']=='SELL'
    state=s.executor._state();assert state['short_quantity']==65
    # A restarted controller uses the same journal and does not repeat entry.
    s.executor=PremiumExecutor(PcJournal(tmp_path/'journal.sqlite3'),s.gateway,s.protection,s.gate,CFG,clock=lambda:s.now)
    s.short.update(bid=52,ask=52.05)
    for _ in range(20):
        s.tick()
        if s.executor._state()['phase']=='CLOSED': break
    assert s.executor._state()['phase']=='CLOSED'
    standard=[v for k,v in s.broker.writes if k=='ORDER']
    assert [r['transaction_type'] for r in standard]==['BUY','SELL','BUY','SELL']
    assert standard[2]['trading_symbol']==s.short['symbol']
    assert not s.journal.slot_status()['new_entry_blocked']
    assert policy.intent(s.journal.store)['enabled']


def test_partial_hedge_cancels_remaining_and_unwinds_without_any_short(tmp_path):
    s=Session(tmp_path);s.broker.autofill=False
    s.tick();s.tick();row=next(iter(s.broker.orders.values()))
    row.update(filled_quantity=30,average_fill_price=5)
    for _ in range(4):s.tick()
    s.broker.autofill=True
    for _ in range(15):
        s.tick()
        if s.executor._state()['phase']=='CLOSED':break
    standard=[v for k,v in s.broker.writes if k=='ORDER']
    assert len(standard)==2 and all(r['trading_symbol']==s.hedge['symbol'] for r in standard)
    assert standard[1]['transaction_type']=='SELL' and standard[1]['quantity']==30
    assert s.executor._state()['phase']=='CLOSED'


def test_uncertain_order_creation_is_recovered_without_resubmission(tmp_path):
    s=Session(tmp_path);s.broker.timeout_order=True;s.monitoring()
    assert sum(k=='ORDER' for k,_ in s.broker.writes)==2
    assert s.protection.reconcile(s.executor._state()['protection_key'])['status']=='VERIFIED_ACTIVE'


def test_smart_creation_timeout_original_reference_and_missing_link(tmp_path):
    s=Session(tmp_path);s.broker.timeout_smart=True;s.monitoring()
    assert sum(k=='GTT' for k,_ in s.broker.writes)==1
    row=next(iter(s.broker.smart.values()));s.broker.trigger(row,linked=False)
    for _ in range(4):s.tick()
    assert sum(k=='ORDER' for k,_ in s.broker.writes)==2
    assert s.journal.store.meta('premium-executor-status')['reason'].startswith('PROTECTION_')
    # No reserved parent reference -> no guessed child adoption or second BUY.
    assert not s.journal.store.read('SELECT 1 FROM pc_orders WHERE broker_hash=?',('child-external123',))


def test_gtt_cancel_trigger_race_closes_hedge_only_after_child_and_no_double_buy(tmp_path):
    s=Session(tmp_path);s.monitoring();s.broker.cancel_race=True
    s.short.update(bid=60,ask=60.05)
    for _ in range(20):
        s.tick()
        if s.executor._state()['phase']=='CLOSED':break
    assert s.executor._state()['phase']=='CLOSED'
    standard=[v for k,v in s.broker.writes if k=='ORDER']
    assert [r['transaction_type'] for r in standard]==['BUY','SELL','SELL']
    assert standard[-1]['trading_symbol']==s.hedge['symbol']


@pytest.mark.parametrize('status',['ACTIVE','COMPLETED','CANCELLED'])
def test_trigger_evidence_overrides_parent_status_and_never_double_closes(tmp_path,status):
    s=Session(tmp_path);s.monitoring();row=next(iter(s.broker.smart.values()))
    s.broker.trigger(row);row['status']=status
    result=s.protection.reconcile(s.executor._state()['protection_key'])
    assert result['status']=='CHILD_FILLED'
    assert sum(k=='ORDER' for k,_ in s.broker.writes)==2
    assert s.journal.store.read('SELECT filled FROM pc_orders WHERE reference=?',(row['reference_id'],))[0]['filled']==65


def test_unknown_create_recovers_original_day_all_states_without_retry_or_manual_adoption(tmp_path):
    s=Session(tmp_path);s.monitoring();key=s.executor._state()['protection_key']
    record=s.protection.record(key);record.pop('smart_id');s.protection._save(key,record)
    row=next(iter(s.broker.smart.values()));s.broker.trigger(row)
    s.now+=timedelta(days=1);calls=[]
    original=s.broker.get_smart_order_list
    def read(**kw):calls.append(kw);return original(**kw)
    s.broker.get_smart_order_list=read
    writes=len(s.broker.writes)
    assert s.protection.reconcile(key)['status']=='CHILD_FILLED'
    assert {r['status'] for r in calls}=={'ACTIVE','COMPLETED','CANCELLED'}
    assert all(r['start_date_time']=='2026-10-05T00:00:00' and r['end_date_time']=='2026-10-06T00:00:00' for r in calls)
    assert len(s.broker.writes)==writes


def test_parent_reference_mismatch_never_certifies_protection(tmp_path):
    s=Session(tmp_path);s.monitoring();row=next(iter(s.broker.smart.values()))
    row['reference_id']='manual-reference'
    assert s.protection.reconcile(s.executor._state()['protection_key'])['status']=='PROTECTION_RECONCILIATION_REQUIRED'


def test_trail_tightens_broker_stop_once_and_never_claims_guaranteed_cap(tmp_path):
    s=Session(tmp_path);s.monitoring();row=next(iter(s.broker.smart.values()));old=float(row['trigger_price'])
    s.short.update(bid=12,ask=12.05)
    for _ in range(4):s.tick()
    assert float(row['trigger_price'])<old
    count=sum(k=='MODIFY' for k,_ in s.broker.writes)
    s.short.update(bid=18,ask=18.05)
    for _ in range(3):s.tick()
    assert sum(k=='MODIFY' for k,_ in s.broker.writes)==count
    assert not s.executor.public(s.observation())['loss_cap_guaranteed']
    assert cash_stop(975,65,100,.05,500)['trigger_price']==21.15


def test_modify_timeout_retains_unknown_without_repeated_put_or_roll(tmp_path):
    s=Session(tmp_path);s.monitoring();s.broker.timeout_modify=True;s.short.update(bid=12,ask=12.05)
    for _ in range(6):s.tick()
    assert sum(k=='MODIFY' for k,_ in s.broker.writes)==1
    assert sum(k=='ORDER' for k,_ in s.broker.writes)==2
    assert s.journal.store.meta('premium-executor-status')['reason']=='PROTECTION_PROTECTION_RECONCILIATION_REQUIRED'


def test_below_eight_roll_closes_old_short_preserves_hedge_and_reprotects(tmp_path):
    s=Session(tmp_path);s.monitoring();original_hedge=s.hedge['symbol'];old=s.short
    old.update(bid=6.95,ask=7)
    new=contract(25050,10,10.05);s.books[new['symbol']]=new
    s.selected=copy.deepcopy(s.selected);s.selected.update(short=new,held_hedge=True,incremental_costs_inr=80,
        hedge_improvement_after_costs_inr=0)
    s.prepared=dict(status='PREPARED_ROLL',selected=s.selected)
    for _ in range(25):
        s.tick()
        state=s.executor._state()
        if state['phase']=='MONITORING' and state['candidate']['short']['symbol']==new['symbol']:break
    state=s.executor._state()
    assert state['phase']=='MONITORING' and state['candidate']['short']['symbol']==new['symbol']
    standard=[v for k,v in s.broker.writes if k=='ORDER']
    assert [(r['trading_symbol'],r['transaction_type']) for r in standard]==[
        (original_hedge,'BUY'),(old['symbol'],'SELL'),(old['symbol'],'BUY'),(new['symbol'],'SELL')]
    assert sum(k=='GTT' for k,_ in s.broker.writes)==2
    assert state['strategy']=='EVERYDAY' and state['candidate']['quantity']==65


def test_hedge_roll_requires_strict_net_improvement_and_buys_before_disposal(tmp_path):
    s=Session(tmp_path);s.monitoring();s.short.update(bid=6.95,ask=7)
    new_short=contract(25050,10,10.05);new_hedge=contract(25500,1.95,2)
    s.books.update({r['symbol']:r for r in (new_short,new_hedge)})
    s.selected=copy.deepcopy(s.selected);s.selected.update(short=new_short,hedge=new_hedge,
        hedge_improvement_after_costs_inr=150,incremental_costs_inr=80,held_hedge=False)
    s.prepared=dict(status='PREPARED_ROLL',selected=s.selected)
    for _ in range(30):
        s.tick()
        state=s.executor._state()
        if state['phase']=='MONITORING' and state['candidate']['short']['symbol']==new_short['symbol']:break
    assert state['phase']=='MONITORING'
    standard=[v for k,v in s.broker.writes if k=='ORDER']
    assert [(r['trading_symbol'],r['transaction_type']) for r in standard][2:]==[
        (s.short['symbol'],'BUY'),(new_hedge['symbol'],'BUY'),(s.hedge['symbol'],'SELL'),(new_short['symbol'],'SELL')]
    assert not s.journal.store.read("SELECT 1 FROM pc_slots WHERE strategy<>'EVERYDAY'")


def test_nineteen_hold_boundary_queues_until_next_window_without_clearing_on(tmp_path):
    s=Session(tmp_path);s.monitoring();s.short.update(bid=14.95,ask=15)
    s.now=s.now.replace(hour=19,minute=0,second=0)
    for _ in range(3):s.tick()
    assert s.executor._state()['queued_roll']=='INDEX_TARGET'
    assert s.executor._state()['phase']=='MONITORING'
    assert sum(k=='ORDER' for k,_ in s.broker.writes)==2
    assert policy.intent(s.journal.store)['enabled']


def test_carried_stop_exits_before_fresh_entry_window(tmp_path):
    s=Session(tmp_path);s.monitoring()
    s.now=(s.now+timedelta(days=1)).replace(hour=13,minute=0)
    s.short.update(bid=52,ask=52.05)
    for _ in range(20):
        s.tick()
        if s.executor._state()['phase']=='CLOSED':break
    assert s.executor._state()['phase']=='CLOSED'
    assert s.executor._state()['exit_reason']=='BASKET_STOP_OR_TRAIL'
    orders=[r for k,r in s.broker.writes if k=='ORDER']
    assert [r['transaction_type'] for r in orders]==['BUY','SELL','BUY','SELL']
    assert orders[2]['trading_symbol']==s.short['symbol']


@pytest.mark.parametrize('confirmed',[True,False])
def test_actual_expiry_1900_closes_owned_basket_without_successor_entry(tmp_path,confirmed):
    s=Session(tmp_path);s.monitoring()
    s.now=datetime(2026,10,6,19,0,tzinfo=JST)
    for _ in range(20):
        s.now+=timedelta(seconds=5)
        for book in s.books.values():book['received_at']=s.now.isoformat()
        obs=s.observation()
        if not confirmed:obs['expiry_evidence']['NIFTY']['status']='UNKNOWN'
        s.executor.tick(obs,{})
        if s.executor._state()['phase']=='CLOSED':break
    orders=[r for k,r in s.broker.writes if k=='ORDER']
    if confirmed:
        assert s.executor._state()['phase']=='CLOSED'
        assert s.executor._state()['exit_reason']=='ACTUAL_EXPIRY_1900'
        assert [r['transaction_type'] for r in orders]==['BUY','SELL','BUY','SELL']
        assert s.executor._state()['expiry_successor_review']['index']=='SENSEX'
        s.executor.tick(obs,{})
        assert len([r for k,r in s.broker.writes if k=='ORDER'])==4
    else:
        assert s.executor._state()['phase']=='MONITORING'
        assert len(orders)==2


def test_disconnected_runtime_reports_schedule_and_real_activation_blocks(tmp_path):
    from nifty_engine.agent_engine.oracle_runtime import disconnected_execution
    s=Session(tmp_path);s.gate.mode='paper';s.pause.touch()
    policy.set_intent(s.journal.store,False,NOW)
    at=NOW.replace(hour=12,minute=35)
    value=disconnected_execution(s.gate,None,at,None)
    assert value['connection_status']=='WAITING_FOR_COLLECTION_WINDOW'
    assert {'REPOSITORY_PAUSED','OWNER_ALGO_OFF','LIVE_ENVIRONMENT_NOT_ACTIVATED'}<=set(value['blockers'])
    assert not value['execution_enabled'] and not value['provider_execution_verified']
    assert disconnected_execution(s.gate,None,NOW,None)['connection_status']=='ORACLE_EXECUTOR_CONNECTION_REQUIRED'
    assert disconnected_execution(s.gate,None,at,{'error_type':'ConnectionError'})['connection_status']=='ORACLE_BROKER_CONNECTION_RETRY'


def test_runtime_worker_fault_is_visible_sanitized_and_not_retried(tmp_path):
    from nifty_engine.agent_engine.oracle_runtime import Runtime
    s=Session(tmp_path);calls=[]
    class Stop:
        count=0
        def wait(self,seconds):
            self.count+=1
            return self.count>3
    class Failed:
        def tick(self,*args):
            calls.append(True)
            raise RuntimeError('private broker response must never be exposed')
    runtime=Runtime.__new__(Runtime)
    runtime.stop=Stop();runtime.executor=Failed();runtime.execution_error=None
    runtime.output=tmp_path/'missing.json'
    runtime.output.write_text('{}',encoding='utf-8')
    runtime.state=SimpleNamespace(pnl_lines=SimpleNamespace(store=s.journal.store))
    runtime.execution_loop()
    assert calls==[True]
    assert runtime.execution_error=={'error_type':'RuntimeError'}
    status=s.journal.store.meta('premium-executor-status')
    assert status['reason']=='EXECUTOR_WORKER_RECONCILIATION_REQUIRED'
    assert 'private' not in str(status)


def test_expiry_replacement_exits_owned_basket_and_uses_a_new_strategy_slot(tmp_path):
    s=Session(tmp_path);s.monitoring();old_slot=s.executor._state()['slot'];s.now=s.now.replace(hour=18,minute=0)
    late_short=contract(24850,30,30.05,'PE');late_hedge=contract(24700,9.95,10,'PE')
    late_short.update(expiry='2026-10-05');late_hedge.update(expiry='2026-10-05')
    s.books.update({c['symbol']:c for c in (late_short,late_hedge)})
    s.selected=copy.deepcopy(s.selected);s.selected.update(short=late_short,hedge=late_hedge,strategy='LATE_SESSION',expiry='2026-10-05',
        trend='UP',spot=25000,listed_strikes=list(range(24700,25301,50)))
    s.prepared=dict(status='REVIEW_OWNED_REPLACEMENT',selected=s.selected)
    for _ in range(20):
        s.tick()
        if s.executor._state()['phase']=='CLOSED':break
    assert s.executor._state()['phase']=='CLOSED'
    s.prepared['status']='PREPARED'
    for _ in range(15):
        s.tick()
        if s.executor._state()['phase']=='MONITORING':break
    state=s.executor._state()
    assert state['phase']=='MONITORING' and state['strategy']=='LATE_SESSION' and state['slot']!=old_slot
    assert {r['strategy'] for r in s.journal.store.read('SELECT strategy FROM pc_slots')}=={'EVERYDAY','LATE_SESSION'}
    standard=[v for k,v in s.broker.writes if k=='ORDER']
    assert [r['transaction_type'] for r in standard]==['BUY','SELL','BUY','SELL','BUY','SELL']
    assert standard[4]['trading_symbol']==late_hedge['symbol']


def test_expired_broker_flat_requires_two_reads_and_never_invents_settlement_pnl(tmp_path):
    s=Session(tmp_path);s.monitoring();s.now=datetime(2026,10,7,14,30,tzinfo=JST)
    obs=s.observation();obs['positions']=[]
    result=s.executor.tick(obs,{})
    assert result['reason']=='EXPIRED_FLAT_SECOND_SNAPSHOT_REQUIRED'
    s.now+=timedelta(seconds=5);obs['received_at']=s.now.isoformat()
    result=s.executor.tick(obs,{})
    assert result['reason']=='EXPIRED_BROKER_FLAT_CONFIRMED_NO_SETTLEMENT_PNL_ASSUMED'
    assert not s.journal.slot_status()['new_entry_blocked']
    assert s.executor._state()['settlement_pnl']=='UNKNOWN'
    assert sum(k=='ORDER' for k,_ in s.broker.writes)==2


def test_generated_child_is_acknowledged_before_raw_ownership_and_no_prefix_adoption(tmp_path):
    s=Session(tmp_path);s.monitoring();row=next(iter(s.broker.smart.values()));s.broker.trigger(row)
    s.executor.reconcile_before_collection()
    raw_positions=[dict(trading_symbol=s.hedge['symbol'],quantity=65)]
    owners=s.journal.ownership(list(s.broker.orders.values()),raw_positions,complete=True)
    assert owners[s.hedge['symbol']]=='ENGINE_VERIFIED'
    assert not s.journal.store.read('SELECT 1 FROM pc_protected')
    unowned=copy.deepcopy(next(iter(s.broker.orders.values())))
    unowned.update(order_reference_id='GT111111111111111111',groww_order_id='unowned-broker-id')
    s.journal.ownership([unowned],raw_positions,complete=True)
    assert s.journal.store.read('SELECT 1 FROM pc_protected WHERE symbol=?',(s.hedge['symbol'],))


@pytest.mark.parametrize('fault',['paused','off','stale','incomplete','manual','release','mode'])
def test_every_write_is_blocked_by_fault_without_changing_owner_intent(tmp_path,fault):
    s=Session(tmp_path);s.monitoring();count=len(s.broker.writes)
    s.short.update(bid=60,ask=60.05);obs=s.observation()
    if fault=='paused':s.pause.touch()
    if fault=='off':policy.set_intent(s.journal.store,False,s.now)
    if fault=='stale':obs['received_at']=(s.now-timedelta(seconds=11)).isoformat()
    if fault=='incomplete':obs['complete']=False
    if fault=='manual':
        with s.journal.store.transaction() as db:db.execute('INSERT INTO pc_protected VALUES(?)',(s.short['symbol'],))
    if fault=='release':s.gate.release='b'*40
    if fault=='mode':s.gate.mode='paper'
    result=s.executor.tick(obs,s.prepared)
    assert len(s.broker.writes)==count and result['reason']!='MONITORING_OWNED_BASKET'
    assert policy.intent(s.journal.store)['enabled']==(fault!='off')


def test_capability_is_exact_one_request_rechecks_off_and_not_shared_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    s=Session(tmp_path);obs=s.observation();body={'foo':'bar'};url='https://api.groww.in/v1/order/create'
    assert not consume_write('POST',url,{'json':body})
    with s.gate.authorize('POST','/v1/order/create',body,obs,purpose='ENTRY'):
        assert not consume_write('POST',url,{'json':{'foo':'changed'}})
        assert not consume_write('POST',url+'?x=1',{'json':body})
        with ThreadPoolExecutor(max_workers=1) as pool:assert not pool.submit(consume_write,'POST',url,{'json':body}).result()
        assert consume_write('POST',url,{'json':body})
        assert not consume_write('POST',url,{'json':body})
    with s.gate.authorize('POST','/v1/order/create',body,obs,purpose='ENTRY'):
        policy.set_intent(s.journal.store,False,s.now)
        with pytest.raises(PermissionError):consume_write('POST',url,{'json':body})


def test_pinned_sdk_actual_json_matches_scoped_transport_without_any_network(tmp_path,monkeypatch):
    from growwapi import GrowwAPI
    import requests
    from nifty_engine.agent_engine.market_check import readonly_transport
    s=Session(tmp_path);obs=s.observation();sdk=GrowwAPI('synthetic-access-token')
    calls=[]
    class Reply:
        status_code=200
        ok=True
        def json(self):return {'status':'SUCCESS','payload':{'verified':True}}
    def fake_request(session,method,url,**kwargs):calls.append((method,url,kwargs));return Reply()
    monkeypatch.setattr(requests.sessions.Session,'request',fake_request)
    transport=GrowwOrderTransport(SimpleNamespace(groww=sdk,limiter=SimpleNamespace(wait=lambda:None)),s.gate)
    args=dict(validity='DAY',exchange='NSE',order_type='LIMIT',product='NRML',quantity=65,segment='FNO',
        trading_symbol=s.short['symbol'],transaction_type='SELL',order_reference_id='GT123456789012345678',price=20,timeout=5)
    with readonly_transport([],dashboard=True):
        with pytest.raises(PermissionError):sdk.place_order(**args)
        with transport.scope(obs,'ENTRY'):assert transport.place_order(**args)=={'verified':True}
        assert len(calls)==1 and calls[0][2]['json']['trigger_price'] is None
        smart=dict(smart_order_type='GTT',segment='FNO',trading_symbol=s.short['symbol'],quantity=65,product_type='NRML',
            exchange='NSE',duration='DAY',reference_id='GT123456789012345679',trigger_price='44.20',trigger_direction='UP',
            order={'order_type':'LIMIT','transaction_type':'BUY','price':'44.25'},timeout=5)
        with transport.scope(obs,'PROTECT'):
            transport.create_smart_order(**smart)
            transport.modify_smart_order(smart_order_id='gtt_fake',smart_order_type='GTT',segment='FNO',trigger_price='40.00',
                trigger_direction='UP',order=smart['order'],timeout=5)
            transport.cancel_smart_order(smart_order_id='gtt_fake',smart_order_type='GTT',segment='FNO',timeout=5)
        assert [r[0].upper() for r in calls]==['POST','POST','PUT','POST']


def test_detail_route_rejection_uses_complete_list_exact_id_and_reference(tmp_path):
    s=Session(tmp_path);s.monitoring();row=next(iter(s.broker.orders.values()))
    class Reads:
        def get_order_detail(self,**args):raise RuntimeError('synthetic GA004')
        def get_order_status_by_reference(self,**args):raise RuntimeError('synthetic GA004')
        def get_order_list(self,**args):return {'order_list':[row] if args['page']==0 else []}
    sdk=Reads();market=SimpleNamespace(groww=sdk,limiter=SimpleNamespace(wait=lambda:None))
    transport=GrowwOrderTransport(market,s.gate)
    assert transport.get_order_detail(segment='FNO',groww_order_id=row['groww_order_id'],timeout=5)==row
    assert transport.get_order_status_by_reference(segment='FNO',order_reference_id=row['order_reference_id'],timeout=5)==row
    with pytest.raises(ValueError):transport.get_order_detail(segment='FNO',groww_order_id='other',timeout=5)
    sdk.get_order_list=lambda **args:{'order_list':[row]}
    with pytest.raises(ValueError):transport.get_order_detail(segment='FNO',groww_order_id=row['groww_order_id'],timeout=5)


def test_previous_day_terminal_fill_proof_survives_day_scoped_missing_history(tmp_path):
    s=Session(tmp_path);s.monitoring();key=s.executor._state()['operations']['entry-hedge']
    s.now+=timedelta(days=1)
    s.broker.get_order_status_by_reference=lambda **args:(_ for _ in ()).throw(KeyError('no historical order'))
    result=s.gateway.reconcile(key)
    assert result['historical_terminal_proof'] and result['filled_quantity']==65
    assert sum(k=='ORDER' for k,_ in s.broker.writes)==2
