"""Validate only the retained headless configuration; no auth, network or activation."""
from pathlib import Path
import json
from nifty_engine.agent_engine.__main__ import load_config
from nifty_engine.agent_engine.owner_study import validate_protocol
from nifty_engine.agent_engine.pc_control import settings as pc_settings
from nifty_engine.agent_engine.premium_strategy import validate as premium_settings
from nifty_engine.agent_engine.report_strategies import load as research_settings
from nifty_engine.agent_engine.normal_theta import load as normal_settings

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    settings = load_config(root / "config/agent_engine.example.json")
    assert settings["enabled"] is False and settings["send_email"] is False
    validate_protocol(json.loads((root / "config/owner_strategies.json").read_text(encoding="utf-8")))
    pc_settings(json.loads((root / "config/pc_app.example.json").read_text(encoding="utf-8")))
    premium_settings(json.loads((root / "config/premium_strategy.json").read_text(encoding="utf-8")))
    assert research_settings(root)['mode']=='MONITOR_ONLY'
    assert normal_settings(root)['historical_iv_required'] is False
    ticket = json.loads((root / "config/manual_ticket.example.json").read_text(encoding="utf-8"))
    assert ticket["quantity"] == 0 and ticket["limit_price"] == "0"
    print("Disabled headless configuration and fixed owner protocol validated.")
