"""Playwright lifecycle: one Chrome window, one persistent session dir, one page.

The session dir at `.session/` exists so a successful sign-in survives a
restart -- that is all it is. Nothing here reads your real Chrome profile, and
nothing copies one.

Credentials never live in this module. See `creds.py`.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

# patchright is a drop-in Playwright fork with the automation tells patched
# out. Target runs PerimeterX (the same vendor that walls Walmart), so keep it.
# Set TARGET_MCP_PLAIN_PLAYWRIGHT=1 to force the stock library.
if os.environ.get("TARGET_MCP_PLAIN_PLAYWRIGHT"):
    from playwright.async_api import Page, async_playwright

    DRIVER = "playwright"
else:
    try:
        from patchright.async_api import Page, async_playwright

        DRIVER = "patchright"
    except ImportError:
        from playwright.async_api import Page, async_playwright

        DRIVER = "playwright"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = Path(os.environ.get("TARGET_MCP_SESSION_DIR", PROJECT_ROOT / ".session"))

HEADLESS = bool(os.environ.get("TARGET_MCP_HEADLESS"))

# Keeps the automation banner and navigator.webdriver away. Not an attempt to
# defeat a bot check -- just not volunteering a flag on a browser you are
# signing into yourself.
CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]

_HIDE_WEBDRIVER = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)


class BrowserSession:
    """Lazily launches Chrome with a persistent session dir and reuses it."""

    def __init__(self, session_dir: Path = SESSION_DIR, headless: bool = HEADLESS):
        self.session_dir = Path(session_dir)
        self.headless = headless
        self._playwright = None
        self._context = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    async def page(self) -> Page:
        """Return the shared page, launching the browser on first use."""
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            self.session_dir.mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.session_dir),
                channel="chrome",
                headless=self.headless,
                args=CHROME_ARGS,
                viewport=None,
                no_viewport=True,
            )
            await self._context.add_init_script(_HIDE_WEBDRIVER)
            self._context.set_default_timeout(30_000)
            self._context.set_default_navigation_timeout(45_000)

            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            return self._page

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
                self._context = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            self._page = None
