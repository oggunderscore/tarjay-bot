from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


TCIN_PATTERN = re.compile(r"/A-(\d+)", re.IGNORECASE)
PRESELECT_PATTERN = re.compile(r"[?&]preselect=(\d+)", re.IGNORECASE)


def extract_tcin(url: str) -> str:
    preselect = PRESELECT_PATTERN.search(url)
    if preselect:
        return preselect.group(1)
    match = TCIN_PATTERN.search(url)
    if not match:
        raise ValueError(f"Could not extract TCIN from URL: {url}")
    return match.group(1)


@dataclass
class ProductTarget:
    name: str
    url: str
    max_quantity: int
    max_price: float
    enabled: bool = True
    tcin: str = field(init=False)

    def __post_init__(self) -> None:
        self.tcin = extract_tcin(self.url)


@dataclass
class MonitorConfig:
    poll_interval_seconds: float = 8.0
    jitter_seconds: float = 2.0
    stock_types: list[str] = field(default_factory=lambda: ["shipping"])
    notify_on_every_poll: bool = False


@dataclass
class LocationConfig:
    zip: str = "10001"
    state: str = "NY"
    latitude: float = 40.7128
    longitude: float = -74.0060
    store_id: str = "1859"


@dataclass
class RedskyConfig:
    api_key: str = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    base_url: str = "https://redsky.target.com/redsky_aggregations/v1/web"


@dataclass
class BrowserConfig:
    headless: bool = False
    slow_mo_ms: int = 50
    user_data_dir: str = "./browser_data/target_session"
    timeout_ms: int = 30000
    nstbrowser_cdp_url: str | None = None
    nstbrowser_api_key: str | None = None
    nstbrowser_profile_id: str | None = None
    screenshot_on_error: bool = True
    screenshot_dir: str = "./screenshots"


@dataclass
class CheckoutConfig:
    enabled: bool = True
    auto_checkout: bool = False
    dry_run: bool = False
    place_order_retry_seconds: float = 2.0
    place_order_max_attempts: int = 300
    stuck_alert_seconds: float = 90.0
    prefer_saved_address: bool = True
    prefer_saved_payment: bool = True
    fulfillment: str = "shipping"


@dataclass
class Settings:
    monitor: MonitorConfig
    location: LocationConfig
    redsky: RedskyConfig
    browser: BrowserConfig
    checkout: CheckoutConfig
    products: list[ProductTarget]
    target_email: str | None
    target_password: str | None
    discord_webhook_url: str | None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file: {path}. Copy {path.name}.example and edit it."
        )
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings(
    config_path: Path,
    products_path: Path,
    monitor_only: bool = False,
    allow_empty_products: bool = False,
) -> Settings:
    load_dotenv()

    config = _read_yaml(config_path)
    products_data = _read_yaml(products_path)

    monitor = MonitorConfig(**config.get("monitor", {}))
    location = LocationConfig(**config.get("location", {}))
    redsky = RedskyConfig(**config.get("redsky", {}))
    browser = BrowserConfig(**config.get("browser", {}))
    checkout = CheckoutConfig(**config.get("checkout", {}))

    if monitor_only:
        checkout.enabled = False

    browser.nstbrowser_cdp_url = os.getenv("NSTBROWSER_CDP_URL") or browser.nstbrowser_cdp_url
    browser.nstbrowser_api_key = os.getenv("NSTBROWSER_API_KEY") or browser.nstbrowser_api_key
    browser.nstbrowser_profile_id = os.getenv("NSTBROWSER_PROFILE_ID") or browser.nstbrowser_profile_id

    products: list[ProductTarget] = []
    for item in products_data.get("products", []):
        if not item.get("enabled", True):
            continue
        products.append(ProductTarget(**item))

    if not products and not allow_empty_products:
        raise ValueError("No enabled products found in products config.")

    return Settings(
        monitor=monitor,
        location=location,
        redsky=redsky,
        browser=browser,
        checkout=checkout,
        products=products,
        target_email=os.getenv("TARGET_EMAIL"),
        target_password=os.getenv("TARGET_PASSWORD"),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
    )
