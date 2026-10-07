"""Stream master reuse keeps exact contracts while refreshing ATM/date selection."""
import csv
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import impulse_feed as module
from nifty_engine.agent_engine.store import Store


NOW=datetime(2026,10,6,4,tzinfo=timezone.utc)


def master(prefix='a'):
    rows=[]
    for index,exchange,atm,step in (('NIFTY','NSE',25000,50),('SENSEX','BSE',80000,100)):
        for expiry in ('2026-10-05','2026-10-06','2026-10-08'):
            for offset in range(-5,9):
                for kind in ('CE','PE'):
                    strike=atm+offset*step
                    symbol=f'{index}-{expiry}-{strike}{kind}'
                    rows.append(dict(underlying_symbol=index,segment='FNO',exchange=exchange,
                        trading_symbol=symbol,instrument_type=kind,expiry_date=expiry,strike_price=str(strike),
                        exchange_token=prefix+symbol,groww_symbol='groww-'+symbol,provider_extra='preserved'))
            rows.append(dict(underlying_symbol=index,segment='FNO',exchange=exchange,
                trading_symbol=index+'-'+expiry+'FUT',instrument_type='FUT',expiry_date=expiry,
                exchange_token=prefix+index+expiry+'F',groww_symbol=index+'-future'))
    rows.extend([
        dict(trading_symbol='RELIANCE',exchange='NSE',segment='CASH',exchange_token='cash'),
        dict(trading_symbol='RELIANCE',exchange='BSE',segment='CASH',exchange_token='wrong-cash'),
        dict(underlying_symbol='NIFTY',trading_symbol='WRONGCE',exchange='BSE',segment='FNO',
             expiry_date='2026-10-06',strike_price='25000',exchange_token='wrong-exchange'),
        dict(underlying_symbol='OTHER',trading_symbol='OTHERCE',exchange='NSE',segment='FNO',
             expiry_date='2026-10-06',strike_price='25000',exchange_token='unrelated'),
    ])
    output=io.StringIO();writer=csv.DictWriter(output,fieldnames=sorted({k for r in rows for k in r}))
    writer.writeheader();writer.writerows(rows);return output.getvalue()


def snapshot(nifty=25000,sensex=80000):
    return {'probes':{index+'_quote':{'value':{'last_price':price}}
                     for index,price in (('NIFTY',nifty),('SENSEX',sensex))}}


def test_cached_rows_preserve_exact_exchange_fields_and_recompute_day_and_atm():
    text=master();rows=module._master_rows(text)
    assert all(r.get('trading_symbol')!='OTHERCE' for r in rows)
    with pytest.raises(TypeError):rows[0]['exchange_token']='changed'
    first=module.universe(text,snapshot(),NOW)
    assert first==module._universe(rows,snapshot(),NOW)
    assert 'WRONGCE' not in first and 'OTHERCE' not in first
    assert first['NIFTY@RELIANCE']['exchange_token']=='cash'
    for index in ('NIFTY','SENSEX'):
        options=[r for r in first.values() if r['index']==index and r['role']=='option']
        assert len(options)==14 and {r['expiry'] for r in options}=={'2026-10-06'}
        assert {r['offset'] for r in options}==set(range(-3,4))
        assert all(r['provider_extra']=='preserved' for r in options)
    later=module._universe(rows,snapshot(25150,80300),NOW+timedelta(days=1))
    for index,atm in (('NIFTY',25150),('SENSEX',80300)):
        options=[r for r in later.values() if r['index']==index and r['role']=='option']
        assert {r['expiry'] for r in options}=={'2026-10-08'}
        assert {r['strike'] for r in options if r['offset']==0}=={atm}
    # Returned members cannot mutate the source cache used by a future refresh.
    key=next(k for k,r in first.items() if r['role']=='option')
    first[key]['exchange_token']='changed'
    assert module._universe(rows,snapshot(),NOW)[key]['exchange_token']!='changed'


class Feed:
    def __init__(self,*args):self.subscriptions=[]
    def subscribe_index_value(self,rows,**kwargs):self.subscriptions.append(('spot',rows))
    def subscribe_ltp(self,rows,**kwargs):self.subscriptions.append(('price',rows))
    def subscribe_market_depth(self,rows,**kwargs):self.subscriptions.append(('depth',rows))


def service(tmp_path,monkeypatch):
    import growwapi
    instance=module.StreamingImpulse(tmp_path,Store(tmp_path/'journal.sqlite3'),clock=lambda:NOW)
    monkeypatch.setattr(growwapi,'GrowwFeed',Feed)
    monkeypatch.setattr(instance,'_private_credentials',lambda:None)
    monkeypatch.setattr(instance,'_baseline',lambda *args:None)
    monkeypatch.setattr(module,'download_instrument_text',lambda:master())
    monkeypatch.setattr(module,'load_json',lambda *args:snapshot())
    return instance,SimpleNamespace(groww=SimpleNamespace())


def test_stream_refresh_reuses_filtered_rows_and_new_connect_replaces_them(tmp_path,monkeypatch):
    instance,market=service(tmp_path,monkeypatch);instance._connect(market)
    old_rows=instance.master_rows;original_reader=module.csv.DictReader
    def forbidden(*args,**kwargs):raise AssertionError('unchanged master must not be reparsed')
    monkeypatch.setattr(module.csv,'DictReader',forbidden)
    monkeypatch.setattr(module,'load_json',lambda *args:snapshot(25150,80300))
    instance._refresh_options();instance._refresh_options()
    assert instance.master_rows is old_rows
    assert any(r['role']=='option' and r['strike']==25300 for r in instance.detector.universe.values())
    monkeypatch.setattr(module.csv,'DictReader',original_reader)
    monkeypatch.setattr(module,'download_instrument_text',lambda:master('b'))
    instance._connect(market)
    assert instance.master_rows is not old_rows
    assert all(r['exchange_token'].startswith('b') for r in instance.detector.universe.values() if r['role']=='option')
    instance._disconnect();assert instance.master_rows is None


def test_failed_master_refresh_does_not_replace_connected_cache(tmp_path,monkeypatch):
    instance,market=service(tmp_path,monkeypatch);instance._connect(market)
    old_rows=instance.master_rows;old_generation=instance.generation;old_feed=instance.feed
    def unavailable():raise ValueError('bounded source unavailable')
    monkeypatch.setattr(module,'download_instrument_text',unavailable)
    with pytest.raises(ValueError):instance._connect(market)
    assert instance.master_rows is old_rows and instance.generation==old_generation and instance.feed is old_feed


def test_reconnect_during_atm_selection_cannot_apply_old_cached_members(tmp_path,monkeypatch):
    instance,market=service(tmp_path,monkeypatch);instance._connect(market)
    before=len(instance.feed.subscriptions);before_symbols=set(instance.detector.universe)
    def changed(*args):
        with instance.lock:
            instance.generation+=1
            instance.master_rows=module._master_rows(master('new'))
        return snapshot(25150,80300)
    monkeypatch.setattr(module,'load_json',changed)
    instance._refresh_options()
    assert len(instance.feed.subscriptions)==before and set(instance.detector.universe)==before_symbols
