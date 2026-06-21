"""Bitbucket Data Center / Server collector behind the Collector contract."""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

_sleep = time.sleep  # indirection so tests can monkeypatch backoff waits


class BitbucketHttpError(Exception):
    """Raised when a Bitbucket REST call fails after retries."""


class _BitbucketClient:
    """Thin stdlib HTTP client: Bearer auth, pagination, 429/5xx backoff."""

    def __init__(self, base_url: str, token: str, verify: bool = True, timeout: int = 30):
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._ctx = ssl.create_default_context()
        if not verify:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def get_json(self, path: str, params=None) -> dict:
        url = self._base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token}"})
        max_retries = 4
        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(req, context=self._ctx, timeout=self._timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    _sleep(5 * (3 ** attempt))  # 5s, 15s, 45s, 135s
                    continue
                raise BitbucketHttpError(f"GET {url} -> HTTP {e.code}: {e.read()[:200]!r}")
            except urllib.error.URLError as e:
                if attempt < max_retries:
                    _sleep(5 * (3 ** attempt))
                    continue
                raise BitbucketHttpError(f"GET {url} -> {e}")
        raise BitbucketHttpError(f"GET {url} -> exhausted retries")

    def paginate(self, path: str, params=None):
        params = dict(params or {})
        params.setdefault("limit", 100)
        start = 0
        while True:
            params["start"] = start
            data = self.get_json(path, params)
            for v in data.get("values", []):
                yield v
            if data.get("isLastPage", True):
                return
            start = data.get("nextPageStart", start + params["limit"])
