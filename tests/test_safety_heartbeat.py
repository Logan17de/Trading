"""Oracle-to-cloud heartbeat cannot carry broker secrets or fake flat positions."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from nifty_engine.agent_engine import safety_heartbeat as mod

NOW=datetime(2026,10,13,5,0,tzinfo=timezone.utc)


def snapshot(*, qty=0, at=NOW):
    return dict(finished_at=at.isoformat(),positions_status='AVAILABLE',
                orders_status='AVAILABLE',ordered_options=[{'quantity':qty,
                    'secret':'API_KEY_PRIVATE','symbol':'NIFTYPRIVATECE'}],
                execution_observation={'positions':[]})


def test_unknown_not_claimed_flat_from_missing_data():
    assert mod.minimal({}, {}, {}, NOW)['risk_state']=='UNKNOWN'
    assert mod.minimal(snapshot(at=NOW-timedelta(minutes=20)),{}, {},NOW)['risk_state']=='UNKNOWN'


def test_active_on_trading_position_or_pending_basket_or_commissioning():
    assert mod.minimal(snapshot(qty=50),{}, {},NOW)['risk_state']=='ACTIVE'
    assert mod.minimal(snapshot(),{'phase':'REPORT_MONITORING'}, {},NOW)['risk_state']=='ACTIVE'
    assert mod.minimal(snapshot(),{}, {'state':'RUNNING'},NOW)['risk_state']=='ACTIVE'
    assert mod.minimal(snapshot(),{}, {'attention':'STOP_MISSING'},NOW)['risk_state']=='ACTIVE'


def test_verified_flat_is_not_inferred_from_empty_stale_orders():
    assert mod.minimal(snapshot(),{}, {},NOW)['risk_state']=='FLAT'
    row=snapshot();row['orders_status']='INCOMPLETE'
    assert mod.minimal(row,{}, {},NOW)['risk_state']=='UNKNOWN'


def test_no_secrets_prices_or_symbol_in_cloud_payload():
    risk=mod.minimal(snapshot(qty=50),{}, {},NOW)
    sent=mod.payload(risk,NOW)
    assert sent=={'source':'oracle','risk_state':'ACTIVE','observed_at':NOW.isoformat()}
    assert 'PRIVATE' not in str(sent)
    assert 'NIFTY' not in str(sent)


def test_wrong_project_does_not_attempt_network():
    sender=Mock(side_effect=AssertionError('NETWORK_FORBIDDEN'))
    assert mod.publish({},NOW,url='https://untrusted.invalid',key='test',opener=sender)=='NOT_CONFIGURED'
    sender.assert_not_called()
