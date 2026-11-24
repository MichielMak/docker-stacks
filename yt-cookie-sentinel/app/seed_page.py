"""Seed page: paste a cookies.txt export in the browser instead of copying a file to the host.

Runs as a thread inside the sentinel, so a seed is checked against YouTube
and pushed to Yattee while you wait. Traefik puts Authentik forward auth in
front of it (internal group only), on the LAN/tailnet entrypoint only.

The page never shows cookie values: only the session status.
"""

import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY_BYTES = 1_000_000
LOCK_TIMEOUT_SECONDS = 300  # a running refresh can take a couple of minutes

_ctx: dict = {}

OUTCOME_TONE = {"seeded": "ok", "inconclusive": "warn", "push_failed": "warn"}

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube cookies</title>
<style>
  :root {{ color-scheme: light dark; --ok: #1a7f37; --warn: #9a6700; --bad: #cf222e; }}
  body {{ font-family: system-ui, sans-serif; max-width: 46rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
  h1 {{ font-size: 1.4rem; margin-bottom: .25rem; }}
  .muted {{ opacity: .7; }}
  .box {{ border: 1px solid #8884; border-radius: .5rem; padding: .75rem 1rem; margin: 1rem 0; }}
  .ok {{ border-color: var(--ok); }} .warn {{ border-color: var(--warn); }} .bad {{ border-color: var(--bad); }}
  .badge {{ font-weight: 600; }} .ok .badge {{ color: var(--ok); }} .warn .badge {{ color: var(--warn); }} .bad .badge {{ color: var(--bad); }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem; margin: 0; }}
  dt {{ opacity: .7; }} dd {{ margin: 0; }}
  textarea {{ width: 100%; min-height: 10rem; font-family: ui-monospace, monospace; font-size: .8rem; box-sizing: border-box; }}
  button {{ font-size: 1rem; padding: .5rem 1.25rem; margin-top: .5rem; cursor: pointer; }}
  ol {{ padding-left: 1.25rem; }}
</style>
</head>
<body>
<h1>YouTube cookies for Yattee</h1>
<p class="muted">The sentinel keeps this session alive. You only need this page when it asks for a new seed.</p>
{result}
<div class="box {status_tone}">
  <div class="badge">{status_label}</div>
  <dl>{status_rows}</dl>
</div>
<h2>New seed</h2>
<ol>
  <li>Open a private/incognito window and log into youtube.com.</li>
  <li>Export the cookies with your cookie extension (Netscape cookies.txt format).</li>
  <li><strong>Close the private window</strong>, so it can't rotate the cookies.</li>
  <li>Paste the export below, or pick the downloaded file. Checking takes up to 2 minutes.</li>
</ol>
<form method="post" action="/seed" id="seed">
  <input type="file" id="file" accept=".txt,text/plain">
  <textarea name="cookies" id="cookies" placeholder="# Netscape HTTP Cookie File" required autocomplete="off" spellcheck="false"></textarea>
  <button type="submit" id="go">Check and use these cookies</button>
</form>
<script>
  document.getElementById('file').addEventListener('change', e => {{
    const f = e.target.files[0];
    if (f) f.text().then(t => {{ document.getElementById('cookies').value = t; }});
  }});
  document.getElementById('seed').addEventListener('submit', () => {{
    const b = document.getElementById('go'); b.disabled = true; b.textContent = 'Checking with YouTube...';
  }});
</script>
</body>
</html>
"""


def _fmt(ts) -> str:
    if not ts:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))


def _status() -> tuple[str, str, str]:
    """(tone, label, <dt>/<dd> rows) from the sentinel state and Yattee's own check."""
    s = _ctx["sentinel"]
    state = s.load_state()
    try:
        yattee_status = _ctx["yattee"].cookie_status().get("status") or "unknown"
    except Exception as exc:
        yattee_status = f"unreachable ({type(exc).__name__})"

    if "dead_since" in state:
        tone, label = "bad", "Session logged out: needs a new seed"
    elif not s.read_file(s.JAR_PATH):
        tone, label = "bad", "No session yet: needs a seed"
    elif yattee_status == "ok" and state.get("logged_in") is True:
        tone, label = "ok", "Working"
    else:
        tone, label = "warn", "Check pending"

    rows = [
        ("Yattee's cookie check", yattee_status),
        ("Last browser visit", f"{_fmt(state.get('refreshed_at'))} (logged in: {state.get('logged_in')})"),
        ("Last push to Yattee", _fmt(state.get("pushed_at"))),
    ]
    if "dead_since" in state:
        rows.append(("Logged out since", _fmt(state["dead_since"])))
    html_rows = "".join(f"<dt>{escape(k)}</dt><dd>{escape(str(v))}</dd>" for k, v in rows)
    return tone, label, html_rows


def _render(result: str = "") -> bytes:
    tone, label, rows = _status()
    return PAGE.format(result=result, status_tone=tone, status_label=escape(label), status_rows=rows).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "yt-cookie-sentinel"

    def log_message(self, fmt, *args):  # no request logging: keep the logs about the session
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, b"ok", "text/plain")
        elif path == "/":
            self._send(200, _render())
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/seed":
            self._send(404, b"not found", "text/plain")
            return
        # Cross-site form posts: the browser always sends Origin on POST.
        origin = urlparse(self.headers.get("Origin") or "").netloc
        if not origin or origin != self.headers.get("Host"):
            self._send(403, b"cross-origin request refused", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(413, b"empty or too large", "text/plain")
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        text = (form.get("cookies") or [""])[0]

        s = _ctx["sentinel"]
        if not s.LOCK.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            outcome, message = "inconclusive", "The sentinel is busy. Try again in a minute."
        else:
            try:
                state = s.load_state()
                outcome, message = s.apply_seed(_ctx["yattee"], state, text)
            except Exception as exc:
                outcome, message = "error", f"Seed failed: {type(exc).__name__}. See the sentinel logs."
                print(f"Seed page: apply_seed failed: {exc!r}")
            finally:
                s.LOCK.release()
        print(f"Seed page: outcome={outcome}")
        if outcome == "seeded":
            s.notify("YouTube cookies seeded", "New session from the seed page, pushed to Yattee.")
        tone = OUTCOME_TONE.get(outcome, "bad")
        result = f'<div class="box {tone}"><div class="badge">{escape(outcome.replace("_", " "))}</div>{escape(message)}</div>'
        self._send(200, _render(result))


def start(yattee, sentinel_module, port: int = 8080) -> None:
    """Serve the page in a daemon thread. sentinel_module is the running sentinel (for its LOCK and state)."""
    _ctx.update(yattee=yattee, sentinel=sentinel_module)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="seed-page", daemon=True).start()
    print(f"Seed page listening on :{port}")
