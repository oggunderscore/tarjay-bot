"""Configuration management slash commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class ConfigCog(commands.Cog, name="Configuration"):
    """View and modify bot configuration via Discord slash commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="config", description="View or update bot configuration")
    @app_commands.describe(
        poll_interval="Polling interval in seconds (e.g. 8)",
        jitter="Jitter seconds added to each poll (e.g. 2)",
        checkout_enabled="Enable or disable checkout capability (true/false)",
        auto_checkout="Auto-checkout on in-stock detection (true/false)",
        fulfillment="Fulfillment type: shipping or pickup",
        stock_types="Stock types to monitor: shipping, pickup, or both",
    )
    async def config(
        self,
        interaction: discord.Interaction,
        poll_interval: float | None = None,
        jitter: float | None = None,
        checkout_enabled: bool | None = None,
        auto_checkout: bool | None = None,
        fulfillment: str | None = None,
        stock_types: str | None = None,
    ) -> None:
        """View current config or update settings."""
        settings = self.bot.settings
        changes: list[str] = []

        if poll_interval is not None:
            settings.monitor.poll_interval_seconds = max(1.0, poll_interval)
            changes.append(f"Poll Interval → **{settings.monitor.poll_interval_seconds}s**")

        if jitter is not None:
            settings.monitor.jitter_seconds = max(0.0, jitter)
            changes.append(f"Jitter → **{settings.monitor.jitter_seconds}s**")

        if checkout_enabled is not None:
            settings.checkout.enabled = checkout_enabled
            changes.append(f"Checkout → **{'enabled' if checkout_enabled else 'disabled'}**")

        if auto_checkout is not None:
            settings.checkout.auto_checkout = auto_checkout
            changes.append(f"Auto-Checkout → **{'on' if auto_checkout else 'off'}**")

        if fulfillment is not None:
            if fulfillment in ("shipping", "pickup"):
                settings.checkout.fulfillment = fulfillment
                changes.append(f"Fulfillment → **{fulfillment}**")
            else:
                await interaction.response.send_message(
                    "❌ Fulfillment must be `shipping` or `pickup`.", ephemeral=True
                )
                return

        if stock_types is not None:
            parsed = [s.strip().lower() for s in stock_types.split(",")]
            valid = [s for s in parsed if s in ("shipping", "pickup")]
            if not valid:
                await interaction.response.send_message(
                    "❌ Stock types must include `shipping`, `pickup`, or both.", ephemeral=True
                )
                return
            settings.monitor.stock_types = valid
            changes.append(f"Stock Types → **{', '.join(valid)}**")

        if changes:
            await interaction.response.send_message(
                "⚙️ Configuration updated:\n" + "\n".join(f"• {c}" for c in changes)
            )
            return

        # No changes — show current config
        embed = discord.Embed(
            title="⚙️ Current Configuration",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Monitor",
            value=(
                f"Poll: **{settings.monitor.poll_interval_seconds}s**\n"
                f"Jitter: **{settings.monitor.jitter_seconds}s**\n"
                f"Stock Types: **{', '.join(settings.monitor.stock_types)}**\n"
                f"Notify Every Poll: **{settings.monitor.notify_on_every_poll}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Checkout",
            value=(
                f"Enabled: **{settings.checkout.enabled}**\n"
                f"Auto-Checkout: **{'on' if settings.checkout.auto_checkout else 'off'}**\n"
                f"Fulfillment: **{settings.checkout.fulfillment}**\n"
                f"Max Attempts: **{settings.checkout.place_order_max_attempts}**\n"
                f"Retry Interval: **{settings.checkout.place_order_retry_seconds}s**\n"
                f"Stuck Alert: **{settings.checkout.stuck_alert_seconds}s**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Location",
            value=(
                f"ZIP: **{settings.location.zip}**\n"
                f"State: **{settings.location.state}**\n"
                f"Store ID: **{settings.location.store_id}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Browser",
            value=(
                f"Headless: **{settings.browser.headless}**\n"
                f"Screenshots: **{settings.browser.screenshot_on_error}**"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="polling", description="Set the stock check polling rate")
    @app_commands.describe(
        interval="Seconds between check cycles (e.g. 30)",
        jitter="Random extra seconds added to each cycle (e.g. 10)",
        notify_every="Send Discord notification on every poll, not just changes (true/false)",
    )
    async def polling(
        self,
        interaction: discord.Interaction,
        interval: float | None = None,
        jitter: float | None = None,
        notify_every: bool | None = None,
    ) -> None:
        """View or change the polling rate for stock checks."""
        settings = self.bot.settings
        changes: list[str] = []

        if interval is not None:
            settings.monitor.poll_interval_seconds = max(5.0, interval)
            changes.append(f"Interval → **{settings.monitor.poll_interval_seconds}s**")
        if jitter is not None:
            settings.monitor.jitter_seconds = max(0.0, jitter)
            changes.append(f"Jitter → **{settings.monitor.jitter_seconds}s**")
        if notify_every is not None:
            settings.monitor.notify_on_every_poll = notify_every
            changes.append(f"Notify Every Poll → **{'on' if notify_every else 'off'}**")

        if changes:
            effective_min = settings.monitor.poll_interval_seconds
            effective_max = effective_min + settings.monitor.jitter_seconds
            changes.append(f"\n⏱️ Effective check rate: every **{effective_min:.0f}-{effective_max:.0f}s**")
            await interaction.response.send_message(
                "⏱️ Polling updated:\n" + "\n".join(f"• {c}" for c in changes)
            )
        else:
            effective_min = settings.monitor.poll_interval_seconds
            effective_max = effective_min + settings.monitor.jitter_seconds
            await interaction.response.send_message(
                f"⏱️ **Current Polling Rate**\n"
                f"• Interval: **{settings.monitor.poll_interval_seconds}s**\n"
                f"• Jitter: **{settings.monitor.jitter_seconds}s**\n"
                f"• Effective: every **{effective_min:.0f}-{effective_max:.0f}s**\n"
                f"• Notify every poll: **{'on' if settings.monitor.notify_on_every_poll else 'off'}**"
            )

    @app_commands.command(name="location", description="Update store location settings")
    @app_commands.describe(
        zip_code="ZIP code (e.g. 10001)",
        state="State abbreviation (e.g. NY)",
        store_id="Target store ID",
    )
    async def location(
        self,
        interaction: discord.Interaction,
        zip_code: str | None = None,
        state: str | None = None,
        store_id: str | None = None,
    ) -> None:
        """Update the Target store location for stock checks."""
        settings = self.bot.settings
        changes: list[str] = []

        if zip_code is not None:
            settings.location.zip = zip_code
            changes.append(f"ZIP → **{zip_code}**")
        if state is not None:
            settings.location.state = state.upper()
            changes.append(f"State → **{state.upper()}**")
        if store_id is not None:
            settings.location.store_id = store_id
            changes.append(f"Store ID → **{store_id}**")

        if not changes:
            await interaction.response.send_message(
                f"📍 Current location: ZIP **{settings.location.zip}**, "
                f"State **{settings.location.state}**, "
                f"Store **{settings.location.store_id}**",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "📍 Location updated:\n" + "\n".join(f"• {c}" for c in changes)
        )

    @app_commands.command(name="help", description="Show all available commands")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Display help information for all commands."""
        embed = discord.Embed(
            title="🤖 ScrapeBot Commands",
            description="Manage your Target stock monitor from Discord.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="📦 Product Management",
            value=(
                "`/add` — Add a product to track\n"
                "`/remove` — Remove a tracked product\n"
                "`/list` — Show all tracked products\n"
                "`/edit` — Edit product settings\n"
                "`/enable` — Re-enable a disabled product\n"
                "`/disable` — Disable a product without removing"
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Monitoring",
            value=(
                "`/status` — Show current bot status\n"
                "`/check` — Force immediate stock check\n"
                "`/checkout` — Manually trigger checkout\n"
                "`/buynow` — Instantly checkout any URL\n"
                "`/autocheckout` — Toggle auto-checkout\n"
                "`/pause` — Pause monitoring\n"
                "`/resume` — Resume monitoring"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ Configuration",
            value=(
                "`/config` — View or update config\n"
                "`/polling` — Set stock check polling rate\n"
                "`/location` — Update store location\n"
                "`/credentials` — View credentials status\n"
                "`/setcredentials` — Set Target login credentials\n"
                "`/clearcredentials` — Remove stored credentials"
            ),
            inline=False,
        )
        embed.set_footer(text="Stock alerts and checkout notifications appear automatically in this channel.")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # Credentials management
    # ------------------------------------------------------------------

    @app_commands.command(name="credentials", description="Check which credentials are configured")
    async def credentials(self, interaction: discord.Interaction) -> None:
        """Show which credentials are set (without revealing values)."""
        settings = self.bot.settings
        email_set = bool(settings.target_email)
        password_set = bool(settings.target_password)
        webhook_set = bool(settings.discord_webhook_url)

        embed = discord.Embed(
            title="🔑 Credentials Status",
            color=discord.Color.green() if (email_set and password_set) else discord.Color.orange(),
        )
        embed.add_field(
            name="Target Email",
            value=f"✅ Set (`{settings.target_email[:3]}***`)" if email_set else "❌ Not set",
            inline=False,
        )
        embed.add_field(
            name="Target Password",
            value="✅ Set (hidden)" if password_set else "❌ Not set",
            inline=False,
        )
        embed.add_field(
            name="Discord Webhook",
            value="✅ Set" if webhook_set else "⚠️ Not set (using bot channel instead)",
            inline=False,
        )

        # Readiness check
        if email_set and password_set:
            embed.add_field(
                name="Checkout Ready",
                value="✅ Credentials configured — checkout can proceed.",
                inline=False,
            )
        else:
            embed.add_field(
                name="Checkout Ready",
                value=(
                    "❌ Missing credentials — checkout will fail.\n"
                    "Use `/setcredentials` or run `--login` to save a browser session."
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setcredentials", description="Set Target login email and password")
    @app_commands.describe(
        email="Your Target account email",
        password="Your Target account password",
    )
    async def set_credentials(
        self,
        interaction: discord.Interaction,
        email: str,
        password: str,
    ) -> None:
        """Set Target login credentials (stored in memory, not persisted to disk)."""
        self.bot.settings.target_email = email.strip()
        self.bot.settings.target_password = password.strip()
        await interaction.response.send_message(
            "✅ Target credentials updated (stored in memory for this session).\n"
            "⚠️ These will be lost on bot restart. Add them to `.env` for persistence.",
            ephemeral=True,
        )

    @app_commands.command(name="clearcredentials", description="Remove stored Target credentials")
    async def clear_credentials(self, interaction: discord.Interaction) -> None:
        """Clear Target login credentials from memory."""
        self.bot.settings.target_email = None
        self.bot.settings.target_password = None
        await interaction.response.send_message(
            "🗑️ Target credentials cleared. Checkout will rely on saved browser session.",
            ephemeral=True,
        )
