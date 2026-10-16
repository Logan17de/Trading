"""No broker writes: explicit owner real Algo On preparation contract."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json

from nifty_engine.agent_engine import premium_strategy
from nifty_engine.agent_engine.dashboard import DashboardState
from nifty_engine.agent_engine.execution_gate import ExecutionGate
from nifty_engine.agent_engine.live_setup import owner_authorization, owner_receipt, public
from nifty_engine.agent_engine.pc_control import JST, PcJournal
from nifty_engine.agent_engine.strategy_groups import set_switch
from nifty_engine.agent_engine.oracle_link import RemoteViewer


NOW=datetime(2026,10,14,15,15,tzinfo=JST)


def test_owner_live_record_is_strictly_bound_and_never_claims_provider_commissioning(tmp_path):
    store=PcJournal(tmp_path/'live.sqlite3').store
    binding=dict(release='a'*40,policy_hash='b'*64,account_fingerprint='c'*64)
    record=owner_receipt(binding,NOW)
    assert record['provider_verified'] is False and record['commissioned'] is False
    assert owner_authorization(store,*binding.values()) is None
    store.set_meta('premium-owner-live-authorization',record)
    assert owner_authorization(store,binding['release'],binding['policy_hash'],binding['account_fingerprint'])==record
    for key in binding:
        assert owner_authorization(store,**dict(binding,**{key:'d'*len(binding[key])})) is None
    store.set_meta('premium-owner-live-authorization',dict(record,owner_approved=False))
    assert owner_authorization(store,*binding.values()) is None
    store.set_meta('premium-owner-live-authorization',dict(record,provider_verified=True))
    assert owner_authorization(store,*binding.values()) is None


def test_live_broker_gate_accepts_only_matching_explicit_owner_receipt(tmp_path):
    journal=PcJournal(tmp_path/'live.sqlite3')
    binding=dict(release='a'*40,policy_hash='b'*64,account_fingerprint='c'*64)
    premium_strategy.set_intent(journal.store,True,NOW)
    set_switch(journal.store,dict(id='normal_theta',enabled=True,revision=0),NOW)
    gate=ExecutionGate(journal,tmp_path/'.trader-paused',mode='live',
        release=binding['release'],host='ORACLE',clock=lambda:NOW,
        policy_hash=binding['policy_hash'],normal_catalog=True,
        commissioning_required=True,strategy_switches_required=True)
    gate.account_fingerprint=binding['account_fingerprint']
    obs=dict(complete=True,received_at=NOW.isoformat())
    assert 'REVIEWED_LIVE_ACTIVATION_REQUIRED' in gate.blockers(obs,strategy='normal_theta')
    journal.store.set_meta('premium-owner-live-authorization',owner_receipt(binding,NOW))
    assert 'REVIEWED_LIVE_ACTIVATION_REQUIRED' not in gate.blockers(obs,strategy='normal_theta')
    assert 'OWNER_ALGO_OFF' not in gate.blockers(obs,strategy='normal_theta')
    assert not gate.blockers(obs,strategy='normal_theta')
    result=public(journal.store,gate)
    assert result['owner_risk_authorized'] and result['first_live_basket_uncommissioned']
    assert result['provider_verified'] is False
    (tmp_path/'.trader-paused').touch()
    assert 'REPOSITORY_PAUSED' in gate.blockers(obs,strategy='normal_theta')
    gate.mode='paper'
    assert 'LIVE_ENVIRONMENT_NOT_ACTIVATED' in gate.blockers(obs,strategy='normal_theta')
    gate.mode='live'
    gate.account_fingerprint='d'*64
    assert 'REVIEWED_LIVE_ACTIVATION_REQUIRED' in gate.blockers(obs,strategy='normal_theta')


def test_desktop_owner_algo_on_does_not_set_intent_when_live_review_rejected():
    state=object.__new__(DashboardState)
    calls=[]
    state.owner_setup=SimpleNamespace(public=lambda:{'requires_reconciliation':False,'operation':None})
    state.remote=SimpleNamespace(
        read=lambda:{'live_setup':{'mode':'paper','paused':True}},
        prepare_owner_live=lambda:(calls.append('review') or {'status':'OWNER_LIVE_SETUP_BLOCKED','reason':'ACTIVE_FNO_SMART_ORDER_PRESENT'}),
        set_intent=lambda enabled:(calls.append('intent') or {'status':'ON_READY'}))
    result=state.algo_set(True)
    assert result['status']=='ON_BLOCKED'
    assert result['desired_enabled'] is False
    assert calls==['review']


def test_desktop_user_algo_on_prepares_real_service_before_saving_on():
    state=object.__new__(DashboardState)
    events=[]
    state.owner_setup=SimpleNamespace(public=lambda:{'requires_reconciliation':False,'operation':None})
    state.remote=SimpleNamespace(
        read=lambda:{'live_setup':{'mode':'paper','paused':True}},
        prepare_owner_live=lambda:(events.append('prepared') or {'status':'LIVE_PREPARED_ALGO_OFF'}),
        set_intent=lambda enabled:(events.append('on' if enabled else 'off') or {'desired_enabled':enabled}))
    assert state.algo_set(True)['desired_enabled']
    assert events==['prepared','on']
    assert state.algo_set(False)['desired_enabled'] is False
    assert events[-1]=='off'


def test_remote_preview_and_confirm_are_exactly_one_owner_click(monkeypatch):
    import nifty_engine.agent_engine.oracle_link as link
    remote=object.__new__(RemoteViewer)
    digest='f'*64
    remote.config=dict(format='trading-oracle-viewer-v1',
        host='example.test',user='ubuntu',identity_file=str(Path('C:/fake/id').absolute()),
        python='/opt/growing-trader/releases/'+'a'*40+'/venv/bin/python',
        root='/var/lib/trading-observer')
    commands=[]
    def subprocess_run(command,**kwargs):
        remote_command=command[-1]
        commands.append(remote_command)
        if remote_command.endswith('live-preview'):
            payload=dict(status='LIVE_STRATEGY_REVIEW_READY',evidence_digest=digest)
        else:
            assert remote_command.endswith('live-enable --confirm '+digest)
            payload=dict(status='LIVE_PREPARED_ALGO_OFF')
        return SimpleNamespace(returncode=0,stdout=json.dumps(payload))
    monkeypatch.setattr(link.subprocess,'run',subprocess_run)
    monkeypatch.setattr(link,'request',lambda config,command:dict(
        live_setup={'mode':'live','paused':False,'owner_risk_authorized':True},
        control={'algo':{'desired_enabled':False}}))
    assert remote.prepare_owner_live()['status']=='LIVE_PREPARED_ALGO_OFF'
    assert len(commands)==2
    assert all('sudo -n ' in cmd and 'owner_setup' in cmd for cmd in commands)

def test_live_preview_can_coexist_with_identified_manual_positions():
    from nifty_engine.agent_engine.owner_setup import validate_live_positions, SetupError
    import pytest
    manual=[dict(trading_symbol='NIFTY26O1322950CE',product='NRML',quantity=65),
            dict(trading_symbol='SENSEX26O1576000CE',product='NRML',quantity=0)]
    assert validate_live_positions(manual)==1
    for rows in (None, [dict(trading_symbol='NIFTY26O1322950CE',product='NRML',quantity='65')],
                 [dict(trading_symbol='NIFTY26O1322950CE',product='NRML',quantity=65.0,)],
                 [dict(trading_symbol='INVALID',product='NRML',quantity=65)]):
        if isinstance(rows,list) and rows and type(rows[0].get('quantity')) is float:
            assert validate_live_positions(rows)==1
        else:
            with pytest.raises(SetupError):validate_live_positions(rows)
