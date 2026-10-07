import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

import pytest

from nifty_engine.agent_engine.contracts import IST, dumps
from nifty_engine.agent_engine.dashboard import (DashboardCollector, account_summary, collect_charts,
    five_minute_candles, funds_summary, public_funds, handler, instrument_metadata, ordered_contracts, polling_loop, viewer_active, view_model)
from nifty_engine.agent_engine.market_check import allowed_request

NOW = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)


def test_groww_money_is_whitelisted_separate_from_investment_value_and_pnl():
    raw = dict(clear_cash=10000, net_margin_used=3000, collateral_available=2000, account_id='PRIVATE',
        fno_margin_details=dict(net_fno_margin_used=2500, option_buy_balance_available=7000,
            option_sell_balance_available=6000))
    value = funds_summary(raw)
    value.update(status='AVAILABLE', received_at=NOW.isoformat())
    model = view_model(dict(funds=value), protocol(), now=NOW)
    assert model['funds']['clear_cash_inr'] == 10000
    assert model['funds']['option_sell_available_inr'] == 6000
    assert model['account']['portfolio_value_inr'] is None
    assert model['account']['today_pnl_inr'] is None
    assert 'PRIVATE' not in dumps(model)
    for at in (NOW + timedelta(seconds=46), NOW - timedelta(seconds=1)):
        assert public_funds(dict(funds=value),at)['clear_cash_inr'] is None
    with pytest.raises(ValueError):funds_summary(dict(clear_cash=float('nan'), fno_margin_details={}))
    zero = funds_summary(dict(clear_cash=0, fno_margin_details={}))
    assert zero['clear_cash_inr'] == 0 and zero['option_buy_available_inr'] is None


def test_money_endpoint_remains_get_only_and_cannot_place_or_calculate_orders():
    url = 'https://api.groww.in/v1/margins/detail/user'
    assert allowed_request('GET',url,dashboard=True)
    assert not allowed_request('GET',url)
    assert not allowed_request('POST',url,dashboard=True)
    assert not allowed_request('POST','https://api.groww.in/v1/margins/detail/orders',dashboard=True)


def test_money_reads_every_five_seconds_and_failed_reads_do_not_keep_fresh_cash():
    current=[NOW]; calls=[]
    def money(**kwargs):
        calls.append(current[0])
        if len(calls)>1:raise OSError('PRIVATE error')
        return dict(clear_cash=10000,fno_margin_details=dict(option_sell_balance_available=5000))
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':100},
        get_order_list=lambda **kwargs:{'order_list':[]},get_positions_for_user=lambda **kwargs:{'positions':[]},
        get_historical_candles=lambda **kwargs:{'interval_in_minutes':5,'candles':[]},
        get_available_margin_details=money)
    collector=DashboardCollector(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),
        clock=lambda:current[0],background_history=False)
    assert collector.sample()['funds']['clear_cash_inr']==10000
    current[0]+=timedelta(seconds=4)
    assert collector.sample()['funds']['clear_cash_inr']==10000 and len(calls)==1
    current[0]+=timedelta(seconds=1)
    result=collector.sample()
    assert result['funds']['status']=='UNAVAILABLE' and len(calls)==2
    assert 'PRIVATE' not in dumps(result)


def test_independent_quote_position_and_order_reads_start_concurrently():
    orders_started, positions_started = threading.Event(), threading.Event()
    def quote(**kwargs):
        assert orders_started.wait(1) and positions_started.wait(1)
        return {'last_price':100}
    def orders(**kwargs):
        orders_started.set(); return {'order_list':[]}
    def positions(**kwargs):
        positions_started.set(); return {'positions':[]}
    broker=SimpleNamespace(get_quote=quote,get_order_list=orders,get_positions_for_user=positions,
        get_historical_candles=lambda **kwargs:{'interval_in_minutes':5,'candles':[]})
    collector=DashboardCollector(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),
        clock=lambda:NOW,background_history=False)
    result=collector.sample()
    assert all(result['probes'][i+'_quote']['ok'] for i in ('NIFTY','SENSEX','BANKNIFTY'))
    assert result['orders_status']=='AVAILABLE' and result['positions_status']=='AVAILABLE'


def protocol():
    from pathlib import Path
    return json.loads((Path(__file__).parents[1] / 'config/owner_strategies.json').read_text())


def ledger(pnls):
    return {'format':'dashboard-account-v1', 'as_of':NOW.isoformat(), 'capital_inr':100000,
        'portfolio_value_inr':101000, 'used_margin_inr':60000, 'available_margin_inr':40000,
        'unrealized_inr':500, 'strategy_capital_inr':{'everyday':10000},
        'closed_trades':[{'id':str(i),'strategy':'everyday','closed_at':NOW.isoformat(),
                          'net_pnl_inr':pnl} for i,pnl in enumerate(pnls)],
        'pnl_series':[], 'portfolio_series':[]}


@pytest.mark.parametrize('pnls,green,red', [([100,200],100,0), ([100,0],100,0),
                                          ([100,-200],50,50), ([-100,-200],0,100)])
def test_strategy_bar_measures_observed_non_loss_share(pnls,green,red):
    result=account_summary(ledger(pnls),NOW)
    row=result['strategy_results']['everyday']
    assert row['non_loss_pct']==green and row['loss_pct']==red
    assert row['net_pnl_inr']==sum(pnls) and row['return_pct']==sum(pnls)/10000*100
    assert row['closed_trades']==len(pnls)
    assert result['today_pnl_inr']==sum(pnls)+500
    assert result['margin_utilization_pct']==60


def test_no_trades_or_ledger_does_not_claim_one_hundred_percent():
    assert account_summary(None,NOW)['today_pnl_inr'] is None
    row=account_summary(ledger([]),NOW)['strategy_results']['everyday']
    assert row['closed_trades']==0 and row['non_loss_pct'] is None and row['return_pct'] is None


def test_old_or_corrupt_account_cannot_be_today_profit():
    value=ledger([100]);value['as_of']='2026-09-30T09:00:00+00:00'
    value['closed_trades'][0]['closed_at']=value['as_of']
    result=account_summary(value,NOW)
    assert result['status']=='STALE_LEDGER' and result['today_pnl_inr'] is None
    assert result['unrealized_inr'] is None
    for mutation in ('duplicate','future','unknown','secret'):
        bad=ledger([100])
        if mutation=='duplicate':bad['closed_trades']+=bad['closed_trades']
        elif mutation=='future':bad['closed_trades'][0]['closed_at']='2027-01-01T00:00:00Z'
        elif mutation=='unknown':bad['closed_trades'][0]['strategy']='unmapped'
        else:bad['api_key']='PRIVATE'
        with pytest.raises(ValueError):account_summary(bad,NOW)


def snapshot():
    return {'finished_at':NOW.isoformat(),'debug_key':'PRIVATE','probes':{
        'NIFTY_quote':{'ok':True,'received_at':NOW.isoformat(),'value':{'last_price':25000,
            'ohlc':{'high':25100,'low':24900,'close':24900},'secret':'PRIVATE'}},
        'NIFTY_chain':{'ok':True,'value':{'expiry':'2026-10-06','call_samples':[
            {'symbol':'NIFTY26O0625400CE','strike':25400,'listed_strike_offset':0,
             'quote':{'last_price':50,'bid_price':49,'offer_price':51,'private':'PRIVATE'}}]}}},
        'orders_status':'AVAILABLE', 'ordered_options':[{'symbol':'NIFTY26O0625400CE', 'index':'NIFTY',
            'side':'SELL','exchange':'NSE','strike':25400,'expiry':'2026-10-06','order_status':'POSITION',
            'groww_symbol':'NSE-NIFTY-06Oct26-25400-CE','quantity':65,
            'quote':{'last_price':50,'bid_price':49,'offer_price':51,'private':'PRIVATE'}}],
        'charts':{'NIFTY':{'candles':[{'at':'2026-10-01T09:15:00+05:30','close':24900}]},
            'NIFTY26O0625400CE':{'candles':[{'at':'2026-10-01T09:15:00+05:30','close':48,'private':'PRIVATE'}]}}}


def test_exact_option_series_and_private_field_filtering():
    result=view_model(snapshot(),protocol(),now=NOW)
    option=result['markets'][0]['options'][0]
    assert option['series'][0]['value']==48
    assert option['last_price']==50 and option['expiry']=='2026-10-06'
    assert result['markets'][0]['series'][0]['value']==24900
    assert result['markets'][0]['change']==100
    assert 'PRIVATE' not in dumps(result)
    assert result['execution_enabled'] is False and result['order_capability'] is False
    value=snapshot();value['charts'].pop('NIFTY26O0625400CE')
    missing=view_model(value,protocol(),now=NOW)['markets'][0]['options'][0]
    assert missing['series']==[] and missing['series_status']=='NO_OPTION_HISTORY'


def test_broker_contract_lookup_and_completed_option_candles():
    calls=[]
    def candles(**kwargs):
        calls.append(kwargs)
        return {'interval_in_minutes':5,'candles':[['2026-10-01T14:25:00',50,50,50,50],
                                                 ['2026-10-01T14:30:00',99,99,99,99]]}
    broker=SimpleNamespace(get_historical_candles=candles)
    charts=collect_charts(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),snapshot(),NOW)
    assert charts['NIFTY26O0625400CE']['groww_symbol']=='NSE-NIFTY-06Oct26-25400-CE'
    assert len(charts['NIFTY26O0625400CE']['candles'])==1
    assert all(c['candle_interval']=='5minute' for c in calls)
    assert any(c['segment']=='FNO' and c['groww_symbol']=='NSE-NIFTY-06Oct26-25400-CE' for c in calls)
    value=snapshot();value['ordered_options'][0].pop('groww_symbol')
    calls.clear();charts=collect_charts(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),value,NOW)
    assert charts['NIFTY26O0625400CE']['status']=='CONTRACT_NOT_CONFIRMED'
    assert all(c['segment']=='CASH' for c in calls)


def test_sampled_options_do_not_create_charts_without_orders():
    value=snapshot();value.pop('ordered_options')
    result=view_model(value,protocol(),now=NOW)
    assert all(not m['options'] for m in result['markets'])
    assert result['poll_interval_seconds']==5 and result['candle_interval_minutes']==5


def test_only_nonzero_active_positions_qualify_even_with_old_or_pending_orders():
    base={'trading_symbol':'NIFTY26O0625400CE','segment':'FNO','exchange':'NSE',
          'transaction_type':'SELL','quantity':65,'filled_quantity':0,'groww_order_id':'PRIVATE'}
    assert ordered_contracts([dict(base,order_status='REJECTED')],[])==[]
    assert ordered_contracts([dict(base,order_status='CANCELLED')],[])==[]
    pending=ordered_contracts([dict(base,order_status='OPEN')],[])
    assert pending==[]
    partial=ordered_contracts([dict(base,order_status='CANCELLED',filled_quantity=10)],[])
    assert partial==[]
    assert ordered_contracts([dict(base,order_status='EXECUTED',filled_quantity=65)],[])==[]
    positions=ordered_contracts([], [dict(base,quantity=-65),dict(base,quantity=0),
                         dict(base,trading_symbol='NIFTY26O0625800CE',quantity=65)])
    assert [p['side'] for p in positions]==['SELL','BUY']
    assert 'PRIVATE' not in dumps(positions)
    assert ordered_contracts([dict(base,order_status='OPEN',exchange='BSE')],[])==[]


def test_exact_public_instrument_metadata_and_no_index_substitute():
    requested={'NIFTY26O0625400CE':{'index':'NIFTY','exchange':'NSE'}}
    header='trading_symbol,exchange,segment,underlying_symbol,groww_symbol,expiry_date,strike_price,debug\n'
    csv=header+'NIFTY26O0625400CE,NSE,FNO,NIFTY,NSE-NIFTY-06Oct26-25400-CE,2026-10-06,25400,PRIVATE\n'
    result=instrument_metadata(csv,requested)
    assert result['NIFTY26O0625400CE']['strike']==25400 and 'PRIVATE' not in dumps(result)
    assert instrument_metadata(csv.replace('NSE,FNO','BSE,FNO'),requested)=={}
    assert instrument_metadata(csv.replace('06Oct26-25400-CE','06Oct26-25400-PE'),requested)=={}


def test_five_minute_bars_exclude_unfinished_or_invalid_candles():
    end=NOW.astimezone(IST)
    raw={'interval_in_minutes':5,'candles':[['2026-10-01T14:25:00',1,2,0,0],
             ['2026-10-01T14:30:00',3,4,2,3]]}
    assert len(five_minute_candles(raw,end))==1
    for bad in (dict(raw,interval_in_minutes=1),
                dict(raw,candles=[['2026-10-01T14:26:00',1,2,0,1]]),
                dict(raw,candles=[['2026-10-01T14:25:00',1,0,0,1]])):
        with pytest.raises(ValueError):five_minute_candles(bad,end)


def test_collector_rechecks_orders_each_cycle_and_caches_five_minute_history():
    calls=[]; current=[NOW]; rows=[{'trading_symbol':'NIFTY26O0625400CE','segment':'FNO','exchange':'NSE',
        'transaction_type':'SELL','quantity':65,'filled_quantity':65,'order_status':'EXECUTED'}]
    def orders(page,**kwargs):
        calls.append(('orders',page))
        return {'order_list':rows if page==0 else []}
    def candles(**kwargs):
        calls.append(('candles',kwargs))
        return {'interval_in_minutes':5,'candles':[['2026-10-01T14:25:00',1,2,0,1]]}
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':100,'secret':'PRIVATE'},
        get_order_list=orders,get_positions_for_user=lambda **kwargs:{'positions':[dict(rows[0],quantity=-65)] if rows else []},
        get_historical_candles=candles)
    market=SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None))
    collector=DashboardCollector(market,clock=lambda:current[0],background_history=False,
        metadata_loader=lambda req:{'NIFTY26O0625400CE':{'groww_symbol':'NSE-NIFTY-06Oct26-25400-CE'}})
    first=collector.sample(); assert first['orders_status']=='AVAILABLE' and first['sequence']==1
    assert len([c for c in calls if c[0]=='candles'])==4
    current[0]+=timedelta(seconds=5)
    second=collector.sample()
    assert second['sequence']==2 and len([c for c in calls if c[0]=='candles'])==4
    assert len([c for c in calls if c[0]=='orders'])==4
    assert 'PRIVATE' not in dumps(second)
    rows.clear();current[0]+=timedelta(seconds=5)
    third=collector.sample()
    assert third['ordered_options']==[] and 'NIFTY26O0625400CE' not in third['charts']
    def failed(**kwargs):raise RuntimeError('PRIVATE')
    broker.get_order_list=broker.get_positions_for_user=failed
    fourth=collector.sample()
    assert fourth['orders_status']=='UNAVAILABLE' and fourth['ordered_options']==[]
    assert 'PRIVATE' not in dumps(fourth)


def test_polling_is_five_seconds_non_overlapping_and_expires_without_viewer(tmp_path):
    elapsed=[0];starts=[]
    def sample():
        starts.append(elapsed[0]);elapsed[0]+=2
        return {'sequence':len(starts)}
    polling_loop(SimpleNamespace(sample=sample),tmp_path/'market-check-live.json',lambda:len(starts)<3,
        monotonic=lambda:elapsed[0],sleep=lambda n:elapsed.__setitem__(0,elapsed[0]+n))
    assert starts==[0,5,10]
    assert json.loads((tmp_path/'market-check-live.json').read_text())['sequence']==3
    lease=tmp_path/'viewer.lease';assert not viewer_active(lease)
    lease.touch();at=lease.stat().st_mtime
    assert viewer_active(lease,now=at+19) and not viewer_active(lease,now=at+21)


def test_slow_history_does_not_stop_current_quote_reads():
    started, release = threading.Event(), threading.Event()
    def history(**kwargs):
        started.set()
        if not release.wait(timeout=3):raise TimeoutError('test history timeout')
        return {'interval_in_minutes':5,'candles':[['2026-10-01T14:25:00',1,2,0,1]]}
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':100},
        get_order_list=lambda **kwargs:{'order_list':[]},
        get_positions_for_user=lambda **kwargs:{'positions':[]},get_historical_candles=history)
    collector=DashboardCollector(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),
        clock=lambda:NOW)
    try:
        first=collector.sample()
        assert started.wait(timeout=2) and first['history_refreshing']
        second=collector.sample()
        assert second['sequence']==2 and second['status']=='READ_ONLY_DATA_AVAILABLE'
        assert second['history_refreshing'] and second['charts']=={}
        release.set();collector.history_future.result(timeout=2)
        third=collector.sample()
        assert not third['history_refreshing'] and len(third['charts'])==3
    finally:
        release.set();collector.close()


def test_dashboard_transport_reads_orders_but_rejects_all_order_writes():
    assert allowed_request('GET','https://api.groww.in/v1/order/list',dashboard=True)
    assert allowed_request('GET','https://api.groww.in/v1/positions/user',dashboard=True)
    assert not allowed_request('GET','https://api.groww.in/v1/order/list')
    for path in ('create','modify','cancel','list'):
        assert not allowed_request('POST','https://api.groww.in/v1/order/'+path,dashboard=True)
    assert allowed_request('GET','https://growwapi-assets.groww.in/instruments/instrument.csv',dashboard=True)
    assert not allowed_request('GET','https://growwapi-assets.groww.in/instruments/instrument.csv?token=PRIVATE',dashboard=True)


def test_local_server_never_serves_secrets_or_accepts_foreign_refresh():
    refreshed=[]
    state=SimpleNamespace(token='test-local-token', read=lambda:view_model(None,protocol(),now=NOW),
        refresh=lambda:refreshed.append(True) or 'STARTED')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(state))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(url+'/api/dashboard') as response:
            assert response.headers['Cache-Control']=='no-store'
            assert 'frame-ancestors' in response.headers['Content-Security-Policy']
            assert json.load(response)['order_capability'] is False
        with pytest.raises(HTTPError) as error:urlopen(url+'/../api_key.txt')
        assert error.value.code==404
        for headers in ({}, {'X-Local-Token':state.token,'Origin':'https://example.com'},
                        {'X-Local-Token':state.token,'Host':'malicious.example'}):
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url+'/api/refresh',data=b'',headers=headers,method='POST'))
            assert error.value.code==403
        assert not refreshed
        with urlopen(Request(url+'/api/refresh',data=b'',headers={'X-Local-Token':state.token},method='POST')) as response:
            assert json.load(response)['status']=='STARTED'
        assert len(refreshed)==1
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
