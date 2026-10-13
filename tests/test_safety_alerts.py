"""Owner-requested safety notifications; no unrelated email is permitted."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from nifty_engine.agent_engine.alert_cadence import (
    auth_slot, position_risk, may_send, category, expected_auth_failure)
from nifty_engine.agent_engine.auth_probe import inspect, run_once, KEY as AUTH_KEY
from nifty_engine.agent_engine.failure_alerts import Monitor, faults
from nifty_engine.agent_engine.store import Store

JST=ZoneInfo("Asia/Tokyo")
TIME=datetime(2026,10,13,14,0,tzinfo=JST)
ENV={'RESEND_API_KEY':'test-only','TRADING_REPORT_FROM':'test@example.invalid',
     'TRADING_REPORT_TO':'owner@example.invalid'}


def view(now=TIME, *, active=False, owner_test=False):
    market={'index':'NIFTY','options':[{'symbol':'NIFTYTESTCE','quantity':50}]} if active else {
        'index':'NIFTY','options':[]}
    result={
        'format':'trading-dashboard-v1','order_capability':False,
        'as_of':now.isoformat(),'markets':[market],
        'positions_status':'AVAILABLE','orders_status':'AVAILABLE',
        'runtime':{'boot_id':'test','heartbeat_sequence':1,'heartbeat_at':now.isoformat()},
        'execution_controller':{'phase':'MONITORING' if active else 'IDLE',
                                'protection':'VERIFIED_ACTIVE','blockers':[]},
        'control':{'algo':{'desired_enabled':False}},
        'live_setup':{'mode':'paper','paused':True,'owner_test':{}},
        'funds':{'status':'AVAILABLE','received_at':now.isoformat()},
        'daily_email':{'status':'DISABLED_OWNER_SAFETY_ALERTS_ONLY'},
    }
    if owner_test:
        result['live_setup']['owner_test']={'state':'ERROR','reason':'COMMISSIONING_FAILED'}
    return result


def failure(now=TIME, *, active=True):
    v=view(now,active=active)
    v['execution_controller']['worker_failure']={
        'error_type':'RuntimeError','stage':'EXECUTOR_TICK','private':'DONT_LEAK'}
    return v


def calls():
    delivered=[]
    def send(payload,key,token):
        delivered.append((payload,key,token))
        return 'accepted-receipt-'+str(len(delivered))
    return delivered, send


def test_exposure_cannot_be_guessed_flat():
    assert position_risk(view(),TIME)=='FLAT'
    assert position_risk(view(active=True),TIME)=='ACTIVE'
    assert position_risk(view(owner_test=True),TIME)=='ACTIVE'
    stale=view(TIME-timedelta(hours=1))
    assert position_risk(stale,TIME)=='UNKNOWN'
    assert position_risk(None,TIME)=='UNKNOWN'


def test_critical_incident_every_twenty_minutes_and_only_whitelisted(tmp_path):
    monitor=Monitor(Store(tmp_path/'alerts.db'))
    delivered,send=calls()
    monitor.tick(failure(),TIME)
    monitor.deliver(TIME,ENV,send=send)
    assert len(delivered)==1
    monitor.tick(failure(TIME+timedelta(minutes=19)),TIME+timedelta(minutes=19))
    monitor.deliver(TIME+timedelta(minutes=19),ENV,send=send)
    assert len(delivered)==1
    monitor.tick(failure(TIME+timedelta(minutes=20)),TIME+timedelta(minutes=20))
    monitor.deliver(TIME+timedelta(minutes=20),ENV,send=send)
    assert len(delivered)==2
    assert delivered[0][2]!=delivered[1][2]
    assert delivered[0][0]['subject']!=delivered[1][0]['subject']
    assert 'DONT_LEAK' not in str(delivered)


def test_idle_trade_failure_two_hour_cadence_only_in_session(tmp_path):
    monitor=Monitor(Store(tmp_path/'alerts.db'))
    delivered,send=calls()
    monitor.tick(failure(active=False),TIME)
    monitor.deliver(TIME,ENV,send=send)
    assert len(delivered)==1
    monitor.tick(failure(TIME+timedelta(minutes=119),active=False),TIME+timedelta(minutes=119))
    monitor.deliver(TIME+timedelta(minutes=119),ENV,send=send)
    assert len(delivered)==1
    monitor.tick(failure(TIME+timedelta(minutes=120),active=False),TIME+timedelta(minutes=120))
    monitor.deliver(TIME+timedelta(minutes=120),ENV,send=send)
    assert len(delivered)==2


def test_unrelated_incidents_never_mail(tmp_path):
    monitor=Monitor(Store(tmp_path/'alerts.db'))
    v=view()
    v['research_archive']={'status':'RETRY_PENDING'}
    v['funds']={'status':'UNAVAILABLE'}
    v['daily_email']={'status':'FAILED'}
    monitor.tick(v,TIME)
    assert any(r['code'] in ('FUNDS_UNAVAILABLE','DAILY_EMAIL_FAILED','RESEARCH_ARCHIVE_FAILED')
               for r in monitor.public()['incidents'])
    monitor.deliver(TIME,ENV,send=lambda *a:pytest.fail('routine email not allowed'))


def test_active_risk_mail_continues_outside_quiet_hours(tmp_path):
    at=TIME.replace(hour=22)
    monitor=Monitor(Store(tmp_path/'alerts.db'))
    delivered,send=calls()
    monitor.tick(failure(at),at)
    monitor.deliver(at,ENV,send=send)
    assert len(delivered)==1
    later=at+timedelta(minutes=20)
    monitor.tick(failure(later),later)
    monitor.deliver(later,ENV,send=send)
    assert len(delivered)==2


def test_stop_protection_is_critical_only_for_live_owned_position():
    v=view(active=True)
    v['execution_controller']['protection']='UNKNOWN_STALE_READBACK'
    assert 'STOP_PROTECTION_FAILED' not in faults(v,TIME)
    v['live_setup']['mode']='live'
    assert 'STOP_PROTECTION_FAILED' in faults(v,TIME)


@pytest.mark.parametrize('hour,minute,yes',[
    (11,59,False),(12,0,True),(12,4,True),(12,5,False),
    (14,59,False),(15,0,True),(15,4,True),(15,5,False)])
def test_auth_only_at_exact_slots(hour,minute,yes):
    at=TIME.replace(hour=hour,minute=minute)
    assert bool(auth_slot(at)) is yes


def test_auth_probe_invalid_only_from_actual_401_403(tmp_path):
    at=TIME.replace(hour=12,minute=0)
    calls=[]
    def bad():
        calls.append(1)
        error=RuntimeError('dont print credential')
        error.code=403
        raise error
    store=Store(tmp_path/'auth.db')
    result=run_once(store,at,bad)
    assert result['status']=='INVALID' and result['provider_code']=='403'
    assert run_once(store,at+timedelta(seconds=20),bad)==result
    assert len(calls)==1
    assert store.meta(AUTH_KEY)==result
    assert expected_auth_failure(result,at)
    assert 'dont print credential' not in str(result)
    assert not expected_auth_failure(result,at.replace(hour=15))


def test_auth_mail_once_per_checkpoint_and_not_between(tmp_path):
    monitor=Monitor(Store(tmp_path/'alerts.db'))
    delivered,send=calls()
    for hour in (12,15):
        t=TIME.replace(hour=hour,minute=0)
        v=view(t)
        v['auth_probe']={'slot':auth_slot(t),'status':'INVALID',
                         'provider_code':'401','checked_at':t.isoformat()}
        monitor.tick(v,t)
        monitor.deliver(t,ENV,send=send)
        monitor.deliver(t+timedelta(minutes=1),ENV,send=send)
        assert len(delivered)==(1 if hour==12 else 2)


def test_recovery_stops_reminders(tmp_path):
    monitor=Monitor(Store(tmp_path/'alerts.db'))
    delivered,send=calls()
    monitor.tick(failure(),TIME)
    monitor.deliver(TIME,ENV,send=send)
    t=TIME+timedelta(minutes=1)
    monitor.tick(view(t,active=True),t)
    monitor.tick(view(t+timedelta(seconds=65),active=True),t+timedelta(seconds=65))
    monitor.deliver(TIME+timedelta(minutes=20),ENV,send=send)
    assert len(delivered)==1
