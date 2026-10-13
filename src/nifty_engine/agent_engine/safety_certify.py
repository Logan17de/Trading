"""Explicit owner safety-mail attestation, with no broker API or trade capability.

Root-only environment load -> read-only Resend / private Supabase status ->
owner confirmation -> drop to observer user -> write the private observer ledger.

This module cannot provision email secrets, schedule Cron, activate LIVE mode,
remove pauses, select a strategy or place orders.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .contracts import stamp
from .live_start import KEY, MAX_PROOF_AGE
from .store import Store

ROOT=Path('/var/lib/trading-observer')
ENV=Path('/etc/growing-trader/observer.env')
PROJECT='https://imirspxhbnerxknyynqx.supabase.co'
CONFIRM='I_VERIFIED_SAFETY_TESTS'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('REDIRECT_REFUSED')


def _current(at,now,seconds=MAX_PROOF_AGE):
    try:
        age=(now-stamp(at)).total_seconds()
        return 0<=age<=seconds
    except (ValueError,TypeError,KeyError):
        return False


def mail_receipt(incidents,recipient,now):
    """One actual recipient-server acceptance of an explicit test email."""
    if not isinstance(recipient,str) or '@' not in recipient:
        return None
    return max((
        r['accepted_at'] for r in incidents
        if (r.get('code')=='ALERT_TEST' and r.get('mail_status')=='ACCEPTED'
            and r.get('recipient_server_accepted') is True
            and _current(r.get('accepted_at'),now))
    ),default=None)


def cloud_receipt(last_accepted_at,cron_ready,now):
    return bool(cron_ready is True and _current(last_accepted_at,now))


def _request(path,key,*,method='GET'):
    if not isinstance(key,str) or not key:
        raise ValueError('PRIVATE_SERVICE_ROLE_REQUIRED')
    headers={'apikey':key,'Accept':'application/json'}
    if key.count('.')==2: headers['Authorization']='Bearer '+key
    if method=='POST': headers['Content-Type']='application/json'
    request=urllib.request.Request(PROJECT+path,method=method,headers=headers,
                                   data=b'{}' if method=='POST' else None)
    with urllib.request.build_opener(NoRedirect).open(request,timeout=8) as response:
        if response.status!=200:raise ValueError('VERIFIED_CLOUD_RECEIPT_REQUIRED')
        raw=response.read(8193)
    if len(raw)>8192:raise ValueError('BOUNDED_PRIVATE_RESPONSE_REQUIRED')
    return json.loads(raw)


def cloud_state(key):
    row=_request('/rest/v1/trading_vm_safety_outbox?select=last_accepted_at&singleton=eq.true',key)
    cron=_request('/rest/v1/rpc/trading_vm_cron_ready',key,method='POST')
    if not isinstance(row,list) or len(row)!=1 or not isinstance(row[0],dict) or type(cron) is not bool:
        raise ValueError('PRIVATE_CLOUD_MONITOR_EVIDENCE_REQUIRED')
    return row[0].get('last_accepted_at'),cron


def prove(release,now,local_accepted,cloud_accepted,cron_active):
    if not re.fullmatch('[a-f0-9]{40}',release):
        raise ValueError('IMMUTABLE_RELEASE_REQUIRED')
    if not _current(local_accepted,now):
        return dict(status='BLOCKED',reason='LOCAL_TEST_EMAIL_NOT_DELIVERED',broker_writes=False)
    if not cloud_receipt(cloud_accepted,cron_active,now):
        return dict(status='BLOCKED',reason='CLOUD_CRON_AND_REAL_OUTAGE_MAIL_REQUIRED',broker_writes=False)
    earliest=min(stamp(local_accepted),stamp(cloud_accepted))
    return dict(format=KEY,release=release,owner_verified=True,
                critical_transport_tested=True,independent_cloud_outage_tested=True,
                api_checkpoint_code_tested=True,
                provider_acceptance_at=earliest.isoformat(),
                reviewed_at=now.isoformat())


def run(*,install=False,confirm=None):
    if os.name!='posix' or os.geteuid()!=0:
        raise ValueError('ROOT_OWNER_COMMISSIONING_REQUIRED')
    from .owner_setup import environment
    from .failure_alerts import saved_status
    values=environment(ENV)
    now=datetime.now(timezone.utc)
    release=Path(__file__).resolve().parents[3].name
    local=mail_receipt(saved_status(ROOT)['incidents'],values.get('TRADING_REPORT_TO'),now)
    cloud,active=cloud_state(values.get('SUPABASE_SERVICE_ROLE_KEY'))
    result=prove(release,now,local,cloud,active)
    if result.get('status')=='BLOCKED' or not install:
        return result if result.get('status')=='BLOCKED' else dict(
            status='EVIDENCE_READY_FOR_EXPLICIT_OWNER_INSTALL',broker_writes=False,
            checked_at=now.isoformat())
    if confirm!=CONFIRM:raise ValueError('EXACT_SAFETY_OWNER_CONFIRMATION_REQUIRED')
    # Re-run the reviewed synthetic failure/alert/start checks from this exact
    # immutable release with no broker, Supabase or mail credentials.
    release_root=Path(__file__).resolve().parents[3]
    checked=subprocess.run([sys.executable,'-I','-m','pytest','-q',
                            str(release_root/'tests/test_safety_alerts.py'),
                            str(release_root/'tests/test_safety_heartbeat.py'),
                            str(release_root/'tests/test_live_start.py')],
                           cwd=release_root,stdin=subprocess.DEVNULL,
                           capture_output=True,text=True,timeout=300,
                           env={k:v for k,v in os.environ.items()
                                if k in ('PATH','LANG','LC_ALL','HOME','TMPDIR')})
    if checked.returncode:
        raise ValueError('EXACT_RELEASE_SAFETY_TESTS_FAILED')
    # Never write an observer-owned SQLite WAL as root; relinquish privileges.
    import pwd
    observer=pwd.getpwnam('trading-observer')
    os.setgroups([])
    os.setgid(observer.pw_gid)
    os.setuid(observer.pw_uid)
    Store(ROOT/'.agent-state/pc-monitor.sqlite3').set_meta(KEY,result)
    return dict(status='SAFETY_ALERT_EVIDENCE_INSTALLED',broker_writes=False,
                reviewed_at=result['reviewed_at'],live_mode_changed=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','install'))
    parser.add_argument('--confirm')
    args=parser.parse_args()
    try:
        print(json.dumps(run(install=args.action=='install',confirm=args.confirm),
                         sort_keys=True))
    except Exception as exc:
        print(json.dumps({'status':'SAFETY_COMMISSIONING_UNAVAILABLE',
                          'reason':type(exc).__name__,'broker_writes':False,
                          'live_mode_changed':False},sort_keys=True))
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
