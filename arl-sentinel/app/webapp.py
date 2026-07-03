"""Tiny web UI that displays the current Deezer ARL tracked by the sentinel.

Reads the shared /data/state.json written by sentinel.py and renders a
good-looking page at arl.<DOMAIN>. Also exposes:
  - /arl.txt    plain-text ARL (handy for scripts / copy-paste)
  - /api/state  JSON with the ARL, validity and timestamps

Uses only the Python standard library plus `requests` (already a dependency)
for the optional live validity check against Deezer.
"""

import json
import os
import time
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from deezer_api import check_arl

STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")
LISTEN_PORT = int(os.environ.get("WEB_PORT", "8080"))
# How long (seconds) to cache a live Deezer validity check between page loads.
VALIDITY_CACHE_SECONDS = int(os.environ.get("VALIDITY_CACHE_SECONDS", "300"))

_validity_cache: dict = {"arl": None, "checked_at": 0.0, "result": None}


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def live_validity(arl: str) -> dict:
    """Return a cached-or-fresh Deezer validity result for the given ARL."""
    now = time.time()
    if (
        _validity_cache["arl"] == arl
        and _validity_cache["result"] is not None
        and now - _validity_cache["checked_at"] < VALIDITY_CACHE_SECONDS
    ):
        return _validity_cache["result"]

    result = check_arl(arl)
    _validity_cache.update({"arl": arl, "checked_at": now, "result": result})
    return result


def fmt_timestamp(ts) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def build_context() -> dict:
    state = load_state()
    arl = state.get("arl")
    ctx = {
        "arl": arl,
        "checked_at": state.get("checked_at"),
        "valid": None,
        "user_name": None,
        "user_id": None,
    }
    if arl:
        result = live_validity(arl)
        ctx["valid"] = bool(result.get("valid"))
        ctx["user_name"] = result.get("name")
        ctx["user_id"] = result.get("user_id")
    return ctx


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Deezer ARL</title>
<style>
  :root {{
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2e3a;
    --text: #e6e8ee;
    --muted: #8b90a0;
    --accent: #a238ff;
    --accent-2: #ff6b2c;
    --ok: #2ecc71;
    --bad: #ff5c5c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: radial-gradient(1200px 600px at 50% -10%, #241a3a 0%, var(--bg) 55%);
    color: var(--text);
  }}
  .card {{
    width: 100%;
    max-width: 720px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 32px;
    box-shadow: 0 20px 60px rgba(0,0,0,.45);
  }}
  .head {{
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 8px;
  }}
  .logo {{
    width: 44px; height: 44px;
    border-radius: 12px;
    display: grid; place-items: center;
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    font-weight: 800;
    font-size: 20px;
    color: #fff;
  }}
  h1 {{ font-size: 22px; margin: 0; }}
  .sub {{ color: var(--muted); font-size: 14px; margin: 2px 0 0; }}
  .badge {{
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 12px; border-radius: 999px;
    font-size: 13px; font-weight: 600;
    margin: 20px 0 8px;
  }}
  .badge.ok {{ background: rgba(46,204,113,.15); color: var(--ok); }}
  .badge.bad {{ background: rgba(255,92,92,.15); color: var(--bad); }}
  .badge.unknown {{ background: rgba(139,144,160,.15); color: var(--muted); }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: currentColor; }}
  .arl-box {{
    position: relative;
    margin-top: 14px;
    background: #0c0e14;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 56px 16px 16px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px;
    line-height: 1.5;
    word-break: break-all;
    color: #cfe3ff;
  }}
  .copy {{
    position: absolute; top: 10px; right: 10px;
    border: 1px solid var(--border);
    background: #171a24;
    color: var(--text);
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
    cursor: pointer;
  }}
  .copy:hover {{ border-color: var(--accent); }}
  .meta {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-top: 22px;
  }}
  .meta .item {{
    background: #14171f;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
  }}
  .meta .k {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
  .meta .v {{ margin-top: 4px; font-size: 15px; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; text-align: center; }}
  a {{ color: var(--accent); text-decoration: none; }}
</style>
</head>
<body>
  <div class="card">
    <div class="head">
      <div class="logo">ARL</div>
      <div>
        <h1>Deezer ARL</h1>
        <p class="sub">Current token managed by ARL Sentinel</p>
      </div>
    </div>

    {status_badge}

    {arl_section}

    <div class="meta">
      <div class="item">
        <div class="k">Account</div>
        <div class="v">{account}</div>
      </div>
      <div class="item">
        <div class="k">User ID</div>
        <div class="v">{user_id}</div>
      </div>
      <div class="item">
        <div class="k">Last checked by sentinel</div>
        <div class="v">{checked_at}</div>
      </div>
      <div class="item">
        <div class="k">Live validity checked</div>
        <div class="v">{now}</div>
      </div>
    </div>

    <footer>Also available as <a href="/arl.txt">/arl.txt</a> and <a href="/api/state">/api/state</a></footer>
  </div>

<script>
  function copyArl(btn) {{
    const el = document.getElementById('arl-value');
    if (!el) return;
    navigator.clipboard.writeText(el.textContent.trim()).then(() => {{
      const prev = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => {{ btn.textContent = prev; }}, 1500);
    }});
  }}
</script>
</body>
</html>
"""


def render_page(ctx: dict) -> str:
    if ctx["valid"] is True:
        status_badge = '<span class="badge ok"><span class="dot"></span>Valid</span>'
    elif ctx["valid"] is False:
        status_badge = '<span class="badge bad"><span class="dot"></span>Invalid / expired</span>'
    else:
        status_badge = '<span class="badge unknown"><span class="dot"></span>No ARL on record</span>'

    if ctx["arl"]:
        arl_section = (
            '<div class="arl-box">'
            '<button class="copy" onclick="copyArl(this)">Copy</button>'
            f'<span id="arl-value">{escape(ctx["arl"])}</span>'
            "</div>"
        )
    else:
        arl_section = (
            '<div class="arl-box empty">No ARL has been recorded yet. '
            "Drop a valid ARL into the sentinel's inbox to get started.</div>"
        )

    return PAGE_TEMPLATE.format(
        status_badge=status_badge,
        arl_section=arl_section,
        account=escape(str(ctx["user_name"])) if ctx["user_name"] else '<span class="empty">unknown</span>',
        user_id=escape(str(ctx["user_id"])) if ctx["user_id"] else '<span class="empty">-</span>',
        checked_at=escape(fmt_timestamp(ctx["checked_at"])),
        now=escape(fmt_timestamp(time.time())),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "arl-web/1.0"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path in ("/healthz", "/health"):
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return

        ctx = build_context()

        if path == "/arl.txt":
            body = (ctx["arl"] or "").encode("utf-8")
            self._send(200, body, "text/plain; charset=utf-8")
            return

        if path == "/api/state":
            payload = {
                "arl": ctx["arl"],
                "valid": ctx["valid"],
                "user_id": ctx["user_id"],
                "user_name": ctx["user_name"],
                "checked_at": ctx["checked_at"],
            }
            self._send(200, json.dumps(payload, indent=2).encode("utf-8"), "application/json")
            return

        if path == "/":
            self._send(200, render_page(ctx).encode("utf-8"), "text/html; charset=utf-8")
            return

        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:
        print("[web] " + (fmt % args))


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"ARL web UI listening on :{LISTEN_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
