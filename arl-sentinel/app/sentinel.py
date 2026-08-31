"""ARL Sentinel main loop.

Periodically checks whether the Deezer ARL shared by spotizerr, deemix and
Music Assistant is still valid. If the user drops a new ARL into
/inbox/new_arl.txt, validates it and propagates it to all three (restarting
the deemix and spotizerr containers; Music Assistant is updated over its API
and reloads its Deezer provider itself). If the current ARL has expired,
sends a Gotify notification asking for a new one, and optionally attempts an
automated Deezer login (see auto_login.py).
"""

import json
import os
import sqlite3
import sys
import time
import traceback

import requests

from deezer_api import check_arl
from musicassistant import set_musicassistant_arl
from propagate import restart_container, write_deemix_login, write_spotizerr_arl

STATE_PATH = "/data/state.json"
INBOX_PATH = "/inbox/new_arl.txt"
DEEMIX_LOGIN_PATH = "/targets/deemix/login.json"
SPOTIZERR_DB_PATH = "/targets/spotizerr/accounts.db"

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "21600"))
GOTIFY_URL = os.environ.get("GOTIFY_URL", "").rstrip("/")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN", "")
DOCKERPROXY_URL = os.environ.get("DOCKERPROXY_URL", "http://arl-dockerproxy:2375")
ATTEMPT_AUTO_LOGIN = os.environ.get("ATTEMPT_AUTO_LOGIN", "false").lower() == "true"
MUSICASSISTANT_URL = os.environ.get("MUSICASSISTANT_URL", "").rstrip("/")
MUSICASSISTANT_TOKEN = os.environ.get("MUSICASSISTANT_TOKEN", "")


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


def read_spotizerr_arl(db_path: str) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT arl FROM deezer LIMIT 1").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def read_deemix_arl(path: str) -> str | None:
    try:
        with open(path) as f:
            return json.load(f).get("arl")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_inbox() -> str | None:
    if not os.path.exists(INBOX_PATH):
        return None
    with open(INBOX_PATH) as f:
        value = f.read().strip()
    return value or None


def archive_inbox() -> None:
    if os.path.exists(INBOX_PATH):
        os.replace(INBOX_PATH, f"{INBOX_PATH}.applied_{int(time.time())}")


def update_musicassistant(arl: str) -> None:
    """Push the ARL to Music Assistant, if it is configured.

    Failures here must not undo the deemix/spotizerr update, so they are
    reported instead of raised.
    """
    if not MUSICASSISTANT_URL or not MUSICASSISTANT_TOKEN:
        return
    try:
        set_musicassistant_arl(MUSICASSISTANT_URL, MUSICASSISTANT_TOKEN, arl)
        print("Music Assistant's Deezer provider updated.")
    except Exception as exc:
        notify(
            "ARL not applied to Music Assistant",
            f"deemix and spotizerr were updated, but Music Assistant refused the new ARL: {exc}",
            priority=8,
        )


def targets_label() -> str:
    targets = ["deemix", "spotizerr"]
    if MUSICASSISTANT_URL and MUSICASSISTANT_TOKEN:
        targets.append("Music Assistant")
    return ", ".join(targets)


def propagate(arl: str) -> None:
    write_deemix_login(arl, DEEMIX_LOGIN_PATH)
    write_spotizerr_arl(arl, SPOTIZERR_DB_PATH)
    restart_container("deemix", DOCKERPROXY_URL)
    restart_container("spotizerr", DOCKERPROXY_URL)
    update_musicassistant(arl)


def try_auto_login() -> None:
    email = os.environ.get("DEEZER_EMAIL")
    password = os.environ.get("DEEZER_PASSWORD")
    if not email or not password:
        notify("ARL auto-login skipped", "ATTEMPT_AUTO_LOGIN is set but DEEZER_EMAIL/DEEZER_PASSWORD are missing.", priority=5)
        return

    from auto_login import attempt_auto_login

    result = attempt_auto_login(email, password)
    if not result["success"]:
        notify("ARL auto-login failed", f"Reason: {result['reason']}", priority=5)
        return

    check = check_arl(result["arl"])
    if not check["valid"]:
        notify("ARL auto-login produced invalid ARL", "Auto-login succeeded but the resulting ARL failed validation.", priority=8)
        return

    propagate(result["arl"])
    save_state({"arl": result["arl"], "checked_at": time.time()})
    notify("ARL auto-refreshed", f"Logged into Deezer automatically and refreshed {targets_label()}.")


def run_cycle() -> None:
    state = load_state()

    if "arl" not in state:
        current = read_spotizerr_arl(SPOTIZERR_DB_PATH)
        state = {"arl": current, "checked_at": None}
        save_state(state)

    candidate = read_inbox()
    if candidate:
        result = check_arl(candidate)
        if result["valid"]:
            propagate(candidate)
            save_state({"arl": candidate, "checked_at": time.time()})
            archive_inbox()
            notify("ARL updated", f"New ARL applied to {targets_label()} (user_id={result['user_id']}).")
        else:
            notify(
                "ARL update failed",
                "The ARL in /inbox/new_arl.txt is not valid according to Deezer. Please check it.",
                priority=8,
            )
        return

    current_arl = state.get("arl")
    if not current_arl:
        notify("ARL Sentinel", "No ARL on record yet - drop a valid ARL into /inbox/new_arl.txt.", priority=8)
        return

    result = check_arl(current_arl)
    state["checked_at"] = time.time()
    save_state(state)

    if result["valid"]:
        if read_deemix_arl(DEEMIX_LOGIN_PATH) != current_arl:
            write_deemix_login(current_arl, DEEMIX_LOGIN_PATH)
            restart_container("deemix", DOCKERPROXY_URL)
            notify("ARL Sentinel", "Re-synced deemix's ARL to match the known-good value.")
        return

    notify(
        "Deezer ARL expired",
        "The current ARL is no longer valid. Log into deezer.com, copy the 'arl' cookie, "
        f"and drop it into /inbox/new_arl.txt to refresh {targets_label()} automatically.",
        priority=8,
    )

    if ATTEMPT_AUTO_LOGIN:
        try:
            try_auto_login()
        except Exception:
            notify("ARL auto-login error", traceback.format_exc()[-1500:], priority=5)


def main() -> None:
    run_once = "--once" in sys.argv
    while True:
        try:
            run_cycle()
        except Exception:
            traceback.print_exc()
            notify("ARL Sentinel error", traceback.format_exc()[-1500:], priority=8)
        if run_once:
            break
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
