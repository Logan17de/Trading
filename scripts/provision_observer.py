"""Root-only migration of existing credentials. Never generates or prints keys."""
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path


def server_configuration(source_values, previous_values):
    """Preserve approved archive credentials through every observer deployment."""
    result={}
    for name in ('SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY'):
        old=previous_values.get(name);new=source_values.get(name)
        if old and new and old!=new:raise ValueError('ARCHIVE_CREDENTIAL_CHANGE_REQUIRES_REVIEW')
        if old or new:result[name]=old or new
    if result and (set(result)!={'SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY'} or
            result['SUPABASE_URL'].rstrip('/')!='https://imirspxhbnerxknyynqx.supabase.co'):
        raise ValueError('APPROVED_ARCHIVE_SETTINGS_REQUIRED')
    return result


def read_environment(path):
    values={}
    for line in path.read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        name,raw=line.split('=',1)
        words=shlex.split(raw,comments=True)
        if len(words)==1 and words[0]:values[name.strip()]=words[0]
    return values


def main():
    if os.name!='posix' or os.geteuid()!=0:raise PermissionError('root required')
    source=Path('/etc/growing-trader/call-seller.env')
    if source.is_symlink() or source.stat().st_uid!=0 or source.stat().st_mode&0o777!=0o600:
        raise PermissionError('existing protected mail environment required')
    names={'RESEND_API_KEY','TRADING_REPORT_FROM','TRADING_REPORT_TO'}
    source_values=read_environment(source)
    mail={k:source_values[k] for k in names if k in source_values}
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
    if target.exists() and (target.stat().st_uid!=0 or target.stat().st_mode&0o777!=0o600):
        raise PermissionError('existing protected observer environment required')
    values.update(server_configuration(source_values,read_environment(target) if target.exists() else {}))
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
