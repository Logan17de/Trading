"""Root-only migration of existing credentials. Never generates or prints keys."""
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path


def main():
    if os.name!='posix' or os.geteuid()!=0:raise PermissionError('root required')
    source=Path('/etc/growing-trader/call-seller.env')
    if source.is_symlink() or source.stat().st_uid!=0 or source.stat().st_mode&0o777!=0o600:
        raise PermissionError('existing protected mail environment required')
    names={'RESEND_API_KEY','TRADING_REPORT_FROM','TRADING_REPORT_TO'}
    mail={}
    for line in source.read_text().splitlines():
        if '=' not in line:continue
        name,raw=line.split('=',1);name=name.strip()
        if name in names:
            words=shlex.split(raw,comments=True)
            if len(words)==1 and words[0]:mail[name]=words[0]
    if set(mail)!=names or mail['TRADING_REPORT_TO']!='loganlogesh17@gmail.com':
        raise ValueError('existing approved mail identities required')
    if sys.argv[1:]==['--mail-pipe']:
        # Only for a caller that captures and DPAPI-encrypts stdout without logging.
        print(json.dumps({'key':mail['RESEND_API_KEY'],'sender':mail['TRADING_REPORT_FROM'],'recipient':mail['TRADING_REPORT_TO']}))
        return
    raw=sys.stdin.read(65537)
    if len(raw)>65536:raise ValueError('bounded credential pipe required')
    value=json.loads(raw)
    if set(value)!={'api_key','api_secret'} or any(not isinstance(v,str) or not 8<=len(v)<=16384 or any(c.isspace() for c in v) for v in value.values()):
        raise ValueError('existing key/secret pair required')
    values=dict(mail,EXECUTION_MODE='paper',GROWW_OBSERVER_API_KEY=value['api_key'],GROWW_OBSERVER_API_SECRET=value['api_secret'])
    target=source.parent/'observer.env'
    if target.is_symlink():raise PermissionError('symlink refused')
    fd,name=tempfile.mkstemp(dir=target.parent,prefix='.observer-')
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as stream:
            for k,v in values.items():
                if any(c in v for c in '\r\n\x00'):raise ValueError('invalid environment value')
                stream.write(k+'='+json.dumps(v)+'\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,target)
    finally:
        if Path(name).exists():Path(name).unlink()
    print('{"status":"EXISTING_KEYS_PROVISIONED","mode":"0600","owner":"root","rotation":false}')


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'PROVISIONING_BLOCKED','error_type':type(exc).__name__}))
        raise SystemExit(2) from None
