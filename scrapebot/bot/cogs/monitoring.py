"""Monitoring control slash commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from scrapebot.models import CheckoutResult, StockStatus


class MonitoringCog(commands.Cog, name="Monitoring"):
    """Control the stock monitoring loop via Discord."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="status", description="Show current bot and monitoring status")
    async def status(self, interaction: discord.Interaction) -> None:
        """Display current monitoring state and product statuses."""
        monitoring = "🟢 Running" if self.bot.is_monitoring else "🔴 Stopped"
        checkout_enabled = "✅ Enabled" if self.bot.settings.checkout.enabled else "❌ Disabled"
        auto_checkout = "🤖 Auto" if self.bot.settings.checkout.auto_checkout else "🖐️ Manual"
        poll_interval = self.bot.settings.monitor.poll_interval_seconds

        embed = discord.Embed(
            title="📊 ScrapeBot Status",
            color=discord.Color.green() if self.bot.is_monitoring else discord.Color.red(),
        )
        embed.add_field(name="Monitor", value=monitoring, inline=True)
        embed.add_field(name="Checkout", value=checkout_enabled, inline=True)
        embed.add_field(name="Auto-Checkout", value=auto_checkout, inline=True)
        embed.add_field(name="Poll Interval", value=f"{poll_interval}s", inline=True)

        # Credentials status
        has_creds = bool(self.bot.settings.target_email and self.bot.settings.target_password)
        creds_status = "✅ Set" if has_creds else "❌ Missing"
        embed.add_field(name="Credentials", value=creds_status, inline=True)

        embed.add_field(
            name="Products Tracked",
            value=str(len(self.bot.settings.products)),
            inline=True,
        )

        # Show per-product status
        if self.bot.monitor:
            status_lines: list[str] = []
            for tcin, state in self.bot.monitor.states.items():
                product = state.product
                if state.last_snapshot:
                    snap = state.last_snapshot
                    emoji = "🟢" if snap.is_available_for_shipping else "🔴"
                    status_text = snap.shipping_status.value
                    qty = f" (qty: {snap.shipping_quantity:.0f})" if snap.shipping_quantity > 0 else ""
                    line = f"{emoji} **{product.name}**: `{status_text}`{qty}"
                else:
                    line = f"⏳ **{product.name}**: Waiting for first poll..."
                if state.checkout_in_progress:
                    line += " 🛒 Checkout in progress"
                elif state.checkout_completed:
                    line += " ✅ Order placed"
                status_lines.append(line)

            if status_lines:
                embed.add_field(
                    name="Product Status",
                    value="\n".join(status_lines),
                    inline=False,
                )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pause", description="Pause stock monitoring")
    async def pause(self, interaction: discord.Interaction) -> None:
        """Pause the monitoring loop without shutting down the bot."""
        if not self.bot.is_monitoring:
            await interaction.response.send_message(
                "⏸️ Monitoring is already paused.", ephemeral=True
            )
            return
        self.bot.stop_monitor()
        await interaction.response.send_message("⏸️ Monitoring paused. Use `/resume` to continue.")

    @app_commands.command(name="resume", description="Resume stock monitoring")
    async def resume(self, interaction: discord.Interaction) -> None:
        """Resume the monitoring loop."""
        if self.bot.is_monitoring:
            await interaction.response.send_message(
                "▶️ Monitoring is already running.", ephemeral=True
            )
            return
        self.bot.resume_monitor()
        await interaction.response.send_message("▶️ Monitoring resumed!")

    @app_commands.command(name="autocheckout", description="Toggle automatic checkout on/off")
    @app_commands.describe(enabled="Turn auto-checkout on (True) or off (False)")
    async def autocheckout(self, interaction: discord.Interaction, enabled: bool) -> None:
        """Toggle whether the bot automatically attempts checkout when items come in stock."""
        self.bot.settings.checkout.auto_checkout = enabled
        if enabled:
            await interaction.response.send_message(
                "🤖 Auto-checkout **enabled** — the bot will automatically attempt purchase when items come in stock."
            )
        else:
            await interaction.response.send_message(
                "🖐️ Auto-checkout **disabled** — use `/checkout <product>` to manually trigger checkout."
            )

    @app_commands.command(name="checkout", description="Manually trigger checkout for a product")
    @app_commands.describe(name_or_tcin="Product name or TCIN to checkout")
    async def checkout(self, interaction: discord.Interaction, name_or_tcin: str) -> None:
        """Manually trigger a checkout attempt for a specific product."""
        if not self.bot.monitor:
            await interaction.response.send_message(
                "❌ Monitor is not initialized.", ephemeral=True
            )
            return

        if not self.bot.settings.checkout.enabled:
            await interaction.response.send_message(
                "❌ Checkout is disabled. Use `/config checkout_enabled:True` to enable.", ephemeral=True
            )
            return

        # Credentials check
        if not self.bot.settings.target_email or not self.bot.settings.target_password:
            await interaction.response.send_message(
                "❌ No Target credentials configured. Use `/setcredentials` or run `--login` to save a browser session.",
                ephemeral=True,
            )
            return

        target = name_or_tcin.strip().lower()
        state = None
        for s in self.bot.monitor.states.values():
            if s.product.tcin == target or s.product.name.lower() == target:
                state = s
                break

        if not state:
            await interaction.response.send_message(
                f"❌ No product found matching `{name_or_tcin}`.", ephemeral=True
            )
            return

        if state.checkout_in_progress:
            await interaction.response.send_message(
                f"⏳ Checkout is already in progress for **{state.product.name}**.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🛒 Starting checkout for **{state.product.name}**..."
        )

        # Trigger checkout in background
        import asyncio
        asyncio.create_task(self.bot.monitor._trigger_checkout(state))

    @app_commands.command(name="buynow", description="Instantly checkout a product by URL")
    @app_commands.describe(
        url="Target.com product URL to buy now",
        max_price="Maximum price to pay (default: 999.99)",
        quantity="Quantity to purchase (default: 1)",
    )
    async def buynow(
        self,
        interaction: discord.Interaction,
        url: str,
        max_price: float = 999.99,
        quantity: int = 1,
    ) -> None:
        """Immediately attempt checkout on a Target product URL."""
        if not self.bot.monitor:
            await interaction.response.send_message(
                "❌ Monitor is not initialized.", ephemeral=True
            )
            return

        if not self.bot.settings.target_email or not self.bot.settings.target_password:
            await interaction.response.send_message(
                "❌ No Target credentials configured. Use `/setcredentials` first.",
                ephemeral=True,
            )
            return

        from scrapebot.config import ProductTarget, extract_tcin

        try:
            tcin = extract_tcin(url)
        except ValueError as exc:
            await interaction.response.send_message(
                f"❌ Invalid URL: {exc}", ephemeral=True
            )
            return

        quantity = max(1, min(10, quantity))
        product = ProductTarget(
            name=f"Quick Buy ({tcin})",
            url=url,
            max_quantity=quantity,
            max_price=max_price,
        )

        await interaction.response.send_message(
            f"🛒 Starting checkout for **{url}**\n"
            f"Max price: ${max_price:.2f} | Qty: {quantity}"
        )

        # Run checkout in background
        import asyncio

        async def _run_buynow():
            try:
                outcome = await self.bot.monitor.checkout.run_checkout(product)
                if outcome.result == CheckoutResult.SUCCESS:
                    pass  # Notifier already sent success message
                elif outcome.result == CheckoutResult.OUT_OF_STOCK:
                    self.bot.monitor.notifier.checkout_error(
                        f"Out of Stock: {outcome.message}", url
                    )
                elif outcome.result == CheckoutResult.PRICE_TOO_HIGH:
                    pass  # Already notified
                else:
                    self.bot.monitor.notifier.checkout_error(
                        f"Failed: {outcome.message}", url
                    )
            except Exception as exc:
                self.bot.monitor.notifier.checkout_error(f"Crash: {exc}", url)

        asyncio.create_task(_run_buynow())

    @app_commands.command(name="check", description="Force an immediate stock check on a product")
    @app_commands.describe(name_or_tcin="Product name or TCIN to check now")
    async def check_now(
        self, interaction: discord.Interaction, name_or_tcin: str
    ) -> None:
        """Trigger an immediate stock check for one product."""
        if not self.bot.monitor:
            await interaction.response.send_message(
                "❌ Monitor is not initialized.", ephemeral=True
            )
            return

        target = name_or_tcin.strip().lower()
        state = None
        for s in self.bot.monitor.states.values():
            if s.product.tcin == target or s.product.name.lower() == target:
                state = s
                break

        if not state:
            await interaction.response.send_message(
                f"❌ No product found matching `{name_or_tcin}`.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            snapshot = await self.bot.monitor._check_stock_via_browser(state.product)
            state.last_snapshot = snapshot
        except Exception as exc:
            await interaction.followup.send(f"❌ Stock check error: `{exc}`")
            return

        emoji = "🟢" if self.bot.monitor._is_in_stock(snapshot) else "🔴"
        embed = discord.Embed(
            title=f"{emoji} Stock Check: {state.product.name}",
            url=state.product.url,
            color=discord.Color.green() if self.bot.monitor._is_in_stock(snapshot) else discord.Color.red(),
        )
        embed.add_field(name="Shipping", value=snapshot.shipping_status.value, inline=True)
        if snapshot.pickup_status:
            embed.add_field(name="Pickup", value=snapshot.pickup_status.value, inline=True)
        embed.add_field(name="Quantity", value=str(int(snapshot.shipping_quantity)), inline=True)
        embed.add_field(name="Sold Out", value="Yes" if snapshot.sold_out else "No", inline=True)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="refreshcookies", description="Re-visit Target.com to refresh the browser session")
    async def refresh_cookies(self, interaction: discord.Interaction) -> None:
        """Navigate to Target.com to refresh the browser session."""
        if not self.bot.monitor or not self.bot.monitor._page:
            await interaction.response.send_message(
                "❌ Browser is not running.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            page = self.bot.monitor._page
            await page.goto("https://www.target.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            await interaction.followup.send(
                f"🔄 Browser session refreshed.\n🔗 `{page.url}`"
            )
        except Exception as exc:
            await interaction.followup.send(f"❌ Failed to refresh: `{exc}`")
