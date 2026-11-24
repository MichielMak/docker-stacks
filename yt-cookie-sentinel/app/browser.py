"""Keep the YouTube session alive in the yt-cookie-browser container.

Each refresh opens a fresh, isolated browser context (like a private window),
loads the stored cookies, visits youtube.com long enough for Google to rotate
the session cookies, reads ytcfg LOGGED_IN and returns the cookies the
browser ended up with. The context is closed right after, so no open tab
keeps rotating the cookies after they are pushed to Yattee.

Can be run standalone to test the browser connection:

    docker exec yt-cookie-sentinel python browser.py
"""

import re
import socket
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

YOUTUBE_URL = "https://www.youtube.com/"
_LOGGED_IN_RE = re.compile(r'"LOGGED_IN"\s*:\s*(true|false)')


def _resolve_to_ip(cdp_url: str) -> str:
    """Chrome's DevTools server only accepts a Host header of localhost or an IP."""
    parsed = urlparse(cdp_url)
    ip = socket.gethostbyname(parsed.hostname)
    return parsed._replace(netloc=f"{ip}:{parsed.port}").geturl()


def _desktop_user_agent(version: str) -> str:
    """The headless shell sends "HeadlessChrome"; look like a normal desktop Chrome instead."""
    major = version.split(".")[0]
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def refresh(cookies: list[dict], cdp_url: str, wait_seconds: int, user_agent: str = "", debug_dir: str = "/data") -> dict:
    """Visit YouTube with the cookies. Returns {"logged_in", "cookies", "error"}.

    logged_in is True/False when ytcfg says so, None when the page gave no
    answer (consent wall, network error, changed page).
    """
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(_resolve_to_ip(cdp_url))
        context = browser.new_context(
            user_agent=user_agent or _desktop_user_agent(browser.version),
            viewport={"width": 1280, "height": 800},
        )
        try:
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()
            page.goto(YOUTUBE_URL, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass  # YouTube keeps connections open; networkidle often never comes
            page.wait_for_timeout(wait_seconds * 1000)

            logged_in = page.evaluate(
                "() => (window.ytcfg && typeof window.ytcfg.get === 'function') ? window.ytcfg.get('LOGGED_IN') : null"
            )
            if logged_in is None:
                m = _LOGGED_IN_RE.search(page.content())
                logged_in = (m.group(1) == "true") if m else None

            error = None
            if logged_in is None:
                error = f"no LOGGED_IN on {page.url}"
                page.screenshot(path=f"{debug_dir}/refresh_unknown.png")
            page.close()
            return {"logged_in": logged_in, "cookies": context.cookies(), "error": error}
        finally:
            context.close()


if __name__ == "__main__":
    import os

    result = refresh([], os.environ.get("CDP_URL", "http://yt-cookie-browser:9222"), 5)
    # Anonymous visit: expect logged_in False. Never print the cookies.
    print({"logged_in": result["logged_in"], "cookie_count": len(result["cookies"]), "error": result["error"]})
