import importlib.util
from pathlib import Path
import pytest

SPEC=importlib.util.spec_from_file_location('observer_provision',Path(__file__).resolve().parents[1]/'scripts/provision_observer.py')
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)
VALUES={'SUPABASE_URL':'https://imirspxhbnerxknyynqx.supabase.co','SUPABASE_SERVICE_ROLE_KEY':'fixture-original-key'}
MAIL={'RESEND_API_KEY':'fixture-mail','TRADING_REPORT_FROM':'verified@example.test','TRADING_REPORT_TO':'owner@example.test'}
CREDENTIALS={'api_key':'fixture-api-key','api_secret':'fixture-api-secret'}


def test_deploy_preserves_approved_server_credentials_without_other_secrets():
    assert MODULE.server_configuration({**VALUES,'BROKER_CREDENTIAL_ENCRYPTION_KEY':'omit'}, {})==VALUES
    assert MODULE.server_configuration({},VALUES)==VALUES
    assert MODULE.server_configuration(VALUES,VALUES)==VALUES
    assert MODULE.server_configuration({}, {})=={}


def test_deploy_refuses_rotation_partial_or_different_project():
    with pytest.raises(ValueError,match='CHANGE'):MODULE.server_configuration({**VALUES,'SUPABASE_SERVICE_ROLE_KEY':'replacement'},VALUES)
    with pytest.raises(ValueError,match='APPROVED'):MODULE.server_configuration({'SUPABASE_URL':VALUES['SUPABASE_URL']},{})
    with pytest.raises(ValueError,match='APPROVED'):MODULE.server_configuration({**VALUES,'SUPABASE_URL':'https://different.supabase.co'},{})


@pytest.mark.parametrize('mode',['paper','live'])
def test_existing_mode_and_other_runtime_settings_survive_redeployment(mode):
    previous={**VALUES,**MAIL,'EXECUTION_MODE':mode,'RUNTIME_SETTING':'preserved',
              'GROWW_OBSERVER_API_KEY':CREDENTIALS['api_key'],'GROWW_OBSERVER_API_SECRET':CREDENTIALS['api_secret']}
    result=MODULE.observer_configuration(MAIL,VALUES,previous,CREDENTIALS)
    assert result==previous
    assert MODULE.observer_configuration(MAIL,VALUES,result,CREDENTIALS)==previous


def test_new_install_defaults_paper_even_if_source_has_live_setting():
    result=MODULE.observer_configuration(MAIL,{**VALUES,'EXECUTION_MODE':'live'},None,CREDENTIALS)
    assert result['EXECUTION_MODE']=='paper'
    assert all(result[key]==value for key,value in {**MAIL,**VALUES}.items())
    assert result['GROWW_OBSERVER_API_KEY']==CREDENTIALS['api_key']
    assert result['GROWW_OBSERVER_API_SECRET']==CREDENTIALS['api_secret']


@pytest.mark.parametrize('previous',[{}, {'EXECUTION_MODE':''}, {'EXECUTION_MODE':'LIVE'},
                                    {'EXECUTION_MODE':'unknown'}, {'EXECUTION_MODE':None}])
def test_existing_environment_without_valid_mode_is_rejected(previous):
    with pytest.raises(ValueError,match='EXECUTION_MODE'):
        MODULE.observer_configuration(MAIL,VALUES,previous,CREDENTIALS)


@pytest.mark.parametrize('evidence',['config','journal','environment'])
def test_old_runtime_without_marker_preserves_cleared_pause(tmp_path,evidence):
    state=tmp_path/'state';state.mkdir()
    original=tmp_path/'original-paused';original.write_text('preserve me')
    environment=tmp_path/'observer.env'
    path={'config':state/'config','journal':state/'.agent-state/pc-monitor.sqlite3',
          'environment':environment}[evidence]
    path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    result=MODULE.stage_runtime_pause(state,original,environment)
    assert result=={'paused':False,'changed':False,'established':True}
    assert not (state/'.trader-paused').exists() and not (state/'.trader-paused').is_symlink()
    assert original.read_text()=='preserve me'


def test_existing_pause_file_is_not_replaced(tmp_path):
    state=tmp_path/'state';state.mkdir()
    pause=state/'.trader-paused';pause.write_text('owner paused')
    result=MODULE.stage_runtime_pause(state,tmp_path/'absent-original',tmp_path/'absent-environment')
    assert result=={'paused':True,'changed':False,'established':False}
    assert pause.read_text()=='owner paused' and not pause.is_symlink()


def test_new_runtime_requires_original_pause(tmp_path):
    with pytest.raises(ValueError,match='INITIAL_PROTECTED_PAUSE_REQUIRED'):
        MODULE.stage_runtime_pause(tmp_path,tmp_path/'absent-original',tmp_path/'absent-environment')
    assert not (tmp_path/'.trader-paused').exists()


def _symlink(link,target):
    try:link.symlink_to(target)
    except OSError as exc:
        if getattr(exc,'winerror',None)==1314:pytest.skip('Windows symlink privilege unavailable')
        raise


def test_new_runtime_initializes_pause_once_then_preserves_owner_clear(tmp_path):
    state=tmp_path/'state';state.mkdir()
    original=tmp_path/'original-paused';original.write_text('original pause')
    environment=tmp_path/'observer.env'
    probe=tmp_path/'symlink-probe';_symlink(probe,original);probe.unlink()
    result=MODULE.stage_runtime_pause(state,original,environment)
    pause=state/'.trader-paused'
    assert result=={'paused':True,'changed':True,'established':False}
    assert pause.is_symlink() and pause.resolve()==original
    (state/'config').mkdir()
    assert not MODULE.stage_runtime_pause(state,original,environment)['changed']
    pause.unlink()  # Simulated explicit owner arming; original remains intact.
    result=MODULE.stage_runtime_pause(state,original,environment)
    assert result=={'paused':False,'changed':False,'established':True}
    assert original.read_text()=='original pause' and not pause.exists()


def test_broken_pause_symlink_remains_paused(tmp_path):
    pause=tmp_path/'.trader-paused';original=tmp_path/'missing-original'
    _symlink(pause,original)
    result=MODULE.stage_runtime_pause(tmp_path,original,tmp_path/'absent-environment')
    assert result['paused'] and not result['changed'] and pause.is_symlink()


def _deployment_database(state,states=(),intent=None):
    import json
    import sqlite3
    journal=state/'.agent-state/pc-monitor.sqlite3';journal.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(journal) as db:
        db.execute('CREATE TABLE pc_orders(state TEXT)')
        db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,body TEXT)')
        db.executemany('INSERT INTO pc_orders(state) VALUES(?)',[(state,) for state in states])
        if intent is not None:db.execute('INSERT INTO meta VALUES(?,?)',('premium-algo-intent',json.dumps(intent)))
    return journal


@pytest.mark.parametrize('state',['RESERVED','ACKNOWLEDGED','FILLED','UNKNOWN',None])
def test_deployment_refuses_any_unclosed_ownership_without_changing_journal(tmp_path,state):
    journal=_deployment_database(tmp_path,['CLOSED',state],{'enabled':False})
    before=journal.read_bytes()
    with pytest.raises(ValueError,match='CLOSE_OWNED_BASKET_BEFORE_DEPLOYMENT'):
        MODULE.deployment_preflight(tmp_path)
    assert journal.read_bytes()==before


def test_deployment_requires_owner_off_and_valid_intent(tmp_path):
    import sqlite3
    journal=_deployment_database(tmp_path,['CLOSED'],{'enabled':True})
    with pytest.raises(ValueError,match='TURN_ALGO_OFF_BEFORE_DEPLOYMENT'):MODULE.deployment_preflight(tmp_path)
    with sqlite3.connect(journal) as db:db.execute("UPDATE meta SET body='{}'")
    with pytest.raises(ValueError,match='DEPLOYMENT_OWNER_INTENT_INVALID'):MODULE.deployment_preflight(tmp_path)


def test_deployment_accepts_only_closed_ownership_or_initial_install(tmp_path):
    assert MODULE.deployment_preflight(tmp_path)=={'journal_present':False,'owner_off':True,'owned_orders_open':False}
    journal=_deployment_database(tmp_path,['CLOSED'],{'enabled':False})
    before=journal.read_bytes()
    assert MODULE.deployment_preflight(tmp_path)=={'journal_present':True,'owner_off':True,'owned_orders_open':False}
    assert journal.read_bytes()==before


def test_deployment_unknown_schema_does_not_silently_mean_flat(tmp_path):
    journal=tmp_path/'.agent-state/pc-monitor.sqlite3';journal.parent.mkdir()
    journal.write_bytes(b'private-content-never-printed')
    with pytest.raises(ValueError,match='^DEPLOYMENT_OWNERSHIP_CHECK_UNAVAILABLE$'):
        MODULE.deployment_preflight(tmp_path)


def test_deployment_refuses_live_running_unpaused_even_when_owner_off(tmp_path):
    _deployment_database(tmp_path,['CLOSED'],{'enabled':False})
    with pytest.raises(ValueError,match='PAUSE_OR_STOP_LIVE_RUNTIME_BEFORE_DEPLOYMENT'):
        MODULE.deployment_preflight(tmp_path,mode='live',service_active=True)
    assert MODULE.deployment_preflight(tmp_path,mode='live',service_active=False)['owner_off']
    (tmp_path/'.trader-paused').touch()
    assert MODULE.deployment_preflight(tmp_path,mode='live',service_active=True)['owner_off']
    assert MODULE.deployment_preflight(tmp_path,mode='paper',service_active=True)['owner_off']


def test_each_deployment_stage_rereads_actual_mode_and_service(tmp_path):
    from types import SimpleNamespace
    _deployment_database(tmp_path,['CLOSED'],{'enabled':False})
    environment=tmp_path/'observer.env';environment.write_text('EXECUTION_MODE="paper"\n')
    states=['active','active','inactive','unrecognized']
    def service(command,**kwargs):
        assert command==['systemctl','is-active','trading-observer']
        assert kwargs=={'capture_output':True,'text':True,'timeout':5}
        return SimpleNamespace(stdout=states.pop(0))
    assert MODULE.deployment_runtime_preflight(tmp_path,environment,run=service)['owner_off']
    environment.write_text('EXECUTION_MODE="live"\n')
    with pytest.raises(ValueError,match='PAUSE_OR_STOP_LIVE_RUNTIME_BEFORE_DEPLOYMENT'):
        MODULE.deployment_runtime_preflight(tmp_path,environment,run=service)
    assert MODULE.deployment_runtime_preflight(tmp_path,environment,run=service)['owner_off']
    with pytest.raises(ValueError,match='DEPLOYMENT_SERVICE_STATE_UNKNOWN'):
        MODULE.deployment_runtime_preflight(tmp_path,environment,run=service)
