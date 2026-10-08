import copy
from datetime import datetime, timedelta, timezone

import pytest

from nifty_engine.agent_engine.failure_alerts import Monitor, faults, diagnostics, MESSAGES, KEY
from nifty_engine.agent_engine.store import Store

NOW=datetime(2026,10,8,6,0,tzinfo=timezone.utc)
ENV={'RESEND_API_KEY':'synthetic-test-key','TRADING_REPORT_FROM':'test@example.invalid',
     'TRADING_REPORT_TO':'owner@example.invalid'}


def view(now=NOW):
    return dict(runtime=dict(boot_id='synthetic',heartbeat_sequence=int(now.timestamp()),heartbeat_at=now.isoformat()),
        as_of=now.isoformat(),funds=dict(status='AVAILABLE',received_at=now.isoformat()),
        positions_status='AVAILABLE',orders_status='AVAILABLE',
        control={'algo':{'desired_enabled':False}},execution_controller={'phase':'IDLE',
            'blockers':['OWNER_ALGO_OFF','REPOSITORY_PAUSED','LIVE_ENVIRONMENT_NOT_ACTIVATED']},
        live_setup={'mode':'paper','paused':True},daily_email={'status':'SCHEDULED_BY_1930_JST'})


def broken(now=NOW):
    v=view(now);v['execution_controller']['worker_failure']=dict(error_type='OperationalError',
        stage='EXECUTOR_TICK',sqlite_code='SQLITE_BUSY',message='DO-NOT-COPY-SECRET',
        frames=[dict(file='premium_executor.py',function='tick',line=300,source='PRIVATE')])
    return v


def test_expected_off_research_unknown_no_trade_and_closed_market_are_quiet():
    v=view();v['normal_theta']={'status':'NO_TRADE'};v['event_calendar']={'status':'UNKNOWN'}
    assert faults(v,NOW)=={}
    assert faults(view(),NOW.replace(hour=22))=={}


@pytest.mark.parametrize('field,code',[
    ('runtime','COLLECTOR_FAILED'),('execution','EXECUTOR_WORKER_FAILED'),('protection','ORDER_RECONCILIATION_REQUIRED'),
    ('funds','FUNDS_UNAVAILABLE'),('orders','ORDERS_UNAVAILABLE'),('positions','POSITIONS_UNAVAILABLE'),
    ('as_of','MARKET_DATA_STALE'),('on','OWNER_ON_EXECUTION_BLOCKED'),('mail','DAILY_EMAIL_FAILED'),
    ('archive','RESEARCH_ARCHIVE_FAILED')])
def test_exact_component_detection(field,code):
    v=view()
    if field=='runtime':v['runtime']['error']={'error_type':'HTTPError','code':'403','message':'PRIVATE'}
    if field=='execution':v=broken()
    if field=='protection':v['execution_controller']['blockers'].append('BROKER_PROTECTION_RECONCILIATION_REQUIRED')
    if field=='funds':v['funds']['status']='UNAVAILABLE'
    if field in ('orders','positions'):v[field+'_status']='INCOMPLETE'
    if field=='as_of':v['as_of']=(NOW-timedelta(seconds=46)).isoformat()
    if field=='on':v['control']['algo']['desired_enabled']=True
    if field=='mail':v['daily_email']['status']='RETRY_PENDING'
    if field=='archive':v['research_archive']={'status':'RETRY_PENDING','failure':'SUPABASE_HTTP_503'}
    assert code in faults(v,NOW)
    assert 'PRIVATE' not in str(faults(v,NOW))


def test_durable_single_incident_frozen_retries_and_provider_receipt(tmp_path):
    path=tmp_path/'alerts.sqlite3';m=Monitor(Store(path));sent=[]
    m.tick(broken(),NOW)
    def uncertain(mail,key,token):
        sent.append((copy.deepcopy(mail),key,token));raise TimeoutError('PRIVATE-CREDENTIAL')
    m.deliver(NOW,ENV,send=uncertain)
    r=m.public()['incidents'][0]
    assert r['mail_status']=='RETRY' and not r['inbox_verified']
    assert 'PRIVATE' not in str(m.state)
    reopened=Monitor(Store(path));changed=dict(ENV,TRADING_REPORT_FROM='changed@example.invalid')
    reopened.tick(broken(NOW+timedelta(seconds=301)),NOW+timedelta(seconds=301))
    def accepted(mail,key,token):sent.append((copy.deepcopy(mail),key,token));return 'synthetic-receipt'
    reopened.deliver(NOW+timedelta(seconds=301),changed,send=accepted)
    assert sent[0]==sent[1] and len(reopened.state['incidents'])<4
    reopened.deliver(NOW+timedelta(seconds=302),ENV,send=lambda *a:pytest.fail('duplicate mail'))
    def receipt(identifier,key):
        return dict(id=identifier,to=[ENV['TRADING_REPORT_TO']],**{'from':ENV['TRADING_REPORT_FROM']},last_event='delivered')
    reopened.reconcile(NOW+timedelta(seconds=303),ENV['RESEND_API_KEY'],fetch=receipt)
    row=next(r for r in reopened.public()['incidents'] if r['code']=='EXECUTOR_WORKER_FAILED')
    assert row['recipient_server_accepted'] and row['mail_status']=='ACCEPTED' and row['inbox_verified'] is False


def test_debounce_recovery_restart_and_unreachable_does_not_clear_unknown_orders(tmp_path):
    m=Monitor(Store(tmp_path/'j.sqlite3'));bad=view();bad['funds']['status']='UNAVAILABLE'
    m.tick(bad,NOW);m.deliver(NOW,ENV,send=lambda *a:pytest.fail('too early'))
    m.tick(view(NOW+timedelta(seconds=30)),NOW+timedelta(seconds=30))
    m.deliver(NOW+timedelta(seconds=65),ENV,send=lambda *a:pytest.fail('recovering'))
    m.tick(view(NOW+timedelta(seconds=95)),NOW+timedelta(seconds=95))
    assert m.public()['incidents'][0]['state']=='RECOVERED'
    m.tick(broken(),NOW+timedelta(seconds=100))
    m.tick(None,NOW+timedelta(seconds=200))
    assert next(r for r in m.public()['incidents'] if r['code']=='EXECUTOR_WORKER_FAILED')['state']=='OPEN'


def test_no_retry_after_provider_dedup_window_and_no_fictional_delivery(tmp_path):
    m=Monitor(Store(tmp_path/'j.sqlite3'));m.tick(broken(),NOW)
    def fail(*a):raise TimeoutError('secret')
    m.deliver(NOW,ENV,send=fail)
    m.deliver(NOW+timedelta(hours=24),ENV,send=lambda *a:pytest.fail('dedup window elapsed'))
    row=m.public()['incidents'][0]
    assert row['mail_status']=='DELIVERY_UNKNOWN' and row['inbox_verified'] is False


def test_changed_failure_code_creates_new_incident_but_timestamps_do_not(tmp_path):
    m=Monitor(Store(tmp_path/'j.sqlite3'));v=view();v['runtime']['error']={'code':'403','error_type':'HTTPError'}
    m.tick(v,NOW);first=next(r['id'] for r in m.public()['incidents'] if r['code']=='COLLECTOR_FAILED')
    v['runtime']['heartbeat_sequence']+=1;v['runtime']['heartbeat_at']=(NOW+timedelta(seconds=5)).isoformat()
    m.tick(v,NOW+timedelta(seconds=5))
    assert len(m.public()['incidents'])==1
    v['runtime']['error']['code']='500';m.tick(v,NOW+timedelta(seconds=10))
    assert len(m.public()['incidents'])==2 and len({r['id'] for r in m.public()['incidents']})==2
    assert first in {r['id'] for r in m.public()['incidents']}


def test_journal_contains_only_monitor_state_and_diagnostics_are_bounded(tmp_path):
    s=Store(tmp_path/'alerts.sqlite3');m=Monitor(s);m.tick(broken(),NOW)
    assert [r['key'] for r in s.read('SELECT key FROM meta')]==[KEY]
    data=diagnostics({'error_type':'HTTPError','code':'403','message':'SECRET','token':'SECRET',
        'frames':[{'file':'/private/credentials.py','function':'get','line':3}]})
    assert data=={'error_type':'HTTPError','provider_code':'403','frames':[]}

