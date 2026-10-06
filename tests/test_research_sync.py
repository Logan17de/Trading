import base64
import gzip
import hashlib
import json
from datetime import datetime,timedelta,timezone
import pytest

from nifty_engine.agent_engine.impulse_research import PremiumImpulseMonitor
from nifty_engine.agent_engine.pc_control import PcJournal
from nifty_engine.agent_engine.research_sync import SupabaseArchive,PROJECT_URL,scrub
from nifty_engine.agent_engine.store import Store

NOW=datetime(2026,10,6,4,tzinfo=timezone.utc)


def test_lossless_raw_chunks_idempotent_retry_after_lost_ack_restart(tmp_path):
    store=Store(tmp_path/'research.sqlite3');clock=[NOW]
    monitor=PremiumImpulseMonitor(store,clock=lambda:clock[0])
    monitor.configure({'NIFTY':dict(index='NIFTY',role='spot')})
    monitor.ingest(dict(symbol='NIFTY',kind='price',provider_at=NOW.timestamp(),price=25000))
    monitor.flush();backend={};attempts=[]
    def post(rows):
        attempts.append(rows)
        for r in rows:backend[(r['source_id'],r['record_key'])]=r
        if len(attempts)==1:raise TimeoutError('private fixture secret must not leak')
    worker=SupabaseArchive(store,url=PROJECT_URL,key='private-fixture',post=post,clock=lambda:clock[0])
    worker.step();assert worker.pending()==1 and worker.status=='RETRY_PENDING'
    assert 'secret' not in json.dumps(worker.public())
    restored=SupabaseArchive(store,url=PROJECT_URL,key='private-fixture',post=post,clock=lambda:clock[0])
    assert restored.source_id==worker.source_id
    restored.step();assert len(attempts)==1
    clock[0]+=timedelta(seconds=6);restored.step()
    assert len(backend)==1 and restored.pending()==0
    packed=list(backend.values())[0]['body']
    raw=gzip.decompress(base64.b64decode(packed['data']))
    assert hashlib.sha256(raw).hexdigest()==packed['raw_sha256']
    decoded=json.loads(raw)
    assert decoded[0]['body']['price']==25000 and decoded[0]['body']['accepted']
    restored.step();assert len(attempts)==2


def test_configuration_missing_keeps_local_outbox_without_network(tmp_path):
    store=Store(tmp_path/'research.sqlite3');store.set_meta('premium-executor-status',{'reason':'PAUSED'})
    calls=[];worker=SupabaseArchive(store,url='',key='',post=lambda rows:calls.append(rows))
    worker.step()
    assert worker.status=='WAITING_FOR_SERVER_CONFIGURATION' and worker.pending()==1 and not calls
    with pytest.raises(ValueError,match='PROJECT'):SupabaseArchive(store,url='https://different.supabase.co',key='x')


def test_mutable_event_and_algo_versions_are_archived_no_manual_or_secrets(tmp_path):
    journal=PcJournal(tmp_path/'research.sqlite3');store=journal.store
    owned=journal.reserve('algo','NIFTY26O0625000CE','SELL',65,strategy='EVERYDAY')
    journal.reserve('algo','NIFTY26O0625100CE','BUY',65,strategy='EVERYDAY')
    store.set_meta('premium-executor-v1',dict(phase='IDLE',api_key='credential',account_id='private',
        fill=dict(groww_order_id='broker-private-id',price=20),secret='private',error='private error'))
    store.set_meta('capital-ledger-v1',{'private_owner_amount':'must not export'})
    remote={}
    def post(rows):
        for r in rows:remote[r['record_key']]=r
    worker=SupabaseArchive(store,url=PROJECT_URL,key='x',post=post)
    worker.step();assert worker.pending()==0 and len(remote)==3
    assert 'capital' not in json.dumps(remote) and 'credential' not in json.dumps(remote)
    assert 'broker-private-id' not in json.dumps(remote) and 'private error' not in json.dumps(remote)
    store.set_meta('premium-executor-v1',{'phase':'BLOCKED'})
    worker.step();assert len(remote)==4
    assert all(r['mode']=='MONITOR_ONLY' for r in remote.values())
    assert all('broker_id' not in r['body'] for r in remote.values())


def test_algo_pnl_only_and_unknown_preserved(tmp_path):
    store=Store(tmp_path/'research.sqlite3')
    with store.transaction() as db:
        db.execute('CREATE TABLE pc_pnl_observations(at REAL PRIMARY KEY,day TEXT,body TEXT)')
        db.execute('INSERT INTO pc_pnl_observations VALUES(?,?,?)',(NOW.timestamp(),'2026-10-06',json.dumps(dict(status='UNKNOWN',buckets=dict(algo={'total_inr':None},self={'total_inr':9999})))))
    remote=[];worker=SupabaseArchive(store,url=PROJECT_URL,key='x',post=lambda rows:remote.extend(rows))
    worker.step()
    assert remote[0]['body']['algo']['total_inr'] is None
    assert 'self' not in remote[0]['body'] and not worker.public()['broker_writes']


def test_scrub_recursive_not_margin_field_or_order_payload():
    r=scrub({'api_secret':'x','nested':[{'pan':'x','span_margin':20,'smart_order_id':'x','quantity':65}]})
    assert 'api_secret' not in r and r['nested'][0]['span_margin']==20
    assert r['nested'][0]['smart_order_id_sha256']==hashlib.sha256(b'x').hexdigest()
