"""Browser lifecycle manager with NSTBrowser SDK integration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from scrapebot.config import Settings

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manage Playwright browser lifecycle.

    Connection priority:
    1. NSTBrowser SDK (api_key + profile_id) — launches profile, gets CDP URL dynamically
    2. NSTBrowser CDP URL (manual/static) — connects directly to a running instance
    3. Local Chrome persistent context — fallback with session persistence
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.browser_cfg = settings.browser
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._nst_client = None  # NstbrowserClient instance (lazy init)

    async def start(self) -> BrowserContext:
        """Start or connect to a browser and return the context."""
        self._playwright = await async_playwright().start()

        # Priority 1: NSTBrowser SDK (dynamic CDP via API)
        if self.browser_cfg.nstbrowser_api_key and self.browser_cfg.nstbrowser_profile_id:
            await self._connect_nstbrowser_sdk()
        # Priority 2: Static CDP URL
        elif self.browser_cfg.nstbrowser_cdp_url:
            await self._connect_cdp(self.browser_cfg.nstbrowser_cdp_url)
        # Priority 3: Local Chrome
        else:
            await self._launch_local()

        assert self._context is not None
        self._context.set_default_timeout(self.browser_cfg.timeout_ms)
        return self._context

    async def _connect_nstbrowser_sdk(self) -> None:
        """Launch a profile via NSTBrowser SDK and connect over CDP."""
        from nstbrowser import NstbrowserClient

        api_key = self.browser_cfg.nstbrowser_api_key
        profile_id = self.browser_cfg.nstbrowser_profile_id

        logger.info("Launching NSTBrowser profile %s via SDK...", profile_id)
        self._nst_client = NstbrowserClient(api_key=api_key)

        # Connect to the browser profile — this launches it and returns CDP info
        response = self._nst_client.cdp_endpoints.connect_browser(
            profile_id=profile_id,
            config={
                "headless": self.browser_cfg.headless,
                "autoClose": False,
            },
        )

        ws_url = response["data"]["webSocketDebuggerUrl"]
        logger.info("NSTBrowser CDP endpoint: %s", ws_url)
        await self._connect_cdp(ws_url)

    async def _connect_cdp(self, ws_url: str) -> None:
        """Connect to a browser via Chrome DevTools Protocol."""
        logger.info("Connecting via CDP: %s", ws_url)
        assert self._playwright is not None
        browser = await self._playwright.chromium.connect_over_cdp(ws_url)
        if browser.contexts:
            self._context = browser.contexts[0]
        else:
            self._context = await browser.new_context()

    async def _launch_local(self) -> None:
        """Launch a local Chrome persistent context."""
        assert self._playwright is not None
        user_data = Path(self.browser_cfg.user_data_dir)
        user_data.mkdir(parents=True, exist_ok=True)

        launch_kwargs = {
            "headless": self.browser_cfg.headless,
            "slow_mo": self.browser_cfg.slow_mo_ms,
        }

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data),
                channel="chrome",
                viewport={"width": 1400, "height": 900},
                locale="en-US",
                **launch_kwargs,
            )
        except Exception:
            logger.warning("Chrome not found; falling back to bundled Chromium")
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data),
                viewport={"width": 1400, "height": 900},
                locale="en-US",
                **launch_kwargs,
            )

    async def stop(self) -> None:
        """Close browser context and stop Playwright."""
        if self._context:
            # Don't close the context for NSTBrowser — it manages its own lifecycle
            if not self.browser_cfg.nstbrowser_api_key and not self.browser_cfg.nstbrowser_cdp_url:
                await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def new_page(self) -> Page:
        """Get or create a page in the browser context."""
        if not self._context:
            await self.start()
        assert self._context is not None
        page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return page

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        """Context manager for a page."""
        page = await self.new_page()
        try:
            yield page
        finally:
            pass

    async def screenshot(self, page: Page, name: str) -> Path | None:
        """Capture a full-page screenshot if enabled."""
        if not self.browser_cfg.screenshot_on_error:
            return None
        out_dir = Path(self.browser_cfg.screenshot_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"{name}-{timestamp}.png"
        await page.screenshot(path=str(path), full_page=True)
        logger.info("Saved screenshot: %s", path)
        return path

    @property
    def is_nstbrowser(self) -> bool:
        """True when using NSTBrowser (SDK or direct CDP)."""
        return bool(
            self.browser_cfg.nstbrowser_api_key or self.browser_cfg.nstbrowser_cdp_url
        )
