"""Discord webhook notification sender for ScrapeBot."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from scrapebot.models import AlertLevel, COLORS

logger = logging.getLogger(__name__)


class DiscordNotifier:
    """Send structured embed notifications to a Discord webhook."""

    def __init__(self, webhook_url: str | None) -> None:
        self.webhook_url = webhook_url
        self._client = httpx.Client(timeout=15.0)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    @property
    def enabled(self) -> bool:
        """True when a webhook URL is configured."""
        return bool(self.webhook_url)

    def send(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        fields: dict[str, str] | None = None,
        url: str | None = None,
    ) -> None:
        """Send a Discord embed notification.

        Falls back to logging when webhook URL is not configured.
        Truncates title to 256, description to 4096, field values to 1024 chars.
        """
        logger.info("%s: %s", title, message)
        if not self.enabled:
            return

        embed_fields = []
        if fields:
            for name, value in fields.items():
                embed_fields.append(
                    {"name": name, "value": value[:1024], "inline": True}
                )

        embed: dict = {
            "title": title[:256],
            "description": message[:4096],
            "color": COLORS[level],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": embed_fields,
        }
        if url:
            embed["url"] = url

        payload = {"embeds": [embed], "username": "ScrapeBot"}

        try:
            response = self._client.post(self.webhook_url, json=payload)  # type: ignore[arg-type]
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Discord webhook failed: %s", exc)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def stock_change(
        self,
        product_name: str,
        url: str,
        old_status: str,
        new_status: str,
        quantity: float | None = None,
    ) -> None:
        """Notify about a product stock status change (Req 3.3)."""
        fields: dict[str, str] = {
            "Old Status": old_status,
            "New Status": new_status,
        }
        if quantity is not None:
            fields["Quantity"] = str(quantity)
        self.send(
            title=f"Stock Update: {product_name}",
            message=f"Status changed from **{old_status}** to **{new_status}**",
            level=AlertLevel.STOCK,
            url=url,
            fields=fields,
        )

    def checkout_progress(
        self, step: str, detail: str, product_url: str | None = None
    ) -> None:
        """Notify about checkout step progress (Req 3.4)."""
        self.send(
            title=f"Checkout: {step}",
            message=detail,
            level=AlertLevel.INFO,
            url=product_url,
        )

    def checkout_success(
        self,
        product_name: str,
        quantity: int,
        attempt_count: int,
        product_url: str | None = None,
    ) -> None:
        """Notify about a successful order placement (Req 3.7)."""
        self.send(
            title=f"Order Placed: {product_name}",
            message=f"Successfully ordered **{quantity}** unit(s) on attempt #{attempt_count}.",
            level=AlertLevel.SUCCESS,
            url=product_url,
            fields={
                "Quantity": str(quantity),
                "Attempt": str(attempt_count),
            },
        )

    def checkout_stuck(
        self, elapsed_seconds: int, product_url: str | None = None
    ) -> None:
        """Notify that checkout has been stuck (Req 3.5).

        elapsed_seconds is the elapsed time in whole seconds.
        """
        self.send(
            title="Checkout Stuck",
            message=f"Place-order loop has been running for **{elapsed_seconds}** seconds.",
            level=AlertLevel.WARNING,
            url=product_url,
        )

    def checkout_error(
        self, reason: str, product_url: str | None = None
    ) -> None:
        """Notify about a checkout failure (Req 3.6)."""
        self.send(
            title="Checkout Failed",
            message=reason,
            level=AlertLevel.ERROR,
            url=product_url,
        )
