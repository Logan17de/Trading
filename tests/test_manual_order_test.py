"""Three manual real-broker actions are isolated from strategy and auto jobs.

Everything here uses the fake broker and a private temporary journal.
"""
import pytest
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine.commissioning_direct import AUTH
from nifty_engine.agent_engine.owner_setup import submit_manual_buy
from test_commissioning_direct import session
from test_broker_commissioning import Session


def test_manual_buy_sends_one_limit_only_without_automatic_sl_or_exit(tmp_path):
    s=session(tmp_path)
    s.store.set_meta(AUTH,{})
    result=submit_manual_buy(s.store,s.commission,s.binding,s.plan,identity(s.plan))
    assert result['status'] in ('CHILD_FILLED','BUY_ACCEPTED','BUY_PENDING_OR_PARTIAL')
    assert [x[1] for x in s.broker.writes]==['/v1/order/create']
    assert s.broker.writes[0][2]['transaction_type']=='BUY'
    assert s.broker.quantity==s.plan['quantity']
    assert s.store.meta(AUTH)['state']=='RUNNING'
    with pytest.raises(ValueError):
        submit_manual_buy(s.store,s.commission,s.binding,s.plan,identity(s.plan))
    assert len(s.broker.writes)==1


def test_manual_stages_buy_sl_then_cancel_sl_before_single_sell(tmp_path):
    s=session(tmp_path)
    s.store.set_meta(AUTH,{})
    h=identity(s.plan)
    submit_manual_buy(s.store,s.commission,s.binding,s.plan,h)
    assert len(s.broker.writes)==1
    result=s.commission.protect(s.plan['id'],h)
    assert result['status']=='STOP_ACTIVE_OBSERVED'
    assert [x[1] for x in s.broker.writes]==['/v1/order/create','/v1/order-advance/create']
    with pytest.raises(ValueError,match='COMMISSIONING_CANCEL_STOP_BEFORE_CLOSE'):
        s.commission.close(s.plan['id'],h)
    assert len(s.broker.writes)==2
    canceled=s.commission.cancel_stop(s.plan['id'],h)
    assert canceled.get('command')=='cancel-stop'
    assert len(s.broker.writes)==3
    result=s.commission.close(s.plan['id'],h)
    assert result['status'] in ('FLAT_AWAITING_SECOND_READ','CLOSED_UNVERIFIED','COMPLETE')
    assert s.broker.quantity==0
    assert sum(1 for x in s.broker.writes if x[1]=='/v1/order/create' and x[2]['transaction_type']=='SELL')==1


def test_manual_buy_requires_direct_one_lot_and_exact_hash(tmp_path):
    s=session(tmp_path)
    s.store.set_meta(AUTH,{})
    with pytest.raises(ValueError):
        submit_manual_buy(s.store,s.commission,s.binding,s.plan,'0'*64)
    assert s.broker.writes==[]
    legacy=Session(tmp_path/'gtt')
    with pytest.raises(ValueError,match='EXACT_DIRECT_LIMIT_BUY_REQUIRED'):
        submit_manual_buy(legacy.store,legacy.commission,legacy.binding,legacy.plan,identity(legacy.plan))
    assert legacy.broker.writes==[]
