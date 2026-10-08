"""All order routes in these tests consume capabilities through a fake broker."""
import copy
from datetime import timedelta

import pytest

from test_broker_commissioning import Session
from test_commissioning_stop import stop_trigger
from nifty_engine.agent_engine import commissioning_direct as direct
from nifty_engine.agent_engine import commissioning_stop as protection
from nifty_engine.agent_engine.broker_commissioning import PREFIX, validated_evidence
from nifty_engine.agent_engine.contracts import identity


def session(tmp_path,index='NIFTY',*,fill=True,lost=False):
    s=Session(tmp_path,index)
    s.plan.update(format=protection.DIRECT_FORMAT,trigger_price='10.00',buy_limit_price='10.00',
                  stop_trigger_price='7.00',stop_limit_price='6.95',sell_limit_price='12.00')
    original=s.broker.place_order
    def place(**args):
        if args['transaction_type']!='BUY':return original(**args)
        body={k:v for k,v in args.items() if k!='timeout'}
        s.broker._write('POST','/v1/order/create',body)
        identifier='buy-'+args['order_reference_id'];qty=args['quantity'] if fill else 0
        s.broker.orders[identifier]=dict(body,groww_order_id=identifier,filled_quantity=qty,
            order_status='EXECUTED' if qty else 'OPEN',average_fill_price=args['price'] if qty else 0)
        s.broker.quantity+=qty
        if lost:raise TimeoutError('provider-private-data')
        return dict(groww_order_id=identifier,order_reference_id=args['order_reference_id'])
    s.broker.place_order=place
    s.store.set_meta(direct.AUTH,dict(plan_id=s.plan['id'],plan_hash=identity(s.plan),binding=s.binding,state='RUNNING'))
    return s


def tick(s):
    s.now+=timedelta(seconds=6);s.commission=s.new_process()
    return direct.advance(s.commission,s.plan)


@pytest.mark.parametrize('index',['NIFTY','SENSEX'])
def test_one_owner_action_places_limit_then_stop_and_follows_target_without_second_sell(tmp_path,index):
    s=session(tmp_path,index)
    assert s.commission.preview(s.plan)['broker_writes'] is False
    result=tick(s)
    assert result['status']=='STOP_ACTIVE_OBSERVED'
    assert [r[1] for r in s.broker.writes]==['/v1/order/create','/v1/order-advance/create']
    assert s.broker.writes[0][2]['order_type']=='LIMIT'
    assert len(s.broker.parents)==1 and next(iter(s.broker.parents.values()))['trigger_direction']=='DOWN'
    assert not result['facts']['exact_child_link']  # Direct BUY is not a generated GTT child.
    tick(s)
    assert s.commission.status(s.plan['id'])['facts']['stop_persisted']
    s.market.quote=lambda *a,**k:dict(last_price=12.1,bid_price=12.05,offer_price=12.1,last_trade_time=s.now.timestamp())
    tick(s)  # target reached: cancel only the exact stop
    assert s.broker.writes[-1][1].startswith('/v1/order-advance/cancel/')
    assert not any(w[1]=='/v1/order/create' and w[2]['transaction_type']=='SELL' for w in s.broker.writes)
    tick(s)  # confirmed inactive: one sell
    assert s.broker.quantity==0
    result=tick(s)
    assert result['status']=='CLOSED_UNVERIFIED'  # No false generated-child evidence.
    count=len(s.broker.writes);tick(s);assert len(s.broker.writes)==count
    with pytest.raises(ValueError):validated_evidence(s.store,s.plan['id'],s.binding)
    assert s.pause.exists() and not s.store.meta('premium-algo-intent')


def test_natural_stop_fill_proves_exact_generated_exit_and_all_required_facts(tmp_path):
    s=session(tmp_path);tick(s);tick(s)
    stop_trigger(s)
    tick(s);result=tick(s)
    assert result['status']=='COMPLETE' and all(result['facts'].values())
    proof=validated_evidence(s.store,s.plan['id'],s.binding)
    assert s.commission.verify_ready_to_arm(s.plan['id'])==proof
    assert len(s.broker.writes)==2 and s.broker.quantity==0


def test_lost_buy_receipt_recovers_exact_reference_without_repeat(tmp_path):
    s=session(tmp_path,lost=True);tick(s);tick(s)
    assert len([r for r in s.broker.writes if r[1]=='/v1/order/create'])==1
    assert s.store.meta(PREFIX+s.plan['id'])['create_receipt']['source']=='ORIGINAL_REFERENCE_READBACK'


def test_no_auto_stop_until_full_terminal_buy_and_no_auto_resubmit(tmp_path):
    s=session(tmp_path,fill=False)
    for _ in range(3):assert tick(s)['status']=='BUY_PENDING_OR_PARTIAL'
    assert len(s.broker.writes)==1
    row=next(iter(s.broker.orders.values()));row.update(order_status='EXECUTED',filled_quantity=s.plan['quantity'],average_fill_price=10)
    s.broker.quantity=s.plan['quantity']
    assert tick(s)['status']=='STOP_ACTIVE_OBSERVED'
    assert len(s.broker.writes)==2


def test_no_owner_authorization_no_write(tmp_path):
    s=session(tmp_path);s.store.set_meta(direct.AUTH,{})
    with pytest.raises(ValueError,match='AUTHORIZATION_REQUIRED'):tick(s)
    assert not s.broker.writes


def test_crash_before_buy_record_does_not_retry_from_authorization(tmp_path):
    s=session(tmp_path);auth=s.store.meta(direct.AUTH);auth['submit_attempted']=s.now.isoformat();s.store.set_meta(direct.AUTH,auth)
    with pytest.raises(ValueError,match='RECONCILIATION_REQUIRED'):tick(s)
    assert not s.broker.writes


def test_changed_manual_order_blocks_automatic_protection(tmp_path):
    s=session(tmp_path,fill=False);tick(s)
    row=next(iter(s.broker.orders.values()));row.update(order_status='EXECUTED',filled_quantity=s.plan['quantity'],average_fill_price=10)
    s.broker.quantity=s.plan['quantity']
    s.broker.orders['manual']=dict(row,groww_order_id='manual',order_reference_id='manual')
    result=tick(s)
    assert result['status']=='RECONCILIATION_REQUIRED' and len(s.broker.writes)==1


def test_unknown_stop_create_never_retries_or_sends_target_sell(tmp_path):
    s=session(tmp_path)
    def lost(**args):
        s.broker._write('POST','/v1/order-advance/create',{k:v for k,v in args.items() if k!='timeout'})
        raise TimeoutError('private')
    s.broker.create_smart_order=lost
    assert tick(s)['status']=='RECONCILIATION_REQUIRED'
    tick(s);assert len(s.broker.writes)==2


def test_stop_cancel_fill_race_never_submits_a_duplicate_sell(tmp_path):
    s=session(tmp_path);tick(s);tick(s)
    s.market.quote=lambda *a,**k:dict(last_price=12.1,bid_price=12.05,offer_price=12.1,last_trade_time=s.now.timestamp())
    def race(**args):
        s.broker._write('POST','/v1/order-advance/cancel/FNO/GTT/'+args['smart_order_id'],None)
        stop_trigger(s)
        return dict(smart_order_id=args['smart_order_id'],status='CANCELLED')
    s.broker.cancel_smart_order=race
    tick(s);result=tick(s)
    assert result['status']=='COMPLETE' and s.broker.quantity==0
    assert len(s.broker.writes)==3


def test_tampered_direct_evidence_cannot_arm(tmp_path):
    s=session(tmp_path);tick(s);tick(s);stop_trigger(s);tick(s);tick(s)
    record=s.store.meta(PREFIX+s.plan['id']);record['request']['price']=11
    s.store.set_meta(PREFIX+s.plan['id'],record)
    with pytest.raises(ValueError):validated_evidence(s.store,s.plan['id'],s.binding)


def test_partial_buy_is_explicitly_unprotected_and_needs_review(tmp_path):
    s=session(tmp_path,fill=False);tick(s)
    row=next(iter(s.broker.orders.values()));row.update(filled_quantity=1,average_fill_price=10)
    s.broker.quantity=1
    result=tick(s)
    assert result['reason']=='COMMISSIONING_PARTIAL_BUY_REQUIRES_REVIEW'
    assert not result['facts']['stop_order_write'] and len(s.broker.writes)==1


def test_partial_terminal_stop_exit_needs_attention_without_another_sell(tmp_path):
    s=session(tmp_path);tick(s);tick(s);stop_trigger(s)
    row=next(r for r in s.broker.orders.values() if r['transaction_type']=='SELL')
    row.update(order_status='CANCELLED',filled_quantity=5)
    s.broker.quantity=s.plan['quantity']-5
    with pytest.raises(ValueError,match='COMMISSIONING_EXIT_REMAINDER_REQUIRES_REVIEW'):tick(s)
    assert len(s.broker.writes)==2


def test_stop_cancelled_outside_target_path_alerts_for_unprotected_position(tmp_path):
    s=session(tmp_path);tick(s);tick(s)
    next(iter(s.broker.parents.values()))['status']='CANCELLED'
    with pytest.raises(ValueError,match='COMMISSIONING_STOP_INACTIVE_WITH_OPEN_POSITION'):tick(s)
    assert len(s.broker.writes)==2


def test_unfilled_target_order_is_monitored_without_resubmission(tmp_path):
    s=session(tmp_path);tick(s);tick(s)
    s.market.quote=lambda *a,**k:dict(last_price=12.1,bid_price=12.05,offer_price=12.1,last_trade_time=s.now.timestamp())
    tick(s)
    def pending(**args):
        body={k:v for k,v in args.items() if k!='timeout'}
        s.broker._write('POST','/v1/order/create',body)
        identifier='sell-'+args['order_reference_id']
        s.broker.orders[identifier]=dict(body,groww_order_id=identifier,filled_quantity=0,
            order_status='OPEN',average_fill_price=0)
        return dict(groww_order_id=identifier,order_reference_id=args['order_reference_id'])
    s.broker.place_order=pending
    assert tick(s)['status']=='CLOSE_PENDING_OR_PARTIAL'
    result=tick(s)
    assert result['status']=='CLOSE_PENDING_OR_PARTIAL'
    assert result['attention']=='COMMISSIONING_TARGET_LIMIT_PENDING_WITHOUT_STOP'
    assert len(s.broker.writes)==4
