"""Drop unrelated environment credentials before starting a service process."""
from __future__ import annotations

import os
import sys

BASE = {"PATH", "HOME", "LANG", "TZ", "PYTHONPATH", "SYSTEMROOT", "TMPDIR"}
ROLE_KEYS = {
    "scheduler": {"RESEND_API_KEY", "TRADING_REPORT_FROM", "TRADING_REPORT_TO"},
    "worker": set(),  # Supported auth cache belongs exclusively to the analyst UID.
}


def filtered_environment(role, source):
    if role not in ROLE_KEYS:
        raise ValueError("unknown service role")
    return {k: v for k, v in source.items() if k in BASE | ROLE_KEYS[role]}


def main():
    role, *args = sys.argv[1:]
    env = filtered_environment(role, os.environ)
    module = "nifty_engine.agent_engine"
    os.execve(sys.executable, [sys.executable, "-m", module, *args], env)


if __name__ == "__main__":
    main()
