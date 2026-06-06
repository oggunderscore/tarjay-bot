"""Redsky API client for querying Target product fulfillment data.

Supports two modes:
1. HTTP mode (httpx) — direct API calls, may be blocked by bot protection
2. Browser mode — fetches API through a Playwright page (NSTBrowser/proxy),
   bypassing PerimeterX by using a real browser network stack
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from scrapebot.config import LocationConfig, RedskyConfig
from scrapebot.models import StockSnapshot, StockStatus

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.target.com",
    "Referer": "https://www.target.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


class RedskyClient:
    """Query Target's Redsky fulfillment API.

    When a browser page is attached (via `attach_page`), API calls route through
    the browser's network stack — inheriting the proxy, cookies, and anti-detect
    fingerprint from NSTBrowser. This bypasses PerimeterX bot protection.

    Without a page attached, falls back to direct HTTP via httpx.
    """

    def __init__(self, redsky: RedskyConfig, location: LocationConfig) -> None:
        self.redsky = redsky
        self.location = location
        self._client = httpx.Client(timeout=20.0, headers=DEFAULT_HEADERS)
        self._page = None  # Optional Playwright page for browser-based fetches

    def close(self) -> None:
        self._client.close()

    def attach_page(self, page) -> None:
        """Attach a Playwright page for browser-based API fetching.

        When set, get_stock() will use page.evaluate(fetch(...)) instead of httpx,
        routing requests through the browser's network stack (with proxy/cookies).
        """
        self._page = page
        logger.info("Redsky client attached to browser page — API calls will route through browser")

    def detach_page(self) -> None:
        """Detach the browser page, reverting to httpx for API calls."""
        self._page = None

    @property
    def using_browser(self) -> bool:
        """True when API calls are routed through a browser page."""
        return self._page is not None

    def _fulfillment_url(self, tcin: str) -> str:
        params = (
            f"key={self.redsky.api_key}"
            f"&tcin={tcin}"
            f"&store_id={self.location.store_id}"
            f"&zip={self.location.zip}"
            f"&state={self.location.state}"
            f"&latitude={self.location.latitude}"
            f"&longitude={self.location.longitude}"
            f"&pricing_store_id={self.location.store_id}"
            f"&has_pricing_store_id=true"
            f"&is_bot=false"
        )
        return f"{self.redsky.base_url}/product_fulfillment_v1?{params}"

    @staticmethod
    def _parse_status(value: str | None) -> StockStatus:
        if not value:
            return StockStatus.UNKNOWN
        try:
            return StockStatus(value)
        except ValueError:
            return StockStatus.UNKNOWN

    def get_stock(self, tcin: str) -> StockSnapshot:
        """Fetch stock status for a product.

        Uses browser page.evaluate(fetch) if a page is attached,
        otherwise falls back to httpx (may get 403'd without proxy).
        """
        url = self._fulfillment_url(tcin)

        if self._page:
            return self._get_stock_via_browser(tcin, url)

        response = self._client.get(url)
        response.raise_for_status()
        return self._parse_response(tcin, response.json())

    def _get_stock_via_browser(self, tcin: str, url: str) -> StockSnapshot:
        """Fetch stock data through the attached browser page.

        This is a synchronous wrapper around the async page.evaluate().
        We use the page's event loop to run the fetch.
        """
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already in an async context — create a task
            import concurrent.futures
            import threading

            result = {}
            exception = None

            def _run():
                nonlocal result, exception
                try:
                    new_loop = asyncio.new_event_loop()
                    result = new_loop.run_until_complete(self._async_fetch_via_browser(url))
                    new_loop.close()
                except Exception as exc:
                    exception = exc

            thread = threading.Thread(target=_run)
            thread.start()
            thread.join(timeout=25)

            if exception:
                raise exception
            if not result:
                raise RuntimeError("Browser fetch timed out")

            return self._parse_response(tcin, result)
        else:
            payload = loop.run_until_complete(self._async_fetch_via_browser(url))
            return self._parse_response(tcin, payload)

    async def _async_fetch_via_browser(self, url: str) -> dict:
        """Use page.evaluate to make a fetch() call through the browser."""
        js_code = """
            async (url) => {
                const response = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                    },
                    credentials: 'include'
                });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return await response.json();
            }
        """
        return await self._page.evaluate(js_code, url)

    async def async_get_stock(self, tcin: str) -> StockSnapshot:
        """Async version of get_stock — preferred when in async context with browser page."""
        url = self._fulfillment_url(tcin)

        if self._page:
            payload = await self._async_fetch_via_browser(url)
            return self._parse_response(tcin, payload)

        # Fallback to httpx (sync in async context)
        response = self._client.get(url)
        response.raise_for_status()
        return self._parse_response(tcin, response.json())

    def _parse_response(self, tcin: str, payload: dict) -> StockSnapshot:
        """Parse a Redsky JSON response into a StockSnapshot."""
        product = payload.get("data", {}).get("product", {})
        fulfillment = product.get("fulfillment", {})

        shipping = fulfillment.get("shipping_options", {})
        shipping_status = self._parse_status(shipping.get("availability_status"))
        shipping_qty = float(shipping.get("available_to_promise_quantity") or 0)

        pickup_status: StockStatus | None = None
        store_options = fulfillment.get("store_options") or []
        if store_options:
            pickup_status = self._parse_status(
                (store_options[0].get("order_pickup") or {}).get("availability_status")
            )

        sold_out = bool(fulfillment.get("sold_out"))

        return StockSnapshot(
            tcin=tcin,
            shipping_status=shipping_status,
            shipping_quantity=shipping_qty,
            pickup_status=pickup_status,
            sold_out=sold_out,
            raw=payload,
        )
