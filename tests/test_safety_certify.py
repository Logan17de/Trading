"""No network, no credentials, no broker SDK in this proof validation."""
from datetime import datetime, timedelta, timezone

from nifty_engine.agent_engine import safety_certify as s

NOW=datetime(2026,10,13,8,0,tzinfo=timezone.utc)
SHA="a"*40
EMAIL="owner@example.invalid"


def local_receipt(at=NOW):
    return dict(code="ALERT_TEST",mail_status="ACCEPTED",
                recipient_server_accepted=True,accepted_at=at.isoformat())


def test_explicit_real_mail_receipt_not_accepted_without_provider_confirmation():
    assert s.mail_receipt([],EMAIL,NOW) is None
    assert s.mail_receipt([local_receipt()],EMAIL,NOW)==NOW.isoformat()
    assert s.mail_receipt([dict(local_receipt(),recipient_server_accepted=False)],EMAIL,NOW) is None
    assert s.mail_receipt([dict(local_receipt(),code="DAILY_EMAIL_FAILED")],EMAIL,NOW) is None
    assert s.mail_receipt([local_receipt()],"",NOW) is None
    assert s.mail_receipt([local_receipt(NOW-timedelta(days=8))],EMAIL,NOW) is None


def test_cloud_outage_mail_and_external_live_cron_both_required():
    assert s.cloud_receipt(NOW.isoformat(),True,NOW)
    assert not s.cloud_receipt(NOW.isoformat(),False,NOW)
    assert not s.cloud_receipt((NOW-timedelta(days=8)).isoformat(),True,NOW)
    assert not s.cloud_receipt((NOW+timedelta(minutes=2)).isoformat(),True,NOW)


def test_safety_activation_evidence_only_after_both_deliveries():
    assert s.prove(SHA,NOW,None,NOW.isoformat(),True)["status"]=="BLOCKED"
    assert s.prove(SHA,NOW,NOW.isoformat(),None,True)["status"]=="BLOCKED"
    assert s.prove(SHA,NOW,NOW.isoformat(),NOW.isoformat(),False)["status"]=="BLOCKED"
    proof=s.prove(SHA,NOW,NOW.isoformat(),NOW.isoformat(),True)
    assert proof["critical_transport_tested"] is True
    assert proof["independent_cloud_outage_tested"] is True
    assert proof["api_checkpoint_code_tested"] is True
    assert proof["release"]==SHA
    assert 'key' not in str(proof).lower()
    assert proof["provider_acceptance_at"]==NOW.isoformat()
