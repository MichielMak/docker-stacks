"""Small client for the yattee-server admin API (HTTP Basic Auth, admin user)."""

import requests


class YatteeError(Exception):
    pass


class Yattee:
    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        resp = self.session.request(method, f"{self.url}{path}", timeout=60, **kwargs)
        if resp.status_code >= 400:
            # Error details from Yattee never contain the cookie values.
            raise YatteeError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    def cookie_status(self) -> dict:
        """Yattee's own verdict on its YouTube cookies: status ok / stale / none."""
        return self._request("GET", "/info").json().get("cookies") or {}

    def youtube_site(self) -> dict:
        """The site with extractor pattern "youtube", including its credentials (without values)."""
        for site in self._request("GET", "/api/sites").json():
            if (site.get("extractor_pattern") or "").strip().lower() == "youtube":
                return self._request("GET", f"/api/sites/{site['id']}").json()
        raise YatteeError('No site with extractor pattern "youtube" in Yattee. Add one under Sites first.')

    def replace_cookies(self, jar_text: str) -> dict:
        """Upload a new cookies_file credential, then delete the old ones.

        Yattee probes the jar on upload and refuses it (HTTP 400) when YouTube
        says it is logged out, so the old credentials stay when that happens.
        """
        site = self.youtube_site()
        new = self._request(
            "POST",
            f"/api/sites/{site['id']}/credentials",
            json={"credential_type": "cookies_file", "value": jar_text},
        ).json()
        for cred in site.get("credentials") or []:
            if cred["credential_type"] == "cookies_file" and cred["id"] != new["id"]:
                self._request("DELETE", f"/api/sites/{site['id']}/credentials/{cred['id']}")
        return new
