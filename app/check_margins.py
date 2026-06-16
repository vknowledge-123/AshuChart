from __future__ import annotations

import os

from kiteconnect import KiteConnect


def main() -> None:
    """
    Small utility script to print Zerodha margin information.

    Required environment variables:
      - KITE_API_KEY
      - KITE_ACCESS_TOKEN
    """
    api_key = (os.getenv("KITE_API_KEY") or "").strip()
    access_token = (os.getenv("KITE_ACCESS_TOKEN") or "").strip()

    if not api_key or not access_token:
        raise SystemExit("Missing KITE_API_KEY / KITE_ACCESS_TOKEN in environment.")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    m = kite.margins()
    print("CASH:", m["equity"]["available"].get("cash"))
    print("COLLATERAL:", m["equity"]["available"].get("collateral"))
    print("LIVE_BALANCE:", m["equity"]["available"].get("live_balance"))
    print("UTILISED:", m["equity"].get("utilised"))


if __name__ == "__main__":
    main()
