"""Remove an item from the Target cart right now, without waiting on the MCP
server to pick up new tools.

    python remove.py                  # removes whichever cart line best matches "milk"
    python remove.py bananas          # fuzzy-matches cart line text
    python remove.py 13276134         # exact TCIN, skips fuzzy matching
    python remove.py hummus --view    # also print the cart after removing

A TCIN is Target's own numeric product id (the one in /p/-/A-<TCIN> URLs and
in item_id from search/cart results) -- not something you're expected to know
by heart. Pass a plain digit string to use one directly; anything else is
matched by name against the current cart contents.

Uses the same persistent .session/ profile the MCP server uses, so it reuses
your existing sign-in. Because Chrome only lets one process hold that profile
at a time, close/stop the target-grocery MCP server (or its Chrome window)
before running this -- otherwise Chrome will refuse to launch a second copy
against the same user-data-dir.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json

from target_mcp import target
from target_mcp.browser import BrowserSession

DEFAULT_QUERY = "milk"


def find_match(cart: dict, query: str) -> tuple[str, str] | None:
    """Best cart line for a free-text query: substring match first, then
    fuzzy. Returns (item_id, line_text) or None if nothing in the cart is
    close enough to be worth acting on."""
    items = [i for i in cart.get("items", []) if i.get("item_id")]
    query_l = query.lower()

    for item in items:
        if query_l in (item.get("text") or "").lower():
            return item["item_id"], item["text"]

    texts = [(item["item_id"], item.get("text") or "") for item in items]
    close = difflib.get_close_matches(
        query_l, [t.lower() for _, t in texts], n=1, cutoff=0.3
    )
    if not close:
        return None
    idx = [t.lower() for _, t in texts].index(close[0])
    return texts[idx]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("item", nargs="?", default=DEFAULT_QUERY, help="TCIN or name")
    parser.add_argument("--view", action="store_true", help="print the cart afterward")
    args = parser.parse_args()

    session = BrowserSession()
    try:
        page = await session.page()

        if args.item.isdigit():
            item_id = args.item
        else:
            cart = await target.view_cart(page)
            match = find_match(cart, args.item)
            if match is None:
                print(f"No cart line matches {args.item!r}. Current cart:")
                print(json.dumps(cart, indent=2))
                return
            item_id, line_text = match
            print(f"Matched {args.item!r} -> {item_id}  ({line_text[:80]})")

        print(f"\n== remove {item_id} ==")
        result = await target.remove_from_cart(page, item_id)
        print(json.dumps(result, indent=2))

        if args.view or not result.get("ok"):
            print("\n== cart ==")
            print(json.dumps(await target.view_cart(page), indent=2))
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
