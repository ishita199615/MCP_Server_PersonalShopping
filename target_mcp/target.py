"""Everything that knows what Target's pages look like.

This is the only module that touches Target-specific URLs, JSON shapes, and
DOM selectors. When Target changes their markup -- and they will -- this is
the file to fix.

Parsing strategy: Target's search results are NOT in the HTML and NOT in
`__NEXT_DATA__` (both were checked; neither contains a single `tcin`). The
product list arrives in a separate call the page makes to
`cdui-orchestrations.target.com/.../pages/slp`. So instead of scraping the
page, we navigate and read Target's own API response as it goes by. That means
we get their structured data with their parameters -- store, ZIP, session --
without reconstructing the request ourselves.

The one Target-specific trap: for groceries, `shipping_options` is almost
always OUT_OF_STOCK, because milk does not ship. Stock lives in
`store_options[].order_pickup`. Reading the shipping field would mark the
entire dairy aisle unavailable.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Iterator
from urllib.parse import quote_plus

from playwright.async_api import Page

BASE = "https://www.target.com"

# /account redirects to the real, parameterized login URL. Bare /login bounces
# to the homepage, so do not use it.
ACCOUNT_URL = f"{BASE}/account"
CART_URL = f"{BASE}/cart"

# The call that actually carries search results.
SLP_MARKER = "cdui_orchestrations/v1/pages/slp"

PRICE_RE = re.compile(r"\$\s?(\d[\d,]*\.\d{2})")

# Text on a bot wall. We detect it and hand control back to the user -- we
# never try to solve or bypass it.
BLOCK_MARKERS = (
    "robot or human",
    "are you a human",
    "access denied",
    "unusual traffic",
    "pardon the interruption",
    "request blocked",
)

# Verified against the live PDP. `orderPickupButton` is the grocery add button
# and reads "Add to cart"; `chooseOptionsButton` appears on cards and variants.
ATC_SELECTORS = (
    '[data-test="orderPickupButton"]',
    '[data-test="shippingButton"]',
    '[data-test="addToCartButton"]',
    '[data-test="chooseOptionsButton"]',
    'button:has-text("Add to cart")',
)

# Fulfillment tabs on the product page. Pickup first: it is the one that
# actually works for groceries.
FULFILLMENT_SELECTORS = {
    "pickup": '[data-test="fulfillment-cell-pickup"]',
    "delivery": '[data-test="fulfillment-cell-delivery"]',
    "shipping": '[data-test="fulfillment-cell-shipping"]',
}

# Present only when signed out -- Target swaps the buy button for this.
SIGNED_OUT_PDP_MARKER = '[data-test="sign-in-to-buy-now-button"]'

# And this replaces the buy button when the item is unavailable at your store
# for every fulfillment method.
OUT_OF_STOCK_MARKER = '[data-test="showInStockPrimaryButton"]'

STORE_BUTTON = '[data-test="@web/StoreName/Button"]'
ZIP_BUTTON = '[data-test="@web/ZipCodeButton/StyledZipCodeButton"]'

# --- sign-in (verified against the live form) ------------------------------

USERNAME_SELECTORS = (
    "input#username",
    'input[name="username"]',
    'input[autocomplete^="username"]',
    'input[type="email"]',
)

CONTINUE_SELECTORS = (
    "button#login",
    'button[type="submit"]:has-text("Continue")',
    'button[type="submit"]',
)

# After the username step Target does NOT show a password box. It shows a
# method chooser -- "Use a passkey" / "Enter your password" / "Get a code" --
# and these are not <button> elements, so ordinary button selectors miss them.
# Pick the password option to reveal the field.
PASSWORD_OPTION_SELECTORS = (
    "#password-option",
    'button:has-text("Enter your password")',
    '[role="button"]:has-text("Enter your password")',
    'a:has-text("Enter your password")',
    'text="Enter your password"',
)

PASSWORD_SELECTORS = (
    "input#password",
    'input[name="password"]',
    'input[type="password"]',
)

SUBMIT_SELECTORS = (
    "button#login",
    'button[type="submit"]:has-text("Sign in")',
    'button[type="submit"]',
)

KEEP_SIGNED_IN = "input#keepMeSignedIn"

OTP_MARKERS = (
    "one-time code",
    "verification code",
    "enter the code",
    "we sent a code",
    "6-digit code",
    "check your email",
)

BAD_CRED_MARKERS = (
    "password is incorrect",
    "doesn't match",
    "does not match",
    "we don't recognize",
    "please enter a valid",
    "sorry, we can't find",
)


class Blocked(Exception):
    """Target served a bot challenge instead of the page."""


# --------------------------------------------------------------------------
# page helpers
# --------------------------------------------------------------------------


async def goto(page: Page, url: str) -> None:
    """Navigate and raise Blocked if we landed on a bot challenge."""
    await page.goto(url, wait_until="domcontentloaded")
    if await is_blocked(page):
        raise Blocked(
            "Target served a bot challenge. Solve it in the open Chrome window, "
            "then retry."
        )


async def is_blocked(page: Page) -> bool:
    try:
        body = (await page.inner_text("body"))[:4000].lower()
    except Exception:
        return False
    return any(marker in body for marker in BLOCK_MARKERS)


async def _text(page: Page, selector: str) -> str | None:
    try:
        loc = page.locator(selector).first
        if await loc.count():
            return (await loc.inner_text()).strip()
    except Exception:
        pass
    return None


async def store_info(page: Page) -> dict[str, Any]:
    """Which store and ZIP the site currently thinks you are shopping.

    Grocery price and stock are per-store, so a wrong store here is a quiet
    wrong answer everywhere else.

    These controls live in the shopping header, which /account does not have --
    so fall back to the homepage rather than reporting null and letting the
    caller assume there is no store set.
    """
    store = await _text(page, STORE_BUTTON)
    zip_code = await _text(page, ZIP_BUTTON)

    if store is None and zip_code is None:
        await page.goto(BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        store = await _text(page, STORE_BUTTON)
        zip_code = await _text(page, ZIP_BUTTON)

    return {
        "store": store,
        "zip": zip_code,
        "note": "Change either in the Chrome window; both persist in the session.",
    }


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------


async def _settle_redirect(page: Page, timeout_ms: int = 9000) -> None:
    """Give a client-side redirect time to land.

    Polls instead of sleeping flat, so the signed-out case (which redirects
    quickly) does not pay the full wait, and the signed-in case settles on
    networkidle rather than a guess.
    """
    step = 500
    for _ in range(timeout_ms // step):
        if "/login" in page.url.lower():
            return
        await page.wait_for_timeout(step)
        try:
            await page.wait_for_load_state("networkidle", timeout=step)
            if "/login" in page.url.lower():
                return
            break  # settled, and not on the login page
        except Exception:
            continue


async def check_login(page: Page) -> dict[str, Any]:
    """Report whether the stored session is still live.

    /account redirects signed-out visitors to /login, so the URL is real
    evidence rather than a guess -- but the redirect is client-side and lands a
    beat AFTER domcontentloaded. Reading the URL straight away reports
    logged_in: true for a session that is signed out. Wait for it.
    """
    await goto(page, ACCOUNT_URL)
    await _settle_redirect(page)

    url = page.url.lower()
    logged_in = "/login" not in url and "signin" not in url

    return {
        "logged_in": logged_in,
        "current_url": page.url,
        "store": await store_info(page) if logged_in else None,
        "hint": (
            "Session is live."
            if logged_in
            else "Call login to sign in with the credentials in .env."
        ),
    }


async def open_login(page: Page) -> None:
    await goto(page, ACCOUNT_URL)


async def _fill_first(
    page: Page, selectors: tuple[str, ...], value: str, timeout: int = 8000
) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.fill(value)
            return selector
        except Exception:
            continue
    return None


async def _click_first(page: Page, selectors: tuple[str, ...], timeout: int = 8000):
    """Click whichever of these selectors shows up first. None if none do."""
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.click()
            return selector
        except Exception:
            continue
    return None


async def _login_state(page: Page) -> str:
    try:
        body = (await page.inner_text("body"))[:6000].lower()
    except Exception:
        body = ""

    if any(m in body for m in BLOCK_MARKERS):
        return "challenge"
    if any(m in body for m in OTP_MARKERS):
        return "needs_code"
    if any(m in body for m in BAD_CRED_MARKERS):
        return "bad_credentials"
    url = page.url.lower()
    if "/login" not in url and "signin" not in url:
        return "signed_in"
    return "unknown"


async def sign_in(page: Page, email: str, password: str) -> dict[str, Any]:
    """Drive Target's two-step sign-in with the given credentials.

    Neither argument is echoed into the return value -- this output crosses the
    MCP channel and lands in a model's context.

    A one-time code prompt is a normal outcome, not a failure: the browser
    window stays open for you to type it.
    """
    await goto(page, ACCOUNT_URL)

    if await _login_state(page) == "signed_in":
        return {"ok": True, "state": "already_signed_in"}

    if await _fill_first(page, USERNAME_SELECTORS, email) is None:
        return {
            "ok": False,
            "state": "username_field_not_found",
            "message": "No username field on the sign-in page. Target changed "
            "the markup, or a challenge is showing -- check the Chrome window.",
            "url": page.url,
        }

    # Ask Target to keep the session; it is the whole point of .session/.
    try:
        box = page.locator(KEEP_SIGNED_IN).first
        if await box.count() and not await box.is_checked():
            await box.check()
    except Exception:
        pass

    await _click_first(page, CONTINUE_SELECTORS, timeout=6000)
    await page.wait_for_timeout(2000)

    state = await _login_state(page)
    if state in ("challenge", "needs_code", "bad_credentials"):
        return {"ok": False, "state": state, **_advice(state), "url": page.url}

    # Choose password over passkey / emailed code. Only needed when the
    # chooser is showing, so a miss here is not an error.
    if not await page.locator(PASSWORD_SELECTORS[0]).count():
        await _click_first(page, PASSWORD_OPTION_SELECTORS, timeout=4000)
        await page.wait_for_timeout(1500)

    if await _fill_first(page, PASSWORD_SELECTORS, password) is None:
        return {
            "ok": False,
            "state": "password_field_not_found",
            "message": "Got past the username step but found no password field. "
            "Target may be offering a passkey or emailing a code instead -- "
            "check the window.",
            "url": page.url,
        }

    await _click_first(page, SUBMIT_SELECTORS, timeout=6000)

    # Judging too early reports "unknown" for a sign-in that actually worked --
    # the hop from /login to /account lands a beat after the form submits.
    await _settle_redirect(page)

    state = await _login_state(page)
    return {"ok": state == "signed_in", "state": state, **_advice(state), "url": page.url}


def _advice(state: str) -> dict[str, str]:
    return {
        "signed_in": {"message": "Signed in."},
        "needs_code": {
            "message": "Target wants a one-time code. Type it into the open "
            "Chrome window, then call login again. Nothing here can read your "
            "email or texts.",
        },
        "bad_credentials": {
            "message": "Target rejected the credentials. Check TARGET_EMAIL and "
            "TARGET_PASSWORD in .env.",
        },
        "challenge": {
            "message": "Target served a bot challenge. Clear it by hand in the "
            "Chrome window, then retry.",
        },
        "unknown": {
            "message": "Submitted, but the resulting page is not recognizable "
            "as signed in, a code prompt, or an error. Look at the window.",
        },
    }.get(state, {})


# --------------------------------------------------------------------------
# product JSON extraction
# --------------------------------------------------------------------------


def _walk(node: Any) -> Iterator[dict]:
    """Yield every dict nested anywhere inside node."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _looks_like_product(d: dict) -> bool:
    # __typename is Target's own label for these; tcin alone also appears on
    # recommendation stubs that carry no price.
    return bool(d.get("tcin")) and isinstance(d.get("item"), dict)


def _first(d: dict, *paths: str) -> Any:
    """Return the first non-None value among dotted paths."""
    for path in paths:
        cur: Any = d
        for key in path.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        if cur is not None:
            return cur
    return None


def _clean(text: Any) -> str | None:
    """Target ships HTML entities inside JSON strings ("Good &#38; Gather&#8482;")."""
    if not text:
        return None
    return html.unescape(str(text)).strip()


def _fulfillment(d: dict) -> dict[str, Any]:
    """Per-channel availability.

    Groceries do not ship, so `shipping` being OUT_OF_STOCK says nothing about
    whether you can buy the item. Pickup and delivery are what matter.
    """
    f = d.get("fulfillment") or {}
    out: dict[str, Any] = {"pickup": None, "delivery": None, "shipping": None}

    shipping = _first(f, "shipping_options.availability_status")
    if shipping:
        out["shipping"] = shipping == "IN_STOCK"

    for option in f.get("store_options") or []:
        for channel, key in (("pickup", "order_pickup"), ("delivery", "in_store_only")):
            status = _first(option, f"{key}.availability_status")
            if status and out[channel] is None:
                out[channel] = status == "IN_STOCK"

    # scheduled delivery lives at the top level on some payloads
    sd = _first(f, "scheduled_delivery.availability_status")
    if sd and out["delivery"] is None:
        out["delivery"] = sd == "IN_STOCK"

    if f.get("sold_out") or f.get("is_out_of_stock_in_all_store_locations"):
        out = {k: False for k in out}
    return out


def normalize_product(d: dict) -> dict[str, Any]:
    """Flatten one Target product into our stable shape."""
    tcin = str(d.get("tcin"))
    url = _first(d, "item.enrichment.buy_url") or f"{BASE}/p/-/A-{tcin}"

    channels = _fulfillment(d)
    # "Can I actually get this?" -- true if any real channel says yes.
    in_stock = (
        None
        if all(v is None for v in channels.values())
        else any(bool(v) for v in channels.values())
    )

    return {
        "item_id": tcin,
        "name": _clean(_first(d, "item.product_description.title")),
        # Unlike Walmart's payload, Target actually populates brand.
        "brand": _clean(_first(d, "item.primary_brand.name")),
        "price": _first(d, "price.current_retail"),
        "price_string": _first(d, "price.formatted_current_price"),
        # The field that makes grocery comparison work.
        "unit_price": _first(d, "price.formatted_unit_price"),
        "in_stock": in_stock,
        "fulfillment": channels,
        "rating": _first(d, "ratings_and_reviews.statistics.rating.average"),
        "review_count": _first(d, "ratings_and_reviews.statistics.rating.count"),
        "snap_eligible": _first(d, "item.compliance.is_snap_eligible"),
        "url": url,
    }


def _is_sponsored(d: dict) -> bool:
    return bool(d.get("is_sponsored_sku") or d.get("sponsored_ad"))


def extract_products(data: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Find products anywhere in an slp payload, dedup, drop sponsored.

    Walks rather than indexing a fixed path: the real path today is
    data_source_modules[0].module_data.search_response.products, and that is
    exactly the kind of thing that moves.

    Two passes, because one is not enough: a sponsored product also appears
    elsewhere in the payload as an unflagged copy, so filtering node-by-node
    lets the ad back in through the side door. Collect every tcin that is
    sponsored anywhere first, then exclude all of them.
    """
    sponsored: set[str] = {
        str(node["tcin"])
        for node in _walk(data)
        if node.get("tcin") and _is_sponsored(node)
    }

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for node in _walk(data):
        if not _looks_like_product(node) or str(node["tcin"]) in sponsored:
            continue
        tcin = str(node["tcin"])
        if tcin in seen:
            continue
        seen.add(tcin)
        out.append(normalize_product(node))
        if len(out) >= limit:
            break
    return out


async def _scrape_search_dom(page: Page, limit: int) -> list[dict[str, Any]]:
    """Fallback for when the slp response never arrives."""
    rows = await page.evaluate(
        """
        (limit) => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/p/"]')) {
            const m = a.href.match(/\\/A-(\\d+)/);
            if (!m || seen.has(m[1])) continue;
            seen.add(m[1]);
            const card = a.closest('[data-test*="product"]') || a.parentElement;
            out.push({
              item_id: m[1],
              text: (card ? card.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 300),
              url: a.href.split('?')[0],
            });
            if (out.length >= limit) break;
          }
          return out;
        }
        """,
        limit,
    )

    products = []
    for row in rows:
        match = PRICE_RE.search(row.get("text") or "")
        products.append(
            {
                "item_id": row["item_id"],
                "name": (row.get("text") or "")[:120] or None,
                "brand": None,
                "price": float(match.group(1).replace(",", "")) if match else None,
                "price_string": match.group(0) if match else None,
                "unit_price": None,
                "in_stock": None,
                "fulfillment": {"pickup": None, "delivery": None, "shipping": None},
                "rating": None,
                "review_count": None,
                "snap_eligible": None,
                "url": row.get("url"),
                "source": "dom-fallback",
            }
        )
    return products


async def search(page: Page, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Target and return up to `limit` non-sponsored candidates.

    Navigates the real page and reads Target's own search response as it goes
    by, so the results carry the store, ZIP and session the browser is using.
    """
    url = f"{BASE}/s?searchTerm={quote_plus(query)}"

    payload: dict[str, Any] | None = None
    try:
        async with page.expect_response(
            lambda r: SLP_MARKER in r.url and r.status == 200, timeout=30_000
        ) as slp:
            await page.goto(url, wait_until="domcontentloaded")
        payload = json.loads(await (await slp.value).text())
    except Exception:
        # Either the endpoint moved or the navigation itself failed; the block
        # check below tells us which.
        payload = None

    if await is_blocked(page):
        raise Blocked(
            "Target served a bot challenge. Solve it in the open Chrome window, "
            "then retry."
        )

    if payload:
        products = extract_products(payload, limit)
        if products:
            return products

    await page.wait_for_timeout(2500)
    return await _scrape_search_dom(page, limit)


# --------------------------------------------------------------------------
# cart writes
# --------------------------------------------------------------------------


async def add_to_cart(page: Page, item_id: str, quantity: int = 1) -> dict[str, Any]:
    """Open a product page and add it. Returns a result dict, never raises on
    an ordinary failure (out of stock, button missing, not signed in)."""
    await goto(page, f"{BASE}/p/-/A-{item_id}")

    if await page.locator(SIGNED_OUT_PDP_MARKER).count():
        return {
            "ok": False,
            "item_id": item_id,
            "reason": "not_signed_in",
            "detail": "Target is showing 'Sign in to buy now'. Call login first.",
        }

    # Pick a channel that is actually available. The cells are never `disabled`
    # -- an unavailable one just reads "Not available" -- so is_enabled() is no
    # help and clicking pickup blindly strands us on a dead tab.
    chosen = None
    for channel in ("pickup", "delivery", "shipping"):
        cell = page.locator(FULFILLMENT_SELECTORS[channel]).first
        try:
            if not await cell.count():
                continue
            if "not available" in (await cell.inner_text()).lower():
                continue
            await cell.click()
            await page.wait_for_timeout(1000)
            chosen = channel
            break
        except Exception:
            continue

    # Only meaningful AFTER a channel is chosen: this marker shows whenever the
    # selected tab is unavailable, even when another channel would work. The
    # banana that exposed this was dead on pickup and deliverable the same day.
    if chosen is None or await page.locator(OUT_OF_STOCK_MARKER).count():
        store = await _text(page, STORE_BUTTON)
        return {
            "ok": False,
            "item_id": item_id,
            "reason": "out_of_stock_at_store",
            "store": store,
            "detail": f"No fulfillment method available at {store or 'your store'}. "
            "Try another store, or a different product.",
        }

    clicked = await _click_first(page, ATC_SELECTORS)
    if clicked is None:
        return {
            "ok": False,
            "item_id": item_id,
            "reason": "add_button_not_found",
            "detail": "No Add to cart control on the page. It may be unavailable "
            "at your store, or the markup changed.",
        }

    await page.wait_for_timeout(2000)

    return {
        "ok": True,
        "item_id": item_id,
        "fulfillment": chosen,
        "quantity_requested": quantity,
        "quantity_added": 1,
        # Honest: the quantity stepper was not present on the product page used
        # to build this, so repeat adds are not implemented. Call again to add
        # another, or change the quantity in the cart.
        "partial": quantity > 1,
        "note": (
            "Added 1. Quantity above 1 is not automated -- adjust it in the cart."
            if quantity > 1
            else None
        ),
        "url": f"{BASE}/p/-/A-{item_id}",
    }


async def view_cart(page: Page) -> dict[str, Any]:
    """Read the cart back. Best-effort: the cart is client-rendered, so this
    returns raw text alongside parsed rows rather than pretending an empty
    parse means an empty cart."""
    await goto(page, CART_URL)
    await page.wait_for_timeout(3500)

    # Anchor on the per-line delete button: it exists once per real cart line
    # and nowhere else. Selecting [data-test*="cartItem"] returns the same
    # product six times because those hooks nest, and selecting every /p/ link
    # scoops up the recommendation carousels further down the page -- both were
    # observed with a single item in the cart.
    rows = await page.evaluate(
        """
        () => {
          const seen = new Set();
          const out = [];
          for (const btn of document.querySelectorAll('[data-test="cartItem-deleteBtn"]')) {
            let node = btn, box = null, link = null;
            for (let i = 0; i < 10 && node; i++) {
              link = node.querySelector && node.querySelector('a[href*="/p/"]');
              if (link && /\\$\\d/.test(node.innerText || '')) { box = node; break; }
              node = node.parentElement;
            }
            if (!box || !link) continue;
            const m = link.href.match(/\\/A-(\\d+)/);
            const id = m ? m[1] : null;
            if (id && seen.has(id)) continue;
            if (id) seen.add(id);
            out.push({
              item_id: id,
              text: box.innerText.replace(/\\s+/g, ' ').trim().slice(0, 220),
            });
          }
          return out;
        }
        """
    )

    # The order summary has its own elements. "Subtotal" does not appear as
    # body text at all, so regexing the page for it silently found nothing.
    summary = await page.evaluate(
        """
        () => {
          const read = (sel) => {
            const el = document.querySelector(sel);
            return el ? el.innerText.replace(/\\s+/g, ' ').trim() : null;
          };
          return {
            subtotal: read('[data-test="cart-summary-subtotal"]')
                   || read('[data-test="cart-summary-subTotal"]'),
            total: read('[data-test="cart-summary-total"]'),
            count: read('[data-test="cart-summary-item-count"]'),
          };
        }
        """
    )

    def money(text: str | None) -> float | None:
        match = PRICE_RE.search(text or "")
        return float(match.group(1).replace(",", "")) if match else None

    items = []
    for row in rows:
        match = PRICE_RE.search(row["text"])
        items.append(
            {
                "item_id": row["item_id"],
                "text": row["text"],
                "price": float(match.group(1).replace(",", "")) if match else None,
            }
        )

    subtotal = money(summary.get("subtotal"))
    return {
        "items": items,
        "item_count": len(items),
        "subtotal": subtotal,
        "total": money(summary.get("total")),
        "summary_item_count": summary.get("count"),
        "parsed": bool(items) or subtotal is not None,
        "url": CART_URL,
    }


async def remove_from_cart(page: Page, item_id: str) -> dict[str, Any]:
    """Remove one line from the cart by item_id (TCIN).

    Same row-walk view_cart uses to build item_id: there is no per-item hook,
    so match on the /p/ link nested under each delete button rather than
    trusting cart line order or count.
    """
    await goto(page, CART_URL)
    await page.wait_for_timeout(3500)

    clicked = await page.evaluate(
        """
        (itemId) => {
          for (const btn of document.querySelectorAll('[data-test="cartItem-deleteBtn"]')) {
            let node = btn;
            for (let i = 0; i < 10 && node; i++) {
              const link = node.querySelector && node.querySelector('a[href*="/p/"]');
              if (link) {
                const m = link.href.match(/\\/A-(\\d+)/);
                if (m && m[1] === itemId) {
                  btn.click();
                  return true;
                }
              }
              node = node.parentElement;
            }
          }
          return false;
        }
        """,
        item_id,
    )

    if not clicked:
        return {
            "ok": False,
            "item_id": item_id,
            "reason": "item_not_in_cart",
            "detail": "No cart line matched this item_id. Call view_cart to check "
            "current contents.",
        }

    await page.wait_for_timeout(1500)

    # Some cart lines confirm the delete with a follow-up dialog rather than
    # removing immediately; click through it if one shows up.
    for selector in (
        '[data-test="cartItem-removeConfirmButton"]',
        'button:has-text("Yes, remove")',
        'button:has-text("Remove")',
    ):
        try:
            confirm = page.locator(selector).first
            if await confirm.count() and await confirm.is_visible():
                await confirm.click()
                await page.wait_for_timeout(1000)
                break
        except Exception:
            continue

    return {"ok": True, "item_id": item_id, "url": CART_URL}
