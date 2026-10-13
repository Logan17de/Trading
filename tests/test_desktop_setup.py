import copy
import json
import subprocess
import threading
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine.desktop_setup import OwnerSetupController,KEY,PREVIEW
from nifty_engine.agent_engine.store import Store

NOW=datetime(2026,10,7,5,tzinfo=timezone.utc)
HASH='b'*64
DIGEST='c'*64


def spec():
    return dict(contract=dict(symbol='NIFTY26O2025000CE',index='NIFTY',expiry='2026-10-20',lot_size=65,tick_size=0.05),
        quantity=65,trigger_price='10.00',buy_limit_price='10.05',sell_limit_price='9.95',stop_trigger_price='7.00',stop_limit_price='6.95',
        valid_until=(NOW+timedelta(hours=1)).isoformat(),egress_ip='8.8.8.8')


def response(status,**extra):
    return SimpleNamespace(returncode=0,stdout=json.dumps(dict(status=status,**extra)),stderr='PRIVATE STDERR')


@pytest.fixture
def app(tmp_path):
    state=tmp_path/'.agent-state';state.mkdir()
    cfg=dict(format='trading-oracle-viewer-v1',host='example.com',user='ubuntu',identity_file=str(tmp_path/'key'),
        python='/opt/growing-trader/releases/'+'a'*40+'/venv/bin/python',root='/var/lib/trading-observer')
    (state/'oracle-viewer.json').write_text(json.dumps(cfg))
    store=Store(state/'private.sqlite3')
    calls=[]
    def run(args,**kw):
        calls.append((args,kw))
        plan_id=args[-1].split(' --id ')[1].split()[0]
        return response(PREVIEW,plan_id=plan_id,plan_hash=HASH,broker_writes=False,
            api_key='PRIVATE',account_fingerprint='PRIVATE',groww_order_id='PRIVATE')
    ctl=OwnerSetupController(tmp_path,store,run=run,clock=lambda:NOW)
    return ctl,store,calls,cfg


def preview(app):
    ctl,store,calls,cfg=app
    result=ctl.command({'action':'preview','spec':spec()})
    assert result['status']==PREVIEW
    return result


@pytest.mark.parametrize('action', ['protect','cancel-stop'])
def test_protection_action_requires_exact_owner_confirm_and_never_poll_retries(app, action):
    ctl,store,calls,cfg=app; preview(app)
    saved=store.meta(KEY)
    saved['result']={'status':'CHILD_FILLED','broker_actions':[action]}
    store.set_meta(KEY,saved)
    count=len(calls)
    assert ctl.command({'action':action,'confirm':'f'*64})['status']=='OWNER_EXACT_CONFIRMATION_REQUIRED'
    assert len(calls)==count
    ctl.run=lambda *a,**k: response('STOP_ACTIVE_OBSERVED',command=action,broker_write_attempted=True,
        broker_write_outcome='ACCEPTED',allowed_actions=['cancel-stop'],facts={'stop_order_write':True})
    value=ctl.command({'action':action,'confirm':HASH})
    assert value['facts']['stop_order_write']
    assert action not in value['allowed_actions']
    assert ctl.public()['status_source']=='SAVED_LOCAL_STATE'


def test_new_release_can_archive_only_a_reconciled_cancelled_test(app):
    ctl,store,calls,cfg=app; preview(app); old=store.meta(KEY)
    old.update(uncertain=True,pending_action='submit');store.set_meta(KEY,old)
    cfg['python']='/opt/growing-trader/releases/'+'e'*40+'/venv/bin/python'
    (ctl.root/'.agent-state/oracle-viewer.json').write_text(json.dumps(cfg))
    assert ctl.command({'action':'status'})['status']=='OWNER_SETUP_BINDING_CHANGED'
    ctl.run=lambda *a,**k: response('ACTIVE_PARENT_OBSERVED',broker_writes=False)
    assert ctl.command({'action':'retire'})['status']=='ACTIVE_PARENT_OBSERVED'
    assert store.meta(KEY)==old
    ctl.run=lambda *a,**k: response('PRIOR_TEST_RETIRED',plan_hash=HASH,broker_writes=False)
    value=ctl.command({'action':'retire'})
    assert value['allowed_actions']==['status','preview'] and store.meta(KEY)=={}
    assert store.meta(KEY+'-archive-'+old['plan_id'])['pending_action']=='submit'


def test_fixed_preview_stdin_returns_only_owner_spec_and_hash(app):
    ctl,store,calls,cfg=app
    result=preview(app)
    args,kw=calls[0]
    saved=store.meta(KEY)
    assert args[-1]=='sudo -n '+cfg['python']+' -I -m nifty_engine.agent_engine.owner_setup preview-stdin --id '+saved['plan_id']
    assert kw['input']==json.dumps(spec(),ensure_ascii=True,sort_keys=True,separators=(',',':'))
    assert 'PRIVATE' not in json.dumps(result) and saved['plan_id'] not in json.dumps(result)
    assert result['plan_hash']==HASH and result['plan_spec']==spec()
    assert {'StrictHostKeyChecking=yes','ClearAllForwardings=yes','IdentityAgent=none','BatchMode=yes'}<=set(args)
    assert {'submit','status','capture'}<=set(result['allowed_actions'])


def test_direct_buy_is_owner_click_only_and_not_repeated_after_restart(app):
    ctl,store,calls,cfg=app
    value=dict(spec(),entry_mode='LIMIT',trigger_price='10.05',sell_limit_price='12.00')
    ready=ctl.command({'action':'preview','spec':value})
    assert 'buy-protect' in ready['allowed_actions'] and 'submit' not in ready['allowed_actions']
    ctl.run=lambda *a,**k: response('OWNER_TEST_RUNNING',plan_hash=HASH,broker_writes=False)
    started=ctl.command({'action':'buy-protect','confirm':HASH})
    assert 'buy-protect' not in started['allowed_actions']
    again=OwnerSetupController(ctl.root,store,run=lambda *a,**k:pytest.fail('repeated start'),clock=lambda:NOW)
    assert 'buy-protect' not in again.public()['allowed_actions']
    assert 'buy-protect' not in again.command({'action':'buy-protect','confirm':HASH})['allowed_actions']


def test_contract_lookup_is_read_only_and_filters_private_fields(app):
    ctl,store,calls,cfg=app;preview(app);before=store.meta(KEY)
    def lookup(args,**kw):
        assert ' lookup-stdin --id ' in args[-1]
        assert json.loads(kw['input'])=={'index':'NIFTY','symbol':spec()['contract']['symbol']}
        return response('OWNER_CONTRACT_FOUND',contract=spec()['contract'],egress_ip='8.8.8.8',
            checked_at=NOW.isoformat(),quote={'last_price':10,'bid_price':9.95,'offer_price':10,
                'timestamp_status':'RECENT_TRADE_ONLY','api_key':'PRIVATE'},account_id='PRIVATE',broker_writes=False)
    ctl.run=lookup
    result=ctl.command({'action':'lookup','spec':{'index':'NIFTY','symbol':spec()['contract']['symbol']}})
    assert result['contract']==spec()['contract'] and result['quote']['offer_price']==10
    assert store.meta(KEY)==before and 'PRIVATE' not in str(result)


def test_archived_draft_survives_page_restart_without_broker_request(app):
    ctl,store,calls,cfg=app
    store.set_meta(KEY+'-archive-'+'a'*32,{'spec':spec()})
    result=ctl.public()
    assert result['draft_spec']==spec() and result['plan_spec'] is None and calls==[]


def test_explicit_review_can_replace_unused_old_release_preview_without_extra_owner_action(app):
    ctl,store,calls,cfg=app;preview(app);old=store.meta(KEY)
    cfg['python']='/opt/growing-trader/releases/'+'e'*40+'/venv/bin/python'
    (ctl.root/'.agent-state/oracle-viewer.json').write_text(json.dumps(cfg))
    seen=[]
    def run(args,**kw):
        seen.append(args[-1])
        if ' retire ' in args[-1]:return response('PRIOR_TEST_RETIRED',plan_hash=HASH,broker_writes=False)
        return response(PREVIEW,plan_hash=HASH,plan_id=args[-1].split(' --id ')[1],broker_writes=False)
    ctl.run=run
    result=ctl.command({'action':'preview','spec':spec()})
    assert result['status']==PREVIEW and len(seen)==2
    assert ' retire ' in seen[0] and ' preview-stdin ' in seen[1]
    assert store.meta(KEY+'-archive-'+old['plan_id'])['attempted_writes']==[]
    assert store.meta(KEY)['plan_id']!=old['plan_id']


@pytest.mark.parametrize('field,value',[('host',';danger'),('python','python;danger'),('user','root'),('root','/tmp/other')])
def test_tampered_transport_never_invokes_process(app,field,value):
    ctl,store,calls,cfg=app
    cfg[field]=value
    (ctl.root/'.agent-state/oracle-viewer.json').write_text(json.dumps(cfg))
    assert ctl.command({'action':'preview','spec':spec()})['status']=='INVALID_OWNER_SETUP_COMMAND'
    assert calls==[]


@pytest.mark.parametrize('mutate',[
    lambda p:p.update(api_key='PRIVATE'),lambda p:p.update(quantity=130),
    lambda p:p['contract'].update(symbol='NIFTY;rmCE'),lambda p:p.update(buy_limit_price='0'),
    lambda p:p.update(trigger_price='NaN'),lambda p:p.update(egress_ip='127.0.0.1'),
    lambda p:p.update(valid_until='2026-10-06T00:00:00Z')])
def test_invalid_or_extra_private_spec_never_invokes_process(app,mutate):
    ctl,store,calls,cfg=app
    value=spec();mutate(value)
    result=ctl.command({'action':'preview','spec':value})
    assert result['status']=='INVALID_OWNER_SETUP_COMMAND' and calls==[]
    assert store.meta(KEY) is None and 'PRIVATE' not in json.dumps(result)


def test_write_needs_exact_confirm_and_persists_before_process_without_sql_lock(app):
    ctl,store,calls,cfg=app
    preview(app)
    assert ctl.command({'action':'submit','confirm':'d'*64})['status']=='OWNER_EXACT_CONFIRMATION_REQUIRED'
    assert len(calls)==1
    def submit(args,**kw):
        saved=store.meta(KEY)
        assert saved['pending_action']=='submit' and saved['attempted_writes']==['submit']
        store.set_meta('concurrent-write',True)  # No transaction is held over SSH.
        assert args[-1].endswith(' --confirm '+HASH) and kw['input']==''
        return response('ACTIVE_PARENT_OBSERVED',plan_hash=HASH,command='submit',allowed_actions=['cancel'],
            broker_write_attempted=True,broker_writes=True,broker_write_outcome='ACCEPTED')
    ctl.run=submit
    result=ctl.command({'action':'submit','confirm':HASH})
    assert result['broker_write_outcome']=='ACCEPTED' and result['broker_writes'] is True
    ctl.run=lambda *a,**k:pytest.fail('duplicate submit invoked')
    assert 'submit' not in ctl.command({'action':'submit','confirm':HASH})['allowed_actions']


def test_preview_timeout_recovers_original_id_after_restart_without_new_preview(app):
    ctl,store,calls,cfg=app
    def lost(*a,**kw):raise subprocess.TimeoutExpired('SECRET COMMAND',1,output='PRIVATE',stderr='PRIVATE')
    ctl.run=lost
    first=ctl.command({'action':'preview','spec':spec()})
    saved=store.meta(KEY);plan_id=saved['plan_id']
    assert first['requires_reconciliation'] and first['pending_action']=='preview'
    recovered=[]
    def status(args,**kw):
        recovered.append(args[-1]);assert args[-1].endswith('status --id '+plan_id)
        return response(PREVIEW,plan_hash=HASH,plan_id=plan_id,broker_writes=False)
    restarted=OwnerSetupController(ctl.root,store,run=status,clock=lambda:NOW)
    result=restarted.command({'action':'preview','spec':spec()})
    assert len(recovered)==1 and store.meta(KEY)['plan_id']==plan_id
    assert result['plan_hash']==HASH and not result['requires_reconciliation']
    assert 'PRIVATE' not in json.dumps(first) and 'SECRET' not in json.dumps(first)


def test_unknown_submit_never_retries_even_after_restart_and_failed_status(app):
    ctl,store,calls,cfg=app;preview(app)
    plan_id=store.meta(KEY)['plan_id']
    def lost(*a,**kw):raise TimeoutError('PRIVATE')
    ctl.run=lost
    first=ctl.command({'action':'submit','confirm':HASH})
    assert first['broker_writes'] is None and first['pending_action']=='submit'
    again=OwnerSetupController(ctl.root,store,run=lambda *a,**k:pytest.fail('repeated write'),clock=lambda:NOW)
    result=again.command({'action':'submit','confirm':HASH})
    assert result['requires_reconciliation']
    again.run=lost
    result=again.command({'action':'status'})
    assert result['broker_writes'] is None and result['pending_action']=='submit'
    assert store.meta(KEY)['plan_id']==plan_id
    again.run=lambda *a,**k:response('ACTIVE_PARENT_OBSERVED',plan_hash=HASH,broker_writes=False,allowed_actions=['cancel'])
    result=again.command({'action':'capture'})
    assert not result['requires_reconciliation'] and 'cancel' in result['allowed_actions']
    assert 'submit' not in result['allowed_actions']


def test_new_preview_reads_current_plan_and_refuses_unsettled_parent(app):
    ctl,store,calls,cfg=app;preview(app)
    plan_id=store.meta(KEY)['plan_id'];commands=[]
    def status(args,**kw):
        commands.append(args[-1]);return response('ACTIVE_PARENT_OBSERVED',plan_hash=HASH,broker_writes=False)
    ctl.run=status
    changed=spec();changed['sell_limit_price']='9.90'
    result=ctl.command({'action':'preview','spec':changed})
    assert len(commands)==1 and commands[0].endswith('status --id '+plan_id)
    assert result['reason']=='EXISTING_PLAN_REQUIRES_REVIEW' and store.meta(KEY)['spec']==spec()


def test_arm_requires_prior_exact_evidence_digest_and_never_defaults_to_plan_hash(app):
    ctl,store,calls,cfg=app;preview(app)
    ctl.run=lambda *a,**k:response('COMPLETE',plan_hash=HASH,broker_writes=False)
    ctl.command({'action':'status'})
    assert ctl.command({'action':'arm','confirm':HASH})['status']=='OWNER_EXACT_CONFIRMATION_REQUIRED'
    ctl.run=lambda *a,**k:response('VERIFIED_OWNER_EVIDENCE',evidence_digest=DIGEST,broker_writes=False,algo_enabled=False)
    assert ctl.command({'action':'arm-preview'})['evidence_digest']==DIGEST
    def arm(args,**kw):
        assert args[-1].endswith(' --confirm '+DIGEST)
        assert kw['timeout']==1600
        return response('LIVE_PREPARED_ALGO_OFF',broker_writes=False,algo_enabled=False)
    ctl.run=arm
    result=ctl.command({'action':'arm','confirm':DIGEST})
    assert result['status']=='LIVE_PREPARED_ALGO_OFF' and result['algo_enabled'] is False


def test_command_lock_rejects_overlap_without_waiting_or_second_process(app):
    ctl,store,calls,cfg=app
    entered=threading.Event();release=threading.Event()
    original=ctl.run
    def waiting(*a,**kw):
        entered.set();assert release.wait(3);return original(*a,**kw)
    ctl.run=waiting
    thread=threading.Thread(target=lambda:ctl.command({'action':'preview','spec':spec()}));thread.start()
    try:
        assert entered.wait(1)
        other=OwnerSetupController(ctl.root,store,run=lambda *a,**k:pytest.fail('overlapping process'))
        assert other.command({'action':'status'})['status']=='OWNER_SETUP_BUSY'
    finally:release.set();thread.join(3)
    assert len(calls)==1


@pytest.mark.parametrize('payload',[lambda p:'PRIVATE '+json.dumps(p),lambda p:'x'*32769,
    lambda p:json.dumps(dict(p,plan_hash='d'*64)),lambda p:json.dumps(dict(p,status='PRIVATE_VALUE'))])
def test_invalid_or_wrong_bound_remote_response_stays_unknown_without_leaks(app,payload):
    ctl,store,calls,cfg=app;preview(app)
    ctl.run=lambda *a,**k:SimpleNamespace(returncode=0,stdout=payload(dict(status='ACTIVE_PARENT_OBSERVED',plan_hash=HASH)),stderr='PRIVATE')
    result=ctl.command({'action':'submit','confirm':HASH})
    assert result['status']=='OWNER_SETUP_RESULT_UNKNOWN' and result['broker_writes'] is None
    assert 'PRIVATE' not in json.dumps(result)


def test_saved_plan_cannot_move_to_changed_release_or_host(app):
    ctl,store,calls,cfg=app;preview(app)
    cfg['host']='another.example.com'
    (ctl.root/'.agent-state/oracle-viewer.json').write_text(json.dumps(cfg))
    assert ctl.command({'action':'status'})['status']=='OWNER_SETUP_BINDING_CHANGED'
    assert len(calls)==1


def test_process_crash_after_pending_write_cannot_relabel_unknown_as_no_write(app):
    ctl,store,calls,cfg=app;preview(app)
    saved=store.meta(KEY)
    saved.update(pending_action='submit',attempted_writes=['submit'],uncertain=False)
    store.set_meta(KEY,saved)
    def down(*args,**kwargs):raise TimeoutError('PRIVATE')
    recovered=OwnerSetupController(ctl.root,store,run=down,clock=lambda:NOW)
    assert recovered.public()['requires_reconciliation']
    result=recovered.command({'action':'status'})
    assert result['broker_writes'] is None and result['pending_action']=='submit'


def test_rejected_read_only_preview_can_be_replaced_after_confirmed_missing_remote_plan(app):
    ctl,store,calls,cfg=app
    ctl.run=lambda *a,**k:SimpleNamespace(returncode=2,stdout=json.dumps(dict(status='OWNER_SETUP_BLOCKED',
        reason='COMMISSIONING_CURRENT_MASTER_MISMATCH')),stderr='PRIVATE')
    result=ctl.command({'action':'preview','spec':spec()})
    original_id=store.meta(KEY)['plan_id']
    assert result['status']=='OWNER_SETUP_BLOCKED' and not result['requires_reconciliation']
    def followup(args,**kwargs):
        if ' status ' in args[-1]:
            assert args[-1].endswith(original_id)
            return SimpleNamespace(returncode=2,stdout=json.dumps(dict(status='OWNER_SETUP_BLOCKED',
                reason='PREVIEW_PLAN_FIRST')),stderr='PRIVATE')
        identifier=args[-1].split(' --id ')[1]
        assert identifier!=original_id
        return response(PREVIEW,plan_id=identifier,plan_hash=HASH,broker_writes=False)
    ctl.run=followup
    assert ctl.command({'action':'preview','spec':spec()})['status']==PREVIEW


def test_expired_unsubmitted_preview_allows_explicit_replacement_but_binding_change_does_not(app):
    ctl,store,calls,cfg=app;preview(app)
    original_id=store.meta(KEY)['plan_id']
    def followup(args,**kwargs):
        if ' status ' in args[-1]:return response('OWNER_PREVIEW_EXPIRED',plan_hash=HASH)
        identifier=args[-1].split(' --id ')[1]
        return response(PREVIEW,plan_id=identifier,plan_hash=HASH,broker_writes=False)
    ctl.run=followup
    assert ctl.command({'action':'preview','spec':spec()})['status']==PREVIEW
    assert store.meta(KEY)['plan_id']!=original_id
    ctl.run=lambda *a,**k:response('OWNER_PREVIEW_BINDING_CHANGED',plan_hash=HASH)
    result=ctl.command({'action':'preview','spec':spec()})
    assert result['status']=='OWNER_PREVIEW_BINDING_CHANGED' and 'submit' not in result['allowed_actions']


def test_partial_cleanup_requires_current_remote_action_and_explicit_capture_before_next_write(app):
    ctl,store,calls,cfg=app;preview(app)
    ctl.run=lambda *a,**k:response('CHILD_PENDING_OR_PARTIAL',plan_hash=HASH,allowed_actions=['cancel'],broker_writes=False)
    result=ctl.command({'action':'capture'})
    assert 'cancel' in result['allowed_actions'] and 'close' not in result['allowed_actions']
    ctl.run=lambda *a,**k:response('CHILD_TERMINAL_WITH_REMAINDER',plan_hash=HASH,allowed_actions=['close'],
        command='cancel',broker_writes=True,broker_write_attempted=True,broker_write_outcome='ACCEPTED')
    result=ctl.command({'action':'cancel','confirm':HASH})
    assert 'close' in result['allowed_actions']
    ctl.run=lambda *a,**k:response('CLOSE_PENDING_OR_PARTIAL',plan_hash=HASH,allowed_actions=['cancel'],
        command='close',broker_writes=True,broker_write_attempted=True,broker_write_outcome='ACCEPTED')
    result=ctl.command({'action':'close','confirm':HASH})
    assert 'close' not in result['allowed_actions'] and 'cancel' not in result['allowed_actions']
    ctl.run=lambda *a,**k:response('CLOSE_PENDING_OR_PARTIAL',plan_hash=HASH,allowed_actions=['cancel'],broker_writes=False)
    result=ctl.command({'action':'capture'})
    assert 'cancel' in result['allowed_actions']
    ctl.run=lambda *a,**k:response('CHILD_TERMINAL_WITH_REMAINDER',plan_hash=HASH,allowed_actions=['close'],
        command='cancel',broker_writes=True,broker_write_attempted=True,broker_write_outcome='ACCEPTED')
    result=ctl.command({'action':'cancel','confirm':HASH})
    assert 'close' in result['allowed_actions']
    assert store.meta(KEY)['attempted_writes']==['cancel','close','cancel']


def test_unknown_arm_cannot_claim_ready_from_only_complete_test_status(app):
    ctl,store,calls,cfg=app;preview(app)
    ctl.run=lambda *a,**k:response('VERIFIED_OWNER_EVIDENCE',evidence_digest=DIGEST,broker_writes=False)
    ctl.command({'action':'arm-preview'})
    def lost(*a,**k):raise TimeoutError('PRIVATE')
    ctl.run=lost
    assert ctl.command({'action':'arm','confirm':DIGEST})['requires_reconciliation']
    ctl.run=lambda *a,**k:response('COMPLETE',plan_hash=HASH,broker_writes=False)
    result=ctl.command({'action':'status'})
    assert result['requires_reconciliation'] and result['pending_action']=='arm' and 'arm' not in result['allowed_actions']
    ctl.run=lambda *a,**k:response('LIVE_PREPARED_ALGO_OFF',plan_hash=HASH,broker_writes=False,algo_enabled=False)
    result=ctl.command({'action':'status'})
    assert result['status']=='LIVE_PREPARED_ALGO_OFF' and result['algo_enabled'] is False
    assert not result['requires_reconciliation'] and result['pending_action'] is None


@pytest.mark.parametrize('state,extra',[
    ('OWNER_PREVIEW_EXPIRED',{}),('OWNER_SETUP_BLOCKED',{'reason':'PREVIEW_PLAN_FIRST'})])
def test_proven_unsubmitted_failure_exposes_new_preview_control_only_without_attempts(app,state,extra):
    ctl,store,calls,cfg=app;preview(app)
    ctl.run=lambda *a,**k:response(state,plan_hash=HASH,**extra)
    result=ctl.command({'action':'status'})
    assert 'preview' in result['allowed_actions']
    assert 'preview' in ctl.public()['allowed_actions']
    saved=store.meta(KEY);saved['attempted_writes']=['submit'];store.set_meta(KEY,saved)
    assert 'preview' not in ctl.public()['allowed_actions']
    saved['attempted_writes']=[];saved.update(pending_action='preview',uncertain=True);store.set_meta(KEY,saved)
    assert 'preview' not in ctl.public()['allowed_actions']


def test_invalid_empty_spec_preserves_correction_controls_without_changing_saved_plan(app):
    ctl,store,calls,cfg=app
    result=ctl.command({'action':'preview','spec':{}})
    assert result['status']=='INVALID_OWNER_SETUP_COMMAND' and 'preview' in result['allowed_actions']
    assert store.meta(KEY) is None and calls==[]
    preview(app);before=store.meta(KEY)
    result=ctl.command({'action':'preview','spec':{}})
    assert result['plan_hash']==HASH and result['plan_spec']==spec() and 'preview' in result['allowed_actions']
    assert store.meta(KEY)==before
    saved=copy.deepcopy(before);saved.update(pending_action='submit',uncertain=True,attempted_writes=['submit'])
    store.set_meta(KEY,saved)
    result=ctl.command({'action':'preview','spec':{}})
    assert result['requires_reconciliation'] and result['pending_action']=='submit'
    assert result['allowed_actions']==['status','capture','retire'] and store.meta(KEY)==saved


def test_missing_private_connection_has_precise_reason_and_preserves_pending_plan(app):
    ctl,store,calls,cfg=app;preview(app)
    before=store.meta(KEY)
    (ctl.root/'.agent-state/oracle-viewer.json').unlink()
    result=ctl.command({'action':'status'})
    assert result['status']=='ORACLE_CONNECTION_REQUIRED'
    assert result['reason']=='PRIVATE_ORACLE_CONNECTION_CONFIG_REQUIRED'
    assert result['plan_hash']==HASH and store.meta(KEY)==before and len(calls)==1


def test_saved_status_is_local_and_does_not_wait_for_remote_action(app):
    ctl,store,calls,cfg=app;preview(app)
    entered=threading.Event();release=threading.Event()
    def waiting(*a,**kw):
        entered.set();assert release.wait(3)
        return response('ACTIVE_PARENT_OBSERVED',plan_hash=HASH,broker_writes=False)
    ctl.run=waiting
    thread=threading.Thread(target=lambda:ctl.command({'action':'status'}));thread.start()
    try:
        assert entered.wait(1)
        saved=ctl.public()
        assert saved['status_source']=='SAVED_LOCAL_STATE'
        assert saved['operation']['action']=='status' and saved['operation']['elapsed_seconds']>=0
        assert len(calls)==1 and saved['requires_reconciliation']
    finally:release.set();thread.join(3)
    assert ctl.public()['operation'] is None


@pytest.mark.parametrize('changes,reason',[
    ({'buy_limit_price':'9.00'},'COMMISSIONING_BUY_LIMIT_BELOW_TRIGGER'),
    ({'buy_limit_price':'10.03'},'COMMISSIONING_PRICE_NOT_ON_TICK'),
    ({'valid_until':NOW.isoformat()},'COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG')])
def test_form_errors_are_precise_and_never_call_oracle(app,changes,reason):
    ctl,store,calls,cfg=app
    value=spec();value.update(changes)
    result=ctl.command({'action':'preview','spec':value})
    assert result['reason']==reason and result['status']=='INVALID_OWNER_SETUP_COMMAND'
    assert calls==[] and store.meta(KEY) is None


def test_one_lot_above_old_purchase_cap_reaches_oracle_preview_unchanged(app):
    ctl,store,calls,cfg=app
    value=spec();value.update(trigger_price='25.00',buy_limit_price='25.50')
    result=ctl.command({'action':'preview','spec':value})
    assert result['status']==PREVIEW
    sent=json.loads(calls[0][1]['input'])
    assert sent['quantity']==65 and sent['buy_limit_price']=='25.50'
    assert len(calls)==1 and ' preview-stdin ' in calls[0][0][-1]


@pytest.mark.parametrize('blocked_reason', [
    'COMMISSIONING_TRIGGER_MUST_START_ABOVE_MARKET',
    'COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG',
    'TURN_ALGO_OFF_BEFORE_SETUP',
])
def test_rejected_price_preview_is_editable_but_still_checks_original_plan(app, blocked_reason):
    ctl,store,calls,cfg=app
    def blocked(*a,**kw):
        return response('OWNER_SETUP_BLOCKED',reason=blocked_reason)
    ctl.run=blocked
    result=ctl.command({'action':'preview','spec':spec()})
    assert 'preview' in result['allowed_actions'] and 'submit' not in result['allowed_actions']
    old=store.meta(KEY);commands=[]
    def retry(args,**kw):
        commands.append(args[-1])
        if ' status ' in args[-1]:
            return response('OWNER_SETUP_BLOCKED',reason='PREVIEW_PLAN_FIRST')
        return response(PREVIEW,plan_id=args[-1].split(' --id ')[1],plan_hash=HASH)
    ctl.run=retry
    changed=spec();changed['trigger_price']='10.05'
    assert ctl.command({'action':'preview','spec':changed})['status']==PREVIEW
    assert commands[0].endswith('status --id '+old['plan_id'])
    assert len(commands)==2 and store.meta(KEY)['plan_id']!=old['plan_id']
    saved=store.meta(KEY);saved['result']={'status':'OWNER_SETUP_BLOCKED','reason':'COMMISSIONING_TRIGGER_MUST_START_ABOVE_MARKET'}
    saved['attempted_writes']=['submit'];store.set_meta(KEY,saved)
    assert 'preview' not in ctl.public()['allowed_actions']
