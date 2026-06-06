from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from scrapebot.checkout.browser import BrowserManager
from scrapebot.checkout.flow import CheckoutFlow
from scrapebot.config import ProductTarget, Settings
from scrapebot.discord_notifier import DiscordNotifier
from scrapebot.models import (
    AlertLevel,
    CheckoutResult,
    ProductState,
    StockSnapshot,
    StockStatus,
)
from scrapebot.redsky import RedskyClient

logger = logging.getLogger(__name__)

PRICE_PATTERN = re.compile(r"\$\s*(\d+(?:\.\d{2})?)")


class MonitorService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redsky = RedskyClient(settings.redsky, settings.location)
        self.notifier = DiscordNotifier(settings.discord_webhook_url)
        self.browser = BrowserManager(settings)
        self.checkout = CheckoutFlow(settings, self.browser, self.notifier, self.redsky)
        self.states: dict[str, ProductState] = {
            p.tcin: ProductState(product=p) for p in settings.products
        }
        self._page: Page | None = None

    async def _ensure_browser(self) -> None:
        """Start NSTBrowser, authenticate, and keep a page ready for stock checks."""
        if self._page is not None:
            return

        logger.info("Starting NSTBrowser...")
        await self.browser.start()
        self._page = await self.browser.new_page()

        # Pre-authenticate if checkout is enabled and credentials are set
        if (
            self.settings.checkout.enabled
            and self.settings.target_email
            and self.settings.target_password
        ):
            logger.info("Pre-authenticating Target session...")
            self.notifier.send(
                title="🌐 Browser Connected",
                message="NSTBrowser session started. Authenticating...",
                level=AlertLevel.INFO,
            )
            await self.checkout.ensure_logged_in(self._page)
            self.notifier.send(
                title="✅ Authenticated",
                message=f"Logged in and ready for checkout\n🔗 `{self._page.url}`",
                level=AlertLevel.SUCCESS,
            )
        else:
            await self._page.goto(
                "https://www.target.com", wait_until="domcontentloaded", timeout=30000
            )
            await self._page.wait_for_timeout(2000)
            self.notifier.send(
                title="🌐 Browser Connected",
                message="NSTBrowser session started.",
                level=AlertLevel.INFO,
            )

    async def login_only(self) -> None:
        """Open browser for manual login, wait for user confirmation, then close."""
        await self.browser.start()
        page = await self.browser.new_page()
        await self.checkout.ensure_logged_in(page)
        print("Complete any MFA or CAPTCHA in the browser window, then press Enter here...")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await self.browser.stop()

    # ------------------------------------------------------------------
    # Browser-based stock checking
    # ------------------------------------------------------------------

    async def _check_stock_via_browser(self, product: ProductTarget) -> StockSnapshot:
        """Navigate to the product page and read stock status from the DOM.

        Always selects the Shipping fulfillment tab to check shipping availability.
        """
        assert self._page is not None
        page = self._page

        await page.goto(product.url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)  # Let JS render fulfillment section

        # Always select Shipping tab first
        shipping_tab = page.locator('[data-test="fulfillment-cell-shipping"]').first
        if await shipping_tab.count() > 0:
            await shipping_tab.click()
            await page.wait_for_timeout(1500)

        # Determine shipping status
        shipping_status = StockStatus.UNKNOWN
        pickup_status: StockStatus | None = None
        sold_out = False
        shipping_qty: float = 0

        # Check for sold out / OOS indicators
        oos_selectors = [
            'button:has-text("Out of stock")',
            'button:has-text("Sold out")',
            'button:disabled:has-text("Out of stock")',
            '[data-test="outOfStockButton"]',
        ]
        for selector in oos_selectors:
            if await page.locator(selector).count() > 0:
                sold_out = True
                shipping_status = StockStatus.OUT_OF_STOCK
                break

        if not sold_out:
            # With shipping tab selected, check if "Add to cart" is available
            add_cart = page.locator('button:has-text("Add to cart")').first
            if await add_cart.count() > 0 and await add_cart.is_enabled():
                shipping_status = StockStatus.IN_STOCK
            else:
                # Check for "This item isn't available for shipping" or similar
                not_avail = page.locator('text=/not available/i, text=/unavailable/i').first
                if await not_avail.count() > 0:
                    shipping_status = StockStatus.OUT_OF_STOCK
                else:
                    shipping_status = StockStatus.OUT_OF_STOCK

            # Check pickup tab status from the button text (without clicking it)
            pickup_tab = page.locator('[data-test="fulfillment-cell-pickup"]').first
            if await pickup_tab.count() > 0:
                pickup_text = await pickup_tab.inner_text()
                if "unavailable" in pickup_text.lower() or "out of stock" in pickup_text.lower():
                    pickup_status = StockStatus.OUT_OF_STOCK
                else:
                    pickup_status = StockStatus.IN_STOCK
            else:
                # Try the older button format
                pickup_btn = page.locator('button:has-text("Pickup")').first
                if await pickup_btn.count() > 0:
                    pickup_text = await pickup_btn.inner_text()
                    if "unavailable" in pickup_text.lower():
                        pickup_status = StockStatus.OUT_OF_STOCK
                    else:
                        pickup_status = StockStatus.IN_STOCK

            # Check for "Only X left" quantity indicator
            only_left = page.locator('text=/Only \\d+ left/')
            if await only_left.count() > 0:
                text = await only_left.first.inner_text()
                qty_match = re.search(r"Only (\d+) left", text)
                if qty_match:
                    shipping_qty = float(qty_match.group(1))
            elif shipping_status == StockStatus.IN_STOCK:
                shipping_qty = 1  # At least 1 available

        return StockSnapshot(
            tcin=product.tcin,
            shipping_status=shipping_status,
            shipping_quantity=shipping_qty,
            pickup_status=pickup_status,
            sold_out=sold_out,
        )

    # ------------------------------------------------------------------
    # Stock evaluation helpers
    # ------------------------------------------------------------------

    def _is_in_stock(self, snapshot: StockSnapshot) -> bool:
        """Evaluate in-stock per configured stock_types."""
        monitor = self.settings.monitor
        if "shipping" in monitor.stock_types and snapshot.is_available_for_shipping:
            return True
        if "pickup" in monitor.stock_types and snapshot.is_available_for_pickup:
            return True
        return False

    def _status_label(self, snapshot: StockSnapshot) -> str:
        """Derive composite status label."""
        parts = [
            f"sold_out={snapshot.sold_out}",
            f"ship={snapshot.shipping_status.value}",
        ]
        if snapshot.pickup_status is not None:
            parts.append(f"pickup={snapshot.pickup_status.value}")
        else:
            parts.append("pickup=UNKNOWN")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_product(self, state: ProductState) -> None:
        """Poll a single product by navigating to its page and reading stock status."""
        product = state.product
        check_time = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        try:
            snapshot = await self._check_stock_via_browser(product)
        except Exception as exc:
            logger.error("Stock check failed for %s: %s", product.name, exc)
            self.notifier.send(
                title=f"⚠️ Check Failed: {product.name}",
                message=f"Could not load product page\n`{exc}`",
                level=AlertLevel.WARNING,
                url=product.url,
            )
            return

        in_stock = self._is_in_stock(snapshot)
        label = self._status_label(snapshot)
        previous = state.last_snapshot
        state.last_snapshot = snapshot

        is_first_poll = previous is None
        changed = is_first_poll or self._status_label(previous) != label

        # Always log
        logger.info(
            "%s | %s | qty=%.0f | checked at %s",
            product.name,
            label,
            snapshot.shipping_quantity,
            check_time,
        )

        # Send Discord notification on status change or every poll if configured
        if self.settings.monitor.notify_on_every_poll:
            old = "N/A" if is_first_poll else self._status_label(previous)
            self.notifier.send(
                title=f"📋 Stock Check: {product.name}",
                message=(
                    f"**Status:** {label}\n"
                    f"**In Stock:** {'✅ Yes' if in_stock else '❌ No'}\n"
                    f"**Checked at:** {check_time}\n"
                    f"**End of check cycle**"
                ),
                level=AlertLevel.STOCK if in_stock else AlertLevel.INFO,
                url=product.url,
                fields={
                    "Shipping": snapshot.shipping_status.value,
                    "Pickup": snapshot.pickup_status.value if snapshot.pickup_status else "N/A",
                    "Qty": str(int(snapshot.shipping_quantity)),
                },
            )
        elif changed and not is_first_poll:
            self.notifier.stock_change(
                product_name=product.name,
                url=product.url,
                old_status=self._status_label(previous),
                new_status=label,
                quantity=snapshot.shipping_quantity,
            )
        elif is_first_poll:
            # Report initial status on first check
            self.notifier.send(
                title=f"📋 Initial Check: {product.name}",
                message=(
                    f"**Status:** {label}\n"
                    f"**In Stock:** {'✅ Yes' if in_stock else '❌ No'}\n"
                    f"**Checked at:** {check_time}"
                ),
                level=AlertLevel.STOCK if in_stock else AlertLevel.INFO,
                url=product.url,
                fields={
                    "Shipping": snapshot.shipping_status.value,
                    "Pickup": snapshot.pickup_status.value if snapshot.pickup_status else "N/A",
                    "Qty": str(int(snapshot.shipping_quantity)),
                },
            )

        # Trigger checkout when product is in-stock and auto_checkout is enabled
        if (
            in_stock
            and self.settings.checkout.enabled
            and self.settings.checkout.auto_checkout
            and not state.checkout_in_progress
            and not state.checkout_completed
        ):
            await self._trigger_checkout(state)

    async def _trigger_checkout(self, state: ProductState) -> None:
        """Attempt automated checkout for a product that is in stock."""
        product = state.product

        if not self.settings.target_email or not self.settings.target_password:
            logger.warning(
                "Skipping checkout for %s — no Target credentials configured", product.name
            )
            self.notifier.send(
                title=f"⚠️ In Stock: {product.name}",
                message=(
                    "Product is available but **no Target credentials** are set.\n"
                    "Use `/setcredentials` or run `--login` to enable checkout."
                ),
                level=AlertLevel.WARNING,
                url=product.url,
            )
            return

        state.checkout_in_progress = True
        self.notifier.send(
            title=f"🛒 In Stock: {product.name}",
            message=(
                f"Starting checkout "
                f"(max qty {product.max_quantity}, max price ${product.max_price:.2f})."
            ),
            level=AlertLevel.STOCK,
            url=product.url,
        )

        try:
            outcome = await self.checkout.run_checkout(product)
            if outcome.result == CheckoutResult.SUCCESS:
                state.checkout_completed = True
            elif outcome.result == CheckoutResult.OUT_OF_STOCK:
                self.notifier.checkout_error(f"Out of Stock: {outcome.message}", product.url)
            elif outcome.result == CheckoutResult.PRICE_TOO_HIGH:
                pass  # Already notified by CheckoutFlow
            else:
                self.notifier.checkout_error(f"Failed: {outcome.message}", product.url)
        except Exception as exc:
            logger.exception("Checkout crashed for %s", product.name)
            self.notifier.checkout_error(f"Crash: {exc}", product.url)
        finally:
            state.checkout_in_progress = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main polling loop — checks stock by navigating to product pages."""
        await self._ensure_browser()

        self.notifier.send(
            title="ScrapeBot Started",
            message=(
                f"Monitoring **{len(self.states)}** product(s) via browser.\n"
                f"Checkout {'enabled' if self.settings.checkout.enabled else 'disabled'}.\n"
                f"Poll interval: **{self.settings.monitor.poll_interval_seconds}s** "
                f"(+{self.settings.monitor.jitter_seconds}s jitter)"
            ),
            level=AlertLevel.INFO,
        )

        try:
            while True:
                # Check each product sequentially (one browser page)
                for state in self.states.values():
                    if not state.product.enabled:
                        continue
                    await self._poll_product(state)

                    # Small delay between products to avoid rapid-fire navigation
                    if len(self.states) > 1:
                        await asyncio.sleep(random.uniform(1.5, 3.0))

                # End of cycle — wait before next poll round
                base = self.settings.monitor.poll_interval_seconds
                jitter = self.settings.monitor.jitter_seconds
                delay = base + random.uniform(0, jitter)
                logger.info("End of check cycle. Next check in %.1fs", delay)
                await asyncio.sleep(delay)
        finally:
            self.notifier.close()
