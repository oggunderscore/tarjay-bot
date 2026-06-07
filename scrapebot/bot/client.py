"""Discord bot client for ScrapeBot — runs the monitor and exposes slash commands."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from scrapebot.bot.notifier import ChannelNotifier
from scrapebot.config import ProductTarget, Settings, extract_tcin, load_settings
from scrapebot.monitor import MonitorService

logger = logging.getLogger(__name__)


class ScrapeBot(commands.Bot):
    """Discord bot that wraps MonitorService and provides interactive commands."""

    def __init__(
        self,
        settings: Settings,
        products_path: Path,
        notification_channel_id: int,
        **kwargs: Any,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            **kwargs,
        )
        self.settings = settings
        self.products_path = products_path
        self.notification_channel_id = notification_channel_id
        self.monitor: MonitorService | None = None
        self._monitor_task: asyncio.Task | None = None
        self._paused = False

    async def setup_hook(self) -> None:
        """Load cogs on startup."""
        from scrapebot.bot.cogs.products import ProductsCog
        from scrapebot.bot.cogs.monitoring import MonitoringCog
        from scrapebot.bot.cogs.config import ConfigCog

        await self.add_cog(ProductsCog(self))
        await self.add_cog(MonitoringCog(self))
        await self.add_cog(ConfigCog(self))

    async def on_ready(self) -> None:
        """Start the monitor loop once the bot is connected."""
        assert self.user is not None
        logger.info("Bot connected as %s (ID: %s)", self.user.name, self.user.id)

        # Sync slash commands to the guild for instant availability
        channel = self.get_channel(self.notification_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.notification_channel_id)

        if channel and hasattr(channel, "guild"):
            guild = channel.guild
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash commands synced to guild %s (instant)", guild.name)
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to 1 hour)")

        channel = self.get_channel(self.notification_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.notification_channel_id)

        # Replace the webhook-based notifier with a channel-based one
        notifier = ChannelNotifier(channel)  # type: ignore[arg-type]
        self.monitor = MonitorService(self.settings)
        self.monitor.notifier = notifier

        if not self._paused:
            self._start_monitor()

        await notifier.send_embed(
            title="ScrapeBot Online",
            description=(
                f"Monitoring **{len(self.settings.products)}** product(s).\n"
                f"Checkout **{'enabled' if self.settings.checkout.enabled else 'disabled'}**.\n"
                f"Use `/help` or `/list` to get started."
            ),
            color=discord.Color.green(),
        )

    def _start_monitor(self) -> None:
        """Start the background monitor polling task."""
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._run_monitor(), name="scrapebot-monitor")

    async def _run_monitor(self) -> None:
        """Wrapper around MonitorService.run() with error recovery."""
        assert self.monitor is not None
        try:
            await self.monitor.run()
        except asyncio.CancelledError:
            logger.info("Monitor task cancelled")
        except Exception:
            logger.exception("Monitor crashed — will not auto-restart")

    def stop_monitor(self) -> None:
        """Stop the monitor polling loop."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None
        self._paused = True

    def resume_monitor(self) -> None:
        """Resume the monitor polling loop."""
        self._paused = False
        self._start_monitor()

    @property
    def is_monitoring(self) -> bool:
        """True when the monitor loop is actively running."""
        return self._monitor_task is not None and not self._monitor_task.done()

    def reload_products(self) -> None:
        """Reload products from YAML and update the monitor state."""
        import yaml

        with self.products_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        products: list[ProductTarget] = []
        for item in data.get("products", []):
            if not item.get("enabled", True):
                continue
            products.append(ProductTarget(**item))

        self.settings.products = products
        if self.monitor:
            # Rebuild states dict preserving existing state where possible
            new_states = {}
            for p in products:
                if p.tcin in self.monitor.states:
                    existing = self.monitor.states[p.tcin]
                    existing.product = p
                    new_states[p.tcin] = existing
                else:
                    from scrapebot.models import ProductState
                    new_states[p.tcin] = ProductState(product=p)
            self.monitor.states = new_states

    def save_products(self) -> None:
        """Write the current product list back to products.yaml."""
        import yaml

        data = {
            "products": [
                {
                    "name": p.name,
                    "url": p.url,
                    "max_quantity": p.max_quantity,
                    "max_price": p.max_price,
                    "enabled": p.enabled,
                }
                for p in self.settings.products
            ]
        }
        with self.products_path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
