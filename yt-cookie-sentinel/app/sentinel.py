"""YouTube cookie sentinel for yattee-server.

Keeps Yattee's YouTube account cookies fresh without manual exports:

1. Seed once: export cookies.txt from a private/incognito YouTube window,
   close that window, and drop the file into /inbox/cookies.txt.
2. Every REFRESH_INTERVAL_SECONDS (and whenever Yattee reports its cookies
   stale), the sentinel loads the stored cookies into a fresh context in the
   yt-cookie-browser container, visits youtube.com so Google rotates the
   session cookies, stores the result in /data/cookies.txt and replaces the
   cookies_file credential of Yattee's "youtube" site with it.
3. When YouTube says the session is logged out, it sends a Gotify message
   asking for a new seed (at most once per DEAD_NOTICE_INTERVAL_SECONDS).

Never log or print cookie values; use cookiejar.fingerprint() instead.
"""

import json
import os
import sys
import time
import traceback

import requests

import browser
import cookiejar
from yattee import Yattee, YatteeError

STATE_PATH = "/data/state.json"
JAR_PATH = "/data/cookies.txt"
HEARTBEAT_PATH = "/data/heartbeat"
INBOX_PATH = "/inbox/cookies.txt"

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "3600"))
REFRESH_INTERVAL_SECONDS = int(os.environ.get("REFRESH_INTERVAL_SECONDS", "21600"))
DEAD_NOTICE_INTERVAL_SECONDS = int(os.environ.get("DEAD_NOTICE_INTERVAL_SECONDS", "86400"))
ERROR_NOTICE_INTERVAL_SECONDS = int(os.environ.get("ERROR_NOTICE_INTERVAL_SECONDS", "21600"))
PAGE_WAIT_SECONDS = int(os.environ.get("PAGE_WAIT_SECONDS", "20"))
CDP_URL = os.environ.get("CDP_URL", "http://yt-cookie-browser:9222")
USER_AGENT = os.environ.get("USER_AGENT", "")
GOTIFY_URL = os.environ.get("GOTIFY_URL", "").rstrip("/")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN", "")
YATTEE_URL = os.environ.get("YATTEE_URL", "http://yattee-server:8085")
YATTEE_USERNAME = os.environ.get("YATTEE_USERNAME", "")
YATTEE_PASSWORD = os.environ.get("YATTEE_PASSWORD", "")

SEED_HELP = (
    "Open a private/incognito window, log into YouTube, export cookies.txt for youtube.com "
    "with the cookie extension, close the private window, and drop the file into "
    f"{INBOX_PATH} (on the host: $DOCKER_DATA_DIR/yt-cookie-sentinel/inbox/cookies.txt)."
)


# --- notifications and state -------------------------------------------------


def notify(title: str, message: str, priority: int = 5) -> None:
    print(f"[notify p{priority}] {title}: {message}")
    if not GOTIFY_URL or not GOTIFY_TOKEN:
        return
    try:
        requests.post(
            f"{GOTIFY_URL}/message",
            params={"token": GOTIFY_TOKEN},
            json={"title": title, "message": message, "priority": priority},
            timeout=10,
        )
    except requests.RequestException as exc:
        print(f"Failed to send Gotify notification: {exc}")


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def notify_throttled(state: dict, key: str, interval: int, title: str, message: str, priority: int) -> None:
    """Send a notification at most once per interval for the same key."""
    sent = state.setdefault("notified_at", {})
    if time.time() - sent.get(key, 0) < interval:
        print(f"[throttled] {title}: {message}")
        return
    notify(title, message, priority)
    sent[key] = time.time()
    save_state(state)


def read_file(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_jar(text: str) -> None:
    tmp = f"{JAR_PATH}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, JAR_PATH)


# --- the work ----------------------------------------------------------------


def push_to_yattee(yattee: Yattee, state: dict, session_text: str) -> bool:
    """Replace Yattee's YouTube cookies with the youtube.com part of the jar."""
    yt_text = cookiejar.format(cookiejar.parse(session_text), cookiejar.YOUTUBE_DOMAINS)
    try:
        cred = yattee.replace_cookies(yt_text)
    except (YatteeError, requests.RequestException) as exc:
        notify_throttled(
            state, "push_failed", ERROR_NOTICE_INTERVAL_SECONDS,
            "YouTube cookies not pushed to Yattee", str(exc), priority=8,
        )
        return False
    state["pushed_fingerprint"] = cookiejar.fingerprint(yt_text)
    state["pushed_at"] = time.time()
    state.get("notified_at", {}).pop("push_failed", None)
    save_state(state)
    print(
        f"Pushed cookies to Yattee: credential {cred['id']}, status {cred.get('status')}, "
        f"fingerprint {state['pushed_fingerprint']}, note {cred.get('last_error')}"
    )
    return True


def refresh_session(state: dict, cookies: list[dict]) -> bool | None:
    """Visit YouTube with the cookies and store whatever the browser returns.

    Returns the LOGGED_IN verdict (True, False or None for no answer).
    """
    result = browser.refresh(cookies, CDP_URL, PAGE_WAIT_SECONDS, USER_AGENT)
    state["refreshed_at"] = time.time()
    state["logged_in"] = result["logged_in"]
    save_state(state)
    if result["logged_in"] is not False and cookiejar.has_auth_cookies(result["cookies"]):
        # Also on "no answer": the browser may have rotated the cookies, and
        # only the new ones are valid from now on.
        write_jar(cookiejar.format(result["cookies"]))
    print(f"Refresh: logged_in={result['logged_in']} cookies={len(result['cookies'])} error={result['error']}")
    if result["logged_in"] is None:
        notify_throttled(
            state, "refresh_unknown", ERROR_NOTICE_INTERVAL_SECONDS,
            "YouTube cookie refresh inconclusive",
            f"{result['error']}. Screenshot: /data/refresh_unknown.png. Will retry next cycle.",
            priority=5,
        )
    return result["logged_in"]


def handle_logged_out(state: dict) -> None:
    state.setdefault("dead_since", time.time())
    save_state(state)
    notify_throttled(
        state, "dead", DEAD_NOTICE_INTERVAL_SECONDS,
        "YouTube cookies expired",
        "YouTube logged the stored session out, so Yattee is anonymous (360p-only on many videos). " + SEED_HELP,
        priority=8,
    )


def handle_inbox(yattee: Yattee, state: dict) -> bool:
    """Apply a new seed from the inbox. Returns True when there was one."""
    text = read_file(INBOX_PATH)
    if text is None:
        return False
    cookies = cookiejar.parse(text)
    if not cookiejar.has_auth_cookies(cookies):
        os.remove(INBOX_PATH)
        notify(
            "YouTube cookie seed rejected",
            "The file has no youtube.com account cookies (LOGIN_INFO and SAPISID). "
            "Export while logged in on youtube.com. " + SEED_HELP,
            priority=8,
        )
        return True

    logged_in = refresh_session(state, cookies)
    if logged_in is None:
        return True  # keep the inbox file and retry next cycle
    os.remove(INBOX_PATH)  # it holds a live session; don't leave it lying around
    if logged_in is False:
        notify(
            "YouTube cookie seed already logged out",
            "YouTube says the new cookies are logged out: the browser you exported from "
            "rotated them. " + SEED_HELP,
            priority=8,
        )
        return True

    state.pop("dead_since", None)
    state.get("notified_at", {}).pop("dead", None)
    save_state(state)
    if push_to_yattee(yattee, state, read_file(JAR_PATH)):
        notify("YouTube cookies seeded", "New session stored and pushed to Yattee. Refreshes are automatic from now on.")
    return True


def run_cycle(yattee: Yattee) -> None:
    state = load_state()

    if handle_inbox(yattee, state):
        return

    jar_text = read_file(JAR_PATH)
    if not jar_text:
        notify_throttled(
            state, "no_jar", DEAD_NOTICE_INTERVAL_SECONDS,
            "YouTube cookie sentinel needs a seed", "No cookies stored yet. " + SEED_HELP, priority=8,
        )
        return

    try:
        yattee_status = yattee.cookie_status().get("status")
    except (YatteeError, requests.RequestException) as exc:
        print(f"Could not read Yattee's cookie status: {exc}")
        yattee_status = None

    due = time.time() - state.get("refreshed_at", 0) >= REFRESH_INTERVAL_SECONDS
    if not due and yattee_status == "ok":
        return
    print(f"Refreshing: due={due} yattee_status={yattee_status}")

    if "dead_since" in state and not due:
        # Already known dead: don't hammer YouTube every hour, just keep reminding.
        handle_logged_out(state)
        return

    logged_in = refresh_session(state, cookiejar.parse(jar_text))
    if logged_in is False:
        handle_logged_out(state)
        return
    if logged_in is None:
        return

    was_dead = state.pop("dead_since", None)
    state.get("notified_at", {}).pop("dead", None)
    save_state(state)
    if push_to_yattee(yattee, state, read_file(JAR_PATH)) and was_dead:
        notify("YouTube cookies working again", "The stored session is logged in again and was pushed to Yattee.")


def main() -> None:
    os.umask(0o077)  # the jar and the state are secrets
    if not YATTEE_USERNAME or not YATTEE_PASSWORD:
        notify("YouTube cookie sentinel", "YATTEE_USERNAME/YATTEE_PASSWORD are missing; cannot push cookies.", priority=8)
    yattee = Yattee(YATTEE_URL, YATTEE_USERNAME, YATTEE_PASSWORD)
    run_once = "--once" in sys.argv
    while True:
        try:
            run_cycle(yattee)
        except Exception:
            traceback.print_exc()
            state = load_state()
            notify_throttled(
                state, "error", ERROR_NOTICE_INTERVAL_SECONDS,
                "YouTube cookie sentinel error", traceback.format_exc()[-1500:], priority=8,
            )
        with open(HEARTBEAT_PATH, "w") as f:
            f.write(str(int(time.time())))
        if run_once:
            break
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
