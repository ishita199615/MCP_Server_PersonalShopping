"""Manual smoke test against the live site. Run this when something breaks.

    python smoke_test.py "whole milk"

Opens Chrome, reports login state and store, searches, prints candidates, and
reads the cart. It does NOT add anything -- pass --add <tcin> for that.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from target_mcp import creds, target
from target_mcp.browser import BrowserSession


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="whole milk")
    parser.add_argument("--add", metavar="TCIN", help="also add this item to cart")
    parser.add_argument("--login", action="store_true", help="sign in from target.env")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    session = BrowserSession()
    try:
        page = await session.page()

        if args.login:
            print("== sign in ==")
            email, password = creds.credentials()
            print(json.dumps(await target.sign_in(page, email, password), indent=2))

        print("\n== login ==")
        print(json.dumps(await target.check_login(page), indent=2))

        print(f"\n== search: {args.query!r} ==")
        results = await target.search(page, args.query, args.limit)
        for r in results:
            print(
                f"  {r['item_id']:>10}  {str(r['price_string']):>8}"
                f"  unit={str(r['unit_price']):>10}"
                f"  pickup={r['fulfillment']['pickup']}"
                f"  {(r['name'] or '')[:52]}"
            )

        if args.add:
            print(f"\n== add {args.add} ==")
            print(json.dumps(await target.add_to_cart(page, args.add), indent=2))

        print("\n== cart ==")
        print(json.dumps(await target.view_cart(page), indent=2))
    finally:
        input("\nPress Enter to close the browser...")
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
