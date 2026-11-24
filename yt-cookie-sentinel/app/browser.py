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
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

YOUTUBE_URL = "https://www.youtube.com/"
_LOGGED_IN_RE = re.compile(r'"LOGGED_IN"\s*:\s*(true|false)')
# How long to wait for an in-flight RotateCookies request before giving up.
ROTATION_TIMEOUT_SECONDS = 60


def _is_rotation(url: str) -> bool:
    """accounts.google.com/RotateCookies and accounts.youtube.com/RotateCookies (not the ...Page iframe)."""
    parsed = urlparse(url)
    return (parsed.hostname or "").startswith("accounts.") and parsed.path.rstrip("/").endswith("/RotateCookies")


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
        rotation = {"pending": set(), "results": [], "accounts": set()}

        def on_request(req):
            parsed = urlparse(req.url)
            if (parsed.hostname or "").startswith("accounts."):
                # Diagnostics only: path without query string, never cookies.
                rotation["accounts"].add(f"{parsed.hostname}{parsed.path}")
            if _is_rotation(req.url):
                rotation["pending"].add(req)

        def on_done(req, outcome):
            if req in rotation["pending"]:
                rotation["pending"].discard(req)
                rotation["results"].append(f"{urlparse(req.url).hostname}:{outcome}")

        def on_finished(req):
            resp = req.response()
            on_done(req, resp.status if resp else "no-response")

        context.on("request", on_request)
        context.on("requestfinished", on_finished)
        context.on("requestfailed", lambda req: on_done(req, f"failed({req.failure})"))
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

            # Google rotates the session cookies with a background RotateCookies
            # request. Once it is sent, Google treats the new cookies as current
            # and later drops the old ones, so never read the cookies (or close
            # the context) while one is still in flight.
            deadline = time.monotonic() + ROTATION_TIMEOUT_SECONDS
            while rotation["pending"] and time.monotonic() < deadline:
                page.wait_for_timeout(500)
            if rotation["results"]:
                page.wait_for_timeout(2000)  # let the Set-Cookie headers land in the jar

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
            if rotation["pending"]:
                error = (error + "; " if error else "") + "RotateCookies still pending at timeout"
            new_cookies = context.cookies()
            page.close()
            return {
                "logged_in": logged_in,
                "cookies": new_cookies,
                "error": error,
                "rotations": rotation["results"],
                "rotation_pending": bool(rotation["pending"]),
                "accounts_requests": sorted(rotation["accounts"]),
                "changed": changed_cookie_names(cookies, new_cookies),
            }
        finally:
            context.close()


def changed_cookie_names(before: list[dict], after: list[dict]) -> list[str]:
    """Names (with domain) of cookies that were added, changed or removed. Never values."""
    old = {(c["domain"], c["name"]): c["value"] for c in before}
    new = {(c["domain"], c["name"]): c["value"] for c in after}
    names = []
    for key in sorted(old.keys() | new.keys()):
        if old.get(key) == new.get(key):
            continue
        suffix = " (new)" if key not in old else " (gone)" if key not in new else ""
        names.append(f"{key[0]}/{key[1]}{suffix}")
    return names


if __name__ == "__main__":
    import os

    result = refresh([], os.environ.get("CDP_URL", "http://yt-cookie-browser:9222"), 5)
    # Anonymous visit: expect logged_in False. Never print the cookies.
    print({k: v for k, v in result.items() if k != "cookies"} | {"cookie_count": len(result["cookies"])})
