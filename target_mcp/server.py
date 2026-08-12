"""MCP tool surface for Target grocery.

Deliberately thin, and deliberately small: five tools. The server searches,
adds, and removes; it never chooses items and it never checks out. Claude
proposes picks, you approve them, you place the order yourself in the browser.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import MCPServer

from . import creds, target
from .browser import BrowserSession

mcp = MCPServer("target-grocery", version="0.2.0")
session = BrowserSession()

TOOL_TIMEOUT = 90  # seconds; keeps a hung page from wedging the server


async def _run(coro_factory, what: str) -> Any:
    """Run one page operation with a timeout, turning expected failures into
    structured returns instead of exceptions across the MCP channel."""
    try:
        page = await asyncio.wait_for(session.page(), timeout=60)
        return await asyncio.wait_for(coro_factory(page), timeout=TOOL_TIMEOUT)
    except creds.MissingCredentials as exc:
        return {"error": "no_credentials", "message": str(exc)}
    except target.Blocked as exc:
        return {"error": "blocked", "message": str(exc)}
    except asyncio.TimeoutError:
        return {
            "error": "timeout",
            "message": f"{what} took longer than {TOOL_TIMEOUT}s. Check the Chrome "
            "window -- it may be waiting on a challenge or a slow page.",
        }
    except Exception as exc:  # noqa: BLE001 - surface, don't crash the server
        return {"error": "unexpected", "message": f"{what} failed: {exc}"}


@mcp.tool()
async def login() -> dict:
    """Report the session state, signing in from target.env if needed.

    Safe to call any time: if the session is already live it just reports, and
    does not touch the login form. The session persists between runs, so in
    practice this is a first-run step.

    Always check `store`. Grocery price and stock are per-store, and signing in
    switches to the store saved on your account -- which may not be near you. A
    store that does not do grocery fulfillment makes every perishable look out
    of stock. Change it in the Chrome window.

    On failure returns a `state`: `needs_code` (Target wants a one-time code --
    type it into the open Chrome window yourself, then call this again),
    `bad_credentials`, or `challenge`. The browser stays open so you can finish
    by hand.

    Never returns the credentials, and never asks you to paste a password into
    chat -- the values come from target.env and go straight to the form.
    """

    async def _login(page):
        status = await target.check_login(page)
        if status["logged_in"]:
            return {"ok": True, "state": "signed_in", "store": status["store"]}

        email, password = creds.credentials()  # raises MissingCredentials
        result = await target.sign_in(page, email, password)
        if result.get("ok"):
            result["store"] = (await target.check_login(page))["store"]
        else:
            result["browser"] = "Chrome is open at the sign-in page -- finish there."
        return result

    return await _run(_login, "signing in")


@mcp.tool()
async def search_grocery(query: str, limit: int = 5) -> dict:
    """Search Target and return candidate products.

    Returns up to `limit` non-sponsored results with item_id, name, brand,
    price, unit_price, fulfillment, and stock. Raise `limit` when a query is
    ambiguous and the top few look wrong.

    Read `fulfillment` before trusting `in_stock`: groceries do not ship, so
    `shipping: false` is normal and says nothing. `pickup` is the one that
    matters.

    Nothing is added to the cart by this tool -- pick from the results and
    call add_to_cart with the item_id you want.
    """
    limit = max(1, min(int(limit), 40))
    result = await _run(
        lambda page: target.search(page, query, limit), f"search for {query!r}"
    )
    if isinstance(result, dict):  # an error envelope
        return result
    return {"query": query, "count": len(result), "results": result}


@mcp.tool()
async def add_to_cart(item_id: str, quantity: int = 1) -> dict:
    """Add one Target item to the cart by its item_id (TCIN).

    Get item_id from search_grocery. Show the user which products you intend
    to add, and what they cost, before calling this. Prefers pickup, since
    that is usually the only channel in stock for groceries.

    Quantity above 1 is not automated -- it adds one and says so.
    """
    quantity = max(1, min(int(quantity), 20))
    return await _run(
        lambda page: target.add_to_cart(page, item_id, quantity),
        f"adding item {item_id}",
    )


@mcp.tool()
async def view_cart() -> dict:
    """Read back the current Target cart contents and subtotal.

    Best-effort parse of a client-rendered page: if `parsed` is false, trust
    the browser window over this output. Checkout is not automated -- place
    the order yourself.
    """
    return await _run(target.view_cart, "reading cart")


@mcp.tool()
async def remove_from_cart(item_id: str) -> dict:
    """Remove one line from the cart by item_id (TCIN).

    Get item_id from view_cart or search_grocery. Removes the whole line --
    there is no partial-quantity removal.
    """
    return await _run(
        lambda page: target.remove_from_cart(page, item_id),
        f"removing item {item_id}",
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
