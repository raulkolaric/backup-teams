"""
src/auth.py — Playwright-based login and Bearer token extraction.

Strategy (Teams v2)
-------------------
Teams v2 calls `authsvc/v1.0/authz` during startup. The RESPONSE body
contains a JSON payload with access tokens for different Microsoft services.
We intercept that response and pull out the token intended for
graph.microsoft.com — that's the one we need for all our API calls.

Fallback: if the response body doesn't have a recognisable structure, we also
watch for any direct request to graph.microsoft.com that carries a Bearer
header (works for some tenant configurations).
"""
import json
import os
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext, Response

log = logging.getLogger("backup_teams.auth")

STATE_FILE = "state.json"
TEAMS_URL  = "https://teams.microsoft.com/v2/"

# Authsvc endpoint that distributes tokens during Teams startup
_AUTHSVC_URL = "authsvc/v1.0/authz"

# Fallback: direct Graph API requests
_GRAPH_DOMAIN = "graph.microsoft.com"


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _extract_from_authsvc_body(body: bytes) -> Optional[str]:
    """
    Parse the authsvc response body and return the graph-scope access token.

    Teams authsvc returns a JSON array of token objects, each with
    an "application" key and a "token" key, e.g.:
        [{"application": "graph", "token": "eyJ…"}, …]

    Some tenants wrap this in {"tokens": […]}.
    We also accept any "eyJ…" token whose audience claim ("aud") contains
    "graph.microsoft.com" after base64 decoding the JWT payload.
    """
    try:
        data = json.loads(body)
    except Exception:
        return None

    # Normalise to a flat list of candidate token objects
    candidates = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        for key in ("tokens", "value", "accessTokens"):
            if isinstance(data.get(key), list):
                candidates = data[key]
                break

    if not candidates:
        return None

    # Priority 1 — explicit "graph" application tag
    for item in candidates:
        if isinstance(item, dict):
            app = str(item.get("application", "")).lower()
            token = item.get("token") or item.get("accessToken") or item.get("value")
            if token and isinstance(token, str) and token.startswith("ey"):
                if "graph" in app:
                    return token

    # Priority 2 — JWT whose payload "aud" claim targets graph
    for item in candidates:
        if isinstance(item, dict):
            token = item.get("token") or item.get("accessToken") or item.get("value")
            if token and isinstance(token, str) and token.startswith("ey"):
                if _jwt_aud_is_graph(token):
                    return token

    return None


def _jwt_aud_is_graph(token: str) -> bool:
    """Decode the JWT payload (base64) and check if aud contains graph."""
    import base64
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return False
        # Add padding
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        aud = payload.get("aud", "")
        return "graph" in str(aud).lower()
    except Exception:
        return False


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

        # "Stay signed in?" prompt
        try:
            page.click("#idSIButton9", timeout=6_000)
        except Exception:
            pass

        # Prefer web app over desktop client
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
    Launch Chromium, log in (or resume session), capture the Graph API token
    from the authsvc response body, close the browser, return the token.

    Raises RuntimeError if no token is captured within the timeout.
    """
    captured: list = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if Path(STATE_FILE).exists():
            log.info("Resuming saved browser session from %s", STATE_FILE)
            context: BrowserContext = browser.new_context(storage_state=STATE_FILE)
        else:
            log.info("No saved session found — will log in fresh.")
            context = browser.new_context()

        page = context.new_page()

        # ── Strategy 1: intercept authsvc RESPONSE body ───────────────────────
        def on_response(response: Response):
            if captured:
                return
            if _AUTHSVC_URL not in response.url:
                return
            try:
                body = response.body()
                token = _extract_from_authsvc_body(body)
                if token:
                    log.info("Graph token extracted from authsvc response ✓")
                    captured.append(token)
                else:
                    log.debug("authsvc response didn't contain a graph token (trying fallback)")
            except Exception as exc:
                log.debug("Could not read authsvc response: %s", exc)

        # ── Strategy 2 (fallback): direct Graph API request header ────────────
        def on_request(request):
            if captured:
                return
            if _GRAPH_DOMAIN not in request.url:
                return
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer ey"):
                token = auth[len("Bearer "):]
                log.info("Graph token captured from direct Graph request ✓")
                captured.append(token)

        page.on("response", on_response)
        page.on("request",  on_request)

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

        # Wait up to 30 s — authsvc fires automatically on Teams startup
        log.info("Teams loaded — waiting for Graph token from authsvc (up to 30s)…")
        for i in range(30):
            if captured:
                break
            page.wait_for_timeout(1_000)
            if i == 15 and not captured:
                log.info("Still waiting — soft-reloading page to re-trigger authsvc…")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    pass

        context.storage_state(path=STATE_FILE)
        log.info("Session saved to %s", STATE_FILE)
        browser.close()

    if not captured:
        raise RuntimeError(
            "Could not capture a Graph API Bearer token.\n"
            "Try deleting state.json, run again, and complete login manually."
        )

    return captured[0]
