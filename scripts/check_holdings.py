"""Read your Groww holdings using the two files in the repository root."""
import json
import re
from pathlib import Path

from growwapi import GrowwAPI


def main():
    folder = Path(__file__).resolve().parents[1]
    api_key = (folder / "api_key.txt").read_text(encoding="utf-8-sig").strip()
    api_secret = (folder / "api_secret.txt").read_text(encoding="utf-8-sig").strip()

    access_token = GrowwAPI.get_access_token(api_key=api_key, secret=api_secret)
    groww = GrowwAPI(access_token)
    holdings = groww.get_holdings_for_user(timeout=15)
    print(json.dumps(holdings, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid printing credential-bearing exceptions or HTTP request objects.
        code = str(getattr(error, "code", ""))
        print(json.dumps({"error_type": type(error).__name__,
                          "code": code if re.fullmatch(r"(?:GA[0-9]{3}|[0-9]{3})", code) else None}))
        raise SystemExit(1) from None
