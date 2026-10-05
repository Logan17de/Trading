import copy
import json
from datetime import datetime, timedelta

import pytest

from nifty_engine.agent_engine.contracts import dumps
from nifty_engine.agent_engine.dashboard import ordered_contracts, view_model
from nifty_engine.agent_engine.pc_control import JST, PcJournal, collection_window, in_window
from nifty_engine.agent_engine.session import ObservedLines, SessionJournal

NOW=datetime(2026,10,5,13,30,tzinfo=JST)
SYMBOL='NIFTY26O0625400CE'


def record_prices(sessions, at, price):
    value=raw(); value['checked_at']=(at-timedelta(seconds=1)).isoformat();value['finished_at']=at.isoformat()
    for probe in value['probes'].values():probe['received_at']=at.isoformat()
    option=value['ordered_options'][0];option['received_at']=at.isoformat();option['quote']['last_price']=price
    sessions.record(value,at)


def test_live_line_recovers_actual_intraperiod_prices_after_restart_and_deduplicates(tmp_path):
    sessions=SessionJournal(PcJournal(tmp_path/'private.sqlite').store)
    for n,price in enumerate([50,52,49,51]):record_prices(sessions,NOW+timedelta(seconds=n*5),price)
    lines=ObservedLines(sessions.store)
    result=lines.read(NOW+timedelta(seconds=15))
    assert [r['value'] for r in result[SYMBOL]] == [50,52,49,51]
    assert len(lines.read(NOW+timedelta(seconds=15))[SYMBOL]) == 4
    assert ObservedLines(sessions.store).read(NOW+timedelta(seconds=15)) == result
    assert set(result[SYMBOL][0]) == {'at','value'} and 'DO_NOT_EXPORT' not in dumps(result)
    assert len(sessions.store.read('SELECT * FROM pc_bars')) == 1  # Research bars stay separate.


def test_live_line_excludes_preopen_future_and_failed_quotes_and_resets_next_day(tmp_path):
    sessions=SessionJournal(PcJournal(tmp_path/'private.sqlite').store)
    record_prices(sessions,NOW.replace(hour=12,minute=44),40)
    record_prices(sessions,NOW,50)
    record_prices(sessions,NOW+timedelta(seconds=5),float('nan'))
    record_prices(sessions,NOW+timedelta(seconds=10),70)
    lines=ObservedLines(sessions.store)
    assert [r['value'] for r in lines.read(NOW+timedelta(seconds=5))[SYMBOL]] == [50]
    assert lines.read(NOW+timedelta(days=1)) == {}


def raw():
    return {'execution_mode':'OBSERVE','order_capability':False,'status':'READ_ONLY_DATA_AVAILABLE',
        'checked_at':(NOW-timedelta(seconds=1)).isoformat(),'finished_at':NOW.isoformat(),
        'orders_status':'AVAILABLE','probes':{i+'_quote':{'ok':True,'received_at':NOW.isoformat(),
            'value':{'last_price':25000,'private':'DO_NOT_EXPORT','ohlc':{},'depth':{}}}
            for i in ('NIFTY','SENSEX','BANKNIFTY')},
        'ordered_options':[{'symbol':SYMBOL,'index':'NIFTY','side':'SELL','expiry':'2026-10-06',
            'order_status':'POSITION','quantity':65,
            'received_at':NOW.isoformat(),'quote':{'last_price':50},'groww_order_id':'DO_NOT_EXPORT'}],
        'charts':{'NIFTY':{'candles':[dict(at=(NOW-timedelta(minutes=5)).isoformat(),open=25000,high=25010,low=24990,close=25000),
            dict(at=NOW.isoformat(),open=999,high=999,low=999,close=999)]}},'api_key':'DO_NOT_EXPORT'}


def test_session_is_durable_deduplicated_and_never_persists_credentials_or_incomplete_bar(tmp_path):
    journal=PcJournal(tmp_path/'private.sqlite'); sessions=SessionJournal(journal.store)
    assert sessions.record(raw(),NOW)
    assert not sessions.record(raw(),NOW)
    again=SessionJournal(PcJournal(tmp_path/'private.sqlite').store)
    assert len(again.store.read('SELECT * FROM pc_observations'))==1
    assert len(again.store.read('SELECT * FROM pc_bars'))==1
    assert 'DO_NOT_EXPORT' not in dumps(again.store.read('SELECT body FROM pc_observations'))
    assert again.coverage('2026-10-05')['status']=='INCOMPLETE_SESSION'
    changed=raw();changed['charts']['NIFTY']['candles'][0]['close']=25001
    sessions.record(changed,NOW)
    assert sessions.store.read("SELECT key FROM meta WHERE key LIKE 'bar-revision-%'")
    assert json.loads(sessions.store.read('SELECT body FROM pc_bars')[0]['body'])['close']==25000


@pytest.mark.parametrize('field,value',[('execution_mode','REPLAY'),('order_capability',True),('status','BLOCKED')])
def test_session_rejects_simulation_and_failed_collectors(tmp_path,field,value):
    sessions=SessionJournal(PcJournal(tmp_path/'private.sqlite').store)
    value_raw=raw();value_raw[field]=value
    with pytest.raises(ValueError):sessions.record(value_raw,NOW)


def test_publisher_preserves_unknown_net_accounting_news_and_source_time(tmp_path):
    sessions=SessionJournal(PcJournal(tmp_path/'private.sqlite').store)
    body=sessions.publish(raw(),NOW)
    assert body['mode']=='OBSERVE' and body['source']=='groww-local-session-v1'
    assert body['portfolio']['realized_pnl'] is None and body['portfolio']['unrealized_pnl'] is None
    assert 'NEWS_HIGH_UNKNOWN_OR_STALE' in body['reasons']
    assert sessions.store.latest()==body
    assert 'DO_NOT_EXPORT' not in dumps(body)
    bad=raw();bad['finished_at']=(NOW+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):sessions.publish(bad,NOW)


def test_unvalidated_or_stale_news_never_clears_unknown(tmp_path):
    sessions=SessionJournal(PcJournal(tmp_path/'private.sqlite').store)
    news={'risk':'LOW','assessed_at':NOW.isoformat(),'articles':[]}
    assert 'NEWS_HIGH_UNKNOWN_OR_STALE' in sessions.publish(raw(),NOW,news=news)['reasons']
    articles=[dict(id='rbi',title='Policy news',source='rbi.org.in',published_at=NOW.isoformat()),
        dict(id='et',title='Market news',source='economictimes.indiatimes.com',published_at=NOW.isoformat())]
    news.update(summary='Reviewed evidence',evidence_ids=['rbi','et'],articles=articles,
        assessed_at=(NOW-timedelta(minutes=4)).isoformat())
    assert 'NEWS_HIGH_UNKNOWN_OR_STALE' in sessions.publish(raw(),NOW,news=news)['reasons']
    news['assessed_at']=NOW.isoformat()
    assert 'NEWS_HIGH_UNKNOWN_OR_STALE' not in sessions.publish(raw(),NOW,news=news)['reasons']


def test_session_metadata_cannot_persist_untyped_private_values(tmp_path):
    sessions=SessionJournal(PcJournal(tmp_path/'private.sqlite').store)
    value=raw();option=value['ordered_options'][0]
    option.update(lot_size={'secret':'DO_NOT_EXPORT'},expiry='DO_NOT_EXPORT',received_at='DO_NOT_EXPORT')
    option['quote']={'ohlc':None,'depth':None}
    sessions.record(value,NOW)
    assert 'DO_NOT_EXPORT' not in dumps(sessions.store.read('SELECT body FROM pc_observations'))


def position():
    return dict(trading_symbol=SYMBOL,exchange='NSE',segment='FNO',product='NRML',quantity=65,net_price=110)


def test_open_positions_get_verified_entry_stop_and_oco_target_without_private_ids():
    p=position()
    stop=dict(p,quantity=65,order_type='SL',transaction_type='SELL',order_status='TRIGGER_PENDING',
              filled_quantity=0,trigger_price=95,groww_order_id='DO_NOT_EXPORT')
    oco=dict(p,smart_order_type='OCO',product_type='NRML',status='ACTIVE',
             stop_loss={'trigger_price':'97.5'},target={'trigger_price':'150'},smart_order_id='DO_NOT_EXPORT')
    result=ordered_contracts([stop],[p],[oco])[0]
    assert {(r['kind'],r['price']) for r in result['price_levels']}=={('ENTRY',110),('SL',95),('SL',97.5),('TARGET',150)}
    assert 'DO_NOT_EXPORT' not in dumps(result)
    assert ordered_contracts([stop],[],[oco])==[]
    assert ordered_contracts([stop],[dict(p,quantity=0)],[oco])==[]
    assert [r['kind'] for r in ordered_contracts([dict(stop,order_status='CANCELLED')],[p],[dict(oco,status='COMPLETED')])[0]['price_levels']]==['ENTRY']
    assert [r['kind'] for r in ordered_contracts([dict(stop,transaction_type='BUY')],[p],[dict(oco,product_type='MIS')])[0]['price_levels']]==['ENTRY']


def test_observer_covers_whole_session_but_action_window_remains_exact():
    assert collection_window(NOW.replace(hour=12,minute=40))
    assert collection_window(NOW.replace(hour=19,minute=44))
    assert not collection_window(NOW.replace(hour=19,minute=45))
    assert not in_window(NOW.replace(hour=18,minute=15))
    assert not in_window(NOW.replace(hour=19,minute=0))
