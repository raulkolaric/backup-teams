"""
src/auth.py — Playwright-based login and Bearer token extraction.

Strategy
--------
1. Load state.json if it exists (re-uses a previous authenticated session).
2. Navigate to teams.microsoft.com.
3. If redirected to the Microsoft login page, fill in EMAIL/PASSWORD from .env.
4. Intercept every outgoing request header: the first request to
   graph.microsoft.com that carries "Authorization: Bearer ..." gives us
   the live token. We grab it and immediately close the browser.
5. Save the updated session to state.json for next time.

The token is valid for ~1 hour. teams_scraper.py will call get_bearer_token()
again automatically if it receives a 401 during a run.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext

log = logging.getLogger("backup_teams.auth")

STATE_FILE = "state.json"
TEAMS_URL  = "https://teams.microsoft.com"


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _do_login(page: Page) -> bool:
    """Fill in credentials on the Microsoft login page."""
    email    = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    if not email or not password:
        log.error("EMAIL / PASSWORD not set in .env — cannot auto-login.")
        return False

    try:
        page.wait_for_selector('input[type="email"]', timeout=30_000)
        page.fill('input[type="email"]', email)
        page.click("#idSIButton9")

        page.wait_for_selector('input[type="password"]', timeout=30_000)
        page.fill('input[type="password"]', password)
        page.wait_for_timeout(1_200)
        page.click("#idSIButton9")

        # "Stay signed in?" prompt — click Yes
        try:
            page.click("#idSIButton9", timeout=6_000)
        except Exception:
            pass

        # Prefer the web app over the desktop client redirect
        try:
            page.locator(
                'text=/Use (o aplicativo Web|the web app) em vez disso|instead/i'
            ).click(timeout=10_000)
        except Exception:
            pass

        return True
    except Exception as exc:
        log.warning("Auto-login failed: %s", exc)
        return False


def _extract_token_from_request(request) -> Optional[str]:
    """Return the Bearer token from a Graph API request header, or None."""
    if "graph.microsoft.com" not in request.url:
        return None
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


# ─── Public API ───────────────────────────────────────────────────────────────

def get_bearer_token() -> str:
    """
    Launch Chromium, log in (or resume session), intercept the first Graph API
    Bearer token, then close the browser and return the token string.

    Raises RuntimeError if no token is captured within the timeout.
    """
    captured: list = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        # Re-use saved session if available
        if Path(STATE_FILE).exists():
            log.info("Resuming saved browser session from %s", STATE_FILE)
            context: BrowserContext = browser.new_context(storage_state=STATE_FILE)
        else:
            log.info("No saved session found — will log in fresh.")
            context = browser.new_context()

        page = context.new_page()

        # Intercept every request to catch the token as early as possible
        def on_request(request):
            if not captured:
                tok = _extract_token_from_request(request)
                if tok:
                    log.info("Bearer token captured ✓")
                    captured.append(tok)

        page.on("request", on_request)

        log.info("Navigating to Teams…")
        page.goto(TEAMS_URL)
        page.wait_for_timeout(4_000)

        # If we ended up on the login page, authenticate
        if any(
            host in page.url
            for host in ("login.microsoftonline.com", "login.live.com")
        ):
            log.info("Login page detected — authenticating…")
            _do_login(page)
            log.info("Waiting for Teams to load (up to 5 min for 2FA)…")
            page.wait_for_url(f"{TEAMS_URL}/**", timeout=300_000)
            page.wait_for_timeout(4_000)

        # Wait up to 30 s for a Graph API call to appear so we can grab the token
        for _ in range(30):
            if captured:
                break
            # Trigger a navigation that forces Graph API calls
            page.goto(f"{TEAMS_URL}/_#/school/teams")
            page.wait_for_timeout(1_000)

        # Persist the session for next run before closing
        context.storage_state(path=STATE_FILE)
        log.info("Session saved to %s", STATE_FILE)
        browser.close()

    if not captured:
        raise RuntimeError(
            "Could not capture a Bearer token from the Teams session. "
            "Try deleting state.json and re-running to force a fresh login."
        )

    return captured[0]
