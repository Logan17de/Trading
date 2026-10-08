"""Synthetic owner job supervision; no real subprocess, credentials or broker."""
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess

import pytest

from nifty_engine.agent_engine import owner_setup as setup, owner_test_job as job
from nifty_engine.agent_engine import commissioning_direct as direct
from nifty_engine.agent_engine.contracts import identity
from nifty_engine.agent_engine.store import Store
from test_commissioning_direct import session


def test_authorization_is_exact_once_and_performs_no_broker_write(tmp_path):
    s=session(tmp_path);s.store.set_meta(direct.AUTH,{})
    with pytest.raises(ValueError,match='EXACT_OWNER_PLAN_HASH_REQUIRED'):
        setup.authorize_test(s.store,s.commission,s.binding,s.plan,'0'*64)
    assert s.store.meta(direct.AUTH)=={}
    result=setup.authorize_test(s.store,s.commission,s.binding,s.plan,identity(s.plan))
    assert result['status']=='OWNER_TEST_RUNNING' and s.broker.writes==[]
    with pytest.raises(ValueError,match='ALREADY_AUTHORIZED'):
        setup.authorize_test(s.store,s.commission,s.binding,s.plan,identity(s.plan))
    other=dict(s.plan,id='f'*32)
    with pytest.raises(ValueError,match='ALREADY_RUNNING'):
        setup.authorize_test(s.store,s.commission,s.binding,other,identity(other))
    assert s.broker.writes==[] and s.pause.exists()


def test_unused_preview_can_be_archived_but_authorized_test_cannot(tmp_path):
    s=session(tmp_path);s.plan['id']='a'*32
    s.store.set_meta(direct.AUTH,dict(plan_id=s.plan['id'],state='RUNNING'))
    s.store.set_meta(setup.PLAN_KEY+s.plan['id'],s.plan)
    with pytest.raises(ValueError,match='AUTHORIZED_TEST_REQUIRES_REVIEW'):
        setup.retire_plan(s.store,s.commission,s.binding,s.plan['id'])
    s.store.set_meta(direct.AUTH,{})
    result=setup.retire_plan(s.store,s.commission,s.binding,s.plan['id'])
    assert result['status']=='PRIOR_TEST_RETIRED' and result['preview_only']
    assert s.store.meta(setup.PLAN_KEY+s.plan['id'])==s.plan and s.broker.writes==[]
    missing=setup.retire_plan(s.store,s.commission,s.binding,'b'*32)
    assert missing['status']=='PRIOR_TEST_RETIRED' and missing['preview_only']


@pytest.mark.parametrize('failure',[False,True,'timeout'])
def test_job_launch_is_fixed_and_failed_or_unknown_start_revokes_continuation(tmp_path,monkeypatch,failure):
    monkeypatch.setattr(setup,'ROOT',tmp_path)
    store=Store(tmp_path/'.agent-state/pc-monitor.sqlite3');plan_id='a'*32
    store.set_meta(direct.AUTH,dict(plan_id=plan_id,state='RUNNING'))
    worker=Mock(return_value={'status':'OWNER_TEST_RUNNING'})
    monkeypatch.setattr(setup,'run_worker',worker)
    calls=[]
    def run(args,**kw):
        calls.append((args,kw))
        if failure=='timeout':raise subprocess.TimeoutExpired('private',10)
        return SimpleNamespace(returncode=int(failure))
    monkeypatch.setattr(setup.subprocess,'run',run)
    args=SimpleNamespace(id=plan_id,confirm='b'*64)
    if failure:
        with pytest.raises(setup.SetupError,match='OWNER_TEST_JOB_START_FAILED'):setup.start_test_job(args,{})
        assert store.meta(direct.AUTH)['state']=='ERROR'
    else:
        assert setup.start_test_job(args,{})=={'status':'OWNER_TEST_RUNNING'}
        assert store.meta(direct.AUTH)['state']=='RUNNING'
    worker.assert_called_once_with('authorize-test',args,{})
    command,kw=calls[0]
    assert '--unit=trading-owner-test-'+plan_id in command
    assert '--property=RuntimeMaxSec=43200' in command
    assert command[-3:]==['nifty_engine.agent_engine.owner_test_job','--id',plan_id]
    assert kw['capture_output'] and kw['timeout']==10


@pytest.mark.parametrize('result,state',[
    ({'status':'BUY_PENDING_OR_PARTIAL'},'RUNNING'),
    ({'status':'COMPLETE'},'FINISHED'),
    ({'status':'CLOSED_UNVERIFIED'},'FINISHED'),
    ({'status':'RECONCILIATION_REQUIRED','reason':'COMMISSIONING_PARTIAL_BUY_REQUIRES_REVIEW'},'ERROR')])
def test_job_checkpoint_persists_and_rejects_unrelated_plan(tmp_path,monkeypatch,result,state):
    monkeypatch.setattr(setup,'ROOT',tmp_path)
    path=tmp_path/'.agent-state/pc-monitor.sqlite3';store=Store(path)
    original=dict(plan_id='a'*32,plan_hash='b'*64,state='RUNNING',submit_attempted='original')
    store.set_meta(direct.AUTH,original);store.set_meta('owner-intent',False)
    assert job.record_status('a'*32,result)==state
    saved=Store(path).meta(direct.AUTH)
    assert saved['state']==state and saved['submit_attempted']=='original' and saved['checked_at']
    assert store.meta('owner-intent') is False
    with pytest.raises(setup.SetupError,match='EXACT_OWNER_TEST_AUTHORIZATION_REQUIRED'):
        job.record_status('c'*32,result)
    assert store.meta(direct.AUTH)==saved
