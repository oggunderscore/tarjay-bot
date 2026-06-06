"""Launch NSTBrowser profile and start Playwright MCP server connected to it via CDP.

Usage:
    python start_mcp.py

This script:
1. Connects to NSTBrowser using your API key and profile ID from .env
2. Gets the CDP WebSocket URL for the launched profile
3. Prints the MCP config you can use, OR starts the Playwright MCP server directly
"""

import json
import os
import subprocess
import sys

from dotenv import load_dotenv
from nstbrowser import NstbrowserClient


def main():
    load_dotenv()

    api_key = os.getenv("NSTBROWSER_API_KEY")
    profile_id = os.getenv("NSTBROWSER_PROFILE_ID")

    if not api_key or not profile_id:
        print("ERROR: NSTBROWSER_API_KEY and NSTBROWSER_PROFILE_ID must be set in .env")
        sys.exit(1)

    print(f"Launching NSTBrowser profile {profile_id}...")
    client = NstbrowserClient(api_key=api_key)

    response = client.cdp_endpoints.connect_browser(
        profile_id=profile_id,
        config={
            "headless": False,
            "autoClose": False,
        },
    )

    ws_url = response["data"]["webSocketDebuggerUrl"]
    # Playwright MCP --cdp-endpoint expects an http URL for the /json/version endpoint
    # but also accepts ws:// URLs directly
    cdp_endpoint = ws_url.replace("ws://", "http://").split("/devtools/browser")[0]

    print(f"\nNSTBrowser CDP WebSocket: {ws_url}")
    print(f"CDP HTTP endpoint: {cdp_endpoint}")
    print()
    print("=" * 60)
    print("Playwright MCP is now starting connected to NSTBrowser...")
    print("=" * 60)
    print()

    # Start Playwright MCP server with the CDP endpoint
    subprocess.run(
        ["npx", "@playwright/mcp@latest", f"--cdp-endpoint={cdp_endpoint}"],
        shell=True,
    )


if __name__ == "__main__":
    main()
