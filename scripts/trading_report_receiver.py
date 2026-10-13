"""Fixed Oracle receiver for authorized PC OBSERVE reports. No broker capability.

Root reads only the existing approved mail settings, then drops to the existing
growing-research identity. This script lives in a root-owned reviewed release.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import stat
import sys

# -I ignores PYTHONPATH: import only the source adjacent to this pinned release.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from datetime import datetime, timezone
from nifty_engine.agent_engine.contracts import IST, dumps, keys, snapshot, stamp
from nifty_engine.agent_engine.reporting import build_report, queue_report, deliver_once, reconcile_receipt
from nifty_engine.agent_engine.store import Store


def receive(payload,store,now,*,send=None,fetch=None):
    keys(payload,{'format','day','snapshot','coverage'})
    if payload['format']!='trading-pc-report-v1' or payload['day']!=now.astimezone(IST).date().isoformat():
        raise ValueError('current session report required')
    body=snapshot(payload['snapshot'])
    if (body['mode']!='OBSERVE' or body['source']!='groww-local-session-v1'
            or body['portfolio']['accounting_day']!=payload['day']
            or stamp(body['observed_at']).astimezone(IST).date()!=now.astimezone(IST).date()
            or not 0<=(now-stamp(body['observed_at'])).total_seconds()<=7200):
        raise ValueError('actual current observation publication required')
    coverage=payload['coverage']
    keys(coverage,{'status','observations','complete_observations','max_gap_seconds','expected_interval_seconds',
        'source_timestamp_semantics_verified','first_at','last_at'})
    if coverage['status'] not in ('COMPLETE_OBSERVED_SESSION','INCOMPLETE_SESSION'):
        raise ValueError('unknown coverage status')
    store.ingest(body,now)
    store.set_meta('session_coverage_'+payload['day'],coverage)
    bundle=build_report(store,payload['day'],now)
    queue_report(store,bundle)
    status=deliver_once(store,now,**({'send':send} if send else {}))
    accepted=store.read("SELECT id,status FROM reports WHERE day=?",(payload['day'],))
    row=accepted[0]
    receipt=reconcile_receipt(store,row['id'],now,**({'fetch':fetch} if fetch else {})) if row['status']=='ACCEPTED' else {}
    return {'status':'RECIPIENT_SERVER_ACCEPTED' if receipt.get('status')=='RECIPIENT_SERVER_ACCEPTED' else status,
            'provider_accepted':row['status']=='ACCEPTED',
            'recipient_server_accepted':receipt.get('status')=='RECIPIENT_SERVER_ACCEPTED','inbox_verified':False}


def main():
    os.umask(0o077)
    if os.name!='posix' or os.geteuid()!=0: raise ValueError('reviewed Oracle bootstrap required')
    root=Path(__file__).resolve().parents[1]
    if any(root.joinpath(*parts).stat().st_uid!=0 for parts in [(),('scripts',),('scripts','trading_report_receiver.py')]):
        raise ValueError('release must be root owned')
    env=Path('/etc/growing-trader/call-seller.env')
    if env.is_symlink() or env.stat().st_uid!=0 or stat.S_IMODE(env.stat().st_mode)!=0o600:
        raise ValueError('mail environment ownership invalid')
    values={}
    for line in env.read_text().splitlines():
        for token in shlex.split(line,comments=True):
            name,sep,value=token.partition('=')
            if sep and name in {'RESEND_API_KEY','TRADING_REPORT_FROM','TRADING_REPORT_TO'}: values[name]=value
    if values.get('TRADING_REPORT_TO')!='loganlogesh17@gmail.com': raise ValueError('approved recipient differs')
    import pwd
    user=pwd.getpwnam('growing-research')
    os.environ.clear();os.environ.update(values)
    os.setgroups([]);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
    raw=sys.stdin.read(128001)
    if len(raw)>128000: raise ValueError('report input too large')
    store=Store('/var/lib/growing-research/pc-report-outbox.sqlite3',shared_group=True)
    print(dumps(receive(json.loads(raw),store,datetime.now(timezone.utc))))


if __name__=='__main__':
    try: main()
    except Exception:
        print(dumps({'status':'REVIEW_REQUIRED','provider_accepted':False,
                     'recipient_server_accepted':False,'inbox_verified':False}))
        raise SystemExit(2) from None
