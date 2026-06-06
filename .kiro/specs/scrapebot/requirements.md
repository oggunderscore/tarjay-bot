# Requirements Document

## Introduction

ScrapeBot is a Target.com stock monitoring and auto-checkout application. The system polls Target's Redsky product fulfillment API to detect product availability, sends Discord notifications on stock changes and checkout events, and optionally performs automated browser-based checkout using Playwright. Configuration is managed through YAML files and environment variables, supporting multiple products with per-product quantity and price guards.

## Glossary

- **Monitor**: The polling subsystem that periodically checks product availability via the Redsky API
- **Redsky_API**: Target's `product_fulfillment_v1` aggregation endpoint that returns shipping and pickup availability for a given TCIN
- **TCIN**: Target's unique product identifier extracted from the product URL (the numeric ID after `/A-`)
- **Stock_Snapshot**: A point-in-time record of a product's shipping status, pickup status, available quantity, and sold-out flag
- **Checkout_Flow**: The browser automation subsystem that navigates Target.com to purchase an in-stock product
- **Browser_Manager**: The component responsible for launching, connecting to, and managing Playwright browser contexts
- **Discord_Notifier**: The subsystem that sends embed messages to a Discord channel via webhook
- **Config_Loader**: The component that reads YAML configuration files and environment variables into application settings
- **Place_Order_Loop**: The retry mechanism that repeatedly clicks the "Place your order" button, dismisses popups, and checks for order confirmation or out-of-stock conditions
- **Product_Target**: A configured product entry containing URL, name, max quantity, max price, and enabled flag
- **NSTBrowser**: An optional external anti-detection browser connected via Chrome DevTools Protocol (CDP)

## Requirements

### Requirement 1: Stock Polling via Redsky API

**User Story:** As a user, I want the application to poll Target's Redsky API for product availability, so that I am alerted when products come in stock.

#### Acceptance Criteria

1. WHEN the Monitor starts, THE Monitor SHALL poll the Redsky_API `product_fulfillment_v1` endpoint for each enabled Product_Target concurrently, repeating every `poll_interval_seconds` (default 8 seconds), passing the configured location context (ZIP, state, store_id, latitude, longitude) as query parameters
2. THE Monitor SHALL add a uniformly-distributed random jitter between 0 and `jitter_seconds` (default 2 seconds) to each poll cycle delay so that the actual delay per cycle equals `poll_interval_seconds` + random(0, `jitter_seconds`)
3. WHEN the Redsky_API returns a successful HTTP response (status 2xx), THE Monitor SHALL parse the shipping availability status, pickup availability status, available-to-promise quantity, and sold-out flag from the JSON payload into a Stock_Snapshot, mapping any unrecognized status value to UNKNOWN
4. IF the Redsky_API returns a non-success HTTP status or the request fails due to a network error, THEN THE Monitor SHALL log the error and send a Discord notification indicating the failure, and continue the polling loop for all products without interruption
5. THE Monitor SHALL evaluate a Product_Target as in-stock only when the Stock_Snapshot status for at least one of the configured `stock_types` (one or both of "shipping" and "pickup") equals IN_STOCK and the sold-out flag is false
6. THE Monitor SHALL enforce an HTTP request timeout of 20 seconds per Redsky_API call; IF the request exceeds this timeout, THEN THE Monitor SHALL treat it as a failed request per criterion 4

### Requirement 2: Stock Change Detection

**User Story:** As a user, I want to be notified when a product's stock status changes, so that I can act on availability shifts.

#### Acceptance Criteria

1. WHEN the status label for a product differs between the current poll and the immediately preceding poll for the same TCIN, THE Monitor SHALL send a stock change notification to the Discord_Notifier containing the product name, product URL, previous status label, new status label, and current shipping quantity
2. THE Monitor SHALL derive the status label for a Stock_Snapshot by combining the sold_out flag, shipping_status value, and pickup_status value into a single composite string, such that any change in any of those constituent fields produces a different label
3. WHERE the `notify_on_every_poll` option is enabled, THE Monitor SHALL send a Discord notification on every poll regardless of whether the status label changed, including on the first poll for a product
4. IF a product has no previous Stock_Snapshot stored (first poll), THEN THE Monitor SHALL suppress the stock change notification and store the current snapshot as the baseline for future comparisons
5. IF the Discord_Notifier fails to deliver a stock change notification, THEN THE Monitor SHALL log the failure and continue polling without interrupting the monitoring loop

### Requirement 3: Discord Webhook Notifications

**User Story:** As a user, I want to receive structured Discord notifications for stock changes and checkout events, so that I have real-time visibility into the bot's activity.

#### Acceptance Criteria

1. THE Discord_Notifier SHALL send messages as Discord embed objects with a title (max 256 characters), description (max 4096 characters), color, ISO 8601 UTC timestamp, and optional fields (each field value max 1024 characters), truncating any content that exceeds these limits
2. THE Discord_Notifier SHALL use distinct embed colors for each alert level: INFO (blue), SUCCESS (green), WARNING (yellow), ERROR (red), and STOCK (purple)
3. WHEN a stock change is detected, THE Discord_Notifier SHALL include the product name, URL, old status, new status, and available quantity (or omit quantity if unavailable) in the notification
4. WHEN a checkout step is reached, THE Discord_Notifier SHALL send an INFO-level notification with the step name and detail message
5. WHEN the Place_Order_Loop has been running continuously for longer than the configured `stuck_alert_seconds` threshold, THE Discord_Notifier SHALL send exactly one WARNING-level stuck alert per checkout loop including the elapsed time in whole seconds
6. WHEN checkout fails or an exception occurs, THE Discord_Notifier SHALL send an ERROR-level notification with the failure reason
7. WHEN an order is successfully placed, THE Discord_Notifier SHALL send a SUCCESS-level notification with the product name, quantity ordered, and the attempt count at which the order succeeded
8. IF the Discord webhook URL is not configured, THEN THE Discord_Notifier SHALL log the notification title and message to the application log without attempting any HTTP requests
9. IF the Discord webhook HTTP request fails, THEN THE Discord_Notifier SHALL log an error message containing the failure reason and continue operation without retrying the failed request
10. THE Discord_Notifier SHALL use a 15-second timeout for each webhook HTTP request

### Requirement 4: Auto-Checkout via Browser Automation

**User Story:** As a user, I want the application to automatically purchase in-stock products through Target.com, so that I can secure items before they sell out.

#### Acceptance Criteria

1. WHEN a product transitions to in-stock and checkout is enabled, THE Checkout_Flow SHALL navigate to the product page within 30 seconds, check for out-of-stock indicators and enabled fulfillment buttons to confirm availability, and attempt to add the item to the cart
2. IF the product page price exceeds the configured `max_price`, THEN THE Checkout_Flow SHALL abort checkout for that product and send a PRICE_TOO_HIGH notification
3. IF the product page price cannot be read (no price element found), THEN THE Checkout_Flow SHALL proceed with the add-to-cart step without aborting
4. WHEN the product is available and price is within limits, THE Checkout_Flow SHALL set the quantity to `max_quantity` (range: 1 to 10) using either a quantity selector dropdown or increment button before adding to cart
5. WHEN adding `max_quantity` to cart fails and `max_quantity` is greater than 1, THE Checkout_Flow SHALL retry the entire checkout attempt with quantity 1 as a fallback
6. WHEN the item is added to cart, THE Checkout_Flow SHALL navigate to the cart page, click the checkout button, select saved address if `prefer_saved_address` is configured, select saved payment if `prefer_saved_payment` is configured, and enter the Place_Order_Loop
7. WHILE in the Place_Order_Loop, THE Checkout_Flow SHALL click the place-order button every `place_order_retry_seconds` (default 2 seconds) for up to `place_order_max_attempts` (default 300) attempts, checking for order confirmation after each attempt
8. IF the Place_Order_Loop exceeds `place_order_max_attempts` without order confirmation, THEN THE Checkout_Flow SHALL abort with a FAILED result indicating the maximum attempts were exceeded
9. IF the product is detected as out of stock on the product page or during the Place_Order_Loop via the stock API, THEN THE Checkout_Flow SHALL abort and return an OUT_OF_STOCK result
10. THE Checkout_Flow SHALL dismiss popups and modals at each navigation step by clicking up to 3 instances per known close-button selector, with a 1500 millisecond timeout per click attempt

### Requirement 5: Place Order Retry Loop

**User Story:** As a user, I want the checkout to persistently attempt placing the order, so that transient failures do not prevent a successful purchase.

#### Acceptance Criteria

1. WHILE the Place_Order_Loop is active, THE Place_Order_Loop SHALL click the "Place your order" button and then wait `place_order_retry_seconds` (default: 2 seconds) before the next iteration
2. THE Place_Order_Loop SHALL check for order confirmation after each attempt by inspecting whether the page URL contains "confirm", "thank", or "order-confirmation", or whether a known order-confirmation element is present in the DOM
3. WHEN the order confirmation page is detected, THE Place_Order_Loop SHALL return a SUCCESS result and send a success notification
4. IF the "Place your order" button is not visible or not enabled, THEN THE Place_Order_Loop SHALL attempt to dismiss overlay popups and click visible "Try again" or "Continue" buttons before the next iteration
5. WHEN the total elapsed time since loop start exceeds `stuck_alert_seconds` (default: 90 seconds), THE Place_Order_Loop SHALL send a stuck notification and capture a screenshot exactly once per checkout attempt
6. THE Place_Order_Loop SHALL call the Redsky_API during each iteration to retrieve the product shipping status and sold-out flag
7. IF the Redsky_API returns a shipping status of OUT_OF_STOCK and the sold-out flag is true, THEN THE Place_Order_Loop SHALL abort and return an OUT_OF_STOCK result
8. WHEN the attempt count reaches `place_order_max_attempts` (default: 300), THE Place_Order_Loop SHALL abort and return a FAILED result
9. IF the Redsky_API call fails due to a network or server error during the loop, THEN THE Place_Order_Loop SHALL log the failure and continue to the next iteration without aborting

### Requirement 6: Configuration System

**User Story:** As a user, I want to configure the bot through YAML files and environment variables, so that I can adjust behavior without modifying code.

#### Acceptance Criteria

1. THE Config_Loader SHALL read application settings from `config.yaml` including monitor, location, redsky, browser, and checkout sections, applying built-in defaults for any optional keys omitted within a section
2. THE Config_Loader SHALL read the product list from `products.yaml` including name, URL, max_quantity, max_price, and enabled flag per product, loading only products whose enabled flag is true (or absent, defaulting to true)
3. THE Config_Loader SHALL read secrets (DISCORD_WEBHOOK_URL, TARGET_EMAIL, TARGET_PASSWORD, NSTBROWSER_CDP_URL) from environment variables loaded via `.env` file, treating any absent environment variable as unset (None) without raising an error
4. WHEN a required configuration file (`config.yaml` or `products.yaml`) is missing, THE Config_Loader SHALL raise a FileNotFoundError with a message referencing the corresponding `.example` template file
5. WHEN no enabled products are found in `products.yaml` after filtering by the enabled flag, THE Config_Loader SHALL raise a ValueError indicating no products are configured
6. THE Config_Loader SHALL extract the TCIN from each product URL by first checking for a `preselect` query parameter containing digits, and if absent, by parsing the `/A-{digits}` path pattern
7. IF a product URL does not match either the `preselect` query parameter pattern or the `/A-{digits}` path pattern, THEN THE Config_Loader SHALL raise a ValueError with a message indicating the TCIN could not be extracted from that URL

### Requirement 7: Browser Session Management

**User Story:** As a user, I want my Target.com login session to persist across bot restarts, so that I do not have to log in every time.

#### Acceptance Criteria

1. THE Browser_Manager SHALL launch a Playwright persistent browser context using a configured `user_data_dir` to preserve cookies and session data, creating the directory if it does not exist
2. WHEN the `--login` CLI flag is provided, THE Browser_Manager SHALL open a visible (non-headless) browser window navigated to Target.com and wait for the user to press Enter in the terminal to confirm login completion, then save the session
3. WHEN TARGET_EMAIL and TARGET_PASSWORD environment variables are set, THE Checkout_Flow SHALL check if the user is already logged in by detecting a Target account navigation element; IF not logged in, THEN THE Checkout_Flow SHALL navigate to the login page, fill email and password fields, submit the form, and wait for network idle
4. IF automated login fails (form submission error, timeout, or account element not detected after submission), THEN THE Checkout_Flow SHALL raise a RuntimeError with a message instructing the user to run `--login` mode
5. WHERE the NSTBROWSER_CDP_URL environment variable is set, THE Browser_Manager SHALL connect to the external browser via CDP instead of launching a local Chrome instance
6. WHEN NSTBrowser is connected via CDP and existing browser contexts are available, THE Browser_Manager SHALL reuse the first existing context; IF no contexts exist, THEN THE Browser_Manager SHALL create a new context
7. IF the NSTBROWSER_CDP_URL connection fails, THEN THE Browser_Manager SHALL raise a connection error without falling back to local Chrome
8. IF Chrome channel is not available on the system, THEN THE Browser_Manager SHALL fall back to bundled Chromium with a warning log message

### Requirement 8: Quantity and Price Guards

**User Story:** As a user, I want per-product quantity limits and price caps, so that the bot does not overspend or purchase more than intended.

#### Acceptance Criteria

1. THE Checkout_Flow SHALL attempt to add the configured `max_quantity` (integer, minimum 1) for a product as the first checkout attempt
2. WHEN adding `max_quantity` fails because the add-to-cart action is unavailable or the quantity selector does not accept the value, and `max_quantity` is greater than 1, THE Checkout_Flow SHALL fall back to quantity 1 and retry the full checkout sequence from the product detail page
3. IF `max_quantity` is 1 and the add-to-cart action is unavailable, THEN THE Checkout_Flow SHALL abort checkout with a FAILED result without further retry
4. THE Checkout_Flow SHALL read the product price from the product detail page by parsing the first dollar amount (format: digits with optional decimal, e.g. "12.99") from the price element before adding to cart
5. IF the parsed price is strictly greater than the product's configured `max_price`, THEN THE Checkout_Flow SHALL abort checkout, not add the item to cart, and notify the user with a PRICE_TOO_HIGH alert that includes the parsed price and the configured limit
6. IF the price element is missing from the page or its text does not contain a parseable dollar amount, THEN THE Checkout_Flow SHALL proceed with checkout without blocking the purchase

### Requirement 9: Monitor-Only Mode

**User Story:** As a user, I want a mode that only monitors stock and sends alerts without attempting checkout, so that I can test or passively track availability.

#### Acceptance Criteria

1. WHEN the `--monitor-only` CLI flag is provided, THE Monitor SHALL set `checkout.enabled` to false regardless of the value specified in the config file
2. WHILE monitor-only mode is active, THE Monitor SHALL continue polling the Redsky API at the configured `poll_interval_seconds` (plus jitter) and sending stock change notifications via the Discord webhook each time a product's stock status changes between consecutive polls
3. WHILE monitor-only mode is active, THE Monitor SHALL not instantiate a browser session or invoke the Checkout_Flow when a product transitions to an in-stock state
4. WHEN the Monitor starts with `--monitor-only` active, THE Monitor SHALL send a Discord startup notification indicating that checkout is disabled so the user can confirm the flag took effect
5. IF `--monitor-only` is provided together with `--login`, THEN THE Monitor SHALL execute the login session flow (launching the browser for session saving) and exit without entering the polling loop

### Requirement 10: Error Handling and Screenshots

**User Story:** As a user, I want the bot to capture screenshots on errors and continue operating, so that I can diagnose failures without losing monitoring uptime.

#### Acceptance Criteria

1. WHEN an exception occurs during checkout, THE Browser_Manager SHALL capture a full-page screenshot in PNG format and save it to the configured `screenshot_dir`, creating the directory if it does not exist
2. WHERE `screenshot_on_error` is enabled in browser config, THE Browser_Manager SHALL save screenshots with a filename containing the error context prefix, the product TCIN, and a timestamp (e.g., `checkout-error-{tcin}-{timestamp}.png`)
3. IF `screenshot_on_error` is disabled in browser config, THEN THE Browser_Manager SHALL skip screenshot capture and return without saving a file
4. WHEN the Place_Order_Loop has been running for longer than the configured `stuck_alert_seconds` threshold, THE Browser_Manager SHALL capture a full-page screenshot of the current page state and THE Monitor SHALL send a stuck alert notification
5. IF a Redsky_API poll fails for one product, THEN THE Monitor SHALL log the error, send an error notification, and continue polling other products in the same poll cycle without interruption
6. IF an unhandled exception occurs during checkout for one product, THEN THE Monitor SHALL log the exception at ERROR level, capture a screenshot if enabled, and resume monitoring for that product on subsequent poll cycles

### Requirement 11: CLI Entry Point

**User Story:** As a user, I want a command-line interface to run the bot with options for config paths, monitor-only mode, login mode, and verbosity.

#### Acceptance Criteria

1. THE CLI SHALL accept `--config` to specify the path to the main config YAML (default: `config.yaml` relative to the working directory)
2. THE CLI SHALL accept `--products` to specify the path to the products YAML (default: `products.yaml` relative to the working directory)
3. THE CLI SHALL accept `--monitor-only` to disable checkout attempts while continuing to monitor product availability
4. THE CLI SHALL accept `--login` to open a browser for manual login session saving and then exit
5. THE CLI SHALL accept `--verbose` or `-v` to enable debug-level logging (DEBUG level) instead of the default INFO level
6. WHEN `--login` is provided, THE CLI SHALL start the browser, wait for the user to authenticate, save the session to the configured user data directory, print a confirmation message, and exit with code 0
7. IF the file specified by `--config` or `--products` does not exist, THEN THE CLI SHALL print an error message indicating which file was not found and exit with a non-zero exit code without starting any monitoring or login flow
8. IF `--login` is provided and session saving fails, THEN THE CLI SHALL print an error message indicating the failure reason and exit with a non-zero exit code
9. WHEN `--login` is provided together with `--monitor-only`, THE CLI SHALL execute only the login flow, ignoring `--monitor-only`
