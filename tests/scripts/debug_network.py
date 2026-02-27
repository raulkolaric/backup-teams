"""
debug_network.py — Diagnostic script to dump all Teams network traffic.

Run:  python debug_network.py
      Log output goes to network_dump.log and the terminal.

What to look for in the log:
  - Any URL containing "authsvc", "auth", "token"
  - Response bodies that contain "ey" (JWT prefix) or "token"
  - Any URL that hits graph.microsoft.com with an Authorization header
"""
import json
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

STATE_FILE = "state.json"
TEAMS_URL  = "https://teams.microsoft.com/v2/"
LOG_FILE   = "network_dump.log"

log_lines = []

def log(msg: str):
    print(msg)
    log_lines.append(msg)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if Path(STATE_FILE).exists():
            log("Using saved session...")
            context = browser.new_context(storage_state=STATE_FILE)
        else:
            log("No session — proceeding without (will hit login page)")
            context = browser.new_context()

        page = context.new_page()

        # ── Log ALL requests with Authorization headers ────────────────────────
        def on_request(req):
            auth = req.headers.get("authorization", "")
            if auth:
                log(f"\n[REQUEST] {req.url[:120]}")
                log(f"  Auth header: {auth[:80]}...")

        # ── Log ALL JSON responses ─────────────────────────────────────────────
        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            url = resp.url
            # Only log Microsoft API calls
            if not any(d in url for d in [
                "microsoft.com", "microsoftonline.com",
                "skype.com", "office.com", "office365.com"
            ]):
                return
            try:
                body_bytes = resp.body()
                body_str   = body_bytes.decode("utf-8", errors="replace")

                # Only log if it looks interesting
                if not any(k in body_str for k in ["token", "ey", "access", "Bearer"]):
                    return

                log(f"\n[RESPONSE] {url[:120]}")
                log(f"  Status: {resp.status}")
                log(f"  Body preview (first 800 chars):")

                # Pretty-print JSON if possible
                try:
                    parsed = json.loads(body_str)
                    pretty = json.dumps(parsed, indent=2)
                    log(pretty[:2000])
                except Exception:
                    log(body_str[:800])
            except Exception as e:
                log(f"  [Could not read body: {e}]")

        page.on("request",  on_request)
        page.on("response", on_response)

        log(f"Navigating to {TEAMS_URL}...")
        page.goto(TEAMS_URL)

        log("\n==================================================")
        log("Teams is loading. Watch the log below for tokens.")
        log("DO NOT CLOSE THE BROWSER — wait 30 seconds.")
        log("==================================================\n")

        # Wait and capture
        page.wait_for_timeout(30_000)

        # Save session
        context.storage_state(path=STATE_FILE)
        log("\nSession saved. Closing browser.")
        browser.close()

    # Write full log to file
    Path(LOG_FILE).write_text("\n".join(log_lines))
    log(f"\n\nFull log written to: {LOG_FILE}")


if __name__ == "__main__":
    main()
