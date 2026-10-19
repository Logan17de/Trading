from datetime import timedelta
from types import SimpleNamespace
import copy

from nifty_engine.agent_engine.live_buffers import OperationalStore, LiveLines
from nifty_engine.agent_engine.lean_state import operational_copy
from nifty_engine.agent_engine.pc_control import PcJournal
from nifty_engine.agent_engine.normal_data import connect_snapshot
from test_report_data import NOW, snapshot
from test_normal_theta import CFG, evidence, intraday_evidence


def test_current_reader_uses_only_normal_inputs_and_refreshes_real_books(tmp_path):
    from nifty_engine.agent_engine.normal_data import NormalData
    j=PcJournal(tmp_path/'j.sqlite3');store=OperationalStore(j.store)
    clock=[NOW];calls=[];margin_at=[]
    def latest():
        raw=snapshot();raw['finished_at']=clock[0].isoformat()
        for p in raw['probes'].values():p['received_at']=clock[0].isoformat();p['value']['last_price']=25100
        raw['funds']=dict(received_at=clock[0].isoformat(),option_buy_available_inr=50000,option_sell_available_inr=50000)
        return raw
    reader=NormalData(SimpleNamespace(),SimpleNamespace(store=store),CFG,clock=lambda:clock[0],snapshot_reader=latest)
    assert reader.calendar is None
    def history(index,now):
        store.set_meta('report-history-status-'+index,dict(day=now.date().isoformat(),status='AVAILABLE'))
        store.set_meta('report-daily-features-'+index,evidence(index)['features'])
    def intraday(index,now):
        store.set_meta('normal-theta-intraday-'+index,intraday_evidence(index,now))
    def expiries(index,now):
        value=dict(status='CONFIRMED_CURRENT_MASTER',day_jst=now.date().isoformat(),expiries=['2026-11-10'],received_at=now.isoformat())
        store.set_meta('report-expiries-'+index,value);return value
    def books(index,expiry,protected,**kwargs):
        calls.append(index)
        return [dict(r,received_at=clock[0].isoformat(),greeks_received_at=clock[0].isoformat()) for r in evidence(index)['contracts']]
    def margins(index,rows,*args,**kwargs):
        clock[0]+=timedelta(seconds=6);margin_at.append(clock[0].isoformat())
        return {'exact-basket':{'received_at':margin_at[-1]}}
    reader.history=history;reader.intraday=intraday;reader.expiries=expiries;reader.books=books;reader.margins=margins
    result=reader.check(latest())
    assert result['status']=='CURRENT' and calls==['SENSEX','SENSEX']
    current=store.meta('normal-theta-evidence-SENSEX')
    assert current['margins']['exact-basket']['received_at']==margin_at[0]
    assert all(r['received_at']==clock[0].isoformat() for r in current['contracts'])
    assert j.store.meta('normal-theta-evidence-SENSEX') is None


def test_market_cache_is_ram_only_but_owner_state_survives_restart(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3');store=OperationalStore(j.store)
    store.set_meta('normal-theta-evidence-NIFTY',evidence())
    store.set_meta('capital-ledger-v1',{'private':'ledger'})
    assert store.meta('normal-theta-evidence-NIFTY')
    assert j.store.meta('normal-theta-evidence-NIFTY') is None
    restarted=OperationalStore(PcJournal(tmp_path/'j.sqlite3').store)
    assert restarted.meta('normal-theta-evidence-NIFTY') is None
    assert restarted.meta('capital-ledger-v1')=={'private':'ledger'}


def test_old_history_cannot_be_redated_into_current_normal_features(tmp_path):
    j=PcJournal(tmp_path/'j.sqlite3');store=OperationalStore(j.store)
    store.set_meta('report-daily-features-NIFTY',evidence()['features'])
    store.set_meta('report-history-status-NIFTY',dict(status='AVAILABLE',day='2026-10-01'))
    connect_snapshot(store,snapshot(),CFG,NOW)
    assert 'ma20' not in store.meta('normal-theta-evidence-NIFTY')['features']
    store.set_meta('report-history-status-NIFTY',dict(status='AVAILABLE',day=NOW.date().isoformat()))
    connect_snapshot(store,snapshot(),CFG,NOW)
    assert 'ma20' in store.meta('normal-theta-evidence-NIFTY')['features']


def test_operational_copy_preserves_exact_owner_order_capital_and_mail_but_no_bulk(tmp_path):
    j=PcJournal(tmp_path/'old.sqlite3')
    from nifty_engine.agent_engine.session import SessionJournal
    SessionJournal(j.store)
    j.store.set_meta('premium-algo-intent',dict(enabled=False))
    j.store.set_meta('capital-ledger-v1',{'owner_value':'private'})
    with j.store.transaction() as db:
        db.execute("INSERT INTO pc_observations VALUES('x',1,'2026-10-01',1,'{}')")
        db.execute("INSERT INTO pc_slots VALUES('slot','normal_theta')")
        db.execute("INSERT INTO pc_orders(reference,slot,symbol,side,quantity) VALUES('exact','slot','NIFTY26N1025000PE','SELL',65)")
    result=operational_copy(j.store.path,tmp_path/'new.sqlite3')
    assert result['status']=='VERIFIED_OPERATIONAL_COPY'
    new=PcJournal(tmp_path/'new.sqlite3')
    assert new.store.meta('capital-ledger-v1')==j.store.meta('capital-ledger-v1')
    assert new.store.read('SELECT * FROM pc_orders')==j.store.read('SELECT * FROM pc_orders')
    assert not new.store.read('SELECT * FROM pc_observations')
    assert len(j.store.read('SELECT * FROM pc_observations'))==1
    import pytest
    with pytest.raises(FileExistsError):operational_copy(j.store.path,new.store.path)
