"""Experiment: log into Deezer via the shared headless-chrome container and
extract a fresh `arl` cookie, to see whether this can be automated reliably.

Can be run standalone for manual testing:

    docker compose run --rm arl-sentinel python auto_login.py
"""

import os
import socket
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

DEEZER_LOGIN_URL = "https://www.deezer.com/login"
DEFAULT_CDP_URL = "http://headless-chrome:9222"


def _resolve_to_ip(cdp_url: str) -> str:
    """Replace the hostname in a CDP URL with its resolved IP.

    Chrome's DevTools server rejects connections whose Host header isn't
    "localhost" or a literal IP (DNS-rebinding protection), so a plain
    Docker DNS name like "headless-chrome" gets a 500. Resolving to the
    container's IP keeps --remote-allow-origins=* effective. The URL must
    be http(s):// (not ws://) so Playwright fetches /json/version first to
    discover the real /devtools/browser/<id> websocket path.
    """
    parsed = urlparse(cdp_url)
    ip = socket.gethostbyname(parsed.hostname)
    return parsed._replace(netloc=f"{ip}:{parsed.port}").geturl()


COOKIE_CONSENT_BUTTON_TEXTS = ["Accepteren", "Alles accepteren", "Accept all", "Accept"]


def _dismiss_cookie_banner(page) -> None:
    """Click through Deezer's cookie-consent dialog if present."""
    for text in COOKIE_CONSENT_BUTTON_TEXTS:
        button = page.get_by_role("button", name=text, exact=False)
        if button.count() > 0:
            try:
                button.first.click(timeout=2000)
                return
            except Exception:
                continue


LOGIN_BUTTON_TEXTS = ["Inloggen", "Log in", "Sign in"]


def _click_login_button(page) -> None:
    """Click the login/submit button if a login form is present."""
    for text in LOGIN_BUTTON_TEXTS:
        button = page.get_by_role("button", name=text, exact=False)
        if button.count() > 0:
            try:
                button.first.click(timeout=2000)
                return
            except Exception:
                continue
    button = page.locator('button[type="submit"]')
    if button.count() > 0:
        try:
            button.first.click(timeout=2000)
        except Exception:
            pass


def attempt_auto_login(email: str, password: str, cdp_url: str = DEFAULT_CDP_URL, debug_dir: str = "/data") -> dict:
    """Try to log into Deezer and return {"success", "reason", "arl"}.

    Uses a fresh, isolated browser context (no shared cookies) so each
    attempt starts from a logged-out state.
    """
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(_resolve_to_ip(cdp_url))
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(DEEZER_LOGIN_URL, timeout=30000)
            _dismiss_cookie_banner(page)
            page.wait_for_selector('input[type="email"]', timeout=15000)
            page.fill('input[type="email"]', email)
            page.fill('input[type="password"]', password)
            _click_login_button(page)

            # Deezer shows a timed "Security check in progress" interstitial
            # after login that claims to redirect on its own once done - poll
            # for the arl cookie (or a real reCAPTCHA) rather than a flat sleep.
            # The redirect can land back on a pre-filled (but not submitted)
            # login form, so retry the login click while polling.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                _dismiss_cookie_banner(page)

                if page.locator(
                    'iframe[src*="recaptcha"], iframe[title*="recaptcha" i], .g-recaptcha'
                ).count() > 0:
                    page.screenshot(path=f"{debug_dir}/auto_login_captcha.png")
                    return {"success": False, "reason": "captcha", "arl": None}

                cookies = context.cookies("https://www.deezer.com")
                arl_cookie = next((c["value"] for c in cookies if c["name"] == "arl"), None)
                if arl_cookie:
                    return {"success": True, "reason": "ok", "arl": arl_cookie}

                if page.locator('input[type="password"]').count() > 0:
                    _click_login_button(page)

                time.sleep(2)

            page.screenshot(path=f"{debug_dir}/auto_login_unknown.png")
            return {"success": False, "reason": "no_arl_cookie", "arl": None}
        finally:
            context.close()


if __name__ == "__main__":
    result = attempt_auto_login(os.environ["DEEZER_EMAIL"], os.environ["DEEZER_PASSWORD"])
    print(result)
