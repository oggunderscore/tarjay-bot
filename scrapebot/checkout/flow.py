from __future__ import annotations

import asyncio
import logging
import re
import time

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from scrapebot.checkout.browser import BrowserManager
from scrapebot.config import ProductTarget, Settings
from scrapebot.discord_notifier import DiscordNotifier
from scrapebot.models import AlertLevel, CheckoutOutcome, CheckoutResult, StockStatus
from scrapebot.redsky import RedskyClient

logger = logging.getLogger(__name__)

PRICE_PATTERN = re.compile(r"\$?\s*(\d+(?:\.\d{2})?)")


class CheckoutFlow:
    def __init__(
        self,
        settings: Settings,
        browser: BrowserManager,
        notifier: DiscordNotifier,
        redsky: RedskyClient,
    ) -> None:
        self.settings = settings
        self.browser = browser
        self.notifier = notifier
        self.redsky = redsky
        self.checkout_cfg = settings.checkout
        self._logged_in = False

    async def ensure_logged_in(self, page: Page) -> None:
        # Skip if we already verified login this session
        if self._logged_in:
            return

        await page.goto("https://www.target.com/account", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await self._dismiss_popups(page)

        # Check if already logged in by looking for account indicators
        logged_in_selectors = [
            '[data-test="accountNav"]',
            '[data-test="@web/AccountLink"]',
            'a[href*="/account"]:has-text("Account")',
            'span:has-text("Hi,")',
        ]
        for selector in logged_in_selectors:
            if await page.locator(selector).count() > 0:
                logger.info("Already logged in to Target")
                self._logged_in = True
                return

        # If URL didn't redirect to login, check if we're on a signed-in page
        if "/account" in page.url and "login" not in page.url:
            logger.info("Already logged in to Target (on account page)")
            self._logged_in = True
            return

        await self._do_login(page)

    async def _handle_reauth_if_needed(self, page: Page) -> bool:
        """Check if we hit a re-authentication page and handle it.

        Target sometimes asks to re-authenticate mid-flow (e.g., after Buy Now).
        This shows "Sign in to your account" with auth method choices.
        Returns True if re-auth was handled, False if not on a login page.
        """
        # Check if we're on a login/sign-in page
        if "login" not in page.url.lower():
            # Also check for sign-in content on the page itself (could be in a modal)
            sign_in_text = page.locator('text=/Sign in to your account/i').first
            password_method = page.locator(
                'div:has-text("Enter your password"), button:has-text("Enter your password")'
            ).first
            if await sign_in_text.count() == 0 and await password_method.count() == 0:
                return False

        logger.info("Re-authentication required — handling login flow")
        self.notifier.checkout_progress("Re-Auth", "Session expired — signing in again...")
        await self._do_login(page)
        return True

    async def _do_login(self, page: Page) -> None:
        """Perform the full login flow — handles both fresh login and re-auth."""
        email = self.settings.target_email
        password = self.settings.target_password
        if not email or not password:
            raise RuntimeError(
                "Not logged in. Run `python -m scrapebot --login` once, "
                "or set TARGET_EMAIL and TARGET_PASSWORD in .env"
            )

        # Check if we're already on a sign-in page with auth method choices
        # (re-auth flow — no email needed, just pick password method)
        password_method = page.locator(
            '#password, div[role="button"]:has-text("password"), '
            'button:has-text("Enter your password")'
        ).first

        if await password_method.count() > 0 and await password_method.is_visible():
            # We're on the re-auth page — click "Enter your password" directly
            logger.info("On re-auth page — clicking 'Enter your password'")
            await password_method.click()
            await page.wait_for_timeout(2000)
        else:
            # Fresh login — need to enter email first
            self.notifier.checkout_progress("Login", "Signing in to Target account")

            # Navigate to login if not already there
            if "login" not in page.url.lower():
                login_url = (
                    "https://www.target.com/login?client_id=ecom-web-1.0.0"
                    "&ui_namespace=ui-default&back_button_action=browser"
                    "&keep_me_signed_in=true&kmsi_default=false"
                    "&actions=create_session_request_username&signin_amr=true"
                )
                await page.goto(login_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("networkidle", timeout=30000)
                await page.wait_for_timeout(3000)
                await self._dismiss_popups(page)

            # Enter email
            email_selectors = [
                '#username',
                'input#username',
                'input[name="username"]',
                'input[type="email"]',
                'input[id*="user"]',
                'input[autocomplete="username"]',
            ]
            email_input = None
            for selector in email_selectors:
                loc = page.locator(selector).first
                try:
                    await loc.wait_for(state="visible", timeout=5000)
                    email_input = loc
                    break
                except PlaywrightTimeout:
                    continue

            if email_input is None:
                fallback = page.locator('input[type="text"], input[type="email"]').first
                try:
                    await fallback.wait_for(state="visible", timeout=10000)
                    email_input = fallback
                except PlaywrightTimeout:
                    # Maybe we're actually on the auth method page after all
                    password_method = page.locator(
                        '#password, div[role="button"]:has-text("password"), '
                        'button:has-text("Enter your password")'
                    ).first
                    if await password_method.count() > 0:
                        await password_method.click()
                        await page.wait_for_timeout(2000)
                        email_input = None  # Skip email, go to password
                    else:
                        await self.browser.screenshot(page, "login-no-email-input")
                        raise RuntimeError("Could not find email input on Target login page.")

            if email_input is not None:
                await email_input.fill(email)
                await page.wait_for_timeout(500)

                # Click continue/submit after email
                continue_selectors = [
                    'button[type="submit"]',
                    'button:has-text("Continue")',
                    'button:has-text("Next")',
                ]
                for selector in continue_selectors:
                    btn = page.locator(selector).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        break

                await page.wait_for_timeout(3000)
                await self._dismiss_popups(page)

                # Click "Enter your password" method button
                password_method_selectors = [
                    '#password',
                    'div[role="button"]:has-text("password")',
                    'button:has-text("Enter your password")',
                ]
                for selector in password_method_selectors:
                    loc = page.locator(selector).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click()
                        await page.wait_for_timeout(2000)
                        break

        # Now enter the password
        password_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            'input[autocomplete="current-password"]',
        ]
        password_input = None
        for selector in password_selectors:
            loc = page.locator(selector).first
            try:
                await loc.wait_for(state="visible", timeout=8000)
                password_input = loc
                break
            except PlaywrightTimeout:
                continue

        if password_input is None:
            await self.browser.screenshot(page, "login-no-password-input")
            raise RuntimeError("Could not find password input on Target login page.")

        await password_input.fill(password)
        await page.wait_for_timeout(500)

        # Click sign in / submit
        submit_selectors = [
            'button[type="submit"]',
            'button[data-test="login-submit"]',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
        ]
        for selector in submit_selectors:
            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                break

        await page.wait_for_load_state("networkidle", timeout=30000)
        await page.wait_for_timeout(3000)
        await self._dismiss_popups(page)
        logger.info("Login submitted — current URL: %s", page.url)
        self._logged_in = True
        self.notifier.checkout_progress(
            "Login Complete",
            f"Successfully signed in\n🔗 `{page.url}`",
        )

    async def run_checkout(self, product: ProductTarget) -> CheckoutOutcome:
        page = await self.browser.new_page()

        try:
            await self.ensure_logged_in(page)
            quantities = [product.max_quantity]
            if product.max_quantity > 1:
                quantities.append(1)

            for qty in quantities:
                self.notifier.checkout_progress(
                    "Starting",
                    f"Attempting checkout for **{product.name}** with quantity **{qty}**",
                    product.url,
                )
                outcome = await self._checkout_with_quantity(page, product, qty)
                if outcome.result in {
                    CheckoutResult.SUCCESS,
                    CheckoutResult.OUT_OF_STOCK,
                    CheckoutResult.PRICE_TOO_HIGH,
                }:
                    return outcome
                if qty == product.max_quantity and product.max_quantity > 1:
                    self.notifier.checkout_progress(
                        "Retry",
                        f"Max quantity failed ({outcome.message}). Retrying with quantity 1.",
                        product.url,
                    )

            return CheckoutOutcome(
                result=CheckoutResult.FAILED,
                message="Checkout failed after all quantity attempts",
            )
        except Exception as exc:
            await self.browser.screenshot(page, f"checkout-error-{product.tcin}")
            self.notifier.checkout_error(f"Exception: {exc}", product.url)
            raise

    async def _checkout_with_quantity(
        self,
        page: Page,
        product: ProductTarget,
        quantity: int,
    ) -> CheckoutOutcome:
        await page.goto(product.url, wait_until="domcontentloaded")
        await self._dismiss_popups(page)
        self.notifier.checkout_progress(
            "Product Page",
            f"Navigated to **{product.name}**\n🔗 `{page.url}`",
            product.url,
        )

        if not await self._is_pdp_in_stock(page):
            return CheckoutOutcome(
                result=CheckoutResult.OUT_OF_STOCK,
                message="Product page shows out of stock",
            )

        price = await self._read_page_price(page)
        if price is not None and price > product.max_price:
            msg = f"Price ${price:.2f} exceeds max ${product.max_price:.2f}"
            self.notifier.checkout_error(msg, product.url)
            return CheckoutOutcome(result=CheckoutResult.PRICE_TOO_HIGH, message=msg)

        if price is not None:
            self.notifier.checkout_progress(
                "Price Check",
                f"Price **${price:.2f}** is within max ${product.max_price:.2f} ✅",
                product.url,
            )

        await self._set_quantity(page, quantity)

        # Use "Buy now" to skip the cart and go straight to checkout
        buy_now = page.locator('button:has-text("Buy now")').first
        if await buy_now.count() > 0 and await buy_now.is_enabled():
            self.notifier.checkout_progress(
                "Buy Now",
                f"Clicking **Buy now** — skipping cart\n🔗 `{page.url}`",
                product.url,
            )
            await buy_now.click()

            # Check if we got redirected to a sign-in page
            await page.wait_for_timeout(3000)
            reauthed = await self._handle_reauth_if_needed(page)
            if reauthed:
                # After re-auth, navigate back to the product page and retry Buy Now
                await page.goto(product.url, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                # Select shipping tab again
                shipping_tab = page.locator('[data-test="fulfillment-cell-shipping"]').first
                if await shipping_tab.count() > 0:
                    await shipping_tab.click()
                    await page.wait_for_timeout(1500)

                # Click Buy Now again
                buy_now = page.locator('button:has-text("Buy now")').first
                try:
                    await buy_now.wait_for(state="visible", timeout=10000)
                    await buy_now.click()
                    self.notifier.checkout_progress(
                        "Buy Now",
                        f"Re-clicking **Buy now** after re-auth\n🔗 `{page.url}`",
                        product.url,
                    )
                except PlaywrightTimeout:
                    return CheckoutOutcome(
                        result=CheckoutResult.FAILED,
                        message="Buy Now button not found after re-auth",
                    )

                # Wait again for potential second re-auth (shouldn't happen but be safe)
                await page.wait_for_timeout(3000)

            # Wait for the side panel / checkout to load (can take up to 15s)
            # Look for "Place your order" OR any checkout-related content
            place_order_btn = page.locator(
                'button[data-test="placeOrderButton"], button:has-text("Place your order")'
            ).first
            panel_loaded = False
            try:
                await place_order_btn.wait_for(state="visible", timeout=20000)
                panel_loaded = True
            except PlaywrightTimeout:
                # Panel may have loaded with different content — check for other indicators
                checkout_indicators = page.locator(
                    'text=/Place your order/i, text=/order summary/i, '
                    'text=/shipping address/i, text=/payment/i'
                ).first
                try:
                    await checkout_indicators.wait_for(state="visible", timeout=5000)
                    panel_loaded = True
                except PlaywrightTimeout:
                    pass

            # Adjust quantity in the checkout panel using + button
            if quantity > 1:
                await self._set_checkout_quantity(page, quantity)

            self.notifier.checkout_progress(
                "Checkout Panel",
                f"Buy now panel {'loaded ✅' if panel_loaded else 'timeout ⚠️'} (qty {quantity})\n🔗 `{page.url}`",
                product.url,
            )

            # DRY RUN: We reached the checkout panel — report success and move on
            if self.checkout_cfg.dry_run:
                msg = (
                    f"🧪 **DRY RUN** — reached checkout panel via Buy Now "
                    f"(qty {quantity})\n"
                    f"🔗 `{page.url}`\n"
                    f"Order was **NOT** placed. Disable `dry_run` to place real orders."
                )
                self.notifier.send(
                    title=f"✅ Dry Run Complete: {product.name}",
                    message=msg,
                    level=AlertLevel.SUCCESS,
                    url=product.url,
                )
                logger.info("DRY RUN: Checkout panel reached via Buy Now — stopping.")
                return CheckoutOutcome(
                    result=CheckoutResult.SUCCESS,
                    message="Dry run: Checkout panel reached (Buy Now)",
                    quantity=quantity,
                )

            # Not dry run — proceed to place order loop
            return await self._place_order_loop(page, product, quantity)
        else:
            # Fallback: Add to cart then navigate to checkout
            added = await self._add_to_cart(page)
            if not added:
                self.notifier.checkout_progress(
                    "Add to Cart",
                    f"❌ Could not add to cart\n🔗 `{page.url}`",
                    product.url,
                )
                if quantity > 1:
                    return CheckoutOutcome(
                        result=CheckoutResult.FAILED,
                        message="Could not add max quantity to cart",
                    )
                return CheckoutOutcome(
                    result=CheckoutResult.OUT_OF_STOCK,
                    message="Add to cart unavailable",
                )

            self.notifier.checkout_progress(
                "Cart",
                f"Added **{quantity}** to cart ✅\nNavigating to cart...",
                product.url,
            )
            await page.goto("https://www.target.com/co-cart", wait_until="domcontentloaded")
            await self._dismiss_popups(page)
            self.notifier.checkout_progress(
                "Cart Page",
                f"On cart page\n🔗 `{page.url}`",
                product.url,
            )

            checkout_btn = page.locator(
                'button[data-test="checkout-button"], button:has-text("Check out")'
            ).first
            if await checkout_btn.count() == 0:
                self.notifier.checkout_progress(
                    "Checkout",
                    f"❌ Checkout button not found on cart page\n🔗 `{page.url}`",
                    product.url,
                )
                return CheckoutOutcome(result=CheckoutResult.FAILED, message="Checkout button not found")
            await checkout_btn.click()
            await page.wait_for_load_state("domcontentloaded")
            self.notifier.checkout_progress(
                "Checkout",
                f"On checkout page\n🔗 `{page.url}`",
                product.url,
            )

        await self._select_saved_address_and_payment(page)
        self.notifier.checkout_progress(
            "Payment",
            f"Address & payment selected\n🔗 `{page.url}`",
            product.url,
        )
        return await self._place_order_loop(page, product, quantity)

    async def _is_pdp_in_stock(self, page: Page) -> bool:
        # Wait for the fulfillment region to render
        try:
            await page.locator('[data-test="fulfillment-cell-shipping"], button:has-text("Add to cart")').first.wait_for(
                state="visible", timeout=8000
            )
        except PlaywrightTimeout:
            pass

        # Select Shipping tab first
        shipping_tab = page.locator('[data-test="fulfillment-cell-shipping"]').first
        if await shipping_tab.count() > 0:
            await shipping_tab.click()
            await asyncio.sleep(1.5)

        # Check for sold out indicators
        oos_selectors = [
            'button:has-text("Out of stock")',
            '[data-test="outOfStockButton"]',
            'button:disabled:has-text("Out of stock")',
            'button:has-text("Sold out")',
        ]
        for selector in oos_selectors:
            if await page.locator(selector).count() > 0:
                return False

        # With shipping selected, check if Add to cart is available
        add_cart = page.locator('button:has-text("Add to cart")').first
        if await add_cart.count() > 0 and await add_cart.is_enabled():
            return True

        # Check for Buy now as fallback
        buy_now = page.locator('button:has-text("Buy now")').first
        if await buy_now.count() > 0 and await buy_now.is_enabled():
            return True

        return False

    async def _read_page_price(self, page: Page) -> float | None:
        selectors = [
            '[data-test="product-price"]',
            '[data-test="current-price"]',
            'span[data-test="product-price"]',
            '[data-test="product-price-sale"]',
        ]
        for selector in selectors:
            loc = page.locator(selector).first
            if await loc.count() == 0:
                continue
            text = (await loc.inner_text()).strip()
            match = PRICE_PATTERN.search(text.replace(",", ""))
            if match:
                return float(match.group(1))

        # Fallback: look for price pattern near the product title area
        # Target often puts price in a plain div without data-test
        price_area = page.locator('h1').first
        if await price_area.count() > 0:
            # Get the parent section and look for dollar amounts
            section = page.locator('h1 ~ *').first
            if await section.count() > 0:
                try:
                    text = await page.locator('text=/\\$\\d+\\.\\d{2}/').first.inner_text(timeout=3000)
                    match = PRICE_PATTERN.search(text.replace(",", ""))
                    if match:
                        return float(match.group(1))
                except Exception:
                    pass
        return None

    async def _set_quantity(self, page: Page, quantity: int) -> None:
        """Set quantity on the product page using the Qty custom dropdown."""
        if quantity <= 1:
            return

        # Target uses a custom button dropdown: click "Qty" button to open, then pick option
        qty_btn = page.locator('button:has(span:has-text("Qty"))').first
        if await qty_btn.count() == 0:
            qty_btn = page.locator('button:has-text("Qty")').first

        if await qty_btn.count() > 0:
            await qty_btn.click()
            await asyncio.sleep(1)

            # The dropdown options appear as a listbox or list items
            # Click the option matching the desired quantity
            option = page.locator(f'[role="option"]:text-is("{quantity}")').first
            if await option.count() == 0:
                option = page.locator(f'li:text-is("{quantity}")').first
            if await option.count() == 0:
                option = page.locator(f'[data-value="{quantity}"]').first
            if await option.count() == 0:
                # Try any element that is exactly the number text
                option = page.get_by_text(str(quantity), exact=True).first

            if await option.count() > 0:
                await option.click()
                await asyncio.sleep(0.5)
                logger.info("Set product page quantity to %d", quantity)
                return
            else:
                # Close the dropdown if we couldn't find the option
                await page.keyboard.press("Escape")
                logger.warning("Opened Qty dropdown but could not find option %d", quantity)
        else:
            logger.warning("Could not find Qty button on product page")

    async def _set_checkout_quantity(self, page: Page, quantity: int) -> None:
        """Adjust quantity in the checkout side panel using the select dropdown."""
        if quantity <= 1:
            return

        # Target's Buy Now panel uses a <select> dropdown for quantity
        qty_select_selectors = [
            'select[aria-label*="quantity" i]',
            'select[aria-label*="Quantity" i]',
            'select[data-test*="quantity" i]',
            'select[name*="quantity" i]',
            'select',  # fallback: any select in the panel area
        ]

        qty_select = None
        for selector in qty_select_selectors:
            loc = page.locator(selector).first
            try:
                await loc.wait_for(state="visible", timeout=5000)
                qty_select = loc
                logger.info("Found quantity select with: %s", selector)
                break
            except PlaywrightTimeout:
                continue

        if qty_select is not None:
            # Click the select to open it, then use selectOption (most React-compatible way)
            try:
                await qty_select.select_option(value=str(quantity))
            except Exception:
                # If value doesn't match, try by label
                try:
                    await qty_select.select_option(label=str(quantity))
                except Exception:
                    # Last resort: JS approach
                    await qty_select.evaluate(
                        "(el, val) => { el.value = val; el.dispatchEvent(new Event('change', {bubbles: true})); }",
                        str(quantity),
                    )
            await page.wait_for_timeout(1500)
            logger.info("Set checkout quantity to %d via select dropdown", quantity)
            self.notifier.checkout_progress(
                "Quantity",
                f"Set quantity to **{quantity}** ✅",
            )
            return

        # Fallback: try the + (Increment) button
        plus_btn = page.locator('button[aria-label="Increment"]').first
        try:
            await plus_btn.wait_for(state="visible", timeout=5000)
            for i in range(quantity - 1):
                await plus_btn.click()
                await asyncio.sleep(1.0)
            logger.info("Set checkout quantity to %d via + button", quantity)
            self.notifier.checkout_progress(
                "Quantity",
                f"Set quantity to **{quantity}** ✅",
            )
        except PlaywrightTimeout:
            logger.warning("Could not find quantity control in checkout panel")
            self.notifier.checkout_progress(
                "Quantity",
                f"⚠️ Could not change quantity to {quantity}",
            )

    async def _add_to_cart(self, page: Page) -> bool:
        # Always select Shipping tab first (use the data-test selector from Target's DOM)
        shipping_tab = page.locator('[data-test="fulfillment-cell-shipping"]').first
        if await shipping_tab.count() > 0:
            await shipping_tab.click()
            await asyncio.sleep(1.5)

        # Now find the Add to cart button
        add_to_cart_selectors = [
            'button:has-text("Add to cart")',
            'button[data-test="shipItButton"]',
            'button[data-test="shippingButton"]',
        ]
        btn = None
        for selector in add_to_cart_selectors:
            loc = page.locator(selector).first
            if await loc.count() > 0 and await loc.is_enabled():
                btn = loc
                break

        if btn is None:
            return False

        await btn.click()
        await asyncio.sleep(2)
        await self._dismiss_popups(page)

        # Wait for cart confirmation (modal or redirect)
        view_cart = page.locator(
            'a[data-test="cart-link"], button:has-text("View cart"), '
            'a:has-text("View cart"), a:has-text("View cart & check out")'
        )
        try:
            await view_cart.first.wait_for(state="visible", timeout=8000)
        except PlaywrightTimeout:
            pass
        return True

    async def _select_saved_address_and_payment(self, page: Page) -> None:
        await self._dismiss_popups(page)

        if self.checkout_cfg.prefer_saved_address:
            saved_addr = page.locator(
                'button[data-test="saved-address"], input[type="radio"][name*="address"]'
            ).first
            if await saved_addr.count() > 0:
                await saved_addr.click()
                await asyncio.sleep(0.5)

        continue_btn = page.locator(
            'button[data-test="continueButton"], button:has-text("Continue"), button:has-text("Save and continue")'
        ).first
        if await continue_btn.count() > 0 and await continue_btn.is_enabled():
            await continue_btn.click()
            await page.wait_for_load_state("domcontentloaded")
            await self._dismiss_popups(page)

        if self.checkout_cfg.prefer_saved_payment:
            saved_pay = page.locator(
                'input[type="radio"][name*="payment"], button[data-test="saved-payment"]'
            ).first
            if await saved_pay.count() > 0:
                await saved_pay.click()
                await asyncio.sleep(0.5)

        continue_btn = page.locator(
            'button[data-test="continueButton"], button:has-text("Continue"), button:has-text("Save and continue")'
        ).first
        if await continue_btn.count() > 0 and await continue_btn.is_enabled():
            await continue_btn.click()
            await page.wait_for_load_state("domcontentloaded")

    async def _place_order_loop(
        self,
        page: Page,
        product: ProductTarget,
        quantity: int,
    ) -> CheckoutOutcome:
        attempts = 0
        loop_started = time.monotonic()
        stuck_notified = False

        while attempts < self.checkout_cfg.place_order_max_attempts:
            attempts += 1
            await self._dismiss_popups(page)

            if await self._is_order_success(page):
                msg = f"Order confirmed (qty {quantity}, attempt {attempts})"
                self.notifier.checkout_success(product.name, quantity, attempts, product.url)
                return CheckoutOutcome(
                    result=CheckoutResult.SUCCESS,
                    message=msg,
                    quantity=quantity,
                )

            place_order = page.locator(
                'button[data-test="placeOrderButton"], button:has-text("Place your order")'
            ).first
            if await place_order.count() > 0 and await place_order.is_enabled():
                # DRY RUN: Stop here — we confirmed we can reach "Place your order"
                if self.checkout_cfg.dry_run:
                    msg = (
                        f"🧪 **DRY RUN** — reached \"Place your order\" button "
                        f"(qty {quantity}, attempt {attempts})\n"
                        f"🔗 `{page.url}`\n"
                        f"Order was **NOT** placed. Disable `dry_run` to place real orders."
                    )
                    self.notifier.send(
                        title=f"✅ Dry Run Complete: {product.name}",
                        message=msg,
                        level=AlertLevel.SUCCESS,
                        url=product.url,
                    )
                    logger.info("DRY RUN: Place your order button found — stopping. URL: %s", page.url)
                    return CheckoutOutcome(
                        result=CheckoutResult.SUCCESS,
                        message=f"Dry run: Place your order button found (attempt {attempts})",
                        quantity=quantity,
                    )

                self.notifier.checkout_progress(
                    "Place Order",
                    f"Clicking Place your order (attempt {attempts})\n🔗 `{page.url}`",
                    product.url,
                )
                await place_order.click()
            else:
                retry = page.locator(
                    'button:has-text("Try again"), button:has-text("Continue")'
                ).first
                if await retry.count() > 0 and await retry.is_enabled():
                    await retry.click()

            await asyncio.sleep(self.checkout_cfg.place_order_retry_seconds)

            elapsed = time.monotonic() - loop_started
            if elapsed >= self.checkout_cfg.stuck_alert_seconds and not stuck_notified:
                stuck_notified = True
                await self.browser.screenshot(page, f"stuck-{product.tcin}")
                self.notifier.checkout_stuck(int(elapsed), product.url)

        return CheckoutOutcome(
            result=CheckoutResult.FAILED,
            message=f"Exceeded max place-order attempts ({attempts})",
            quantity=quantity,
        )

    async def _is_order_success(self, page: Page) -> bool:
        url = page.url.lower()
        if "confirm" in url or "thank" in url or "order-confirmation" in url:
            return True

        success_selectors = [
            '[data-test="orderConfirmation"]',
            'h1:has-text("Thanks for shopping")',
            'h1:has-text("Order confirmed")',
            "text=We'll send a confirmation email",
        ]
        for selector in success_selectors:
            if await page.locator(selector).count() > 0:
                return True
        return False

    async def _dismiss_popups(self, page: Page) -> None:
        selectors = [
            'button[aria-label="close"]',
            'button[data-test="modal-close-button"]',
            'button:has-text("Close")',
            'button:has-text("No thanks")',
            'button:has-text("Not now")',
            'button:has-text("Maybe later")',
            'button:has-text("Dismiss")',
            'button:has-text("Got it")',
            "#overlayClose",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            count = await loc.count()
            for idx in range(min(count, 3)):
                try:
                    button = loc.nth(idx)
                    if await button.is_visible():
                        await button.click(timeout=1500)
                        await asyncio.sleep(0.2)
                except PlaywrightTimeout:
                    continue
                except Exception:
                    continue
