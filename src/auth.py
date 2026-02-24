"""
src/auth.py — Playwright-based login and Bearer token extraction.

Strategy (Teams v2 / BFF architecture)
---------------------------------------
Teams v2 uses a Backend-for-Frontend pattern: the browser never calls
graph.microsoft.com directly. It talks to teams.microsoft.com/api/... instead.

However, MSAL.js (the Microsoft auth library bundled in the Teams app) still
caches tokens for all scopes—including Graph—in browser localStorage.

We:
  1. Log in (or resume saved session).
  2. Wait for Teams to fully load.
  3. Call page.evaluate() to read MSAL's localStorage token cache directly.
  4. Return the first unexpired token whose target scope includes graph.microsoft.com.

This works regardless of whether the page ever makes a direct Graph API call.
"""
import json
import os
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext

log = logging.getLogger("backup_teams.auth")

STATE_FILE = "state.json"
TEAMS_URL  = "https://teams.microsoft.com/v2/"


# ─── MSAL localStorage extraction ─────────────────────────────────────────────

_MSAL_JS = """
() => {
    /*
     Read ALL localStorage entries and find one that:
       - is a JSON object with a "secret" field (MSAL access token entry)
       - "secret" starts with "ey" (JWT)
       - "target" (scope string) mentions graph.microsoft.com
       - is not expired  (expiresOn is a Unix timestamp in seconds)

     MSAL key formats vary across versions:
       <client_id>-login.windows.net-accesstoken-<...>-graph.microsoft.com-...
       <many variants>
     So we scan all keys, not just specific patterns.
    */
    const now = Math.floor(Date.now() / 1000);
    const candidates = [];

    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        try {
            const raw = localStorage.getItem(key);
            if (!raw || raw.length < 100) continue;
            const obj = JSON.parse(raw);
            if (!obj || typeof obj !== 'object') continue;

            const secret  = obj.secret  || obj.access_token || obj.token;
            const target  = obj.target  || obj.scope        || obj.scopes || '';
            const expires = obj.expiresOn || obj.expires_on || obj.ext_expires_on || 0;

            if (!secret || typeof secret !== 'string' || !secret.startsWith('ey'))  continue;
            if (!target || typeof target !== 'string') continue;
            if (!target.toLowerCase().includes('graph')) continue;
            if (expires && (parseInt(expires, 10) < now)) continue;  // expired

            candidates.push({ token: secret, scope: target, expires: expires });
        } catch(e) {}
    }

    // Return the candidate that expires latest (most valid)
    if (candidates.length === 0) return null;
    candidates.sort((a, b) => (parseInt(b.expires) || 0) - (parseInt(a.expires) || 0));
    return candidates[0].token;
}
"""


def _extract_token_from_storage(page) -> Optional[str]:
    """Run MSAL cache extraction JS in the page context."""
    try:
        token = page.evaluate(_MSAL_JS)
        return token if isinstance(token, str) and token.startswith("ey") else None
    except Exception as exc:
        log.debug("localStorage extraction failed: %s", exc)
        return None


# ─── Login helper ─────────────────────────────────────────────────────────────

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
        try:
            page.click("#idSIButton9", timeout=6_000)
        except Exception:
            pass
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


# ─── Public API ───────────────────────────────────────────────────────────────

def get_bearer_token() -> str:
    """
    Launch Chromium, wait for Teams to load, extract a Graph API access token
    from MSAL's localStorage cache, close the browser, and return the token.

    Raises RuntimeError if no token is found within the timeout.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if Path(STATE_FILE).exists():
            log.info("Resuming saved browser session from %s", STATE_FILE)
            context: BrowserContext = browser.new_context(storage_state=STATE_FILE)
        else:
            log.info("No saved session found — will log in fresh.")
            context = browser.new_context()

        page = context.new_page()

        log.info("Navigating to Teams…")
        page.goto(TEAMS_URL)
        page.wait_for_timeout(4_000)

        # Handle login redirect
        if any(host in page.url for host in ("login.microsoftonline.com", "login.live.com")):
            log.info("Login page detected — authenticating…")
            _do_login(page)
            log.info("Waiting for Teams to load (up to 5 min for 2FA)…")
            base = TEAMS_URL.rstrip("/")
            page.wait_for_url(f"{base}/**", timeout=300_000)
            page.wait_for_timeout(5_000)

        # Wait for Teams to fully populate its MSAL cache (network activity settles)
        log.info("Teams loaded — waiting for MSAL cache to populate…")
        page.wait_for_load_state("networkidle", timeout=30_000)
        page.wait_for_timeout(2_000)

        # Extract Graph token from MSAL localStorage
        token = _extract_token_from_storage(page)

        if not token:
            # One more attempt after a brief extra wait
            log.info("Token not found yet — waiting 5 more seconds…")
            page.wait_for_timeout(5_000)
            token = _extract_token_from_storage(page)

        # Persist session
        context.storage_state(path=STATE_FILE)
        log.info("Session saved to %s", STATE_FILE)
        browser.close()

    if not token:
        raise RuntimeError(
            "Could not find a Graph API token in MSAL localStorage cache.\n"
            "This usually means MSAL hasn't requested a Graph token yet.\n"
            "Try: delete state.json and re-run to force a fresh login, which "
            "triggers MSAL to fetch tokens for all configured scopes."
        )

    log.info("Graph API token extracted from MSAL cache ✓")
    return token
