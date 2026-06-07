// ============================================================
// Tarjay Auto-Checkout — Target Content Script
// Handles auto-checkout on Target product pages
// ============================================================

(function () {
  'use strict';

  let checkoutConfig = null;

  async function init() {
    const tabId = await getTabId();
    if (!tabId) return;

    // Check if there's a checkout intent for this tab
    const key = `checkout_${tabId}`;
    const data = await chrome.storage.local.get([key]);
    checkoutConfig = data[key];

    if (!checkoutConfig) return;

    // Clean up the stored intent
    await chrome.storage.local.remove([key]);

    console.log(`[Tarjay] Auto-checkout initiated for SKU: ${checkoutConfig.sku}`);

    // Wait for page to fully load
    await waitFor(2000);

    // Start the checkout flow
    await runCheckout();
  }

  async function getTabId() {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: 'get-tab-id' }, (response) => {
        // Fallback: extract from URL or use a different method
        resolve(null);
      });
    });
  }

  async function runCheckout() {
    const behavior = checkoutConfig.behavior;

    // Step 1: Check if we need to log in
    if (behavior.autoLogin && isLoginPage()) {
      await handleLogin();
      return; // Page will redirect after login, content script will re-run
    }

    // Step 2: Wait for fulfillment section to load
    const fulfillment = await waitForElement('[data-test="fulfillment-cell-shipping"], button:has([class*="Qty"])');
    if (!fulfillment) {
      reportFailed('Page did not load fulfillment section');
      return;
    }

    // Step 3: Check if out of stock
    if (isOutOfStock()) {
      chrome.runtime.sendMessage({
        type: 'oos-detected',
        sku: checkoutConfig.sku,
      });
      return;
    }

    // Step 4: Select Shipping if configured
    if (behavior.forceShipping) {
      const shippingTab = document.querySelector('[data-test="fulfillment-cell-shipping"]');
      if (shippingTab) {
        shippingTab.click();
        await waitFor(1500);
      }
    }

    // Step 5: Click Buy Now
    const buyNow = findButton('Buy now');
    if (!buyNow) {
      // Fallback to Add to Cart
      const addToCart = findButton('Add to cart');
      if (addToCart) {
        addToCart.click();
        await waitFor(2000);
        // Navigate to cart
        window.location.href = 'https://www.target.com/co-cart';
      } else {
        reportFailed('No Buy Now or Add to Cart button found');
      }
      return;
    }

    buyNow.click();
    await waitFor(3000);

    // Step 6: Check for re-auth
    if (isLoginPage() || document.querySelector('[class*="password"], button:has-text("Enter your password")')) {
      if (behavior.autoLogin) {
        await handleReauth();
        await waitFor(3000);
        // Try Buy Now again
        const buyNowRetry = findButton('Buy now');
        if (buyNowRetry) {
          buyNowRetry.click();
          await waitFor(3000);
        }
      }
    }

    // Step 7: Wait for checkout panel / Place your order
    const placeOrder = await waitForElement(
      'button[data-test="placeOrderButton"], button:has-text("Place your order")',
      20000
    );

    if (!placeOrder) {
      reportFailed('Place your order button not found');
      return;
    }

    // Step 8: Set quantity if needed (via the select in checkout panel)
    // For now, quantity 1 is default — future enhancement

    // Step 9: Place order (if auto-checkout enabled)
    if (behavior.autoCheckout) {
      console.log('[Tarjay] Clicking Place your order...');
      placeOrder.click();

      // Wait for confirmation
      await waitFor(5000);

      const confirmed = document.querySelector(
        '[data-test="orderConfirmation"], h1:has-text("Order confirmed"), h1:has-text("Thanks")'
      );

      if (confirmed || window.location.href.includes('confirm')) {
        chrome.runtime.sendMessage({
          type: 'checkout-complete',
          sku: checkoutConfig.sku,
        });
      } else {
        reportFailed('Order confirmation not detected');
      }
    }
  }

  // ---- Login Handling ----

  function isLoginPage() {
    return window.location.href.includes('/login') ||
      !!document.querySelector('h1:has-text("Sign in"), text:has-text("Sign in to your account")');
  }

  async function handleLogin() {
    const profile = checkoutConfig.profile;
    if (!profile) return;

    // Check if it's a re-auth page (has "Enter your password" option)
    const passwordMethod = document.querySelector(
      '#password, [role="button"][class*="password"], button'
    );

    // Look for elements containing "Enter your password"
    const allElements = document.querySelectorAll('div, button, span');
    let enterPasswordBtn = null;
    for (const el of allElements) {
      if (el.textContent.includes('Enter your password') && el.closest('button, [role="button"], div[tabindex]')) {
        enterPasswordBtn = el.closest('button, [role="button"], div[tabindex]');
        break;
      }
    }

    if (enterPasswordBtn) {
      enterPasswordBtn.click();
      await waitFor(2000);
    }

    // Fill password
    const passwordInput = await waitForElement('input[type="password"]', 8000);
    if (passwordInput) {
      setInputValue(passwordInput, profile.password);
      await waitFor(500);

      const submitBtn = document.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.click();
    }
  }

  async function handleReauth() {
    await handleLogin();
  }

  // ---- Helpers ----

  function isOutOfStock() {
    const oosTexts = ['out of stock', 'sold out'];
    const buttons = document.querySelectorAll('button');
    for (const btn of buttons) {
      const text = btn.textContent.toLowerCase();
      if (oosTexts.some(t => text.includes(t))) return true;
    }
    return false;
  }

  function findButton(text) {
    const buttons = document.querySelectorAll('button');
    for (const btn of buttons) {
      if (btn.textContent.trim().toLowerCase().includes(text.toLowerCase()) && !btn.disabled) {
        return btn;
      }
    }
    return null;
  }

  function waitFor(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function waitForElement(selector, timeout = 10000) {
    return new Promise((resolve) => {
      const el = document.querySelector(selector);
      if (el) return resolve(el);

      const observer = new MutationObserver(() => {
        const el = document.querySelector(selector);
        if (el) {
          observer.disconnect();
          resolve(el);
        }
      });

      observer.observe(document.body, { childList: true, subtree: true });

      setTimeout(() => {
        observer.disconnect();
        resolve(null);
      }, timeout);
    });
  }

  function setInputValue(input, value) {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value'
    ).set;
    nativeInputValueSetter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function reportFailed(reason) {
    console.log(`[Tarjay] Checkout failed: ${reason}`);
    chrome.runtime.sendMessage({
      type: 'checkout-failed',
      sku: checkoutConfig?.sku || 'unknown',
      reason,
    });
  }

  // ---- Tab ID helper ----
  // The content script needs its own tab ID to look up checkout config
  // We use a workaround: the background script stores config by tab ID when creating the tab
  // But content scripts can't easily get their tab ID. Instead, we'll use URL matching.

  async function initWithUrlMatch() {
    // Extract TCIN from current URL
    const urlMatch = window.location.pathname.match(/A-(\d+)/);
    if (!urlMatch) return;

    const sku = urlMatch[1];

    // Check all pending checkouts
    const allData = await chrome.storage.local.get(null);
    for (const [key, value] of Object.entries(allData)) {
      if (key.startsWith('checkout_') && value.sku === sku) {
        checkoutConfig = value;
        await chrome.storage.local.remove([key]);
        console.log(`[Tarjay] Found checkout config for SKU ${sku}`);
        await runCheckout();
        return;
      }
    }
  }

  // Initialize
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWithUrlMatch);
  } else {
    initWithUrlMatch();
  }
})();
