"""Data models and enums for ScrapeBot runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrapebot.config import ProductTarget


class StockStatus(str, Enum):
    """Availability status for a product fulfillment channel."""

    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    LIMITED = "LIMITED"
    UNKNOWN = "UNKNOWN"


@dataclass
class StockSnapshot:
    """Point-in-time record of product availability."""

    tcin: str
    shipping_status: StockStatus
    shipping_quantity: float
    pickup_status: StockStatus | None
    sold_out: bool
    title: str | None = None
    raw: dict | None = None

    @property
    def is_available_for_shipping(self) -> bool:
        """True when shipping is in stock and product is not sold out."""
        return self.shipping_status == StockStatus.IN_STOCK and not self.sold_out

    @property
    def is_available_for_pickup(self) -> bool:
        """True when pickup is in stock and product is not sold out."""
        return self.pickup_status == StockStatus.IN_STOCK and not self.sold_out


class CheckoutResult(str, Enum):
    """Outcome classification for a checkout attempt."""

    SUCCESS = "success"
    OUT_OF_STOCK = "out_of_stock"
    PRICE_TOO_HIGH = "price_too_high"
    FAILED = "failed"


@dataclass
class CheckoutOutcome:
    """Result of a completed checkout attempt."""

    result: CheckoutResult
    message: str
    quantity: int = 1


class AlertLevel(str, Enum):
    """Severity level for Discord notifications."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    STOCK = "stock"


# Discord embed color mapping per alert level.
COLORS: dict[AlertLevel, int] = {
    AlertLevel.INFO: 0x3498DB,      # Blue
    AlertLevel.SUCCESS: 0x2ECC71,   # Green
    AlertLevel.WARNING: 0xF1C40F,   # Yellow
    AlertLevel.ERROR: 0xE74C3C,     # Red
    AlertLevel.STOCK: 0x9B59B6,     # Purple
}


@dataclass
class ProductState:
    """Tracks runtime state for a monitored product."""

    product: ProductTarget
    last_snapshot: StockSnapshot | None = None
    checkout_in_progress: bool = False
    checkout_completed: bool = False
