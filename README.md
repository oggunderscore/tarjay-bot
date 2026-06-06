# ScrapeBot — Target.com Stock Monitor & Checkout

Educational tool for monitoring Target.com product availability, sending Discord alerts on stock changes, and optionally attempting automated checkout with your own account credentials.

**Use only on products you intend to purchase, with your own Target account, and in compliance with Target's Terms of Service.**

## Features

- **Stock monitoring** via Target's Redsky `product_fulfillment_v1` API (fast polling, low browser overhead)
- **Discord bot** with slash commands — manage products, configure settings, and receive notifications all from Discord
- **Discord alerts** for stock changes, checkout steps, stuck checkout loops, and failures
- **Auto-checkout** with Playwright (Chrome persistent session)
- **Max quantity → qty 1 fallback** if adding max quantity fails
- **Place order retry loop** — dismisses popups/modals and keeps clicking "Place your order" until success or OOS
- **Optional NSTBrowser** integration via CDP (`NSTBROWSER_CDP_URL` in `.env`)
- **Monitor-only mode** (`--monitor-only`) for alert-only testing

## Quick Start

### 1. Install dependencies

```powershell
cd c:\Users\Kevin\Desktop\ScrapeBot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chrome
```

### 2. Configure

```powershell
copy config.yaml.example config.yaml
copy products.yaml.example products.yaml
copy .env.example .env
```

Edit `products.yaml` with your product URLs, max quantity, and max price:

```yaml
products:
  - name: "My Test Item"
    url: "https://www.target.com/p/.../-/A-12345678"
    max_quantity: 2
    max_price: 49.99
    enabled: true
```

Edit `config.yaml` for your ZIP code / store ID and poll interval.

Set `DISCORD_WEBHOOK_URL` in `.env` (Discord → Server Settings → Integrations → Webhooks).

### 3. Save Target login session (recommended)

```powershell
python -m scrapebot --login
```

Log in manually in the browser (including MFA if prompted), then press Enter in the terminal. Session cookies are stored in `browser_data/target_session`.

Alternatively, set `TARGET_EMAIL` and `TARGET_PASSWORD` in `.env`.

### 4. Run

Monitor + checkout (webhook mode):

```powershell
python -m scrapebot
```

Monitor only (no purchase attempts):

```powershell
python -m scrapebot --monitor-only
```

### 5. Run as Discord Bot (recommended)

The bot mode replaces the webhook with a full Discord bot that lets you manage everything from Discord.

**Setup:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. Go to Bot tab → Reset Token → copy the token
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. Go to OAuth2 → URL Generator → select `bot` + `applications.commands` scopes → select `Send Messages`, `Embed Links`, `Read Message History` permissions
5. Use the generated URL to invite the bot to your server
6. Right-click your notifications channel → Copy Channel ID (enable Developer Mode in Discord settings)

Add to `.env`:
```
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_CHANNEL_ID=123456789012345678
```

Run:
```powershell
python -m scrapebot --bot
```

**Available slash commands:**

| Command | Description |
|---------|-------------|
| `/add` | Add a Target product to track |
| `/remove` | Remove a tracked product |
| `/list` | Show all tracked products with status |
| `/edit` | Edit product settings (name, qty, price) |
| `/enable` / `/disable` | Toggle a product on/off |
| `/status` | Show bot status and per-product stock |
| `/check` | Force an immediate stock check |
| `/pause` / `/resume` | Pause or resume monitoring |
| `/config` | View or update config (poll interval, checkout, etc.) |
| `/location` | Update ZIP, state, store ID |
| `/help` | Show all commands |

## Configuration Reference

| Setting | Description |
|---------|-------------|
| `monitor.poll_interval_seconds` | Base delay between stock checks |
| `location.zip` / `store_id` | Fulfillment context for Redsky API |
| `checkout.place_order_retry_seconds` | Delay between "Place your order" clicks |
| `checkout.stuck_alert_seconds` | Discord alert if checkout loop runs this long |
| `browser.headless` | Set `false` for debugging checkout UI |
| `checkout.fulfillment` | `shipping` or `pickup` |

## NSTBrowser (optional)

If you use [NSTBrowser](https://docs.nstbrowser.io/) for anti-detection testing, start a profile and set:

```
NSTBROWSER_CDP_URL=ws://127.0.0.1:8848/devtools/browser/<id>
```

The bot connects via Playwright CDP instead of launching local Chrome.

## Architecture

```
Redsky API  ──► stock polling ──► Discord alerts
                     │
                     ▼ (in stock)
              Playwright checkout
                     │
         add to cart → checkout → place order loop
```

Stock checks use the Redsky aggregation API ([reference gist](https://gist.github.com/LumaDevelopment/f2a34a202fed6ab5a7f3a31282834943)). Checkout uses browser automation because purchase flows require an authenticated session.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Redsky 403/429 | Increase poll interval; avoid hammering the API |
| Not logged in | Run `--login` again or set credentials in `.env` |
| Checkout selectors fail | Target UI changes often — run with `headless: false` and check screenshots in `./screenshots` |
| Price not validated at monitor time | Price is checked on the product page during checkout |

## Legal / Ethical Notice

This project is intended for **personal security and reliability testing** on your own account. Automated purchasing may violate retailer terms of service. Do not use for scalping, inventory denial, or any activity that harms other shoppers.
