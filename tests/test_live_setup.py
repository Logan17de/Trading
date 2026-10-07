"""Synthetic setup replays only; broker calls and service changes are mocked."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import json
import subprocess

import pytest


def test_commissioning_refuses_proxy_egress_mismatch():
    from nifty_engine.agent_engine.owner_setup import require_direct_route, SetupError
    require_direct_route({'NO_PROXY':'localhost','HTTPS_PROXY':''})
    for name in ('HTTP_PROXY','https_proxy','ALL_PROXY'):
        with pytest.raises(SetupError,match='DIRECT_ORACLE_CONNECTION_REQUIRED'):
            require_direct_route({name:'http://private-proxy.invalid'})

from nifty_engine.agent_engine import live_setup, owner_setup
from nifty_engine.agent_engine.contracts import identity


class MemoryStore:
    def __init__(self,value):self.value=value
    def meta(self,key,default=None):return deepcopy(self.value)


@pytest.mark.parametrize('value',[None,[],False,'corrupt',{},
    {'format':'trading-execution-activation-v1','owner_approved':True,'replay_verified':True,
     'broker_write_verified':True,'persistent_gtt_verified':True,'child_link_verified':True}])
def test_corrupt_or_legacy_receipt_does_not_authorize(value):
    assert live_setup.activation(MemoryStore(value),'a'*40,'b'*64) is None


@pytest.fixture
def commissioned(tmp_path):
    from test_broker_commissioning import Session
    session=Session(tmp_path)
    evidence=session.complete()
    session.store.set_meta(live_setup.KEY,live_setup.receipt(evidence,session.now))
    return session,evidence


def test_v2_receipt_revalidates_complete_provider_evidence_and_preserves_off(commissioned):
    s,evidence=commissioned
    assert live_setup.activation(s.store,s.binding['release'],s.binding['policy_hash'],s.binding['account_fingerprint'])==evidence
    assert s.store.meta('premium-algo-intent') is None and s.pause.exists()
    receipt=s.store.meta(live_setup.KEY)
    assert receipt['evidence_digest']==identity(evidence) and receipt['owner_approved'] and receipt['replay_verified']


@pytest.mark.parametrize('fault',['release','policy','account','digest','owner','replay','source_record'])
def test_v2_receipt_never_overrides_changed_binding_or_evidence(commissioned,fault):
    from nifty_engine.agent_engine.broker_commissioning import PREFIX
    s,_=commissioned
    release=s.binding['release'];policy=s.binding['policy_hash'];account=s.binding['account_fingerprint']
    receipt=s.store.meta(live_setup.KEY)
    if fault=='release':release='d'*40
    if fault=='policy':policy='e'*64
    if fault=='account':account='f'*64
    if fault=='digest':receipt['evidence_digest']='0'*64
    if fault=='owner':receipt['owner_approved']=False
    if fault=='replay':receipt['replay_verified']=False
    if fault=='source_record':
        record=s.store.meta(PREFIX+s.plan['id']);record['child']['order']['order_reference_id']='foreign-order'
        s.store.set_meta(PREFIX+s.plan['id'],record)
    s.store.set_meta(live_setup.KEY,receipt)
    assert live_setup.activation(s.store,release,policy,account) is None


def test_production_gate_requires_current_account_then_owner_start(commissioned):
    from nifty_engine.agent_engine.execution_gate import ExecutionGate
    from nifty_engine.agent_engine.premium_strategy import set_intent
    s,_=commissioned
    gate=ExecutionGate(s.journal,s.pause,mode='paper',release=s.binding['release'],host='ORACLE',
        clock=lambda:s.now,policy_hash=s.binding['policy_hash'],commissioning_required=True)
    observation={'complete':True,'received_at':s.now.isoformat()}
    assert 'REVIEWED_LIVE_ACTIVATION_REQUIRED' in gate.blockers(observation)
    gate.account_fingerprint=s.binding['account_fingerprint']
    assert 'REVIEWED_LIVE_ACTIVATION_REQUIRED' not in gate.blockers(observation)
    assert {'LIVE_ENVIRONMENT_NOT_ACTIVATED','REPOSITORY_PAUSED','OWNER_ALGO_OFF'}<=set(gate.blockers(observation))
    gate.mode='live';s.pause.unlink()
    assert gate.blockers(observation)==['OWNER_ALGO_OFF']
    set_intent(s.store,True,s.now)
    assert gate.blockers(observation)==[]
    gate.account_fingerprint='f'*64
    assert 'REVIEWED_LIVE_ACTIVATION_REQUIRED' in gate.blockers(observation)


def test_setup_projection_exposes_only_status_without_private_provider_identifiers(commissioned):
    s,_=commissioned
    gate=SimpleNamespace(release=s.binding['release'],policy_hash=s.binding['policy_hash'],
        account_fingerprint=s.binding['account_fingerprint'],mode='paper',pause_file=s.pause)
    value=live_setup.public(s.store,gate)
    assert value['provider_verified'] and value['activation_installed'] and value['paused']
    assert value['next_step']=='OWNER_LIVE_SETUP_REQUIRED' and not value['broker_writes']
    for private in (s.plan['id'],s.binding['account_fingerprint'],s.binding['egress_ip']):
        assert private not in str(value)
    gate.mode='live';s.pause.unlink()
    assert live_setup.public(s.store,gate)['next_step']=='OWNER_START'


@pytest.fixture
def arm_recovery(commissioned,tmp_path,monkeypatch):
    s,evidence=commissioned
    s.commission.mode='live';s.pause.unlink()
    release=tmp_path/s.binding['release']
    monkeypatch.setattr(owner_setup,'__file__',str(release/'src/nifty_engine/agent_engine/owner_setup.py'))
    deployed=Mock(return_value=None)
    monkeypatch.setattr(owner_setup,'validate_deployed_release',deployed)
    status=dict(s.commission.status(s.plan['id']),plan_id=s.plan['id'],plan_hash=identity(s.plan))
    return SimpleNamespace(session=s,evidence=evidence,status=status,deployed=deployed,release=release)


def test_lost_arm_reply_is_confirmed_from_actual_live_off_receipt_without_retry(arm_recovery):
    f=arm_recovery;s=f.session
    before=s.store.read('SELECT * FROM meta ORDER BY key');writes=deepcopy(s.broker.writes)
    result=owner_setup.arm_recovery_status(s.store,s.commission,s.binding,s.plan['id'],f.status)
    assert result['status']=='LIVE_PREPARED_ALGO_OFF' and result['plan_id']==s.plan['id']
    assert result['plan_hash']==f.status['plan_hash'] and result['algo_enabled'] is False
    assert result['broker_writes'] is False and result['production_activation_changed'] is False
    f.deployed.assert_called_once_with(f.release,owner_setup.ROOT)
    assert s.store.read('SELECT * FROM meta ORDER BY key')==before and s.broker.writes==writes
    assert not s.pause.exists() and s.commission.mode=='live' and s.store.meta('premium-algo-intent') is None


@pytest.mark.parametrize('fault',['paper','pause','owner_on','receipt_absent','wrong_plan','release','policy',
    'account','ip','service','foreign_code'])
def test_uncertain_arm_never_promoted_without_every_current_fact(arm_recovery,monkeypatch,fault):
    from nifty_engine.agent_engine.premium_strategy import set_intent
    f=arm_recovery;s=f.session;current=deepcopy(s.binding);plan_id=s.plan['id']
    if fault=='paper':s.commission.mode='paper'
    if fault=='pause':s.pause.touch()
    if fault=='owner_on':set_intent(s.store,True,s.now)
    if fault=='receipt_absent':s.store.set_meta(live_setup.KEY,{})
    if fault=='wrong_plan':plan_id='e'*32
    if fault=='release':current['release']='e'*40
    if fault=='policy':current['policy_hash']='f'*64
    if fault=='account':current['account_fingerprint']='f'*64
    if fault=='ip':current['egress_ip']='1.1.1.1'
    if fault=='service':f.deployed.side_effect=owner_setup.SetupError('CURRENT_SERVICE_RELEASE_REQUIRED')
    if fault=='foreign_code':monkeypatch.setattr(owner_setup,'__file__',str(f.release.parent/'wrong/src/nifty_engine/agent_engine/owner_setup.py'))
    before=s.store.read('SELECT * FROM meta ORDER BY key');writes=deepcopy(s.broker.writes)
    result=owner_setup.arm_recovery_status(s.store,s.commission,current,plan_id,f.status)
    assert result is f.status and result['status']!='LIVE_PREPARED_ALGO_OFF'
    assert s.store.read('SELECT * FROM meta ORDER BY key')==before and s.broker.writes==writes


@pytest.mark.parametrize('fault',['pause','owner_on'])
def test_arm_recovery_rechecks_owner_and_pause_after_deployment_read(arm_recovery,fault):
    from nifty_engine.agent_engine.premium_strategy import set_intent
    f=arm_recovery;s=f.session;writes=deepcopy(s.broker.writes)
    def changed(*args):
        if fault=='pause':s.pause.touch()
        else:set_intent(s.store,True,s.now)
    f.deployed.side_effect=changed
    assert owner_setup.arm_recovery_status(s.store,s.commission,s.binding,s.plan['id'],f.status) is f.status
    assert s.broker.writes==writes


def test_mode_edit_preserves_every_other_environment_byte():
    raw=b'# saved\r\nGROWW_OBSERVER_API_KEY="PRIVATE_FIXTURE"\r\nEXECUTION_MODE="paper"\r\nOTHER="keep"\r\n'
    updated=owner_setup.live_environment(raw)
    assert updated==raw.replace(b'EXECUTION_MODE="paper"\r\n',b'EXECUTION_MODE="live"\n')


def test_owner_setup_requires_exact_config_and_service_release(tmp_path):
    release=tmp_path/('a'*40);release.mkdir();(release/'config').mkdir()
    unit=tmp_path/'observer.service'
    command=f'ExecStart={release}/venv/bin/python -I -m nifty_engine.agent_engine.oracle_runtime serve --root {release}'
    unit.write_text('[Service]\n'+command+'\n')
    owner_setup.validate_deployed_release(release,release,unit)
    unit.write_text('[Service]\n'+command.replace('a'*40,'b'*40)+'\n')
    with pytest.raises(owner_setup.SetupError,match='CURRENT_SERVICE_RELEASE_REQUIRED'):
        owner_setup.validate_deployed_release(release,release,unit)
    with pytest.raises(owner_setup.SetupError,match='CURRENT_DEPLOYED_RELEASE_REQUIRED'):
        owner_setup.validate_deployed_release(release,tmp_path/'other-state',unit)
    with pytest.raises(owner_setup.SetupError,match='CURRENT_DEPLOYED_RELEASE_REQUIRED'):
        owner_setup.validate_deployed_release(tmp_path/'mutable-main',release,unit)


@pytest.mark.parametrize('raw',[b'',b'OTHER=one\n',b'EXECUTION_MODE=paper\nEXECUTION_MODE=live\n'])
def test_ambiguous_mode_edit_is_rejected(raw):
    with pytest.raises(owner_setup.SetupError,match='EXACT_EXECUTION_MODE_LINE_REQUIRED'):
        owner_setup.live_environment(raw)


def test_only_internal_typed_error_codes_can_be_displayed():
    assert owner_setup.quiet_error(owner_setup.SetupError('EXACT_EVIDENCE_CONFIRMATION_REQUIRED'))=='EXACT_EVIDENCE_CONFIRMATION_REQUIRED'
    for secret in ('PRIVATE_TOKEN_UPPERCASE','private-key-value','Bearer fixture-secret','https://secret@provider/'):
        assert owner_setup.quiet_error(ValueError(secret))=='ValueError'
        assert secret not in owner_setup.quiet_error(RuntimeError(secret))


def test_assemble_plan_rejects_extra_fields_and_ip_changes():
    current=dict(release='a'*40,policy_hash='b'*64,sdk_version='1.5.0',account_fingerprint='c'*64,egress_ip='8.8.8.8')
    spec=dict(contract={'symbol':'fixture-contract'},quantity=1,trigger_price=1,
              buy_limit_price=1.1,sell_limit_price=.9,valid_until='2026-10-07T06:00:00+00:00',egress_ip='8.8.8.8')
    with pytest.raises(owner_setup.SetupError,match='EXACT_PRIVATE_PLAN_SPEC_REQUIRED'):
        owner_setup.assemble_plan(dict(spec,api_key='private-fixture'),current)
    with pytest.raises(owner_setup.SetupError,match='DECLARED_WHITELIST_IP_DIFFERS'):
        owner_setup.assemble_plan(dict(spec,egress_ip='1.1.1.1'),current)
    plan=owner_setup.assemble_plan(spec,current)
    assert plan['release']==current['release'] and plan['account_fingerprint']==current['account_fingerprint']
    assert len(plan['id'])==32 and plan['format']=='trading-broker-commissioning-plan-v1'
    assert 'api_key' not in plan


@pytest.fixture
def preview_session(tmp_path):
    from test_broker_commissioning import Session
    s=Session(tmp_path);s.plan['id']='d'*32
    fields={'contract','quantity','trigger_price','buy_limit_price','sell_limit_price','valid_until','egress_ip'}
    s.spec={key:deepcopy(s.plan[key]) for key in fields}
    return s


def test_preview_id_is_stable_and_hash_matches_normalized_submission(preview_session):
    from nifty_engine.agent_engine.broker_commissioning import validate_plan
    s=preview_session
    s.spec.update(trigger_price=9,buy_limit_price=10,sell_limit_price=9.5)
    first=owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    saved=s.store.meta(owner_setup.PLAN_KEY+s.plan['id'])
    assert first['plan_id']==s.plan['id'] and first['plan_hash']==identity(saved)
    assert saved==validate_plan(saved,s.binding,s.now)
    assert isinstance(saved['buy_limit_price'],str)
    second=owner_setup.preview_plan(s.store,s.commission,deepcopy(s.spec),s.binding,s.plan['id'])
    assert second==first and s.broker.writes==[]
    s.commission.submit(saved,first['plan_hash'])
    assert len(s.broker.writes)==1
    status=owner_setup.plan_status(s.store,s.commission,s.binding,s.plan['id'])
    assert status['plan_id']==first['plan_id'] and status['plan_hash']==first['plan_hash']
    assert status['status']!='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'
    assert len(s.broker.writes)==1


def test_lost_preview_response_recovers_exact_ready_hash_without_mutation(preview_session):
    s=preview_session
    first=owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    before=s.store.read('SELECT * FROM meta ORDER BY key')
    status=owner_setup.plan_status(s.store,s.commission,s.binding,s.plan['id'])
    assert status==first and status['status']=='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'
    assert s.store.read('SELECT * FROM meta ORDER BY key')==before and s.broker.writes==[]


@pytest.mark.parametrize('fault',['spec','release','account','ip'])
def test_retry_never_replaces_existing_preview_with_different_input(preview_session,fault):
    s=preview_session
    owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    before=s.store.read('SELECT * FROM meta ORDER BY key')
    spec=deepcopy(s.spec);current=deepcopy(s.binding)
    if fault=='spec':spec['buy_limit_price']='10.05'
    if fault=='release':current['release']='e'*40
    if fault=='account':current['account_fingerprint']='f'*64
    if fault=='ip':current['egress_ip']=spec['egress_ip']='1.1.1.1'
    with pytest.raises(ValueError):owner_setup.preview_plan(s.store,s.commission,spec,current,s.plan['id'])
    assert s.store.read('SELECT * FROM meta ORDER BY key')==before and s.broker.writes==[]


def test_expired_preview_has_explicit_nonready_status_and_cannot_be_reused(preview_session,monkeypatch):
    from datetime import timedelta
    s=preview_session
    ready=owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    before=s.store.read('SELECT * FROM meta ORDER BY key');s.now+=timedelta(days=1)
    monkeypatch.setattr(s.commission,'preview',Mock(side_effect=AssertionError('expired preview made broker reads')))
    status=owner_setup.plan_status(s.store,s.commission,s.binding,s.plan['id'])
    assert status['status']=='OWNER_PREVIEW_EXPIRED' and status['plan_hash']==ready['plan_hash']
    assert status['plan_id']==ready['plan_id'] and not status['broker_writes']
    assert owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])==status
    assert s.store.read('SELECT * FROM meta ORDER BY key')==before and s.broker.writes==[]


def test_status_detects_changed_binding_without_relabeling_preview(preview_session,monkeypatch):
    s=preview_session
    ready=owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    current=dict(s.binding,account_fingerprint='f'*64)
    monkeypatch.setattr(s.commission,'preview',Mock(side_effect=AssertionError('wrong account preview read')))
    status=owner_setup.plan_status(s.store,s.commission,current,s.plan['id'])
    assert status['status']=='OWNER_PREVIEW_BINDING_CHANGED' and status['plan_hash']==ready['plan_hash']
    assert not status['broker_writes'] and s.broker.writes==[]


def test_submitted_preview_retry_or_status_cannot_repeat_write_even_after_deadline(preview_session,monkeypatch):
    from datetime import timedelta
    s=preview_session
    ready=owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    saved=s.store.meta(owner_setup.PLAN_KEY+s.plan['id'])
    s.commission.submit(saved,ready['plan_hash']);before=s.store.read('SELECT * FROM meta ORDER BY key')
    s.now+=timedelta(days=1)
    monkeypatch.setattr(s.commission,'preview',Mock(side_effect=AssertionError('submitted plan re-previewed')))
    status=owner_setup.plan_status(s.store,s.commission,s.binding,s.plan['id'])
    assert owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])==status
    assert status['plan_hash']==ready['plan_hash'] and status['status']!='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'
    assert len(s.broker.writes)==1 and s.store.read('SELECT * FROM meta ORDER BY key')==before


def test_existing_submitted_record_without_preview_cannot_be_recreated(preview_session):
    s=preview_session
    s.commission.submit(s.plan,s.commission.preview(s.plan)['plan_hash'])
    before=s.store.read('SELECT * FROM meta ORDER BY key')
    with pytest.raises(owner_setup.SetupError,match='PREVIEW_SUBMITTED_PLAN_RECORD_MISSING'):
        owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    assert s.store.read('SELECT * FROM meta ORDER BY key')==before and len(s.broker.writes)==1


def test_concurrent_submission_during_preview_never_overwrites_record(preview_session,monkeypatch):
    from nifty_engine.agent_engine.broker_commissioning import PREFIX
    s=preview_session;real=s.commission.preview
    unknown={'phase':'GTT_CREATE_UNCERTAIN','private_receipt':'fixture'}
    def interrupted(plan):
        result=real(plan)
        s.store.set_meta(PREFIX+s.plan['id'],unknown)
        return result
    monkeypatch.setattr(s.commission,'preview',interrupted)
    with pytest.raises(owner_setup.SetupError,match='PREVIEW_PLAN_CHANGED_DURING_READ'):
        owner_setup.preview_plan(s.store,s.commission,s.spec,s.binding,s.plan['id'])
    assert s.store.meta(PREFIX+s.plan['id'])==unknown
    assert s.store.meta(owner_setup.PLAN_KEY+s.plan['id']) is None and s.broker.writes==[]


@pytest.mark.parametrize('plan_id',['',False,'../other','A'*32,'f'*31,'f'*33])
def test_supplied_preview_id_requires_exact_lowercase_hex(preview_session,plan_id):
    s=preview_session
    with pytest.raises(owner_setup.SetupError,match='EXACT_PREVIEW_PLAN_ID_REQUIRED'):
        owner_setup.assemble_plan(s.spec,s.binding,plan_id)
    assert s.broker.writes==[]


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','::1','2606:4700:4700::1111'])
def test_ip_probe_rejects_private_or_non_ipv4_without_credentials(monkeypatch,address):
    import urllib.request
    response=SimpleNamespace(read=lambda limit:address.encode())
    manager=Mock();manager.__enter__=Mock(return_value=response);manager.__exit__=Mock(return_value=False)
    def open_request(request,**kwargs):
        assert request.full_url=='https://api.ipify.org'
        assert request.get_header('Authorization') is None and request.data is None
        assert kwargs=={'timeout':5}
        return manager
    monkeypatch.setattr(urllib.request,'build_opener',lambda *handlers:SimpleNamespace(open=open_request))
    with pytest.raises(owner_setup.SetupError,match='PUBLIC_ORACLE_IPV4_REQUIRED'):owner_setup.observed_ip()


def test_worker_credentials_use_stdin_and_never_command_or_output(monkeypatch,capsys):
    values=dict(EXECUTION_MODE='paper',GROWW_OBSERVER_API_KEY='PRIVATE_FIXTURE_KEY',
                GROWW_OBSERVER_API_SECRET='PRIVATE_FIXTURE_SECRET',UNRELATED='omit')
    args=SimpleNamespace(id='a'*32,confirm='b'*64)
    def run(command,**kwargs):
        assert values['GROWW_OBSERVER_API_KEY'] not in str(command)
        assert values['GROWW_OBSERVER_API_SECRET'] not in str(command)
        payload=json.loads(kwargs['input'])
        assert payload['environment']=={key:values[key] for key in ('EXECUTION_MODE','GROWW_OBSERVER_API_KEY','GROWW_OBSERVER_API_SECRET')}
        assert kwargs['capture_output'] and kwargs['timeout']==180
        return SimpleNamespace(returncode=0,stdout='{"status":"PRIVATE_READ_OK"}')
    monkeypatch.setattr(owner_setup.subprocess,'run',run)
    assert owner_setup.run_worker('status',args,values)=={'status':'PRIVATE_READ_OK'}
    assert capsys.readouterr()==('','')


@pytest.fixture
def mocked_arm(tmp_path,monkeypatch):
    root=tmp_path/'state';root.mkdir()
    pause=root/'.trader-paused';pause.write_bytes(b'owner pause\n')
    original=tmp_path/'original-paused';original.write_bytes(b'preserve shared services\n')
    environment=tmp_path/'observer.env'
    raw=b'EXECUTION_MODE="paper"\nGROWW_OBSERVER_API_KEY="PRIVATE_KEY"\nGROWW_OBSERVER_API_SECRET="PRIVATE_SECRET"\n'
    environment.write_bytes(raw)
    monkeypatch.setattr(owner_setup,'ROOT',root)
    monkeypatch.setattr(owner_setup,'ENV',environment)
    monkeypatch.setattr(owner_setup,'ORIGINAL_PAUSE',original)
    monkeypatch.setattr(owner_setup,'validate_deployed_release',lambda *args:None)
    calls=[]
    options=dict(replay_fail=False,install_fail=False,changed_digest=False,start_fail=False,mode_fail=False)
    args=SimpleNamespace(id='a'*32,confirm='b'*64)
    values=dict(EXECUTION_MODE='paper',GROWW_OBSERVER_API_KEY='PRIVATE_KEY',GROWW_OBSERVER_API_SECRET='PRIVATE_SECRET')
    def worker(action,supplied_args,supplied_values):
        calls.append(action)
        assert supplied_args is args and supplied_values==values
        if action=='install-receipt':
            assert 'stop' in calls and pause.exists() and environment.read_bytes()==raw
            if options['install_fail']:raise owner_setup.SetupError('TURN_ALGO_OFF_BEFORE_SETUP')
        digest='c'*64 if action=='install-receipt' and options['changed_digest'] else 'b'*64
        return {'evidence_digest':digest,'algo_enabled':False,'broker_writes':False}
    def process(command,**kwargs):
        if command[:2]==['systemctl','stop']:
            calls.append('stop')
        elif command[:2]==['systemctl','start']:
            calls.append('start')
            if options['start_fail']:
                options['start_fail']=False
                raise subprocess.CalledProcessError(1,command,stderr='SECRET_PROVIDER_DIAGNOSTIC')
        else:
            assert '-m' in command and 'pytest' in command
            assert 'GROWW_OBSERVER_API_KEY' not in kwargs['env']
            assert 'GROWW_OBSERVER_API_SECRET' not in kwargs['env']
            calls.append('replay')
            return SimpleNamespace(returncode=int(options['replay_fail']))
        return SimpleNamespace(returncode=0)
    def atomic(path,body):
        calls.append('write-live' if b'EXECUTION_MODE="live"' in body else 'restore-paper')
        if options['mode_fail'] and b'EXECUTION_MODE="live"' in body:
            options['mode_fail']=False
            raise OSError('PRIVATE_FILESYSTEM_MESSAGE')
        path.write_bytes(body)
    monkeypatch.setattr(owner_setup,'run_worker',worker)
    monkeypatch.setattr(owner_setup.subprocess,'run',process)
    monkeypatch.setattr(owner_setup,'atomic_environment',atomic)
    return SimpleNamespace(args=args,values=values,calls=calls,options=options,root=root,pause=pause,
                           original=original,environment=environment,raw=raw)


def test_owner_arm_prepares_live_and_retains_off_without_any_broker_write(mocked_arm,capsys):
    s=mocked_arm
    result=owner_setup.arm(s.args,s.values)
    assert result['status']=='LIVE_PREPARED_ALGO_OFF' and result['algo_enabled'] is False and result['broker_writes'] is False
    assert s.calls==['arm-preview','replay','stop','install-receipt','write-live','start']
    assert b'EXECUTION_MODE="live"' in s.environment.read_bytes() and not s.pause.exists()
    assert s.original.read_bytes()==b'preserve shared services\n'
    assert s.values['EXECUTION_MODE']=='paper'  # Caller input is not mutated.
    assert capsys.readouterr()==('','')


@pytest.mark.parametrize('fault',['install_fail','changed_digest','mode_fail','start_fail'])
def test_owner_arm_failure_restores_paper_pause_and_original(mocked_arm,fault,capsys):
    s=mocked_arm;s.options[fault]=True
    with pytest.raises((ValueError,OSError,subprocess.CalledProcessError)):owner_setup.arm(s.args,s.values)
    assert s.environment.read_bytes()==s.raw
    assert s.pause.read_bytes()==b'owner pause\n'
    assert s.original.read_bytes()==b'preserve shared services\n'
    assert s.calls[-2:]==['restore-paper','start']
    assert capsys.readouterr()==('','')


@pytest.mark.parametrize('fault',['confirm','replay','mode','pause'])
def test_owner_arm_preflight_does_not_stop_or_change_runtime(mocked_arm,fault):
    s=mocked_arm
    if fault=='confirm':s.args.confirm='d'*64
    if fault=='replay':s.options['replay_fail']=True
    if fault=='mode':s.values['EXECUTION_MODE']='live'
    if fault=='pause':s.pause.unlink()
    with pytest.raises(owner_setup.SetupError):owner_setup.arm(s.args,s.values)
    assert 'stop' not in s.calls and 'install-receipt' not in s.calls and 'write-live' not in s.calls
    assert s.environment.read_bytes()==s.raw
    assert s.original.read_bytes()==b'preserve shared services\n'


@pytest.mark.parametrize('fault',['plan_id','confirm','preview_id'])
def test_owner_root_command_rejects_unconfirmed_plan_before_worker(tmp_path,monkeypatch,fault):
    import sys
    lock_calls=[]
    monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(LOCK_EX=1,LOCK_NB=2,
        flock=lambda *args:lock_calls.append(args)))
    real_open=owner_setup.os.open
    real_stat=owner_setup.os.fstat
    def root_stat(fd):
        fields=list(real_stat(fd));fields[4]=0
        return owner_setup.os.stat_result(fields)
    monkeypatch.setattr(owner_setup.os,'fstat',root_stat)
    monkeypatch.setattr(owner_setup.os,'O_NOFOLLOW',getattr(owner_setup.os,'O_NOFOLLOW',0),raising=False)
    monkeypatch.setattr(owner_setup.os,'open',lambda path,flags,mode:real_open(tmp_path/'setup.lock',flags,mode))
    monkeypatch.setattr(owner_setup,'environment',lambda path:{'EXECUTION_MODE':'paper'})
    worker=Mock(side_effect=AssertionError('unconfirmed command reached worker'))
    monkeypatch.setattr(owner_setup,'run_worker',worker)
    args=SimpleNamespace(action='submit',id='a'*32,confirm='b'*64,spec=None)
    if fault=='plan_id':args.id='../foreign-plan'
    if fault=='confirm':args.confirm='yes'
    if fault=='preview_id':args.action='preview-stdin';args.id='invalid'
    with pytest.raises(owner_setup.SetupError,match='EXACT_PREVIEW_PLAN_ID_REQUIRED|OWNER_EXACT_CONFIRMATION_REQUIRED'):
        owner_setup.root_action(args)
    assert lock_calls and worker.call_count==0
