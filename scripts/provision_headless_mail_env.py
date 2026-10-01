"""Install existing GitHub mail secrets at the owner-approved Oracle path via stdin.

Only creates a missing file. Never replaces an existing environment or prints values.
No service, schedule, pause, broker permission or VM lifecycle action.
"""
import json
import os
import shlex
import stat
import sys
from pathlib import Path

target = Path('/etc/growing-trader/call-seller.env')
payload = json.loads(sys.stdin.read(20000))
allowed = {'RESEND_API_KEY', 'TRADING_REPORT_FROM', 'TRADING_REPORT_TO'}
if set(payload) != allowed or any(not isinstance(v,str) or not v or '\n' in v or '\r' in v for v in payload.values()):
    raise SystemExit('Missing or invalid mail configuration')
if payload['TRADING_REPORT_TO'] != 'loganlogesh17@gmail.com':
    raise SystemExit('Recipient differs from existing approved configuration')
if target.exists() or target.is_symlink():
    mode = target.stat()
    if target.is_symlink() or mode.st_uid != 0 or stat.S_IMODE(mode.st_mode) != 0o600:
        raise SystemExit('Existing environment needs ownership/permission review')
    print('EXISTING_ENV_PRESERVED')
else:
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write('EXECUTION_MODE=paper\n')
        for key in sorted(allowed):
            output.write(key+'='+shlex.quote(payload[key])+'\n')
        output.flush()
        os.fsync(output.fileno())
    print('MAIL_ENV_CREATED')
