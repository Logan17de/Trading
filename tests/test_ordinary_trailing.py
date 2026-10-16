"""Full Normal lifecycle against an isolated fake ordinary-order broker."""
import copy
from datetime import timedelta

import pytest

from nifty_engine.agent_engine.ordinary_protection import OrdinaryStopProtection, ProtectionRouter
from nifty_engine.agent_engine.oracle_orders import OracleOrderGateway
from nifty_engine.agent_engine.premium_executor import PremiumExecutor
from nifty_engine.agent_engine import premium_strategy
from test_normal_theta import NormalSession, CFG, RESEARCH, LEGACY
from test_premium_execution import SimulatedBroker


class StandardBroker(SimulatedBroker):
    def __init__(self):
        super().__init__()
        self.reject_stop=False; self.modify_reply_lost=False; self.modify_unreadable=False; self.modify_fills=False

    def place_order(self, **args):
        auto=self.autofill
        if args['order_type']=='SL': self.autofill=False
        try: result=super().place_order(**args)
        finally: self.autofill=auto
        row=self.orders[args['order_reference_id']]
        if args['order_type']=='SL': row['order_status']='REJECTED' if self.reject_stop else 'TRIGGER_PENDING'
        return result

    def create_smart_order(self, **args): raise AssertionError('NO_GTT_ALLOWED')
    def modify_smart_order(self, **args): raise AssertionError('NO_GTT_ALLOWED')

    def modify_order(self, **args):
        self.writes.append(('MODIFY_SL',copy.deepcopy(args)))
        if self.modify_fills:
            row=self.get_order_detail(**args)
            row.update(order_status='EXECUTED',filled_quantity=row['quantity'],average_fill_price=row['trigger_price'])
            raise TimeoutError('private provider body')
        if not self.modify_unreadable:
            row=self.get_order_detail(**args)
            row.update(trigger_price=args['trigger_price'],price=args['price'])
        if self.modify_reply_lost or self.modify_unreadable: raise TimeoutError('private provider body')


class StandardSession(NormalSession):
    def __init__(self,path,index='NIFTY',regime='BULLISH'):
        super().__init__(path,index,regime)
        self.broker=StandardBroker()
        self.gateway=OracleOrderGateway(self.journal,self.broker,self.gate)
        self.protection=OrdinaryStopProtection(self.journal,self.broker,self.gateway)
        self.executor=PremiumExecutor(self.journal,self.gateway,self.protection,self.gate,LEGACY,
            clock=lambda:self.now,report_cfg=RESEARCH,normal_cfg=CFG)

    def stop_order(self): return next(r for r in self.broker.orders.values() if r['order_type']=='SL')
    def short_leg(self): return next(r for r in self.executor._state()['candidate']['legs'] if r['side']=='SELL')


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
@pytest.mark.parametrize('trend',['BULLISH','BEARISH'])
def test_immediate_entry_filled_stop_and_owned_position_exit(tmp_path,index,trend):
    s=StandardSession(tmp_path,index,trend);s.monitoring()
    writes=[r for kind,r in s.broker.writes if kind=='ORDER']
    assert [(r['transaction_type'],r['order_type']) for r in writes]==[('BUY','LIMIT'),('SELL','LIMIT'),('BUY','SL')]
    assert not s.broker.smart and s.stop_order()['filled_quantity']==0
    s.books[s.short_leg()['symbol']].update(bid=4.95,ask=5)
    s.close()
    assert s.stop_order()['order_status']=='CANCELLED'
    assert s.executor._net(s.executor._state())=={}
    assert [r['transaction_type'] for k,r in s.broker.writes if k=='ORDER']==['BUY','SELL','BUY','BUY','SELL']


def test_trail_tightens_only_and_restart_preserves_exact_stop(tmp_path):
    s=StandardSession(tmp_path);s.monitoring()
    initial=s.stop_order()['trigger_price']
    s.books[s.short_leg()['symbol']].update(bid=30.95,ask=31)
    for _ in range(3):s.tick()
    tightened=s.stop_order()['trigger_price']
    assert tightened<initial and any(k=='MODIFY_SL' for k,_ in s.broker.writes)
    s.executor=PremiumExecutor(s.journal,s.gateway,OrdinaryStopProtection(s.journal,s.broker,s.gateway),s.gate,LEGACY,
        clock=lambda:s.now,report_cfg=RESEARCH,normal_cfg=CFG)
    count=len(s.broker.writes)
    s.books[s.short_leg()['symbol']].update(bid=32.95,ask=33)
    for _ in range(3):s.tick()
    assert s.stop_order()['trigger_price']==tightened and len(s.broker.writes)==count


@pytest.mark.parametrize('partial',[False,True])
def test_actual_stop_fill_exits_remaining_owned_positions(tmp_path,partial):
    s=StandardSession(tmp_path);s.monitoring();stop=s.stop_order()
    stop.update(order_status='OPEN' if partial else 'EXECUTED',
        filled_quantity=stop['quantity']//2 if partial else stop['quantity'],average_fill_price=stop['trigger_price'])
    s.close()
    assert s.executor._net(s.executor._state())=={}
    assert not s.broker.smart
    assert sum(r['quantity'] for k,r in s.broker.writes if k=='ORDER' and r['transaction_type']=='BUY'
        and r['order_type']=='LIMIT' and r['trading_symbol']==stop['trading_symbol'])==(stop['quantity']-stop['filled_quantity'])


def test_rejected_stop_unwinds_without_duplicate_stop(tmp_path):
    s=StandardSession(tmp_path);s.broker.reject_stop=True
    s.close()
    assert len([r for r in s.broker.orders.values() if r['order_type']=='SL'])==1
    assert s.executor._net(s.executor._state())=={}


@pytest.mark.parametrize('lost_reply',[True,False])
def test_modify_uncertainty_reads_exact_original_without_retry(tmp_path,lost_reply):
    s=StandardSession(tmp_path);s.monitoring()
    s.broker.modify_reply_lost=lost_reply;s.broker.modify_unreadable=not lost_reply
    s.books[s.short_leg()['symbol']].update(bid=30.95,ask=31)
    for _ in range(4):s.tick()
    assert len([r for k,r in s.broker.writes if k=='MODIFY_SL'])==1
    assert len([r for r in s.broker.orders.values() if r['order_type']=='SL'])==1
    if not lost_reply:
        assert 'RECONCILIATION_REQUIRED' in s.journal.store.meta('premium-executor-status')['reason']
        record=next(iter(s.executor._state()['protections'].values()))
        assert s.protection.record(record['key'])['status']=='PROTECTION_RECONCILIATION_REQUIRED'


def test_fill_racing_a_modify_reconciles_terminal_original_and_closes_hedge(tmp_path):
    s=StandardSession(tmp_path);s.monitoring();s.broker.modify_fills=True
    s.books[s.short_leg()['symbol']].update(bid=30.95,ask=31)
    s.close()
    assert s.executor._net(s.executor._state())=={}
    assert len([r for k,r in s.broker.writes if k=='MODIFY_SL'])==1


@pytest.mark.parametrize('fault',['off','pause','paper','manual'])
def test_no_writes_when_disabled_or_manual_conflict(tmp_path,fault):
    s=StandardSession(tmp_path);s.monitoring();count=len(s.broker.writes)
    s.books[s.short_leg()['symbol']].update(bid=30.95,ask=31)
    if fault=='off':premium_strategy.set_intent(s.journal.store,False,s.now)
    if fault=='pause':s.pause.touch()
    if fault=='paper':s.gate.mode='paper'
    if fault=='manual':
        with s.journal.store.transaction() as db:
            db.execute('INSERT INTO pc_protected VALUES(?)',(s.short_leg()['symbol'],))
    for _ in range(3):s.tick()
    assert len(s.broker.writes)==count


def test_pending_hedge_never_creates_short_or_stop(tmp_path):
    s=StandardSession(tmp_path);s.broker.autofill=False
    for _ in range(5):s.tick()
    assert len(s.broker.orders)==1
    assert next(iter(s.broker.orders.values()))['transaction_type']=='BUY'
    assert not s.broker.smart
