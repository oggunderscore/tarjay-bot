"""MCP server launcher that connects Playwright MCP to NSTBrowser via CDP.

This script acts as a wrapper: it launches the NSTBrowser profile, gets the CDP
endpoint, then exec's the Playwright MCP server with --cdp-endpoint pointed at it.

Used as the MCP server command in .kiro/settings/mcp.json.
"""

import os
import subprocess
import sys

from dotenv import load_dotenv


def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

    api_key = os.getenv("NSTBROWSER_API_KEY")
    profile_id = os.getenv("NSTBROWSER_PROFILE_ID")

    if not api_key or not profile_id:
        print("ERROR: NSTBROWSER_API_KEY and NSTBROWSER_PROFILE_ID must be set in .env", file=sys.stderr)
        sys.exit(1)

    from nstbrowser import NstbrowserClient

    client = NstbrowserClient(api_key=api_key)
    response = client.cdp_endpoints.connect_browser(
        profile_id=profile_id,
        config={
            "headless": False,
            "autoClose": False,
        },
    )

    ws_url = response["data"]["webSocketDebuggerUrl"]
    # Convert ws://host:port/devtools/browser/id to http://host:port for CDP endpoint
    cdp_endpoint = ws_url.replace("ws://", "http://").split("/devtools/browser")[0]

    print(f"NSTBrowser CDP: {ws_url}", file=sys.stderr)
    print(f"Starting Playwright MCP with --cdp-endpoint={cdp_endpoint}", file=sys.stderr)

    # Replace current process with the Playwright MCP server
    subprocess.run(
        ["npx", "@playwright/mcp@latest", f"--cdp-endpoint={cdp_endpoint}"],
        shell=True,
    )


if __name__ == "__main__":
    main()
