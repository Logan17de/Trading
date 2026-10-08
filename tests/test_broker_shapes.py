import copy

import pytest

from nifty_engine.agent_engine.broker_shapes import smart_order


@pytest.mark.parametrize('row',[
    {'order':{'orderType':'LIMIT','transactionType':'BUY','price':12.5}},
    {'order':{'order_type':'LIMIT','transaction_type':'BUY','price':12.5}},
    {'order':{'order_type':'LIMIT','orderType':'LIMIT','transactionType':'BUY','price':12.5}},
])
def test_smart_aliases_preserve_values_without_mutating_evidence(row):
    row.update(smart_order_id='synthetic-id',reference_id='synthetic-reference',trigger_price=12)
    original=copy.deepcopy(row)
    value=smart_order(row)
    assert value==dict(smart_order_id='synthetic-id',reference_id='synthetic-reference',trigger_price=12,
                      order=dict(order_type='LIMIT',transaction_type='BUY',price=12.5))
    assert row==original


@pytest.mark.parametrize('order',[
    dict(order_type='LIMIT',orderType='MARKET'),
    dict(transaction_type='BUY',transactionType='SELL'),
    dict(order_type=None,orderType='LIMIT'),
])
def test_conflicting_broker_aliases_are_rejected(order):
    with pytest.raises(ValueError,match='SMART_ORDER_FIELD_CONFLICT'):
        smart_order({'order':order})


def test_missing_fields_are_never_invented():
    assert smart_order({'order':{}})=={'order':{}}
    with pytest.raises(ValueError):smart_order(None)


def test_protection_reader_accepts_observed_spelling_but_rejects_changed_price():
    from types import SimpleNamespace
    from nifty_engine.agent_engine.oracle_protection import PersistentProtection
    expected=dict(trading_symbol='NIFTY26O2925000CE',quantity=65,product_type='NRML',exchange='NSE',
                  duration='DAY',trigger_direction='UP',trigger_price='10',
                  order=dict(order_type='LIMIT',transaction_type='BUY',price='10.05'))
    row={**expected,'order':dict(orderType='LIMIT',transactionType='BUY',price=10.05),
         'smart_order_id':'synthetic-parent','smart_order_type':'GTT','trigger_price':10}
    record=dict(request=expected,reference='synthetic-reference',smart_id='synthetic-parent')
    protection=object.__new__(PersistentProtection)
    protection.transport=SimpleNamespace(get_smart_order=lambda **kw:copy.deepcopy(row))
    assert protection._full(record)['order']['transaction_type']=='BUY'
    row['order']['price']=11
    with pytest.raises(ValueError,match='SMART_PROTECTION_READBACK_DIFFERS'):
        protection._full(record)
