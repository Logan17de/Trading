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


def observer_configuration(mail, source_values, previous_values, credentials):
    """Default only a new installation to paper; deployment never changes mode."""
    mode='paper' if previous_values is None else previous_values.get('EXECUTION_MODE')
    if mode not in ('paper','live'):
        raise ValueError('EXISTING_EXECUTION_MODE_MUST_BE_PAPER_OR_LIVE')
    values=dict(previous_values or {})
    values.update(mail,EXECUTION_MODE=mode,GROWW_OBSERVER_API_KEY=credentials['api_key'],
                  GROWW_OBSERVER_API_SECRET=credentials['api_secret'])
    values.update(server_configuration(source_values,previous_values or {}))
    return values


def stage_runtime_pause(state, original_pause, previous_environment):
    """Initialize a new runtime paused; preserve every established pause choice.

    Older installations have no initialization marker. Their prior config,
    journal or protected environment is sufficient evidence of an established
    runtime, including after the owner explicitly cleared its pause file.
    """
    state,original_pause,previous_environment=map(Path,(state,original_pause,previous_environment))
    config=state/'config';pause=state/'.trader-paused'
    established=(config.exists() or config.is_symlink() or
                 (state/'.agent-state/pc-monitor.sqlite3').is_file() or
                 previous_environment.exists() or previous_environment.is_symlink())
    paused=pause.exists() or pause.is_symlink()
    if paused and not (pause.is_file() or pause.is_symlink()):
        raise ValueError('RUNTIME_PAUSE_FILE_REQUIRED')
    if paused or established:
        return {'paused':paused,'changed':False,'established':established}
    if original_pause.is_symlink() or not original_pause.is_file():
        raise ValueError('INITIAL_PROTECTED_PAUSE_REQUIRED')
    # symlink_to refuses an existing path, including a concurrent creation.
    pause.symlink_to(original_pause)
    return {'paused':True,'changed':True,'established':False}


def deployment_preflight(state, *, mode=None, service_active=False):
    """Refuse a release change while exact ownership or owner On is pending."""
    import sqlite3
    if mode is not None and mode not in ('paper','live'):
        raise ValueError('DEPLOYMENT_EXECUTION_MODE_INVALID')
    pause=Path(state)/'.trader-paused'
    if mode=='live' and service_active and not (pause.exists() or pause.is_symlink()):
        raise ValueError('PAUSE_OR_STOP_LIVE_RUNTIME_BEFORE_DEPLOYMENT')
    journal=Path(state)/'.agent-state/pc-monitor.sqlite3'
    if journal.is_symlink():raise ValueError('DEPLOYMENT_JOURNAL_SYMLINK_REFUSED')
    if not journal.exists():return {'journal_present':False,'owner_off':True,'owned_orders_open':False}
    try:
        with sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
            if db.execute("SELECT 1 FROM pc_orders WHERE state IS NULL OR state<>'CLOSED' LIMIT 1").fetchone():
                raise ValueError('CLOSE_OWNED_BASKET_BEFORE_DEPLOYMENT')
            row=db.execute("SELECT body FROM meta WHERE key='premium-algo-intent'").fetchone()
            intent=json.loads(row[0]) if row else {'enabled':False}
            if not isinstance(intent,dict) or type(intent.get('enabled')) is not bool:
                raise ValueError('DEPLOYMENT_OWNER_INTENT_INVALID')
            if intent['enabled']:raise ValueError('TURN_ALGO_OFF_BEFORE_DEPLOYMENT')
    except (sqlite3.Error,OSError,json.JSONDecodeError,TypeError):
        raise ValueError('DEPLOYMENT_OWNERSHIP_CHECK_UNAVAILABLE') from None
    return {'journal_present':True,'owner_off':True,'owned_orders_open':False}


def deployment_runtime_preflight(state, previous_environment, *, run=None):
    """Read current mode and service state again at each deployment boundary."""
    import subprocess
    previous_environment=Path(previous_environment)
    if previous_environment.is_symlink():raise ValueError('DEPLOYMENT_ENVIRONMENT_SYMLINK_REFUSED')
    mode=read_environment(previous_environment).get('EXECUTION_MODE') if previous_environment.exists() else None
    if previous_environment.exists() and mode not in ('paper','live'):
        raise ValueError('DEPLOYMENT_EXECUTION_MODE_INVALID')
    service=(run or subprocess.run)(['systemctl','is-active','trading-observer'],capture_output=True,text=True,timeout=5)
    state_name=service.stdout.strip()
    if state_name not in ('active','activating','deactivating','reloading','inactive','failed','unknown'):
        raise ValueError('DEPLOYMENT_SERVICE_STATE_UNKNOWN')
    return deployment_preflight(state,mode=mode,service_active=state_name not in ('inactive','failed','unknown'))


def acquire_setup_lock():
    """Share the owner setup lock; never stage while an owner is arming."""
    import fcntl
    descriptor=os.open('/run/trading-owner-setup.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    stream=os.fdopen(descriptor,'a')
    try:
        if os.fstat(stream.fileno()).st_uid!=0:raise ValueError('ROOT_SETUP_LOCK_REQUIRED')
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return stream
    except BaseException:
        stream.close()
        raise


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
    target=source.parent/'observer.env'
    if target.is_symlink():raise PermissionError('symlink refused')
    if target.exists() and (target.stat().st_uid!=0 or target.stat().st_mode&0o777!=0o600):
        raise PermissionError('existing protected observer environment required')
    deployment_runtime_preflight(Path('/var/lib/trading-observer'),target)
    previous=read_environment(target) if target.exists() else None
    values=observer_configuration(mail,source_values,previous,value)
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
    try:
        with acquire_setup_lock():main()
    except Exception as exc:
        print(json.dumps({'status':'PROVISIONING_BLOCKED','error_type':type(exc).__name__}))
        raise SystemExit(2) from None
