import importlib.util
from pathlib import Path
import pytest

SPEC=importlib.util.spec_from_file_location('observer_provision',Path(__file__).resolve().parents[1]/'scripts/provision_observer.py')
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)
VALUES={'SUPABASE_URL':'https://imirspxhbnerxknyynqx.supabase.co','SUPABASE_SERVICE_ROLE_KEY':'fixture-original-key'}


def test_deploy_preserves_approved_server_credentials_without_other_secrets():
    assert MODULE.server_configuration({**VALUES,'BROKER_CREDENTIAL_ENCRYPTION_KEY':'omit'}, {})==VALUES
    assert MODULE.server_configuration({},VALUES)==VALUES
    assert MODULE.server_configuration(VALUES,VALUES)==VALUES
    assert MODULE.server_configuration({}, {})=={}


def test_deploy_refuses_rotation_partial_or_different_project():
    with pytest.raises(ValueError,match='CHANGE'):MODULE.server_configuration({**VALUES,'SUPABASE_SERVICE_ROLE_KEY':'replacement'},VALUES)
    with pytest.raises(ValueError,match='APPROVED'):MODULE.server_configuration({'SUPABASE_URL':VALUES['SUPABASE_URL']},{})
    with pytest.raises(ValueError,match='APPROVED'):MODULE.server_configuration({**VALUES,'SUPABASE_URL':'https://different.supabase.co'},{})
