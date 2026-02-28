"""
tests/test_admin_consent.py — Diagnostic tool for University IT Restrictions.

This script opens a visible browser and attempts to log into a widely-used
Standard Third-Party Application (PnP Management Shell) requesting standard
`Files.Read.All` permissions via the official Microsoft OAuth2 flow.

If the University IT Admin has disabled "User Consent for Applications"
(which is extremely common in Education tenants), this test will visually
result in a "Need admin approval" or "Approval required" screen, proving
that a standard OAuth Cloud Architecture will be blocked.

Run this using:
    python -m tests.test_admin_consent
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# We use the PnP Management Shell multi-tenant Client ID as our test proxy.
# It's an official Microsoft open-source tool, but it's classified as a third-party
# application in Azure AD, which makes it perfect to test if your school blocks consent.
TEST_CLIENT_ID = "31359c7f-bd7e-475c-86db-fdb8c937548e"
REDIRECT_URI = "https://pnp.github.io/pnpcore/auth.html"
SCOPES = "Files.Read.All offline_access"

OAUTH_URL = (
    f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    f"?client_id={TEST_CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri={REDIRECT_URI}"
    f"&scope={SCOPES}"
    f"&response_mode=query"
    f"&prompt=consent"
)

def run_test():
    email = os.getenv("EMAIL", "")
    password = os.getenv("PASSWORD", "")

    if not email or not password:
        print("❌ ERROR: EMAIL and PASSWORD must be set in your .env file.")
        sys.exit(1)

    print("\n=======================================================")
    print("🔍 DIAGNOSTIC: Testing University Admin Consent Policies")
    print("=======================================================\n")
    print("We are going to attempt a standard OAuth2 flow for Files.Read.All")
    print("using a known multi-tenant application.")
    print("\nLaunching browser... WATCH the browser window carefully.\n")

    with sync_playwright() as p:
        # We launch HEADED (visible) so you can see exactly what the Admin policy does.
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # 1. Navigate to the OAuth Consent URL
        page.goto(OAUTH_URL)

        # 2. Wait for Microsoft Login Email Field
        try:
            page.wait_for_selector('input[type="email"]', timeout=15000)
            page.fill('input[type="email"]', email)
            page.click('input[type="submit"]')
        except Exception as e:
            print("Could not find the email login field. Microsoft might have loaded a different screen.")

        # 3. Wait for Microsoft Login Password Field
        try:
            page.wait_for_selector('input[type="password"]', timeout=15000)
            page.fill('input[type="password"]', password)
            page.wait_for_timeout(1000) # Buffer for UI animations
            page.click('input[type="submit"]')
        except Exception as e:
            pass
        
        # 4. Stay open so the User can see the result
        print("-------------------------------------------------------")
        print("👀 LOOK AT THE BROWSER WINDOW.")
        print("If you see 'Permissions requested', your IT Admin ALLOWS standard OAuth apps!")
        print("If you see 'Need admin approval' or an error block, your IT Admin BLOCKS apps.")
        print("-------------------------------------------------------")
        print("Press Ctrl+C in this terminal to close the test when you are done reading the screen.")
        
        try:
            # Leave the browser open indefinitely so the user can inspect the URL, 2FA, and MS messages.
            page.wait_for_timeout(3600_000) 
        except KeyboardInterrupt:
            print("\nClosing test.")
        finally:
            browser.close()

if __name__ == "__main__":
    run_test()
