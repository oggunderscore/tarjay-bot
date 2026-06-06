# Implementation Plan: ScrapeBot

## Overview

Implement a Target.com stock monitoring and auto-checkout application using Python asyncio. The system polls Target's Redsky fulfillment API, sends Discord webhook notifications on stock changes, and performs automated browser-based checkout via Playwright. Implementation is structured around configuration loading, the Redsky client, monitor polling loop, Discord notifier, browser management, checkout flow, and CLI entry point.

## Tasks

- [x] 1. Set up project structure, dependencies, and data models
  - [x] 1.1 Create data model definitions and enums
    - Create `scrapebot/models.py` with `StockStatus`, `StockSnapshot`, `CheckoutResult`, `CheckoutOutcome`, `AlertLevel`, `ProductState` dataclasses and enums
    - Define `COLORS` mapping for alert levels
    - Implement `StockSnapshot.is_available_for_shipping` and `is_available_for_pickup` properties
    - _Requirements: 1.3, 1.5, 3.2, 5.7_

  - [x] 1.2 Create configuration dataclasses and loader
    - Define `ProductTarget`, `MonitorConfig`, `LocationConfig`, `RedskyConfig`, `BrowserConfig`, `CheckoutConfig`, `Settings` dataclasses in `scrapebot/config.py`
    - Implement `load_settings(config_path, products_path, monitor_only)` function
    - Implement TCIN extraction from URLs (preselect query param priority, then `/A-{digits}` path)
    - Load secrets from environment variables via `python-dotenv`
    - Apply defaults for omitted optional keys
    - Raise `FileNotFoundError` for missing config files referencing `.example` templates
    - Raise `ValueError` for missing TCINs or empty product lists
    - Filter products by `enabled` flag (default true)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [ ]* 1.3 Write property tests for TCIN extraction and config loading
    - **Property 1: TCIN extraction round-trip**
    - **Property 9: Product enabled filtering**
    - **Validates: Requirements 6.6, 6.2**

  - [x] 1.4 Set up test infrastructure
    - Create `tests/conftest.py` with shared fixtures and Hypothesis profile (max_examples=100)
    - Add test dependencies to `requirements.txt`: pytest>=8.0, hypothesis>=6.100, pytest-asyncio>=0.23, respx>=0.21
    - _Requirements: All (testing infrastructure)_

- [x] 2. Implement Redsky API client
  - [x] 2.1 Implement RedskyClient class
    - Create `scrapebot/redsky.py` with `RedskyClient.__init__`, `get_stock`, and `close` methods
    - Construct fulfillment URL with location parameters and API key
    - Make synchronous HTTP GET with 20-second timeout via `httpx.Client`
    - Parse JSON response into `StockSnapshot` dataclass
    - Map unrecognized status values to `StockStatus.UNKNOWN`
    - _Requirements: 1.1, 1.3, 1.6_

  - [ ]* 2.2 Write property tests for Redsky response parsing
    - **Property 8: Redsky response parsing with UNKNOWN mapping**
    - **Validates: Requirements 1.3**

  - [ ]* 2.3 Write unit tests for RedskyClient
    - Test successful response parsing
    - Test non-2xx response raises `httpx.HTTPStatusError`
    - Test timeout handling
    - Use `respx` for HTTP mocking
    - _Requirements: 1.3, 1.4, 1.6_

- [x] 3. Implement Discord notifier
  - [x] 3.1 Implement DiscordNotifier class
    - Create `scrapebot/discord_notifier.py` with `DiscordNotifier` class
    - Implement `send()` method constructing Discord embed objects with color, timestamp, fields
    - Implement truncation logic: title ≤ 256, description ≤ 4096, field values ≤ 1024 characters
    - Implement convenience methods: `stock_change`, `checkout_progress`, `checkout_success`, `checkout_stuck`, `checkout_error`
    - Fall back to logging when webhook URL is None
    - Use 15-second HTTP timeout
    - Log errors on failed delivery without retrying
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [ ]* 3.2 Write property tests for Discord embed truncation
    - **Property 7: Discord embed truncation**
    - **Validates: Requirements 3.1**

  - [ ]* 3.3 Write unit tests for DiscordNotifier
    - Test color mapping per alert level
    - Test logging fallback when webhook URL is None
    - Test HTTP error handling
    - _Requirements: 3.2, 3.8, 3.9_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement monitor service
  - [x] 5.1 Implement MonitorService class
    - Create `scrapebot/monitor.py` with `MonitorService` class
    - Implement `run()` async method with polling loop using `asyncio.gather` for concurrent product checks
    - Maintain `ProductState` per TCIN tracking last snapshot, checkout_in_progress, checkout_completed
    - Add random jitter (uniform 0 to `jitter_seconds`) to each poll cycle
    - Implement stock change detection by comparing status labels between polls
    - Suppress change notification on first poll (store as baseline)
    - Support `notify_on_every_poll` option
    - Trigger checkout when product transitions to in-stock and checkout is enabled
    - Prevent duplicate checkout attempts with flags
    - Handle API errors gracefully (log + notify + continue)
    - Implement `login_only()` async method
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 9.1, 9.2, 9.3, 9.4_

  - [ ]* 5.2 Write property tests for stock evaluation and change detection
    - **Property 3: Status label sensitivity**
    - **Property 4: In-stock evaluation logic**
    - **Validates: Requirements 2.2, 1.5**

  - [ ]* 5.3 Write unit tests for MonitorService
    - Test first-poll suppression of change notification
    - Test monitor-only mode does not trigger checkout
    - Test concurrent polling of multiple products
    - Test error handling continues polling
    - _Requirements: 2.4, 9.2, 9.3, 1.4_

- [x] 6. Implement browser manager
  - [x] 6.1 Implement BrowserManager class
    - Create `scrapebot/checkout/browser.py` with `BrowserManager` class
    - Implement `start()` launching persistent context with `user_data_dir`
    - Support Chrome channel with Chromium fallback
    - Support NSTBrowser CDP connection (reuse first context or create new)
    - Implement `stop()` to close browser context
    - Implement `new_page()` for page creation
    - Implement `screenshot()` saving full-page PNG with TCIN and timestamp in filename
    - Create screenshot directory on demand
    - Configure viewport, locale, timeout, slow-mo settings
    - _Requirements: 7.1, 7.5, 7.6, 7.7, 7.8, 10.1, 10.2, 10.3_

  - [ ]* 6.2 Write unit tests for BrowserManager
    - Test persistent context configuration
    - Test CDP connection path
    - Test screenshot filename format
    - Test screenshot disabled behavior
    - _Requirements: 7.1, 7.5, 10.2, 10.3_

- [x] 7. Implement checkout flow
  - [x] 7.1 Implement CheckoutFlow class - login and navigation
    - Create `scrapebot/checkout/flow.py` with `CheckoutFlow` class
    - Implement `ensure_logged_in()`: detect account element, auto-login if credentials set, raise RuntimeError if login fails
    - Implement product page navigation with 30-second timeout
    - Implement stock indicator checking on product page
    - Implement popup dismissal (up to 3 close-button clicks, 1500ms timeout each)
    - _Requirements: 4.1, 4.10, 7.3, 7.4_

  - [x] 7.2 Implement CheckoutFlow class - price check and add to cart
    - Implement price reading from product page using dollar amount regex
    - Implement price guard: abort with PRICE_TOO_HIGH if parsed price > max_price
    - Proceed without blocking if price element missing or unparseable
    - Implement quantity setting via dropdown or increment button
    - Implement add-to-cart with quantity fallback (max_quantity → 1)
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 7.3 Implement CheckoutFlow class - cart navigation and Place Order Loop
    - Implement cart → checkout → address/payment selection navigation
    - Implement Place Order Loop: click button every `place_order_retry_seconds`
    - Check for order confirmation via URL patterns ("confirm", "thank", "order-confirmation") and DOM selectors
    - Poll Redsky API each iteration for OOS detection (shipping OUT_OF_STOCK AND sold_out = true → abort)
    - Handle "Try again" and "Continue" buttons
    - Implement stuck alert after `stuck_alert_seconds` with screenshot (once per attempt)
    - Abort after `place_order_max_attempts`
    - Handle Redsky API failures during loop gracefully (log + continue)
    - Return `CheckoutOutcome` with result enum and message
    - _Requirements: 4.6, 4.7, 4.8, 4.9, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 10.4_

  - [ ]* 7.4 Write property tests for price parsing and checkout logic
    - **Property 2: Price parsing correctness**
    - **Property 5: OOS abort condition**
    - **Property 6: Price guard enforcement**
    - **Property 10: Order confirmation URL detection**
    - **Validates: Requirements 8.4, 5.7, 4.2, 8.5, 5.2**

  - [ ]* 7.5 Write unit tests for CheckoutFlow
    - Test quantity fallback sequence (max_quantity → 1)
    - Test price guard abort
    - Test OOS abort during Place Order Loop
    - Test stuck alert fires once
    - Test max attempts reached
    - _Requirements: 4.5, 4.2, 4.9, 3.5, 4.8_

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement CLI entry point
  - [x] 9.1 Implement CLI argument parsing and dispatch
    - Create `scrapebot/__main__.py` with argument parser
    - Accept `--config`, `--products`, `--monitor-only`, `--login`, `--verbose`/`-v`
    - Load `.env` via `python-dotenv`
    - Configure logging level (DEBUG if verbose, INFO otherwise)
    - In `--login` mode: open browser, wait for user Enter, save session, exit code 0
    - In normal mode: start monitor polling loop
    - Handle `KeyboardInterrupt` gracefully (exit code 0)
    - Handle missing config files (print error, exit non-zero)
    - Handle `--login` + `--monitor-only` (execute login only, ignore monitor-only)
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 9.5_

  - [ ]* 9.2 Write unit tests for CLI
    - Test argument parsing defaults
    - Test all flag combinations
    - Test missing config file error handling
    - Test login + monitor-only precedence
    - _Requirements: 11.1, 11.2, 11.7, 11.9_

- [x] 10. Integration wiring and end-to-end validation
  - [x] 10.1 Wire all components together in monitor service
    - Ensure MonitorService instantiates RedskyClient, DiscordNotifier, BrowserManager, CheckoutFlow
    - Connect startup notification on monitor start
    - Connect browser start/stop around checkout attempts
    - Connect screenshot capture on checkout errors
    - Ensure checkout_in_progress flag resets on checkout completion (success or failure)
    - _Requirements: 1.1, 4.1, 9.4, 10.5, 10.6_

  - [ ]* 10.2 Write integration tests
    - Test full monitor loop with mocked Redsky and Discord
    - Test checkout flow with mocked Playwright page
    - Test error recovery: single product failure doesn't stop others
    - _Requirements: 1.4, 2.5, 10.5, 10.6_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document using Hypothesis
- Unit tests validate specific examples and edge cases using pytest
- The project uses Python with asyncio, httpx, Playwright, and python-dotenv
- All HTTP mocking uses `respx` for httpx compatibility

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4"] },
    { "id": 1, "tasks": ["1.2", "3.1"] },
    { "id": 2, "tasks": ["1.3", "2.1", "3.2", "3.3"] },
    { "id": 3, "tasks": ["2.2", "2.3", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "6.1"] },
    { "id": 5, "tasks": ["6.2", "7.1"] },
    { "id": 6, "tasks": ["7.2"] },
    { "id": 7, "tasks": ["7.3"] },
    { "id": 8, "tasks": ["7.4", "7.5", "9.1"] },
    { "id": 9, "tasks": ["9.2", "10.1"] },
    { "id": 10, "tasks": ["10.2"] }
  ]
}
```
