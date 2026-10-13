from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest

from nifty_engine.agent_engine import mail_window as window
from nifty_engine.agent_engine import reporting
from nifty_engine.agent_engine.store import Store
from nifty_engine.agent_engine.contracts import dumps
from nifty_engine.agent_engine.failure_alerts import Monitor
from test_failure_alerts import broken,view,ENV

JST=ZoneInfo('Asia/Tokyo')
OPEN=datetime(2026,10,8,12,45,tzinfo=JST)


@pytest.mark.parametrize('at,expected',[
 ('2026-10-08T12:44:59+09:00','WAIT_MARKET_SESSION'),('2026-10-08T12:45:00+09:00','OPEN'),
 ('2026-10-08T18:59:59+09:00','OPEN'),('2026-10-08T19:00:00+09:00','WAIT_MARKET_SESSION'),
 ('2026-10-08T10:00:00+00:00','WAIT_MARKET_SESSION'),('2026-10-10T14:00:00+09:00','WAIT_MARKET_SESSION'),
 ('2026-10-20T14:00:00+09:00','WAIT_MARKET_SESSION'),('2027-01-04T14:00:00+09:00','MAIL_CALENDAR_REVIEW_REQUIRED')])
def test_exact_clock_weekend_holiday_and_unreviewed_year(at,expected):
    assert window.status(datetime.fromisoformat(at))==expected


def report(store):
    with store.transaction() as db:db.execute('INSERT INTO reports(id,day,payload) VALUES(?,?,?)',
        ('test','2026-10-08',dumps({'mode':'OBSERVE','mail':{'subject':'Test','html':'Test'}})))


def test_queued_report_waits_and_restarts_without_consuming_attempts(tmp_path,monkeypatch):
    for k,v in ENV.items():monkeypatch.setenv(k,v)
    path=tmp_path/'j.sqlite3';store=Store(path);report(store)
    before=store.read('SELECT * FROM reports')
    for now in (OPEN.replace(hour=19),OPEN+timedelta(days=2)):
        assert reporting.deliver_once(store,now,send=lambda *a:pytest.fail('closed send'))=='WAIT_MARKET_SESSION'
    assert store.read('SELECT * FROM reports')==before
    assert reporting.deliver_once(Store(path),OPEN+timedelta(days=1),send=lambda *a:'receipt')=='ACCEPTED'


def test_critical_alerts_continue_outside_regular_mail_window(tmp_path):
    path=tmp_path/'alerts.sqlite3';m=Monitor(Store(path));closed=OPEN.replace(hour=20)
    m.tick(broken(closed),closed)
    calls=[]
    m.deliver(closed,ENV,send=lambda *a:calls.append(a) or 'receipt')
    assert len(calls)==1  # live/unknown exposure overrides the routine quiet hours
    restarted=Monitor(Store(path));now=OPEN+timedelta(days=1)
    restarted.tick(broken(now),now)
    restarted.deliver(now,ENV,send=lambda *a:calls.append(a) or 'receipt')
    assert len(calls)==2


def test_recovered_overnight_incidents_do_not_send_next_session(tmp_path):
    path=tmp_path/'alerts.sqlite3';m=Monitor(Store(path));closed=OPEN.replace(hour=20)
    m.tick(broken(closed),closed)
    m.tick(view(closed+timedelta(seconds=1)),closed+timedelta(seconds=1))
    m.tick(view(closed+timedelta(seconds=62)),closed+timedelta(seconds=62))
    restarted=Monitor(Store(path))
    restarted.deliver(OPEN+timedelta(days=1),ENV,send=lambda *a:pytest.fail('recovered send'))
    assert all(r['state']=='RECOVERED' and r['attempts']==0 for r in restarted.public()['incidents'])


def test_final_transport_guard_prevents_a_boundary_race_and_attempt_is_not_consumed(tmp_path,monkeypatch):
    for k,v in ENV.items():monkeypatch.setenv(k,v)
    store=Store(tmp_path/'j.sqlite3');report(store)
    def closed():raise window.MailWindowClosed('WAIT_MARKET_SESSION')
    monkeypatch.setattr(window,'require_open',closed)
    monkeypatch.setattr(reporting,'build_opener',lambda *a:pytest.fail('provider accessed after close'))
    assert reporting.deliver_once(store,OPEN,send=reporting.resend)=='WAIT_MARKET_SESSION'
    row=store.read('SELECT attempts,first_attempt,status FROM reports')[0]
    assert row=={'attempts':0,'first_attempt':None,'status':'QUEUED'}
    monitor=Monitor(Store(tmp_path/'alerts.sqlite3'));monitor.tick(broken(OPEN),OPEN)
    monitor.deliver(OPEN,ENV,send=reporting.resend)
    assert all(r['attempts']==0 for r in monitor.public()['incidents'])
