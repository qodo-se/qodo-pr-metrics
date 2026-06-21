"""Bitbucket Data Center / Server collector behind the Collector contract."""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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


# Bitbucket activity actions that map to GitHub review states.
_REVIEW_ACTION_STATE = {"APPROVED": "APPROVED"}


def _iso(ms) -> str:
    """Epoch-milliseconds -> ISO-8601 Z string. Empty string for falsy input."""
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _activities_to_comments(activities: list) -> list:
    """Map COMMENTED activities to the comment shape core expects."""
    out = []
    for a in activities:
        if a.get("action") != "COMMENTED":
            continue
        c = a.get("comment") or {}
        out.append({
            "body": c.get("text", "") or "",
            "created_at": _iso(c.get("createdDate")),
            "user_content_edits": [],  # Bitbucket has no per-edit history
            "user": {"login": (c.get("author") or {}).get("name", ""), "type": "User"},
        })
    return out


def _activities_to_reviews(activities: list) -> list:
    """Map APPROVED activities to the review shape parse_reviews expects."""
    out = []
    for a in activities:
        state = _REVIEW_ACTION_STATE.get(a.get("action"))
        if not state:
            continue
        out.append({
            "author": {"login": (a.get("user") or {}).get("name", "")},
            "state": state,
            "submittedAt": _iso(a.get("createdDate")),
        })
    return out


def _pr_meta(pr: dict, project: str, base_url: str) -> dict:
    """Build the provider-agnostic PR metadata dict from a Bitbucket PR list object."""
    links = (pr.get("links") or {}).get("self") or [{}]
    url = links[0].get("href", "") if links else ""
    return {
        "owner": project,
        "repo": "",  # caller fills in the repo slug
        "number": pr.get("id"),
        "node_id": "",  # caller fills "{project}/{slug}/{id}"
        "url": url,
        "creator": ((pr.get("author") or {}).get("user") or {}).get("name", ""),
        "created_at": _iso(pr.get("createdDate")),
        "merged_at": _iso(pr.get("closedDate")),
    }


def _loc_from_diff(diff: dict) -> tuple:
    """Sum (additions, deletions) from a Bitbucket structured /diff response."""
    added = removed = 0
    for fd in diff.get("diffs") or []:
        for hunk in fd.get("hunks") or []:
            for seg in hunk.get("segments") or []:
                n = len(seg.get("lines") or [])
                if seg.get("type") == "ADDED":
                    added += n
                elif seg.get("type") == "REMOVED":
                    removed += n
    return added, removed
