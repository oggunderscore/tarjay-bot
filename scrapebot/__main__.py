from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from colorama import Fore, Style, init as colorama_init
from dotenv import load_dotenv

from scrapebot.config import Settings, load_settings
from scrapebot.monitor import MonitorService

colorama_init(autoreset=True)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrapebot",
        description="Monitor Target.com stock and optionally auto-checkout with Discord alerts.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to main config YAML (default: config.yaml)",
    )
    parser.add_argument(
        "--products",
        default="products.yaml",
        help="Path to products YAML (default: products.yaml)",
    )
    parser.add_argument(
        "--monitor-only",
        action="store_true",
        help="Disable checkout even if enabled in config",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open browser to log into Target and save session, then exit",
    )
    parser.add_argument(
        "--bot",
        action="store_true",
        help="Run as a Discord bot with slash commands (requires DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def run_bot(settings: Settings, products_path: Path) -> None:
    """Launch the Discord bot with the monitor integrated.

    This uses bot.run() which manages its own asyncio event loop,
    so it must be called from a synchronous context (not inside asyncio.run).
    """
    from scrapebot.bot.client import ScrapeBot

    token = os.getenv("DISCORD_BOT_TOKEN")
    channel_id = os.getenv("DISCORD_CHANNEL_ID")

    if not token:
        print(
            f"{Fore.RED}Error: DISCORD_BOT_TOKEN is not set in .env{Style.RESET_ALL}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not channel_id:
        print(
            f"{Fore.RED}Error: DISCORD_CHANNEL_ID is not set in .env{Style.RESET_ALL}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        channel_id_int = int(channel_id)
    except ValueError:
        print(
            f"{Fore.RED}Error: DISCORD_CHANNEL_ID must be a numeric ID{Style.RESET_ALL}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"{Fore.CYAN}Starting ScrapeBot Discord bot...{Style.RESET_ALL}")
    bot = ScrapeBot(
        settings=settings,
        products_path=products_path,
        notification_channel_id=channel_id_int,
    )
    bot.run(token, log_handler=None)


async def async_main(argv: list[str] | None = None) -> int:
    """Async entry point for non-bot modes (monitor, login)."""
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    root = Path.cwd()
    products_path = root / args.products

    try:
        settings = load_settings(
            config_path=root / args.config,
            products_path=products_path,
            monitor_only=args.monitor_only,
        )
    except FileNotFoundError as exc:
        print(f"{Fore.RED}Error: {exc}{Style.RESET_ALL}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"{Fore.RED}Error: {exc}{Style.RESET_ALL}", file=sys.stderr)
        return 1

    service = MonitorService(settings)

    if args.login:
        try:
            await service.login_only()
        except RuntimeError as exc:
            print(f"{Fore.RED}Login failed: {exc}{Style.RESET_ALL}", file=sys.stderr)
            return 1
        print(f"{Fore.GREEN}Login session saved to {settings.browser.user_data_dir}{Style.RESET_ALL}")
        return 0

    print(f"{Fore.CYAN}ScrapeBot monitoring {len(settings.products)} product(s)...{Style.RESET_ALL}")
    await service.run()
    return 0


def main() -> None:
    """Main entry point — routes to bot mode or async monitor mode."""
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)

    # Bot mode runs synchronously (discord.py manages its own event loop)
    if args.bot:
        root = Path.cwd()
        products_path = root / args.products
        try:
            settings = load_settings(
                config_path=root / args.config,
                products_path=products_path,
                monitor_only=args.monitor_only,
                allow_empty_products=True,
            )
        except FileNotFoundError as exc:
            print(f"{Fore.RED}Error: {exc}{Style.RESET_ALL}", file=sys.stderr)
            raise SystemExit(1)
        except ValueError as exc:
            print(f"{Fore.RED}Error: {exc}{Style.RESET_ALL}", file=sys.stderr)
            raise SystemExit(1)

        try:
            run_bot(settings, products_path)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Bot stopped by user.{Style.RESET_ALL}")
        raise SystemExit(0)

    # Non-bot modes use asyncio.run()
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Stopped by user.{Style.RESET_ALL}")
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
