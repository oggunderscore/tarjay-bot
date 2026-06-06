"""Channel-based Discord notifier that sends embeds directly to a text channel."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord

from scrapebot.models import AlertLevel, COLORS

logger = logging.getLogger(__name__)

# Map AlertLevel to discord.Color
_DISCORD_COLORS: dict[AlertLevel, discord.Color] = {
    AlertLevel.INFO: discord.Color(COLORS[AlertLevel.INFO]),
    AlertLevel.SUCCESS: discord.Color(COLORS[AlertLevel.SUCCESS]),
    AlertLevel.WARNING: discord.Color(COLORS[AlertLevel.WARNING]),
    AlertLevel.ERROR: discord.Color(COLORS[AlertLevel.ERROR]),
    AlertLevel.STOCK: discord.Color(COLORS[AlertLevel.STOCK]),
}


class ChannelNotifier:
    """Sends embed notifications to a Discord text channel via the bot API.

    This replaces the webhook-based DiscordNotifier when running in bot mode.
    It exposes the same public interface so MonitorService can use it as a drop-in.
    """

    def __init__(self, channel: discord.TextChannel) -> None:
        self.channel = channel

    def close(self) -> None:
        """No-op — the bot manages its own lifecycle."""

    @property
    def enabled(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Core send
    # ------------------------------------------------------------------

    def send(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        fields: dict[str, str] | None = None,
        url: str | None = None,
    ) -> None:
        """Schedule an embed send on the running event loop (fire-and-forget)."""
        logger.info("%s: %s", title, message)
        try:
            loop = self.channel._state.loop  # type: ignore[attr-defined]
            loop.create_task(self._async_send(title, message, level, fields, url))
        except Exception as exc:
            logger.error("Failed to schedule notification: %s", exc)

    async def _async_send(
        self,
        title: str,
        message: str,
        level: AlertLevel,
        fields: dict[str, str] | None,
        url: str | None,
    ) -> None:
        embed = discord.Embed(
            title=title[:256],
            description=message[:4096],
            color=_DISCORD_COLORS.get(level, discord.Color.blurple()),
            timestamp=datetime.now(timezone.utc),
            url=url,
        )
        if fields:
            for name, value in fields.items():
                embed.add_field(name=name, value=value[:1024], inline=True)
        embed.set_footer(text="ScrapeBot")
        try:
            await self.channel.send(embed=embed)
        except discord.HTTPException as exc:
            logger.error("Discord channel send failed: %s", exc)

    async def send_embed(
        self,
        title: str,
        description: str,
        color: discord.Color | None = None,
        fields: dict[str, str] | None = None,
        url: str | None = None,
    ) -> None:
        """Directly send an embed (awaitable version for cogs)."""
        embed = discord.Embed(
            title=title[:256],
            description=description[:4096],
            color=color or discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
            url=url,
        )
        if fields:
            for name, value in fields.items():
                embed.add_field(name=name, value=value[:1024], inline=True)
        embed.set_footer(text="ScrapeBot")
        await self.channel.send(embed=embed)

    # ------------------------------------------------------------------
    # Convenience methods (same interface as DiscordNotifier)
    # ------------------------------------------------------------------

    def stock_change(
        self,
        product_name: str,
        url: str,
        old_status: str,
        new_status: str,
        quantity: float | None = None,
    ) -> None:
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
        self.send(
            title=f"Order Placed: {product_name}",
            message=f"Successfully ordered **{quantity}** unit(s) on attempt #{attempt_count}.",
            level=AlertLevel.SUCCESS,
            url=product_url,
            fields={"Quantity": str(quantity), "Attempt": str(attempt_count)},
        )

    def checkout_stuck(
        self, elapsed_seconds: int, product_url: str | None = None
    ) -> None:
        self.send(
            title="Checkout Stuck",
            message=f"Place-order loop has been running for **{elapsed_seconds}** seconds.",
            level=AlertLevel.WARNING,
            url=product_url,
        )

    def checkout_error(
        self, reason: str, product_url: str | None = None
    ) -> None:
        self.send(
            title="Checkout Failed",
            message=reason,
            level=AlertLevel.ERROR,
            url=product_url,
        )
