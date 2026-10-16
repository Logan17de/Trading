"""Independent read-only incident monitor. Its only external write is owner email.

The separate journal contains diagnostics and mail receipts, never trading state.
No broker SDK, order route, activation, repair or intent command is available.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import dumps, identity, stamp
from .pc_control import collection_window
from .reporting import resend, receipt_status
from .store import Store

KEY = 'failure-monitor-v1'
ROOT = Path('/var/lib/trading-observer')
MESSAGES = {
    'OBSERVER_UNREACHABLE': ('Oracle observer', 'The local observer socket did not return a valid dashboard.'),
    'WORKER_STUCK': ('Oracle observer', 'The observer heartbeat is stale or its sequence stopped advancing.'),
    'INVALID_HEARTBEAT': ('Oracle observer', 'The observer returned an invalid heartbeat.'),
    'COLLECTOR_FAILED': ('Groww collector', 'The market/account collector reported an error.'),
    'EXECUTOR_WORKER_FAILED': ('Execution worker', 'The execution worker latched a failure. Broker reconciliation is required.'),
    'ORDER_RECONCILIATION_REQUIRED': ('Broker execution', 'An order, protection or external close has an uncertain state. Inspect Groww before another action.'),
    'MARKET_DATA_STALE': ('Market collector', 'No current market/account snapshot is available during the collection window.'),
    'FUNDS_UNAVAILABLE': ('Groww funds', 'The current Groww money read is missing, failed or stale.'),
    'POSITIONS_UNAVAILABLE': ('Groww positions', 'Complete current broker positions are unavailable.'),
    'ORDERS_UNAVAILABLE': ('Groww orders', 'Complete current broker orders are unavailable.'),
    'OWNER_ON_EXECUTION_BLOCKED': ('Live setup', 'Owner intent is On, but live setup or strategy selection still prevents execution.'),
    'DAILY_EMAIL_FAILED': ('Daily report', 'The daily report sender recorded a delivery failure.'),
    'RESEARCH_ARCHIVE_FAILED': ('Research archive', 'The one-way research archive reported a synchronization failure.'),
    'NORMAL_DATA_WORKER_FAILED': ('Normal strategy inputs', 'The current trend/contract reader failed or stopped advancing.'),
    'JOURNAL_BACKUP_FAILED': ('Trade journal backup', 'The most recent journal backup failed verification.'),
    'ALERT_TEST': ('Alert transport test', 'This is a requested notification test. No broker order was sent.'),
    'OWNER_TEST_FAILED': ('Owner broker test', 'The owner test needs attention. Check its entry, stop-loss and remaining position in Groww.'),
}
SETUP_BLOCKERS = {'LIVE_ENVIRONMENT_NOT_ACTIVATED', 'REPOSITORY_PAUSED',
    'REVIEWED_LIVE_ACTIVATION_REQUIRED', 'ALL_EXECUTABLE_STRATEGIES_OFF'}
RECONCILIATION_BLOCKERS = {'BROKER_PROTECTION_RECONCILIATION_REQUIRED',
    'SELF_CLOSE_RECONCILIATION_REQUIRED', 'EXECUTOR_WORKER_RECONCILIATION_REQUIRED'}


def diagnostics(value):
    """Allow only typed diagnostic metadata; never copy raw exception messages."""
    if not isinstance(value, dict): return {}
    result = {}
    for key in ('error_type', 'stage', 'phase', 'sqlite_code', 'protection', 'connection_status'):
        v = value.get(key)
        if isinstance(v, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_,]{0,127}', v): result[key] = v
    code = str(value.get('code', ''))
    if re.fullmatch(r'(?:[1-5][0-9]{2}|GA[0-9]{3})', code): result['provider_code'] = code
    for key in ('received_at', 'heartbeat_at'):
        try: result[key] = stamp(value[key]).isoformat()
        except (KeyError, TypeError, ValueError): pass
    for key in ('paused', 'owner_on'):
        if type(value.get(key)) is bool: result[key] = value[key]
    if value.get('mode') in ('paper', 'live'): result['mode'] = value['mode']
    if type(value.get('heartbeat_sequence')) is int: result['heartbeat_sequence'] = value['heartbeat_sequence']
    result['frames'] = []
    frames=value.get('frames', [])
    for frame in (frames if isinstance(frames,list) else [])[-6:]:
        if not isinstance(frame, dict): continue
        file, function, line = frame.get('file'), frame.get('function'), frame.get('line')
        if (isinstance(file, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,100}\.py', file)
                and isinstance(function, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', function)
                and type(line) is int and 0 < line < 100000):
            result['frames'].append(dict(file=file, function=function, line=line))
    return result


def faults(view, now, health='HEALTHY'):
    found = {}
    def add(code, detail=None, immediate=False):
        found[code] = dict(code=code, component=MESSAGES[code][0], message=MESSAGES[code][1],
            details=diagnostics(detail or {}), immediate=immediate)
    if view is None:
        add('OBSERVER_UNREACHABLE', {'stage':'LOCAL_SOCKET_READ'})
        return found
    runtime = view.get('runtime', {})
    test=view.get('live_setup',{}).get('owner_test',{})
    if test.get('state')=='ERROR':add('OWNER_TEST_FAILED',{'stage':test.get('reason') or test.get('status')},True)
    elif test.get('attention'):add('OWNER_TEST_FAILED',{'stage':test['attention']},True)
    elif test.get('state')=='RUNNING':
        try:stale=(now-stamp(test.get('checked_at'))).total_seconds()>240
        except (ValueError,TypeError):stale=True
        if stale:add('OWNER_TEST_FAILED',{'stage':'OWNER_TEST_JOB_STALE'},True)
    if health in ('WORKER_STUCK', 'INVALID_HEARTBEAT'): add(health, runtime)
    if runtime.get('error'): add('COLLECTOR_FAILED', runtime['error'])
    execution = view.get('execution_controller', {})
    blockers = set(execution.get('blockers', []))
    failure = execution.get('worker_failure')
    if failure: add('EXECUTOR_WORKER_FAILED', failure, True)
    if blockers & RECONCILIATION_BLOCKERS - {'EXECUTOR_WORKER_RECONCILIATION_REQUIRED'} or 'RECONCILIATION_REQUIRED' in execution.get('phase', ''):
        add('ORDER_RECONCILIATION_REQUIRED', execution, True)
    owner_on = view.get('control', {}).get('algo', {}).get('desired_enabled') is True
    if owner_on and blockers & SETUP_BLOCKERS:
        add('OWNER_ON_EXECUTION_BLOCKED', dict(view.get('live_setup', {}), owner_on=True))
        found['OWNER_ON_EXECUTION_BLOCKED']['details']['blockers'] = sorted(blockers & SETUP_BLOCKERS)
    if collection_window(now) and health != 'STARTING':
        def recent(at, limit=45):
            try: return 0 <= (now-stamp(at)).total_seconds() <= limit
            except (ValueError, TypeError): return False
        if not recent(view.get('as_of')): add('MARKET_DATA_STALE', {'received_at':view.get('as_of')})
        funds = view.get('funds', {})
        if funds.get('status') != 'AVAILABLE' or not recent(funds.get('received_at')): add('FUNDS_UNAVAILABLE', funds)
        if view.get('positions_status') != 'AVAILABLE': add('POSITIONS_UNAVAILABLE')
        if view.get('orders_status') != 'AVAILABLE': add('ORDERS_UNAVAILABLE')
        worker=view.get('normal_data_worker')
        if isinstance(worker,dict) and (worker.get('status')=='FAILED' or not recent(worker.get('checked_at'),180)):
            add('NORMAL_DATA_WORKER_FAILED',worker.get('failure',{'stage':'CURRENT_INPUT_WORKER_STALE'}))
    backup=view.get('backup_status',{})
    if backup.get('status')=='FAILED': add('JOURNAL_BACKUP_FAILED',backup.get('failure',{}))
    mail = view.get('daily_email', {})
    status = str(mail.get('status', ''))
    if status in ('FAILED', 'RETRY', 'RETRY_PENDING', 'REVIEW_REQUIRED', 'MAIL_NOT_CONFIGURED',
                  'MAIL_ADDRESS_INVALID', 'EXPIRED', 'DELIVERY_FAILED', 'PROVIDER_BOUNCED',
                  'PROVIDER_FAILED', 'PROVIDER_SUPPRESSED') or status.startswith('REPORT_'):
        add('DAILY_EMAIL_FAILED', {'stage':status})
    archive = view.get('research_archive', {})
    if archive.get('status') in ('FAILED', 'SYNC_FAILED', 'RETRY', 'RETRY_PENDING', 'RETRY_WAIT', 'ARCHIVE_LOCAL_ERROR', 'ERROR'):
        add('RESEARCH_ARCHIVE_FAILED', {'stage':archive.get('failure') or archive['status']})
    return found


def envelope(incident, sender, recipient):
    detail = html.escape(json.dumps(incident['details'], indent=2))
    label = 'Alert test' if incident['code']=='ALERT_TEST' else 'Needs attention'
    return {'from':sender, 'to':[recipient],
        'subject':f"Options Trader · {incident['code']}",
        'html':("<div style='font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px;color:#183247'>"
            f"<div style='color:#c55220;font-weight:bold'>{label}</div>"
            f"<h2>{html.escape(incident['component'])}</h2><p><b>{incident['code']}</b></p>"
            f"<p>{html.escape(incident['message'])}</p>"
            f"<p>UTC {incident['detected_at']}<br>Incident {incident['id']}</p>"
            f"<pre style='padding:16px;background:#f1f6f5;white-space:pre-wrap'>{detail}</pre>"
            "<p>Check Options Trader and Groww. The monitor has not placed, cancelled or modified orders.</p>"
            "<p>Codex incident checks are scheduled; this email does not confirm a wake-up or repair.</p></div>")}


class Monitor:
    def __init__(self, store):
        self.store = store
        self.state = store.meta(KEY, {'incidents':{}, 'previous':None, 'progress_at':None})

    def save(self): self.store.set_meta(KEY, self.state)

    def tick(self, view, now, *, read_error=None):
        from .oracle_link import health
        state = 'UNKNOWN'
        if view is not None:
            state, previous, progress = health(view.get('runtime', {}), now,
                previous=tuple(self.state['previous']) if self.state.get('previous') else None,
                progress_at=self.state.get('progress_at'))
            self.state.update(previous=previous, progress_at=progress)
        found = faults(view, now, state)
        if read_error is not None and 'OBSERVER_UNREACHABLE' in found:
            found['OBSERVER_UNREACHABLE']['details']=diagnostics(dict(read_error,stage='LOCAL_SOCKET_READ'))
        self.record(found, now, complete=view is not None and state=='HEALTHY')
        self.state.update(checked_at=now.isoformat(), health=state)
        self.save()

    def record(self, found, now, *, complete=True):
        rows = self.state['incidents']
        present = set()
        for code, fault in found.items():
            if code not in MESSAGES: raise ValueError('KNOWN_INCIDENT_REQUIRED')
            changing = {k:v for k,v in fault['details'].items() if k not in
                        ('received_at','heartbeat_at','heartbeat_sequence')}
            key = identity([code, changing]); present.add(key)
            row = rows.get(key)
            if row is None or row['state']=='RECOVERED':
                row = rows[key] = dict(fault, id=uuid.uuid4().hex, state='OPEN',
                    detected_at=now.isoformat(), last_seen_at=now.isoformat(),
                    mail_status='PENDING', attempts=0, next_attempt=0, inbox_verified=False)
            row['last_seen_at'] = now.isoformat()
            row.pop('healthy_since', None)
        if complete:
            for key, row in rows.items():
                if key in present or row['state']!='OPEN': continue
                row.setdefault('healthy_since', now.isoformat())
                if (now-stamp(row['healthy_since'])).total_seconds()>=60:
                    row.update(state='RECOVERED', recovered_at=now.isoformat())
        self.save()

    def deliver(self, now, env, *, send=resend):
        from .mail_window import status as mail_window
        self.state['mail_window']=mail_window(now)
        if self.state['mail_window']!='OPEN':
            self.save();return
        # One frozen email per incident. A timeout retries the same envelope/key.
        rows = sorted(self.state['incidents'].values(), key=lambda r:(not r['immediate'], r['detected_at']))
        for row in rows:
            if row['state']!='OPEN' or row['mail_status'] in ('ACCEPTED','DELIVERY_UNKNOWN') or row.get('healthy_since'): continue
            if not row['immediate'] and (now-stamp(row['detected_at'])).total_seconds()<60: continue
            if row['next_attempt']>now.timestamp(): continue
            if row.get('first_attempt') and now.timestamp()-row['first_attempt']>=23*3600:
                row['mail_status']='DELIVERY_UNKNOWN'; self.save(); continue
            row['next_attempt']=now.timestamp()+300
            if not all(env.get(k) for k in ('RESEND_API_KEY','TRADING_REPORT_FROM','TRADING_REPORT_TO')):
                row['mail_status']='MAIL_CONFIGURATION_MISSING'; self.save(); return
            previous_first=row.get('first_attempt')
            row.setdefault('envelope', envelope(row, env['TRADING_REPORT_FROM'], env['TRADING_REPORT_TO']))
            row.setdefault('first_attempt', now.timestamp())
            row['attempts']+=1; row['mail_status']='SENDING'; self.save()
            try:
                provider_id=send(row['envelope'],env['RESEND_API_KEY'],identity(['trading-failure-v1',row['id']]))
                if not isinstance(provider_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}',provider_id):
                    raise ValueError('INVALID_PROVIDER_RECEIPT')
                row.update(mail_status='ACCEPTED',provider_id=provider_id,accepted_at=now.isoformat())
            except Exception as exc:
                from .mail_window import MailWindowClosed
                if isinstance(exc,MailWindowClosed):
                    row.update(mail_status='RETRY' if row['attempts']>1 else 'PENDING',attempts=row['attempts']-1,next_attempt=0)
                    if previous_first is None:row.pop('first_attempt',None)
                    self.state['mail_window']=str(exc);self.save();return
                row.update(mail_status='RETRY',send_error=diagnostics({'error_type':type(exc).__name__}))
            self.save(); return

    def reconcile(self, now, key, *, fetch=receipt_status):
        for row in self.state['incidents'].values():
            if row['mail_status']!='ACCEPTED' or row.get('recipient_server_accepted'): continue
            if now.timestamp()-row.get('receipt_attempt',0)<300: continue
            row['receipt_attempt']=now.timestamp()
            try:
                r=fetch(row['provider_id'],key); mail=row['envelope']
                if r.get('id')!=row['provider_id'] or r.get('from')!=mail['from'] or r.get('to')!=mail['to']:
                    raise ValueError('EXACT_DELIVERY_ENVELOPE_REQUIRED')
                event=r.get('last_event')
                if event in ('sent','delivered','opened','clicked','bounced','failed','suppressed','delivery_delayed','queued'):
                    row.update(provider_event=event,recipient_server_accepted=event in ('delivered','opened','clicked'))
            except Exception: row['receipt_status']='UNAVAILABLE'
            self.save(); return

    def public(self):
        fields=('id','code','component','message','details','state','detected_at','last_seen_at',
            'recovered_at','mail_status','attempts','accepted_at','provider_event','recipient_server_accepted','inbox_verified')
        return dict(format='trading-failure-monitor-v1',checked_at=self.state.get('checked_at'),
            health=self.state.get('health'),mail_window=self.state.get('mail_window'),broker_writes=False,
            incidents=[{k:r[k] for k in fields if k in r} for r in self.state['incidents'].values()])


def read_observer(root):
    # Fixed local read protocol: no arbitrary command or broker endpoint.
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(15); sock.connect(str(root/'observer.sock'))
        sock.sendall(b'{"action":"read"}\n')
        with sock.makefile('rb') as stream: raw=stream.readline(8_000_001)
    if len(raw)>8_000_000: raise ValueError('BOUNDED_OBSERVER_VIEW_REQUIRED')
    view=json.loads(raw)
    if view.get('format')!='trading-dashboard-v1' or view.get('order_capability') is not False:
        raise ValueError('READ_ONLY_OBSERVER_VIEW_REQUIRED')
    return view


def pc_status(root, *, run=None):
    """Read the monitor through private SSH; also return the PC outage incident."""
    import sqlite3
    import subprocess
    from .oracle_link import settings
    run=run or subprocess.run
    config=settings(json.loads((root/'.agent-state/failure-monitor.json').read_text(encoding='utf-8-sig')))
    ssh=Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/OpenSSH/ssh.exe'
    args=[str(ssh),'-F','NUL','-T','-i',config['identity_file'],'-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','IdentityAgent=none',
        '-o','ClearAllForwardings=yes','-o','ConnectTimeout=8','-o','LogLevel=ERROR',
        config['user']+'@'+config['host'],'sudo -n -u trading-observer '+config['python']+
        ' -I -m nifty_engine.agent_engine.failure_alerts status']
    result=dict(format='trading-incident-watch-v1',broker_writes=False,pc_outage=None)
    db_path=(root/'.agent-state/pc-monitor.sqlite3').resolve()
    if db_path.exists():
        with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
            row=db.execute("SELECT body FROM meta WHERE key='vm-incident'").fetchone()
            if row:
                value=json.loads(row[0])
                result['pc_outage']={k:value[k] for k in ('id','status','at','details','alert_status','inbox_verified') if k in value}
    try:
        response=run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20,
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        if response.returncode or len(response.stdout)>500_000: raise ValueError('BOUNDED_MONITOR_RESPONSE_REQUIRED')
        monitor=json.loads(response.stdout)
        if monitor.get('format')!='trading-failure-monitor-v1' or monitor.get('broker_writes') is not False:
            raise ValueError('FIXED_MONITOR_RESPONSE_REQUIRED')
        result['oracle']=monitor
        age=(datetime.now(timezone.utc)-stamp(monitor['checked_at'])).total_seconds()
        result['monitor_status']='CURRENT' if 0<=age<=180 else 'MONITOR_STALE'
    except Exception as exc:
        result.update(monitor_status='MONITOR_UNAVAILABLE',error=diagnostics({'error_type':type(exc).__name__}))
    return result


def saved_status(root):
    """WAL read only: an in-flight send must not lock out diagnostics."""
    import sqlite3
    path=(root/'.agent-state/failure-monitor.sqlite3').resolve()
    monitor=Monitor.__new__(Monitor)
    monitor.state={'incidents':{},'health':'NOT_STARTED'}
    if path.exists():
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
            row=db.execute('SELECT body FROM meta WHERE key=?',(KEY,)).fetchone()
            if row: monitor.state=json.loads(row[0])
    return monitor.public()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('tick','status','test-email','receipt','pc-status'))
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    if args.action=='pc-status':
        print(dumps(pc_status(args.root))); return
    if args.root!=ROOT or os.name!='posix': raise ValueError('FIXED_ORACLE_MONITOR_ROOT_REQUIRED')
    import fcntl
    os.umask(0o077)
    # Reuse the existing environment file, retaining only mail settings. Broker
    # environment values are discarded without being used or exposed.
    env={k:os.environ.get(k,'') for k in ('RESEND_API_KEY','TRADING_REPORT_FROM','TRADING_REPORT_TO')}
    os.environ.clear()
    if args.action=='status':
        print(dumps(saved_status(args.root))); return
    with (args.root/'.agent-state/failure-monitor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        monitor=Monitor(Store(args.root/'.agent-state/failure-monitor.sqlite3'))
        now=datetime.now(timezone.utc)
        if args.action=='tick':
            read_error=None
            try: view=read_observer(args.root)
            except Exception as exc:
                view=None;read_error={'error_type':type(exc).__name__}
            monitor.tick(view,datetime.now(timezone.utc),read_error=read_error)
            monitor.deliver(datetime.now(timezone.utc),env)
        elif args.action=='test-email':
            old=next((r for r in monitor.state['incidents'].values() if r['code']=='ALERT_TEST'),None)
            if old and old.get('mail_status')=='ACCEPTED':
                print(dumps(monitor.public())); return
            fault=dict(code='ALERT_TEST',component=MESSAGES['ALERT_TEST'][0],message=MESSAGES['ALERT_TEST'][1],details={},immediate=True)
            monitor.record({'ALERT_TEST':fault},now,complete=False)
            monitor.deliver(now,env)
        if args.action in ('tick','receipt') and env.get('RESEND_API_KEY'):
            monitor.reconcile(datetime.now(timezone.utc),env['RESEND_API_KEY'])
        print(dumps(monitor.public()))


if __name__=='__main__': main()
