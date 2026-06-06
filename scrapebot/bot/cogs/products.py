"""Product management slash commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from scrapebot.config import ProductTarget, extract_tcin


class ProductsCog(commands.Cog, name="Products"):
    """Manage tracked products via Discord slash commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="add", description="Add a Target product to track")
    @app_commands.describe(
        url="Target.com product URL",
        name="Friendly product name",
        max_quantity="Maximum quantity to purchase (1-10)",
        max_price="Maximum price before aborting checkout",
    )
    async def add_product(
        self,
        interaction: discord.Interaction,
        url: str,
        name: str,
        max_quantity: int = 1,
        max_price: float = 999.99,
    ) -> None:
        """Add a new product to the monitoring list."""
        try:
            tcin = extract_tcin(url)
        except ValueError as exc:
            await interaction.response.send_message(
                f"❌ Invalid URL: {exc}", ephemeral=True
            )
            return

        # Check for duplicate
        for p in self.bot.settings.products:
            if p.tcin == tcin:
                await interaction.response.send_message(
                    f"⚠️ Product with TCIN `{tcin}` is already being tracked as **{p.name}**.",
                    ephemeral=True,
                )
                return

        max_quantity = max(1, min(10, max_quantity))
        product = ProductTarget(
            name=name,
            url=url,
            max_quantity=max_quantity,
            max_price=max_price,
        )
        self.bot.settings.products.append(product)
        self.bot.save_products()
        self.bot.reload_products()

        embed = discord.Embed(
            title="✅ Product Added",
            description=f"Now tracking **{name}**",
            color=discord.Color.green(),
        )
        embed.add_field(name="URL", value=url, inline=False)
        embed.add_field(name="TCIN", value=tcin, inline=True)
        embed.add_field(name="Max Qty", value=str(max_quantity), inline=True)
        embed.add_field(name="Max Price", value=f"${max_price:.2f}", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remove", description="Remove a tracked product")
    @app_commands.describe(name_or_tcin="Product name or TCIN to remove")
    async def remove_product(
        self,
        interaction: discord.Interaction,
        name_or_tcin: str,
    ) -> None:
        """Remove a product from the monitoring list."""
        target = name_or_tcin.strip().lower()
        found = None
        for p in self.bot.settings.products:
            if p.tcin == target or p.name.lower() == target:
                found = p
                break

        if not found:
            await interaction.response.send_message(
                f"❌ No product found matching `{name_or_tcin}`.", ephemeral=True
            )
            return

        self.bot.settings.products.remove(found)
        self.bot.save_products()
        self.bot.reload_products()

        await interaction.response.send_message(
            f"🗑️ Removed **{found.name}** (TCIN: `{found.tcin}`) from tracking."
        )

    @app_commands.command(name="list", description="Show all tracked products")
    async def list_products(self, interaction: discord.Interaction) -> None:
        """List all products currently being tracked."""
        products = self.bot.settings.products
        if not products:
            await interaction.response.send_message(
                "No products are being tracked. Use `/add` to add one.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📋 Tracked Products ({len(products)})",
            color=discord.Color.blurple(),
        )
        for p in products:
            status = "✅ Enabled" if p.enabled else "⏸️ Disabled"
            # Check if we have a last snapshot
            snapshot_info = ""
            if self.bot.monitor and p.tcin in self.bot.monitor.states:
                state = self.bot.monitor.states[p.tcin]
                if state.last_snapshot:
                    snap = state.last_snapshot
                    snapshot_info = f"\nStatus: `{snap.shipping_status.value}`"
                    if snap.shipping_quantity > 0:
                        snapshot_info += f" (qty: {snap.shipping_quantity:.0f})"

            embed.add_field(
                name=f"{p.name}",
                value=(
                    f"TCIN: `{p.tcin}` | {status}\n"
                    f"Max Qty: {p.max_quantity} | Max Price: ${p.max_price:.2f}"
                    f"{snapshot_info}\n"
                    f"[View on Target]({p.url})"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="enable", description="Enable a disabled product")
    @app_commands.describe(name_or_tcin="Product name or TCIN to enable")
    async def enable_product(
        self, interaction: discord.Interaction, name_or_tcin: str
    ) -> None:
        """Re-enable a disabled product."""
        product = self._find_product(name_or_tcin)
        if not product:
            await interaction.response.send_message(
                f"❌ No product found matching `{name_or_tcin}`.", ephemeral=True
            )
            return
        product.enabled = True
        self.bot.save_products()
        self.bot.reload_products()
        await interaction.response.send_message(f"✅ **{product.name}** is now enabled.")

    @app_commands.command(name="disable", description="Disable a product without removing it")
    @app_commands.describe(name_or_tcin="Product name or TCIN to disable")
    async def disable_product(
        self, interaction: discord.Interaction, name_or_tcin: str
    ) -> None:
        """Disable a product without removing it from config."""
        product = self._find_product(name_or_tcin)
        if not product:
            await interaction.response.send_message(
                f"❌ No product found matching `{name_or_tcin}`.", ephemeral=True
            )
            return
        product.enabled = False
        self.bot.save_products()
        self.bot.reload_products()
        await interaction.response.send_message(f"⏸️ **{product.name}** is now disabled.")

    @app_commands.command(name="edit", description="Edit a product's settings")
    @app_commands.describe(
        name_or_tcin="Product name or TCIN to edit",
        new_name="New product name (optional)",
        max_quantity="New max quantity 1-10 (optional)",
        max_price="New max price (optional)",
    )
    async def edit_product(
        self,
        interaction: discord.Interaction,
        name_or_tcin: str,
        new_name: str | None = None,
        max_quantity: int | None = None,
        max_price: float | None = None,
    ) -> None:
        """Edit properties of a tracked product."""
        product = self._find_product(name_or_tcin)
        if not product:
            await interaction.response.send_message(
                f"❌ No product found matching `{name_or_tcin}`.", ephemeral=True
            )
            return

        changes: list[str] = []
        if new_name is not None:
            product.name = new_name
            changes.append(f"Name → **{new_name}**")
        if max_quantity is not None:
            product.max_quantity = max(1, min(10, max_quantity))
            changes.append(f"Max Qty → **{product.max_quantity}**")
        if max_price is not None:
            product.max_price = max_price
            changes.append(f"Max Price → **${max_price:.2f}**")

        if not changes:
            await interaction.response.send_message(
                "Nothing to change. Provide at least one field to update.", ephemeral=True
            )
            return

        self.bot.save_products()
        self.bot.reload_products()
        await interaction.response.send_message(
            f"✏️ Updated **{product.name}**:\n" + "\n".join(f"• {c}" for c in changes)
        )

    def _find_product(self, name_or_tcin: str) -> ProductTarget | None:
        """Find a product by name or TCIN (case-insensitive)."""
        target = name_or_tcin.strip().lower()
        for p in self.bot.settings.products:
            if p.tcin == target or p.name.lower() == target:
                return p
        return None
