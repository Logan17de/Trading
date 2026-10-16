"""Expiry-day exceptions are limited to explicit protected one-lot owner tests."""
import copy
from datetime import timedelta

import pytest

from test_commissioning_direct import session
from nifty_engine.agent_engine.broker_commissioning import validate_plan
from nifty_engine.agent_engine.commissioning_stop import DIRECT_FORMAT


@pytest.mark.parametrize("index", ["NIFTY", "SENSEX"])
def test_owner_one_lot_direct_test_permits_current_expiry_before_cutoff(tmp_path, index):
    s = session(tmp_path, index)
    value = copy.deepcopy(s.plan)
    value["contract"]["expiry"] = s.now.date().isoformat()
    value["valid_until"] = (s.now + timedelta(minutes=20)).isoformat()
    assert value["format"] == DIRECT_FORMAT
    result = validate_plan(value, s.binding, s.now)
    assert result["contract"]["expiry"] == s.now.date().isoformat()
    assert result["quantity"] == result["contract"]["lot_size"]
    assert s.broker.writes == []


@pytest.mark.parametrize("case", [
    "expired_yesterday", "gtt_today", "late_review", "deadline_after_cutoff",
    "submission_over_thirty_minutes",
])
def test_expiry_day_guard_refuses_unsafe_conditions(tmp_path, case):
    s = session(tmp_path)
    value = copy.deepcopy(s.plan)
    value["contract"]["expiry"] = s.now.date().isoformat()
    value["valid_until"] = (s.now + timedelta(minutes=20)).isoformat()
    if case == "expired_yesterday":
        value["contract"]["expiry"] = (s.now - timedelta(days=1)).date().isoformat()
    elif case == "gtt_today":
        from nifty_engine.agent_engine.commissioning_stop import FORMAT
        value["format"] = FORMAT
    elif case == "late_review":
        s.now += timedelta(hours=2)
        value["valid_until"] = (s.now + timedelta(minutes=20)).isoformat()
    elif case == "deadline_after_cutoff":
        value["valid_until"] = (s.now + timedelta(hours=2)).isoformat()
    elif case == "submission_over_thirty_minutes":
        value["valid_until"] = (s.now + timedelta(minutes=35)).isoformat()
    with pytest.raises(ValueError, match="COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG"):
        validate_plan(value, s.binding, s.now)
    assert s.broker.writes == []
