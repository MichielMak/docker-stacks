"""Write a new ARL into the deemix and spotizerr configs and restart both containers."""

import json
import os
import sqlite3
import time

import requests


def _preserve_owner(path: str, tmp_path: str) -> None:
    st = os.stat(path)
    os.chown(tmp_path, st.st_uid, st.st_gid)
    os.chmod(tmp_path, st.st_mode)


def write_deemix_login(arl: str, path: str) -> None:
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    data["arl"] = arl
    data["accessToken"] = None

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)

    if os.path.exists(path):
        _preserve_owner(path, tmp_path)

    os.replace(tmp_path, path)


def write_spotizerr_arl(arl: str, db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("UPDATE deezer SET arl = ?, updated_at = ?", (arl, time.time()))
        conn.commit()
    finally:
        conn.close()


def restart_container(name: str, proxy_url: str) -> None:
    resp = requests.post(f"{proxy_url}/containers/{name}/restart", timeout=60)
    resp.raise_for_status()
