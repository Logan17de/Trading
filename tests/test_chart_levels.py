"""Active-position and exact protective-price matching regression checks."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.dashboard import ordered_contracts
from nifty_engine.agent_engine.market_check import allowed_request
from nifty_engine.agent_engine.smart_read import SmartOrderReader

SYMBOL = 'NIFTY26O0823500CE'
NOW = datetime(2026,10,5,4,tzinfo=timezone.utc)


def position(**changes):
    return dict(dict(trading_symbol=SYMBOL,exchange='NSE',segment='FNO',
        product='NRML',quantity=65,net_price=100), **changes)


def oco(**changes):
    return dict(dict(trading_symbol=SYMBOL,exchange='NSE',segment='FNO',
        product_type='NRML',quantity=65,smart_order_type='OCO',status='ACTIVE',
        stop_loss={'trigger_price':'75'},target={'trigger_price':'150'}), **changes)


def test_average_from_groww_and_exact_carried_position_fallback():
    assert ordered_contracts([], [position()])[0]['price_levels'][0]['price']==100
    carried = position(net_price=0,net_carry_forward_quantity=65,net_carry_forward_price=95)
    assert ordered_contracts([], [carried])[0]['price_levels'][0]['price']==95
    assert ordered_contracts([], [dict(carried,net_carry_forward_quantity=130)])[0]['price_levels']==[]
    # A closing fill never becomes a nonexistent position's entry line.
    assert ordered_contracts([position(order_status='EXECUTED',transaction_type='SELL')],[])==[]


def test_product_rows_have_weighted_entry_and_partial_coverage_is_explicit():
    result = ordered_contracts([], [position(),position(product='MIS',quantity=130,net_price=160)],
        [oco(quantity=32)])[0]
    assert result['quantity']==195
    assert result['price_levels'][0]['price']==140
    assert result['price_levels'][1]['quantity']==32
    assert result['price_levels'][1]['product']=='NRML'
    missing = ordered_contracts([], [position(),position(product='MIS',net_price=None)])[0]
    assert not missing['price_levels']


@pytest.mark.parametrize('changed',[
    {'exchange':'BSE'}, {'trading_symbol':'NIFTY26O0824000CE'}, {'product_type':'MIS'},
    {'status':'CANCELLED'}, {'quantity':130}, {'quantity':0},
    {'stop_loss':{'trigger_price':'160'}}, {'stop_loss':None,'target':{'trigger_price':'nan'}},
])
def test_unrelated_cancelled_oversized_or_invalid_protection_is_not_drawn(changed):
    levels = ordered_contracts([], [position()], [oco(**changed)])[0]['price_levels']
    assert [r['kind'] for r in levels]==['ENTRY']


def test_short_oco_prices_have_inverse_direction_and_unknown_product_does_not_match():
    assert [r['kind'] for r in ordered_contracts([], [position(quantity=-65)], [oco()])[0]['price_levels']]==['ENTRY']
    levels = ordered_contracts([], [position(quantity=-65)],
        [oco(stop_loss={'trigger_price':'140'},target={'trigger_price':'60'})])[0]['price_levels']
    assert {r['kind'] for r in levels}=={'ENTRY','SL','TARGET'}
    assert [r['kind'] for r in ordered_contracts([], [position(product=None)],
        [oco(product_type=None)])[0]['price_levels']]==['ENTRY']


@pytest.mark.parametrize('quantity,side,direction,kind',[(65,'SELL','UP','TARGET'),
    (65,'SELL','DOWN','SL'),(-65,'BUY','DOWN','TARGET'),(-65,'BUY','UP','SL')])
def test_gtt_marker_requires_exact_exit_side_and_trigger_direction(quantity,side,direction,kind):
    record=dict(oco(),smart_order_type='GTT',trigger_price='110',trigger_direction=direction,
        order={'transaction_type':side})
    levels=ordered_contracts([], [position(quantity=quantity)], [record])[0]['price_levels']
    assert levels[-1]['kind']==kind and levels[-1]['price']==110
    record['order']['transaction_type']='BUY' if side=='SELL' else 'SELL'
    assert [r['kind'] for r in ordered_contracts([], [position(quantity=quantity)], [record])[0]['price_levels']]==['ENTRY']


def test_smart_list_without_contract_fetches_details_and_refreshes_in_five_seconds():
    reader = SmartOrderReader(SimpleNamespace(), SimpleNamespace(wait=lambda:None))
    calls=[]
    def read(path,params=None):
        calls.append((path,params))
        if path.endswith('/list'):
            assert params['status']=='ACTIVE' and params['smart_order_type'] in ('OCO','GTT')
            if params['smart_order_type']=='GTT':return {'orders':[]}
            return {'orders':[{'smart_order_id':'oco_test','smart_order_type':'OCO','status':'ACTIVE'}]}
        return dict(oco(),smart_order_id='oco_test')
    reader._get=read
    result=reader([position()],NOW)
    assert result[0]['trading_symbol']==SYMBOL and len(calls)==3
    reader([position()],NOW+timedelta(seconds=4))
    assert len(calls)==3
    reader([position()],NOW+timedelta(seconds=5))
    assert len(calls)==6
    assert allowed_request('GET','https://api.groww.in'+calls[1][0],dashboard=True)
    for method in ('POST','PUT','DELETE'):
        assert not allowed_request(method,'https://api.groww.in'+calls[1][0],dashboard=True)
    assert not allowed_request('GET','https://api.groww.in'+calls[1][0])


def test_smart_details_must_match_list_identity_and_bounded_read_count():
    reader = SmartOrderReader(SimpleNamespace(), SimpleNamespace(wait=lambda:None))
    reader._get=lambda path,params=None: {'orders':[{'smart_order_id':'a'}]} if params else dict(oco(),smart_order_id='b')
    with pytest.raises(ValueError):reader([position()],NOW)
    reader._get=lambda path,params=None: {'orders':[{'smart_order_id':str(n)} for n in range(50)]} if params else dict(oco(trading_symbol='OTHER'),smart_order_id=path.split('/')[-1])
    with pytest.raises(ValueError):reader([position()],NOW)


def test_independent_protection_lists_overlap_and_failed_read_is_not_cached():
    import threading
    reader=SmartOrderReader(SimpleNamespace(),SimpleNamespace(wait=lambda:None))
    began={kind:threading.Event() for kind in ('OCO','GTT')}
    fail=[False]
    calls=[]
    def read(path,params=None):
        kind=params['smart_order_type']; calls.append(kind); began[kind].set()
        assert began['GTT' if kind=='OCO' else 'OCO'].wait(1)
        if fail[0] and kind=='GTT':raise OSError('unavailable')
        return {'orders':[]}
    reader._get=read
    assert reader([position()],NOW)==[]
    assert sorted(calls)==['GTT','OCO']
    fail[0]=True
    with pytest.raises(OSError):reader([position()],NOW+timedelta(seconds=5))
    fail[0]=False
    assert reader([position()],NOW+timedelta(seconds=6))==[]
    assert len(calls)==6  # Failure did not advance the cache's timestamp.
