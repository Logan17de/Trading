from __future__ import annotations

import copy
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

from nifty_engine.agent_engine.__main__ import DEFAULT, scheduler_tick, synthetic
from nifty_engine.agent_engine.contracts import IST, snapshot
from nifty_engine.agent_engine.bridge import calendar_evidence
from nifty_engine.agent_engine.launch import filtered_environment
from nifty_engine.agent_engine.operations import backup_database, restore_database
from nifty_engine.agent_engine.reporting import build_report, queue_report, deliver_once, reconcile_receipt, save_preview
from nifty_engine.agent_engine.runner import CodexRunner, work_once
from nifty_engine.agent_engine.store import Store
from nifty_engine.agent_engine.news import NewsGuard










def test_backup_restore_preserves_pending_state_and_refuses_overwrite(tmp_path):
    store = Store(tmp_path/'engine.sqlite')
    now = datetime(2026,9,30,10,tzinfo=IST)
    store.ingest(synthetic(now,24500), now)
    report = queue_report(store,build_report(store,'2026-09-30',now))
    store.set_meta('sentinel', {'pending': True})
    backup = tmp_path/'backup.sqlite'
    assert backup_database(store.path, backup)['status'] == 'VERIFIED_BACKUP'
    restored = tmp_path/'restored.sqlite'
    restore_database(backup, restored)
    recovered = Store(restored)
    assert recovered.latest() == store.latest()
    assert recovered.meta('sentinel') == {'pending': True}
    assert recovered.status()['reports'][0]['id'] == report
    assert recovered.status()['reports'][0]['status'] == 'QUEUED'
    with pytest.raises(FileExistsError): restore_database(backup, restored)


def test_delivery_event_does_not_claim_inbox_and_does_not_change_pnl(tmp_path, monkeypatch):
    store = Store(tmp_path/'mail.sqlite')
    now = datetime(2026,9,30,10,tzinfo=IST)
    s = synthetic(now,24500); s['mode']='PAPER';store.ingest(s,now)
    for k,v in [('RESEND_API_KEY','test'),('TRADING_REPORT_FROM','sender@example.test'),('TRADING_REPORT_TO','owner@example.test')]:
        monkeypatch.setenv(k,v)
    report = queue_report(store,build_report(store,'2026-09-30',now))
    assert deliver_once(store,now,send=lambda *a:'receipt-1')=='ACCEPTED'
    result = reconcile_receipt(store,report,now,fetch=lambda *a:{'id':'receipt-1','to':['owner@example.test'],
        'from':'sender@example.test','last_event':'delivered','message_id':'<test@example.test>'})
    assert result['status']=='RECIPIENT_SERVER_ACCEPTED'
    assert result['inbox_verified'] is False
    assert store.latest()==s


def test_service_role_environment_separation():
    source = {'PATH':'/bin','SUPABASE_SERVICE_ROLE_KEY':'private','RESEND_API_KEY':'mail',
              'CALL_SELLER_LLM_KEY':'news','CODEX_API_KEY':'analyst'}
    assert filtered_environment('worker',source)=={'PATH':'/bin'}
    assert filtered_environment('scheduler',source)=={'PATH':'/bin','RESEND_API_KEY':'mail'}
    with pytest.raises(ValueError): filtered_environment('publisher',source)


def test_missing_mandatory_news_retains_partial_evidence_but_never_low(monkeypatch):
    import nifty_engine.agent_engine.news as news
    now = datetime.now(IST).isoformat()
    monkeypatch.setattr(news,'request_bytes',lambda url: url.encode())
    monkeypatch.setattr(news,'parse_feed',lambda data,url,at: [] if 'rbi' in url else [
        {'id':'actual','title':'Fixture headline','source':'economictimes.indiatimes.com',
         'url':'https://economictimes.indiatimes.com/article','published_at':now}])
    guard=NewsGuard(['https://rbi.org.in/feed','https://economictimes.indiatimes.com/feed'],
                    ['rbi.org.in','economictimes.indiatimes.com'])
    guard.refresh()
    result,blocks=guard.snapshot(datetime.now(IST))
    assert result['risk']=='UNKNOWN' and blocks
    assert result['articles'][0]['id']=='actual'






def test_calendar_payload_allowlists_evidence_and_rejects_future_review():
    now=datetime(2026,9,30,10,tzinfo=IST)
    source={'reviewed_at':now.isoformat(),'sessions':{'2026-09-30':{
        'open':now.replace(hour=9,minute=15).isoformat(),'close':now.replace(hour=15,minute=30).isoformat()}},
        'events':[],'SUPABASE_SERVICE_ROLE_KEY':'must-not-export'}
    evidence=calendar_evidence(source,now)
    assert evidence is not None and 'SUPABASE_SERVICE_ROLE_KEY' not in evidence
    data=synthetic(now,24500)
    data['context']={'positions':[],'option_quotes':[],'event_calendar':evidence}
    snapshot(data)
    source['reviewed_at']=(now+timedelta(seconds=1)).isoformat()
    assert calendar_evidence(source,now) is None
    assert calendar_evidence(None,now) is None


def enqueue_diagnostic(store, now):
    store.add_watch({'name':'seed','instrument':'NIFTY','metric':'spot','op':'above',
        'threshold':24000,'cooldown_seconds':300,'max_fires':1,
        'expires_at':(now+timedelta(hours=1)).isoformat()},now)
    store.ingest(synthetic(now,24500),now)
    assert store.evaluate(now)==1


@pytest.mark.skipif(os.name!='posix',reason='POSIX subprocess acceptance')
def test_two_subprocess_runs_through_persisted_followup(tmp_path):
    now=datetime.now(IST)
    store=Store(tmp_path/'chain.sqlite');enqueue_diagnostic(store,now)
    home=tmp_path/'auth';home.mkdir()
    executable=tmp_path/'fixture-codex'
    executable.write_text(f'''#!{sys.executable}
import json,sys
from datetime import datetime,timedelta
p=json.loads(sys.stdin.read().splitlines()[-1])
watch={{'name':'follow','instrument':'NIFTY','metric':'spot','op':'below','threshold':24600,
    'cooldown_seconds':300,'max_fires':1,'expires_at':(datetime.fromisoformat(p['now'])+timedelta(hours=1)).isoformat()}}
result={{'snapshot_id':p['snapshot']['id'],'action':'WAIT','summary':'Synthetic adapter acceptance',
    'evidence_ids':[],'research_proposal':None,'watches':[watch] if p['trigger']=='seed' else []}}
open(sys.argv[sys.argv.index('--output-last-message')+1],'w').write(json.dumps(result))
print(json.dumps({{'type':'turn.completed'}}))
''')
    executable.chmod(0o700)
    for iteration in range(2):
        current=now+timedelta(seconds=iteration*20)
        # Reopen the durable store and use a newly spawned analyst each time.
        store=Store(store.path)
        if iteration:
            store.ingest(synthetic(current,24500),current)
            assert store.evaluate(current)==1
            assert store.evaluate(current)==0
        assert work_once(store,CodexRunner(str(executable),str(home)),clock=lambda:current)
    assert store.read("SELECT status,COUNT(*) n FROM jobs GROUP BY status")==[{'status':'SUCCEEDED','n':2}]


@pytest.mark.skipif(os.name!='posix',reason='POSIX process kill acceptance')
def test_killed_analyst_leaves_monitoring_and_eod_independent(tmp_path):
    now=datetime.now(IST)
    store=Store(tmp_path/'failure.sqlite');enqueue_diagnostic(store,now)
    home=tmp_path/'auth';home.mkdir()
    pid_file=tmp_path/'analyst.pid';executable=tmp_path/'slow-codex'
    executable.write_text(f'#!{sys.executable}\nimport os,time\nopen({str(pid_file)!r},"w").write(str(os.getpid()))\ntime.sleep(30)\n')
    executable.chmod(0o700)
    worker=threading.Thread(target=work_once,args=(store,CodexRunner(str(executable),str(home),timeout=10)))
    worker.start()
    try:
        deadline=time.monotonic()+5
        while not pid_file.exists() and time.monotonic()<deadline: time.sleep(.01)
        assert pid_file.exists()
        assert store.read('SELECT status FROM jobs')[0]['status']=='RUNNING'
        feed=tmp_path/'feed.json';feed.write_text(json.dumps(synthetic(now,24500)))
        cfg={**DEFAULT,'snapshot_file':str(feed),'reports_dir':str(tmp_path/'reports')}
        scheduler_tick(Store(store.path),cfg,now.replace(hour=16,minute=15))
        assert (tmp_path/'reports'/now.date().isoformat()/'report.eml').is_file()
        assert worker.is_alive()  # Monitoring/reporting did not wait for the analyst.
        os.kill(int(pid_file.read_text()),signal.SIGKILL)
    finally:
        worker.join(timeout=12)
    assert not worker.is_alive()
    assert store.read('SELECT status FROM jobs')[0]['status']=='FAILED'
    assert Store(store.path).latest()['markets']['NIFTY']['spot']==24500
    assert len(Store(store.path).status()['reports'])==1


def test_mail_restart_after_ambiguous_send_preserves_identity_and_accounting(tmp_path,monkeypatch):
    now=datetime(2026,9,30,10,tzinfo=IST)
    store=Store(tmp_path/'restart.sqlite')
    data=synthetic(now,24500);data['mode']='PAPER';store.ingest(data,now)
    for key,value in [('RESEND_API_KEY','test'),('TRADING_REPORT_FROM','from@example.test'),('TRADING_REPORT_TO','to@example.test')]:
        monkeypatch.setenv(key,value)
    report=queue_report(store,build_report(store,now.date().isoformat(),now))
    calls=[]
    def interrupted(payload,key,identity):
        calls.append((copy.deepcopy(payload),identity))
        raise SystemExit('simulated process loss after request')
    with pytest.raises(SystemExit):deliver_once(store,now,send=interrupted)
    assert store.status()['reports'][0]['status']=='SENDING'
    recovered=Store(store.path)
    def retry(payload,key,identity):
        calls.append((copy.deepcopy(payload),identity));return 'same-provider-receipt'
    assert deliver_once(recovered,now+timedelta(seconds=61),send=retry)=='ACCEPTED'
    assert calls[0]==calls[1] and calls[1][1]==report
    assert recovered.latest()==data


def test_visual_report_retains_unicode_news_evidence(tmp_path):
    now=datetime(2026,9,30,10,tzinfo=IST)
    store=Store(tmp_path/'unicode.sqlite')
    data=synthetic(now,24500)
    title='आरबीआई नीति समीक्षा'
    data['news']=[{'id':'rbi-fixture','title':title,'source':'rbi.org.in','published_at':now.isoformat()}]
    store.ingest(data,now)
    save_preview(build_report(store,now.date().isoformat(),now),tmp_path/'preview')
    assert title in (tmp_path/'preview/report.html').read_text(encoding='utf-8')
