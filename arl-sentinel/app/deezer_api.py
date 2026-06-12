"""Helpers for talking to Deezer's private API to validate an ARL cookie."""

import requests

GW_LIGHT_URL = "https://www.deezer.com/ajax/gw-light.php"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def check_arl(arl: str) -> dict:
    """Validate an ARL against Deezer's getUserData endpoint.

    Returns a dict with at least a "valid" bool. On success it also
    includes "user_id" and "name". On failure it includes "error".
    """
    params = {
        "method": "deezer.getUserData",
        "input": "3",
        "api_version": "1.0",
        "api_token": "",
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": f"arl={arl}",
    }

    try:
        resp = requests.get(GW_LIGHT_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"valid": False, "user_id": 0, "name": None, "error": str(exc)}

    user = data.get("results", {}).get("USER", {})
    user_id = user.get("USER_ID", 0)

    return {
        "valid": bool(user_id),
        "user_id": user_id,
        "name": user.get("BLOG_NAME") or user.get("FIRSTNAME"),
    }
