"""Continue one owner-authorized test on Oracle, independently of the PC.

Started exclusively by the explicit Buy + protect command. Not an enabled
service or observer callback; a restart reads original references, never retries
a submission. Broker state and error details stay in the private journal.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from . import owner_setup as setup
from .commissioning_direct import AUTH, FINAL
from .contracts import dumps


def record_status(plan_id, result):
    # Root opens the existing file only. No schema/credential/ownership changes.
    path=setup.ROOT/'.agent-state/pc-monitor.sqlite3'
    with sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=5) as db:
        row=db.execute('SELECT body FROM meta WHERE key=?',(AUTH,)).fetchone()
        value=json.loads(row[0]) if row else {}
        if value.get('plan_id')!=plan_id:raise setup.SetupError('EXACT_OWNER_TEST_AUTHORIZATION_REQUIRED')
        value.update(checked_at=datetime.now(timezone.utc).isoformat(),status=result['status'],reason=result.get('reason'),attention=result.get('attention'))
        if result.get('reason') or result['status'] in ('RECONCILIATION_REQUIRED','CHILD_TERMINAL_WITH_REMAINDER'):
            value['state']='ERROR'
        elif result['status'] in FINAL:value['state']='FINISHED'
        db.execute('UPDATE meta SET body=? WHERE key=?',(dumps(value),AUTH))
    return value['state']


def main():
    import fcntl
    if os.name!='posix' or os.geteuid()!=0:raise setup.SetupError('OWNER_ROOT_SETUP_REQUIRED')
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--id',required=True)
    plan_id=setup.preview_id(parser.parse_args().id)
    args=SimpleNamespace(id=plan_id,confirm='')
    while True:
        fd=os.open('/run/trading-owner-setup.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a') as lock:
            if os.fstat(lock.fileno()).st_uid!=0:raise setup.SetupError('ROOT_SETUP_LOCK_REQUIRED')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                time.sleep(5);continue
            try:result=setup.run_worker('advance-test',args,setup.environment(setup.ENV))
            except Exception as exc:
                result=dict(status='OWNER_TEST_FAILED',reason=setup.quiet_error(exc))
            state=record_status(plan_id,result)
        if state!='RUNNING':return
        time.sleep(5)


if __name__=='__main__':main()
