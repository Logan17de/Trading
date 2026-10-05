import copy
import json
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from nifty_engine.agent_engine import premium_strategy as p
from nifty_engine.agent_engine.pc_control import JST,PcJournal
from nifty_engine.agent_engine.oracle_link import health,request,RemoteViewer,revalidate
from nifty_engine.agent_engine.visual_report import build,DailyMail
from nifty_engine.agent_engine.dashboard import view_model,DashboardState

ROOT=Path(__file__).parents[1]
NOW=datetime(2026,10,5,14,5,tzinfo=JST)
POLICY=p.load(ROOT)

def test_persistent_on_survives_restart_cutoff_and_failed_readiness(tmp_path):
    journal=PcJournal(tmp_path/'j.sqlite3')
    assert p.intent(journal.store)['enabled'] is False
    p.set_intent(journal.store,True,NOW)
    reopened=PcJournal(tmp_path/'j.sqlite3')
    assert p.intent(reopened.store)['enabled'] is True
    for at in (NOW,NOW.replace(hour=20),NOW+timedelta(days=2)):
        r=p.readiness(POLICY,at,desired_enabled=p.intent(reopened.store)['enabled'])
        assert r['status']=='ON_BLOCKED' and r['desired_enabled']
        assert not r['execution_enabled'] and not r['broker_writes']
    p.set_intent(reopened.store,False,NOW)
    assert not p.intent(PcJournal(tmp_path/'j.sqlite3').store)['enabled']

def test_premium_thresholds_hold_stop_and_hedge_costs():
    position=dict(ownership='ENGINE_VERIFIED',received_at=NOW.isoformat(),short_entry=20,short_premium=15.01)
    assert p.everyday_review(POLICY,position,NOW,end_of_day=True)['action']=='HOLD'
    position['short_premium']=15
    assert p.everyday_review(POLICY,position,NOW,end_of_day=True)['reason']=='END_OF_DAY_PREMIUM_LOW'
    position['short_premium']=8
    assert p.everyday_review(POLICY,position,NOW)['action']=='HOLD'
    position['short_premium']=7.99
    assert p.everyday_review(POLICY,position,NOW)['keep_hedge'] is True
    position['net_pnl_inr']=-2000
    assert p.everyday_review(POLICY,position,NOW)['reason']=='BASKET_LOSS_STOP'
    position['ownership']='MANUAL_OR_UNKNOWN_PROTECTED'
    assert p.everyday_review(POLICY,position,NOW)['action']=='WAIT'
    assert p.hedge_change(1500,1650,50)['action']=='KEEP_HEDGE'
    assert p.hedge_change(1500,1651,50)['action']=='REVIEW_HEDGE_REPLACEMENT'

def test_calendar_window_and_expiry_three_intervals():
    assert p.entry_window(NOW.replace(hour=14,minute=0))
    assert not p.entry_window(NOW.replace(hour=19,minute=0))
    assert p.preferred_index(NOW)=='NIFTY'
    assert p.preferred_index(NOW+timedelta(days=2))=='SENSEX'
    strikes=list(range(24700,25301,50))
    r=p.expiry_short(POLICY,'NIFTY',25000,strikes,'UP',actual_expiry=True)
    assert r['short_strike']==24850 and r['option_type']=='PE' and r['hedge_required']
    assert r['maximum_lots']==2
    assert p.expiry_short(POLICY,'NIFTY',25000,strikes,'DOWN',actual_expiry=True)['short_strike']==25150
    assert p.expiry_short(POLICY,'NIFTY',25000,strikes,'UP',actual_expiry=False)['action']=='WAIT'
    assert p.expiry_short(POLICY,'NIFTY',25000,strikes[::2],'UP',actual_expiry=True)['action']=='WAIT'

def test_matching_position_skips_without_adopting_manual_and_replace_only_owned():
    leg={'symbol':'NIFTY26O0624850PE','side':'SELL','quantity':65,'product':'NRML'}
    manual=dict(leg,ownership='MANUAL_OR_UNKNOWN_PROTECTED')
    assert p.expiry_transition([manual],[leg],complete=True)['action']=='SKIP_MATCHING_POSITION'
    assert p.expiry_transition([manual],[dict(leg,quantity=130)],complete=True)['action']=='WAIT'
    owned=dict(leg,symbol='NIFTY26O0625000CE',ownership='ENGINE_VERIFIED')
    hedge=dict(leg,symbol='NIFTY26O0624700PE',side='BUY')
    r=p.expiry_transition([owned],[leg,hedge],complete=True)
    assert r['action']=='REVIEW_REPLACE_OWNED_SPREAD' and r['manual_positions_untouched']
    assert p.expiry_transition([owned],[leg],complete=False)['action']=='WAIT'
    assert p.expiry_transition([owned],[leg],complete=True)['action']=='WAIT'
    with pytest.raises(ValueError):p.validate(dict(POLICY,maximum_lots=3))

def test_heartbeat_requires_actual_advancement_and_bounded_timestamps():
    v={'boot_id':'boot','heartbeat_sequence':1,'heartbeat_at':NOW.isoformat()}
    state,old,at=health(v,NOW)
    assert state=='HEALTHY'
    assert health(v,NOW+timedelta(seconds=31),previous=old,progress_at=at)[0]=='WORKER_STUCK'
    v.update(heartbeat_at=(NOW+timedelta(seconds=31)).isoformat())
    assert health(v,NOW+timedelta(seconds=31),previous=old,progress_at=at)[0]=='WORKER_STUCK'
    v['heartbeat_sequence']=2
    assert health(v,NOW+timedelta(seconds=31),previous=old,progress_at=at)[0]=='HEALTHY'
    assert health(v,NOW)[0]=='WORKER_STUCK'
    startup=dict(boot_id='new',heartbeat_sequence=1,heartbeat_at=NOW.isoformat(),initializing=True)
    assert health(startup,NOW+timedelta(seconds=119))[0]=='STARTING'
    assert health(startup,NOW+timedelta(seconds=121))[0]=='WORKER_STUCK'

def model():
    protocol=json.loads((ROOT/'config/owner_strategies.json').read_text())
    return view_model(None,protocol,now=NOW)

def test_visual_mail_only_due_at_1930_daily_frozen_retry_never_changes_intent(tmp_path,monkeypatch):
    store=PcJournal(tmp_path/'j.sqlite3').store
    p.set_intent(store,True,NOW)
    monkeypatch.setenv('RESEND_API_KEY','fake-test-key')
    monkeypatch.setenv('TRADING_REPORT_FROM','sender@example.test')
    monkeypatch.setenv('TRADING_REPORT_TO','owner@example.test')
    mail=DailyMail(store);view=model();calls=[]
    due=NOW.replace(hour=19,minute=29)
    assert mail.tick(view,due-timedelta(seconds=1))['status']=='SCHEDULED_BY_1930_JST'
    def failure(payload,key,rid):calls.append(copy.deepcopy(payload));raise TimeoutError('private')
    assert mail.tick(view,due,send=failure)['status']=='RETRY_PENDING'
    changed=model();changed['markets'][0]['price']=99999
    def success(payload,key,rid):calls.append(copy.deepcopy(payload));return 'fake-receipt'
    fetch=lambda rid,key:{'id':rid,'from':'sender@example.test','to':['owner@example.test'],'last_event':'delivered'}
    r=mail.tick(changed,due+timedelta(seconds=61),send=success,fetch=fetch)
    assert r['provider_accepted'] and r['recipient_server_accepted'] and not r['inbox_verified']
    assert calls[0]==calls[1] and p.intent(store)['enabled']
    mail.tick(changed,due+timedelta(seconds=120),send=success,fetch=fetch)
    assert len(calls)==2
    assert 'private' not in json.dumps(store.read('SELECT error FROM reports'))
    # Calendar days, including a weekend, receive an honest no-current-data report.
    weekend=due+timedelta(days=5)
    assert mail.tick(view,weekend,send=success,fetch=fetch)['provider_accepted']

def test_visual_report_has_only_active_strategies_real_curves_and_unknowns():
    v=model();bundle=build(v,'2026-10-05')
    assert 'Swing' not in bundle['mail']['html']
    assert 'Everyday' in bundle['mail']['html'] and 'Late-session' in bundle['mail']['html']
    assert not bundle['mail']['attachments']
    assert 'before charges' in bundle['mail']['html']

def test_cached_vm_response_rechecks_independent_money_quote_and_pnl_clocks():
    view=model();view['as_of']=NOW.isoformat()
    view['funds'].update(status='AVAILABLE',clear_cash_inr=9999,received_at=(NOW-timedelta(seconds=46)).isoformat())
    view['markets'][0].update(price=25000,received_at=(NOW-timedelta(seconds=16)).isoformat())
    view['broker_pnl']['series']=[{'at':NOW.isoformat(),'self':10,'algo':0,'unassigned':0}]
    revalidate(view,NOW)
    assert view['funds']['clear_cash_inr'] is None and view['funds']['status']=='STALE'
    assert view['markets'][0]['price'] is None
    assert view['broker_pnl']['total_inr'] is None and len(view['broker_pnl']['series'])==1

def test_remote_outage_is_deduplicated_and_retries_without_mutating_intent(tmp_path):
    (tmp_path/'.agent-state').mkdir()
    cfg={'format':'trading-oracle-viewer-v1','host':'example.test','user':'ubuntu',
         'identity_file':str((tmp_path/'key').resolve()),'python':'/opt/growing-trader/releases/'+'a'*40+'/venv/bin/python','root':'/var/lib/trading-observer'}
    (tmp_path/'.agent-state/oracle-viewer.json').write_text(json.dumps(cfg))
    store=PcJournal(tmp_path/'j.sqlite3').store;p.set_intent(store,True,NOW)
    remote=RemoteViewer(tmp_path,store);alerts=[]
    def failed(*args):raise ConnectionError('private')
    def alert(*args):alerts.append(args);return 'ACCEPTED'
    assert remote.poll(NOW,fetch=failed,alert=alert)=='RECONNECTING'
    assert remote.poll(NOW+timedelta(seconds=31),fetch=failed,alert=alert)=='VM_UNREACHABLE'
    remote.poll(NOW+timedelta(seconds=100),fetch=failed,alert=alert)
    assert len(alerts)==1 and p.intent(store)['enabled']

def test_heartbeat_clock_is_checked_after_ssh_response_not_before(tmp_path,monkeypatch):
    import nifty_engine.agent_engine.oracle_link as link
    (tmp_path/'.agent-state').mkdir()
    cfg={'format':'trading-oracle-viewer-v1','host':'example.test','user':'ubuntu',
         'identity_file':str((tmp_path/'key').resolve()),'python':'/opt/growing-trader/releases/'+'a'*40+'/venv/bin/python','root':'/var/lib/trading-observer'}
    (tmp_path/'.agent-state/oracle-viewer.json').write_text(json.dumps(cfg))
    remote=RemoteViewer(tmp_path,PcJournal(tmp_path/'j.sqlite3').store)
    current=[NOW]
    monkeypatch.setattr(link,'datetime',SimpleNamespace(now=lambda tz:current[0]))
    def fetch(*args):
        current[0]=NOW+timedelta(seconds=5)
        return {'runtime':{'boot_id':'boot','heartbeat_sequence':2,'heartbeat_at':(NOW+timedelta(seconds=3)).isoformat()}}
    assert remote.poll(fetch=fetch,alert=lambda *args:pytest.fail('false alert'))=='HEALTHY'
