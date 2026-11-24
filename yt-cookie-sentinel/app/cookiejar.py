"""Netscape cookies.txt <-> Playwright cookie dicts.

The seed file comes from a browser extension ("Get cookies.txt LOCALLY" and
similar), which marks HttpOnly cookies with a "#HttpOnly_" domain prefix.
Yattee's own cookie parser skips every line starting with "#", and LOGIN_INFO
is HttpOnly, so format() writes all cookies without that prefix. yt-dlp reads
both forms.
"""

import hashlib

HTTPONLY_PREFIX = "#HttpOnly_"

# The browser needs both to keep the Google session alive. Yattee only gets the
# youtube.com cookies: its parser merges google.com and youtube.com cookies by
# name, and both domains have a SID, SAPISID, etc. with different values.
SESSION_DOMAINS = ("youtube.com", "google.com")
YOUTUBE_DOMAINS = ("youtube.com",)


def _domain_matches(domain: str, suffixes: tuple[str, ...] = SESSION_DOMAINS) -> bool:
    bare = domain.lstrip(".")
    return any(bare == s or bare.endswith("." + s) for s in suffixes)


def parse(text: str) -> list[dict]:
    """Parse a Netscape cookie file into Playwright add_cookies() dicts."""
    cookies = []
    for raw in text.splitlines():
        line = raw.strip()
        http_only = False
        if line.startswith(HTTPONLY_PREFIX):
            line = line[len(HTTPONLY_PREFIX):]
            http_only = True
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _subdomains, path, secure, expires, name = parts[:6]
        value = "\t".join(parts[6:])
        if not _domain_matches(domain):
            continue
        cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
            "httpOnly": http_only,
        }
        try:
            exp = float(expires)
        except ValueError:
            exp = 0
        # 0 marks a session cookie; Playwright wants -1 (or no key) for that.
        cookie["expires"] = exp if exp > 0 else -1
        if name.startswith(("__Secure-", "__Host-")):
            cookie["secure"] = True
        cookies.append(cookie)
    return cookies


def format(cookies: list[dict], domains: tuple[str, ...] = SESSION_DOMAINS) -> str:
    """Write Playwright context.cookies() dicts as a Netscape cookie file."""
    lines = ["# Netscape HTTP Cookie File", "# Written by yt-cookie-sentinel"]
    for c in sorted(cookies, key=lambda c: (c["domain"], c["path"], c["name"])):
        if not _domain_matches(c["domain"], domains):
            continue
        domain = c["domain"]
        expires = c.get("expires", -1)
        lines.append(
            "\t".join(
                [
                    domain,
                    "TRUE" if domain.startswith(".") else "FALSE",
                    c.get("path") or "/",
                    "TRUE" if c.get("secure") else "FALSE",
                    str(int(expires)) if expires and expires > 0 else "0",
                    c["name"],
                    c["value"],
                ]
            )
        )
    return "\n".join(lines) + "\n"


def has_auth_cookies(cookies: list[dict]) -> bool:
    """Same rule as yt-dlp and Yattee: LOGIN_INFO plus one of the SAPISID family."""
    names = {c["name"] for c in cookies if _domain_matches(c["domain"], YOUTUBE_DOMAINS)}
    return "LOGIN_INFO" in names and bool(names & {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"})


def fingerprint(text: str) -> str:
    """Short hash to log or compare a jar without showing it."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]
