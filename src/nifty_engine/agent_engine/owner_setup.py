"""Owner-operated Oracle commissioning. Never invoked by Start or maintenance.

preview/capture/status only read the broker. submit/close/cancel submit the exact
owner-confirmed test plan. arm prepares live mode while global Algo stays Off.
Credentials and provider identities remain in memory/private Oracle storage.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import ipaddress
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import dumps, identity

ROOT = Path('/var/lib/trading-observer')
ENV = Path('/etc/growing-trader/observer.env')
ORIGINAL_PAUSE = Path('/opt/growing-trader/.trader-paused')
PLAN_KEY = 'broker-commissioning-plan-'


class SetupError(ValueError):
    """An internal, non-secret setup reason; provider exceptions never use this."""
    def __init__(self, code):
        self.code = code if re.fullmatch('[A-Z][A-Z0-9_]{1,100}', str(code)) else 'OWNER_SETUP_FAILED'
        super().__init__(self.code)


def validate_deployed_release(release, root=ROOT, unit=Path('/etc/systemd/system/trading-observer.service')):
    if (not re.fullmatch('[a-f0-9]{40}', release.name)
            or (root/'config').resolve() != (release/'config').resolve()):
        raise SetupError('CURRENT_DEPLOYED_RELEASE_REQUIRED')
    starts=[line.strip() for line in unit.read_text().splitlines() if line.startswith('ExecStart=')]
    expected=f'ExecStart={release}/venv/bin/python -I -m nifty_engine.agent_engine.oracle_runtime serve --root {root}'
    if starts!=[expected]:raise SetupError('CURRENT_SERVICE_RELEASE_REQUIRED')


def environment(path):
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o777 != 0o600:
        raise SetupError('PROTECTED_ROOT_ENVIRONMENT_REQUIRED')
    result = {}
    for line in path.read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'): continue
        key, raw = line.split('=', 1)
        words = shlex.split(raw, comments=True)
        if len(words) != 1 or key.strip() in result: raise SetupError('INVALID_ENVIRONMENT')
        result[key.strip()] = words[0]
    if result.get('EXECUTION_MODE') not in ('paper', 'live'): raise SetupError('INVALID_EXECUTION_MODE')
    return result


def observed_ip():
    """No credentials, proxy or redirects are sent to the public IP check."""
    from urllib.request import build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler, Request
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): raise SetupError('IP_REDIRECT_REFUSED')
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
    with opener.open(Request('https://api.ipify.org', headers={'Accept':'text/plain'}), timeout=5) as response:
        value = response.read(65).decode('ascii').strip()
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_global: raise SetupError('PUBLIC_ORACLE_IPV4_REQUIRED')
    return str(address)


def require_direct_route(values):
    if any(value and key.upper() in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY') for key,value in values.items()):
        raise SetupError('DIRECT_ORACLE_CONNECTION_REQUIRED')


def binding(root, market):
    from . import premium_strategy, report_strategies, normal_theta
    release = Path(__file__).resolve().parents[3].name
    if not re.fullmatch('[a-f0-9]{40}', release): raise SetupError('IMMUTABLE_ORACLE_RELEASE_REQUIRED')
    validate_deployed_release(Path(__file__).resolve().parents[3],root)
    market.limiter.wait()
    profile = market.groww.get_user_profile(timeout=5)
    if not all(isinstance(profile.get(k), str) and profile[k] for k in ('vendor_user_id', 'ucc')):
        raise SetupError('EXACT_BROKER_ACCOUNT_REQUIRED')
    sdk = importlib.metadata.version('growwapi')
    if sdk != '1.5.0': raise SetupError('REVIEWED_SDK_REQUIRED')
    normal = normal_theta.load(root)
    return dict(release=release, policy_hash=identity({
        'legacy_management':premium_strategy.load(root), 'research':report_strategies.load(root),
        **({'normal':normal} if normal else {})}), sdk_version=sdk,
        account_fingerprint=identity({k:profile[k] for k in ('vendor_user_id','ucc')}),
        egress_ip=observed_ip(), host='ORACLE')


def preview_id(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):
        raise SetupError('EXACT_PREVIEW_PLAN_ID_REQUIRED')
    return value


def assemble_plan(spec, current, plan_id=None):
    required={'contract','quantity','trigger_price','buy_limit_price','sell_limit_price','valid_until','egress_ip'}
    from .commissioning_stop import FORMAT, DIRECT_FORMAT, FIELDS
    direct=isinstance(spec,dict) and spec.get('entry_mode')=='LIMIT'
    if not isinstance(spec,dict) or set(spec)!=required | FIELDS | ({'entry_mode'} if direct else set()): raise SetupError('EXACT_PRIVATE_PLAN_SPEC_REQUIRED')
    if spec['egress_ip'] != current['egress_ip']: raise SetupError('DECLARED_WHITELIST_IP_DIFFERS')
    clean={k:v for k,v in spec.items() if k!='entry_mode'}
    return dict(clean, format=DIRECT_FORMAT if direct else FORMAT,
                id=uuid.uuid4().hex if plan_id is None else preview_id(plan_id),
                **{k:current[k] for k in ('release','policy_hash','sdk_version','account_fingerprint')})


def plan_status(store, tool, current, plan_id):
    """Recover a lost preview/submit response without ever submitting an order."""
    from .broker_commissioning import PREFIX, CommissioningError, validate_plan
    plan_id=preview_id(plan_id)
    saved=store.meta(PLAN_KEY+plan_id)
    if not isinstance(saved,dict):raise SetupError('PREVIEW_PLAN_FIRST')
    record=store.meta(PREFIX+plan_id)
    try:
        plan=validate_plan(saved,current,tool.clock(),expired=True)
    except CommissioningError as exc:
        reason=str(exc)
        return dict(status='OWNER_PREVIEW_BINDING_CHANGED' if reason=='COMMISSIONING_BINDING_CHANGED' else
                    'OWNER_PREVIEW_INVALID',reason=reason,plan_id=plan_id,plan_hash=identity(saved),broker_writes=False)
    if plan['id']!=plan_id:raise SetupError('PREVIEW_PLAN_ID_CONFLICT')
    plan_hash=identity(plan)
    if record is not None:
        if not isinstance(record,dict) or record.get('plan_hash')!=plan_hash:
            raise SetupError('PREVIEW_SUBMITTED_PLAN_CONFLICT')
        return dict(tool.status(plan_id),plan_id=plan_id,plan_hash=plan_hash)
    try:
        validate_plan(plan,current,tool.clock())
    except CommissioningError as exc:
        if str(exc)!='COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG':raise
        return dict(status='OWNER_PREVIEW_EXPIRED',reason=str(exc),plan_id=plan_id,
                    plan_hash=plan_hash,broker_writes=False)
    result=tool.preview(plan)
    if result.get('plan_hash')!=plan_hash:raise SetupError('PREVIEW_NORMALIZED_HASH_DIFFERS')
    # The worker lock serializes owner actions. Recheck for a record before
    # returning a ready preview in case another trusted process wrote meanwhile.
    if store.meta(PREFIX+plan_id) is not None:return plan_status(store,tool,current,plan_id)
    return dict(result,plan_id=plan_id,plan_hash=plan_hash)


def preview_plan(store, tool, spec, current, plan_id=None):
    """Persist one immutable normalized preview; the supplied ID is retry-safe."""
    from .broker_commissioning import PREFIX, validate_plan
    plan=validate_plan(assemble_plan(spec,current,plan_id),current,tool.clock(),expired=True)
    plan_id=plan['id'];key=PLAN_KEY+plan_id
    saved=store.meta(key)
    if saved is not None:
        if identity(validate_plan(saved,current,tool.clock(),expired=True))!=identity(plan):
            raise SetupError('PREVIEW_PLAN_ID_CONFLICT')
        return plan_status(store,tool,current,plan_id)
    if store.meta(PREFIX+plan_id) is not None:raise SetupError('PREVIEW_SUBMITTED_PLAN_RECORD_MISSING')
    result=tool.preview(plan)
    if result.get('plan_hash')!=identity(plan):raise SetupError('PREVIEW_NORMALIZED_HASH_DIFFERS')
    with store.transaction() as db:
        previous=db.execute('SELECT body FROM meta WHERE key=?',(key,)).fetchone()
        submitted=db.execute('SELECT 1 FROM meta WHERE key=?',(PREFIX+plan_id,)).fetchone()
        if submitted:raise SetupError('PREVIEW_PLAN_CHANGED_DURING_READ')
        if previous:
            if identity(validate_plan(json.loads(previous[0]),current,tool.clock(),expired=True))!=identity(plan):
                raise SetupError('PREVIEW_PLAN_ID_CONFLICT')
        else:db.execute('INSERT INTO meta VALUES(?,?)',(key,dumps(plan)))
    return dict(result,plan_id=plan_id,plan_hash=identity(plan))


def arm_recovery_status(store, tool, current, plan_id, status):
    """Confirm a lost arm reply from present facts; never retry the arm action."""
    from .live_setup import activation
    from .premium_strategy import intent
    if tool.mode!='live' or tool.pause_file.exists() or tool.pause_file.is_symlink():return status
    try:
        proof=activation(store,current['release'],current['policy_hash'],current['account_fingerprint'])
        if not proof or proof.get('plan_id')!=plan_id or proof.get('binding')!=current:return status
        release=Path(__file__).resolve().parents[3]
        if release.name!=current['release']:return status
        validate_deployed_release(release,ROOT)
        if intent(store)['enabled'] or tool.pause_file.exists() or tool.pause_file.is_symlink():return status
    except (KeyError,ValueError,TypeError,OSError):
        return status
    return dict(status,status='LIVE_PREPARED_ALGO_OFF',plan_id=plan_id,algo_enabled=False,
                broker_writes=False,production_activation_changed=False,
                next_step='SELECT_NORMAL_THETA_THEN_OWNER_ALGO_START')


def quiet_error(exc):
    if isinstance(exc, SetupError):return exc.code
    from .broker_commissioning import CommissioningError
    return str(exc) if isinstance(exc,CommissioningError) else type(exc).__name__


def authorize_test(store, tool, current, plan, confirmation):
    """Persist only the owner's exact reviewed instruction; submission checks run in the job."""
    from .commissioning_direct import AUTH
    from .commissioning_stop import DIRECT_FORMAT
    from .broker_commissioning import validate_plan
    if plan.get('format') != DIRECT_FORMAT or confirmation != identity(plan):
        raise SetupError('EXACT_OWNER_PLAN_HASH_REQUIRED')
    validate_plan(plan,current,tool.clock())
    tool._guard()
    prior=store.meta(AUTH,{})
    if prior.get('plan_id')==plan['id']:raise SetupError('OWNER_TEST_ALREADY_AUTHORIZED')
    if prior.get('state')=='RUNNING':raise SetupError('OWNER_TEST_ALREADY_RUNNING')
    store.set_meta(AUTH,dict(plan_id=plan['id'],plan_hash=identity(plan),binding=current,
        state='RUNNING',authorized_at=tool.clock().isoformat(),checked_at=tool.clock().isoformat()))
    return dict(status='OWNER_TEST_RUNNING',plan_id=plan['id'],plan_hash=identity(plan),broker_writes=False)


def submit_manual_buy(store, tool, current, plan, confirmation):
    """One explicitly authorized direct BUY. Never starts the SL/target worker.

    A persisted owner authorization and the exact plan are required, and the
    direct broker submit writes its durable attempt before contacting Groww.
    A lost response must be reconciled, never retried with another BUY.
    """
    from .commissioning_stop import DIRECT_FORMAT
    if plan.get('format') != DIRECT_FORMAT:
        raise SetupError('EXACT_DIRECT_LIMIT_BUY_REQUIRED')
    authorize_test(store, tool, current, plan, confirmation)
    from .commissioning_direct import AUTH
    auth=store.meta(AUTH,{})
    store.set_meta(AUTH,dict(auth,manual_only=True))
    return tool.submit(plan, confirmation)


def finish_manual_buy_when_settled(store, plan_id, status):
    """No background worker exists in manual mode; finish after verified flatness."""
    from .commissioning_direct import AUTH
    auth=store.meta(AUTH,{})
    if (auth.get('manual_only') is True and auth.get('plan_id')==plan_id and
            auth.get('state')=='RUNNING' and
            status.get('status') in ('COMPLETE','CLOSED_UNVERIFIED','CANCELLED_FLAT','ABORTED_BEFORE_WRITE')):
        store.set_meta(AUTH,dict(auth,state='FINISHED',completed_at=status.get('checked_at')))
    return status


def start_test_job(args, values):
    result=run_worker('authorize-test',args,values)
    try:
        proc=subprocess.run(['systemd-run','--quiet','--collect','--unit=trading-owner-test-'+args.id,
            '--property=Restart=on-failure','--property=RestartSec=10','--property=RuntimeMaxSec=43200',
            '--property=StandardOutput=null','--property=StandardError=null','--property=UMask=0077',
            '--property=MemoryMax=160M','--property=TasksMax=12','--property=CPUQuota=25%',
            sys.executable,'-I','-m','nifty_engine.agent_engine.owner_test_job','--id',args.id],
            capture_output=True,text=True,timeout=10)
        if proc.returncode:raise SetupError('OWNER_TEST_JOB_START_FAILED')
    except Exception:
        # A launch timeout can mean systemd accepted the job. Revoke continuation
        # under the same root lock, without claiming that a broker write occurred.
        from .owner_test_job import record_status
        record_status(args.id,dict(status='OWNER_TEST_FAILED',reason='OWNER_TEST_JOB_START_FAILED'))
        raise SetupError('OWNER_TEST_JOB_START_FAILED') from None
    return result


def retire_plan(store, tool, current, plan_id):
    """Read-only broker reconciliation across a source upgrade; never rebind proof."""
    from .broker_commissioning import Commissioning, PREFIX, validate_plan
    record = store.meta(PREFIX + preview_id(plan_id))
    if not isinstance(record, dict):
        # A failed/unused preview has no broker record: writes persist that
        # record before contacting Groww. Keep its draft, without trapping the
        # owner behind an old release or pretending a test was completed.
        from .commissioning_direct import AUTH
        plan=store.meta(PLAN_KEY+plan_id)
        auth=store.meta(AUTH,{})
        if auth.get('plan_id')==plan_id:
            if auth.get('state')!='ERROR':raise SetupError('OWNER_AUTHORIZED_TEST_REQUIRES_REVIEW')
            # The worker lock is held, no pre-write broker record exists, and
            # the supervisor stopped. Retire this failed authorization only.
            tool._guard()
            store.set_meta(AUTH,dict(auth,state='FINISHED',status='ABORTED_BEFORE_WRITE',
                retired_at=tool.clock().isoformat()))
        if plan is None:
            # Preview may fail before its immutable plan is saved. Both the
            # authorization and pre-write broker record are absent for this ID.
            tool._guard()
            return dict(status='PRIOR_TEST_RETIRED',broker_writes=False,preview_only=True,
                        checked_at=tool.clock().isoformat())
        if not isinstance(plan,dict):raise SetupError('ORIGINAL_SUBMITTED_PLAN_REQUIRED')
        original={k:plan[k] for k in current if k!='host'};original['host']='ORACLE'
        if any(original.get(k)!=current.get(k) for k in current if k!='release'):
            raise SetupError('ORIGINAL_ACCOUNT_AND_POLICY_REQUIRED')
        validate_plan(plan,original,tool.clock(),expired=True)
        tool._guard()
        store.set_meta('broker-commissioning-retirement-'+plan_id,dict(
            original_binding=original,verifier_binding=current,preview_only=True,
            checked_at=tool.clock().isoformat(),broker_writes=False,provider_verified=False))
        return dict(status='PRIOR_TEST_RETIRED',plan_hash=identity(plan),broker_writes=False,
                    preview_only=True,checked_at=tool.clock().isoformat())
    original = record['binding']
    if any(original.get(k) != current.get(k) for k in current if k != 'release'):
        raise SetupError('ORIGINAL_ACCOUNT_AND_POLICY_REQUIRED')
    validate_plan(record['plan'], original, tool.clock(), expired=True)
    old = Commissioning(store, tool.market, binding=original, pause_file=tool.pause_file,
                        mode=tool.mode, clock=tool.clock, master=tool.master)
    old._guard()
    status = old.capture(plan_id)
    if status['status'] not in ('CANCELLED_FLAT', 'ABORTED_BEFORE_WRITE','CLOSED_UNVERIFIED'):
        return dict(status, broker_writes=False)
    # COMPLETE live evidence is deliberately not migrated. An aborted attempt
    # cannot have a parent/child; cancelled tests require the recorded flat reads.
    settled = old._load(plan_id)
    if status['status'] in ('CANCELLED_FLAT','CLOSED_UNVERIFIED') and (not status['facts']['flat_confirmed'] or
            not status['facts']['inactive_parent']):
        raise SetupError('COMMISSIONING_CURRENT_FLAT_TERMINAL_STATE_REQUIRED')
    store.set_meta('broker-commissioning-retirement-' + plan_id,
        dict(original_binding=original, verifier_binding=current, record_sha256=identity(settled),
             checked_at=tool.clock().isoformat(), broker_writes=False, provider_verified=False))
    return dict(status='PRIOR_TEST_RETIRED', broker_writes=False, provider_verified=False,
                plan_hash=record['plan_hash'], checked_at=tool.clock().isoformat())


def worker(payload):
    """Only root's fixed child runs this, as the journal's service UID."""
    import fcntl
    import pwd
    from .store import Store
    from .premium_strategy import intent
    from .market_check import readonly_transport
    from .broker_commissioning import Commissioning, validated_evidence
    from .live_setup import receipt
    root=Path(payload['root'])
    if root != ROOT or os.geteuid() != pwd.getpwnam('trading-observer').pw_uid:
        raise SetupError('EXACT_ORACLE_SERVICE_IDENTITY_REQUIRED')
    require_direct_route(os.environ)
    store=Store(root/'.agent-state/pc-monitor.sqlite3')
    if payload['action'] != 'lookup' and intent(store)['enabled']: raise SetupError('TURN_ALGO_OFF_BEFORE_SETUP')
    mode=payload['environment']['EXECUTION_MODE']
    lock=(root/'.agent-state/broker-commissioning.lock').open('a')
    with lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with readonly_transport([],dashboard=True,history=True,timeout_seconds=5):
            from growwapi import GrowwAPI
            from ..brokers.groww_data import GrowwMarketData
            token=GrowwAPI.get_access_token(api_key=payload['environment']['GROWW_OBSERVER_API_KEY'],
                                          secret=payload['environment']['GROWW_OBSERVER_API_SECRET'])
            market=GrowwMarketData(token)
            current=binding(root,market)
            tool=Commissioning(store,market,binding=current,pause_file=root/'.trader-paused',mode=mode,
                               clock=lambda:datetime.now(timezone.utc))
            action=payload['action'];plan_id=payload.get('id')
            if action=='lookup':
                from .execution_data import metadata_catalog
                from .market_check import quote_summary
                from .contracts import EXCHANGES
                spec=payload['spec']
                if (not isinstance(spec,dict) or set(spec)!={'index','symbol'} or spec['index'] not in ('NIFTY','SENSEX') or
                        not isinstance(spec['symbol'],str) or not re.fullmatch('[A-Z0-9]{8,50}(?:CE|PE)',spec['symbol'])):
                    raise SetupError('EXACT_COMMISSIONING_OPTION_REQUIRED')
                candidates=[row for (index,expiry),rows in metadata_catalog(tool.master()).items() if index==spec['index']
                            for symbol,row in rows.items() if symbol==spec['symbol']]
                if len(candidates)!=1:raise SetupError('COMMISSIONING_CURRENT_MASTER_MISMATCH')
                c={k:v for k,v in candidates[0].items() if k!='strike'}
                q=quote_summary(market.quote(c['symbol'],segment='FNO',exchange=EXCHANGES[c['index']]),tool.clock())
                return dict(status='OWNER_CONTRACT_FOUND',contract=c,egress_ip=current['egress_ip'],
                            quote=q,checked_at=tool.clock().isoformat(),broker_writes=False)
            if action=='preview':
                return preview_plan(store,tool,payload['spec'],current,plan_id)
            plan_id=preview_id(plan_id)
            plan=store.meta(PLAN_KEY+plan_id)
            if not plan: raise SetupError('PREVIEW_PLAN_FIRST')
            if action=='authorize-test':
                return authorize_test(store,tool,current,plan,payload['confirm'])
            if action=='manual-buy':
                return submit_manual_buy(store,tool,current,plan,payload['confirm'])
            if action=='advance-test':
                from .commissioning_direct import advance
                return advance(tool,plan)
            if action=='submit':return tool.submit(plan,payload['confirm'])
            if action in ('close','cancel'):return getattr(tool,action)(plan_id,payload['confirm'])
            if action=='protect':return tool.protect(plan_id,payload['confirm'])
            if action=='cancel-stop':return tool.cancel_stop(plan_id,payload['confirm'])
            if action=='retire':return retire_plan(store,tool,current,plan_id)
            if action=='capture':
                return finish_manual_buy_when_settled(store,plan_id,tool.capture(plan_id))
            if action=='status':
                return finish_manual_buy_when_settled(store,plan_id,
                    arm_recovery_status(store,tool,current,plan_id,plan_status(store,tool,current,plan_id)))
            if action in ('arm-preview','install-receipt'):
                proof=tool.verify_ready_to_arm(plan_id)
                digest=identity(proof)
                if action=='install-receipt':
                    if payload['confirm']!=digest:raise SetupError('EXACT_EVIDENCE_CONFIRMATION_REQUIRED')
                    if intent(store)['enabled']:raise SetupError('TURN_ALGO_OFF_BEFORE_SETUP')
                    rows=store.read("SELECT 1 FROM pc_orders WHERE state<>'CLOSED' LIMIT 1")
                    if rows:raise SetupError('OWNED_BASKET_MUST_BE_CLOSED_BEFORE_ARMING')
                    from .operations import backup_database
                    backup_database(store.path,root/'.agent-state/backups'/(
                        'pre-live-setup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.sqlite3'))
                    # A large verified backup can take minutes. Obtain current
                    # account, IP and flat broker evidence again before arming.
                    if binding(root,market)!=current:raise SetupError('BINDING_CHANGED_DURING_BACKUP')
                    proof=tool.verify_ready_to_arm(plan_id)
                    if identity(proof)!=digest:raise SetupError('EVIDENCE_CHANGED_DURING_BACKUP')
                    if intent(store)['enabled']:raise SetupError('TURN_ALGO_OFF_BEFORE_SETUP')
                    store.set_meta('premium-execution-activation',receipt(proof,datetime.now(timezone.utc)))
                return dict(status='VERIFIED_OWNER_EVIDENCE',evidence_digest=digest,broker_writes=False,algo_enabled=False)
            raise SetupError('UNKNOWN_SETUP_ACTION')


def run_worker(action, args, values, **extra):
    payload=dict(action=action,root=str(ROOT),environment={k:values[k] for k in (
        'EXECUTION_MODE','GROWW_OBSERVER_API_KEY','GROWW_OBSERVER_API_SECRET')},id=args.id,confirm=args.confirm,**extra)
    result=subprocess.run(['runuser','-u','trading-observer','--',sys.executable,'-I','-m',
        'nifty_engine.agent_engine.owner_setup','_worker'],input=dumps(payload),capture_output=True,text=True,
        timeout=600 if action=='install-receipt' else 180)
    try:value=json.loads(result.stdout)
    except (ValueError,TypeError):raise SetupError('SETUP_WORKER_UNAVAILABLE') from None
    if result.returncode:raise SetupError(value.get('reason','SETUP_WORKER_FAILED'))
    return value


def atomic_environment(path, raw):
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.live-setup-')
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if Path(name).exists():Path(name).unlink()


def live_environment(raw):
    lines=raw.decode().splitlines(keepends=True)
    found=[i for i,line in enumerate(lines) if line.startswith('EXECUTION_MODE=')]
    if len(found)!=1:raise SetupError('EXACT_EXECUTION_MODE_LINE_REQUIRED')
    lines[found[0]]='EXECUTION_MODE="live"\n'
    return ''.join(lines).encode()


def arm(args, values):
    """Explicit owner command only. Any failure restores paper/pause before restart."""
    if values['EXECUTION_MODE']!='paper':raise SetupError('PAPER_REQUIRED_FOR_COMMISSIONING')
    validate_deployed_release(Path(__file__).resolve().parents[3],ROOT)
    preview=run_worker('arm-preview',args,values)
    if not args.confirm or preview['evidence_digest']!=args.confirm:
        raise SetupError('CONFIRM_EXACT_ARM_PREVIEW_DIGEST')
    release=Path(__file__).resolve().parents[3]
    # Replays run with no broker credentials. Source is the installed exact release.
    checked=subprocess.run([sys.executable,'-I','-m','pytest','-q',
        str(release/'tests/test_broker_commissioning.py'),str(release/'tests/test_commissioning_stop.py'),str(release/'tests/test_live_setup.py'),
        str(release/'tests/test_premium_execution.py'),str(release/'tests/test_external_close.py')],
        cwd=release,capture_output=True,text=True,timeout=600,
        env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','HOME','TMPDIR')})
    if checked.returncode:raise SetupError('DEPLOYED_REPLAY_CHECKS_FAILED')
    pause=ROOT/'.trader-paused'
    if not (pause.exists() or pause.is_symlink()) or not ORIGINAL_PAUSE.is_file():
        raise SetupError('ORIGINAL_AND_RUNTIME_PAUSE_REQUIRED')
    raw=ENV.read_bytes()
    original_link=os.readlink(pause) if pause.is_symlink() else None
    original_content=None if original_link else pause.read_bytes()
    subprocess.run(['systemctl','stop','trading-observer'],check=True,capture_output=True)
    success=False
    try:
        verified=run_worker('install-receipt',args,values)
        if verified['evidence_digest']!=args.confirm:raise SetupError('EVIDENCE_CHANGED_REVIEW_REQUIRED')
        atomic_environment(ENV,live_environment(raw))
        # Unlink this runtime's pause only. The original shared trader stays paused.
        pause.unlink()
        subprocess.run(['systemctl','start','trading-observer'],check=True,capture_output=True)
        success=True
    finally:
        if not success:
            atomic_environment(ENV,raw)
            if not (pause.exists() or pause.is_symlink()):
                if original_link:pause.symlink_to(original_link)
                else:pause.write_bytes(original_content)
            subprocess.run(['systemctl','start','trading-observer'],check=True,capture_output=True)
    return dict(status='LIVE_PREPARED_ALGO_OFF',broker_writes=False,algo_enabled=False,
                next_step='SELECT_NORMAL_THETA_THEN_OWNER_ALGO_START')


def root_action(args):
    """Serialize the entire owner action, including service/mode transitions."""
    import fcntl
    descriptor=os.open('/run/trading-owner-setup.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'a') as lock:
        if os.fstat(lock.fileno()).st_uid!=0:raise SetupError('ROOT_SETUP_LOCK_REQUIRED')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        values=environment(ENV)
        if args.action in ('preview','preview-stdin') and args.id is not None:preview_id(args.id)
        if args.action in ('preview-stdin','lookup-stdin'):
            raw=sys.stdin.read(8193)
            if len(raw)>8192:raise SetupError('BOUNDED_PRIVATE_PLAN_REQUIRED')
            return run_worker('preview' if args.action=='preview-stdin' else 'lookup',args,values,spec=json.loads(raw))
        if args.action=='preview':
            if not args.spec or args.spec.is_symlink() or not args.spec.resolve().is_relative_to(ROOT/'.agent-state'):
                raise SetupError('PRIVATE_ORACLE_PLAN_FILE_REQUIRED')
            if args.spec.stat().st_mode&0o077 or args.spec.stat().st_size>8192:raise SetupError('PRIVATE_BOUNDED_PLAN_REQUIRED')
            return run_worker('preview',args,values,spec=json.loads(args.spec.read_text()))
        if not args.id or not re.fullmatch('[a-f0-9]{32}',args.id):raise SetupError('EXACT_PREVIEW_PLAN_ID_REQUIRED')
        if args.action in ('submit','close','cancel','protect','cancel-stop','arm','buy-protect','manual-buy') and not re.fullmatch('[a-f0-9]{64}',args.confirm):
            raise SetupError('OWNER_EXACT_CONFIRMATION_REQUIRED')
        if args.action=='buy-protect':
            return start_test_job(args,values)
        return arm(args,values) if args.action=='arm' else run_worker(args.action,args,values)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preview','preview-stdin','lookup-stdin','buy-protect','manual-buy','submit','capture','status','close','cancel','protect','cancel-stop','retire','arm-preview','arm','_worker'))
    parser.add_argument('--spec',type=Path,help='Private Oracle JSON file; never a credential file')
    parser.add_argument('--id',help='Optional 32-hex preview ID for safe retries; required for subsequent actions')
    parser.add_argument('--confirm',default='',help='Exact plan hash for a test write, or evidence digest for arm')
    args=parser.parse_args()
    if os.name!='posix':raise SetupError('RUN_SETUP_ON_ORACLE')
    if args.action=='_worker':
        raw=sys.stdin.read(131073)
        if len(raw)>131072:raise SetupError('BOUNDED_SETUP_PIPE_REQUIRED')
        with open(os.devnull,'w') as quiet,contextlib.redirect_stdout(quiet),contextlib.redirect_stderr(quiet):
            logging.disable(logging.CRITICAL)
            result=worker(json.loads(raw))
        print(dumps(result));return
    if os.geteuid()!=0:raise SetupError('OWNER_ROOT_SETUP_REQUIRED')
    print(dumps(root_action(args)))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(dumps(dict(status='OWNER_SETUP_BLOCKED',reason=quiet_error(exc))))
        raise SystemExit(2) from None
