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


from datetime import date
from core import find_qodo_comment


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


class BitbucketCollector:
    def __init__(self, base_url, token, project=None, all_projects=False,
                 verify=True, concurrency=8, loc_mode="all"):
        self._client = _BitbucketClient(base_url, token, verify=verify)
        self._base_url = base_url.rstrip("/")
        self._project = project
        self._all_projects = all_projects
        self._concurrency = concurrency
        self._loc_mode = loc_mode
        self._snapshot_cache = {}   # (since_iso, until_iso) -> snapshot dict

    # ---- enumeration ----------------------------------------------------
    def _list_repos(self):
        """Return [(project_key, slug)] in scope."""
        if self._all_projects:
            return [(r["project"]["key"], r["slug"])
                    for r in self._client.paginate("/rest/api/1.0/repos")]
        return [(self._project, r["slug"])
                for r in self._client.paginate(
                    f"/rest/api/1.0/projects/{self._project}/repos")]

    def _list_merged_prs(self, project, slug, since, until):
        """Yield raw PR list objects merged within [since, until] (client-side filter)."""
        path = f"/rest/api/1.0/projects/{project}/repos/{slug}/pull-requests"
        since_ms = int(datetime(since.year, since.month, since.day,
                                tzinfo=timezone.utc).timestamp() * 1000)
        for pr in self._client.paginate(path, {"state": "MERGED", "order": "NEWEST"}):
            updated = pr.get("updatedDate", 0)
            if updated and updated < since_ms:
                break  # NEWEST order is by updatedDate desc; nothing older can be in window
            closed = pr.get("closedDate", 0)
            cd = date.fromisoformat(_iso(closed)[:10]) if closed else None
            if cd and since <= cd <= until:
                yield pr

    def _activities(self, project, slug, pid):
        return list(self._client.paginate(
            f"/rest/api/1.0/projects/{project}/repos/{slug}/pull-requests/{pid}/activities"))

    def _snapshot(self, since, until=None):
        """Enumerate merged PRs in scope, fetch activities, classify Qodo PRs. Memoized."""
        until = until or date.today()
        key = (since.isoformat(), until.isoformat())
        if key in self._snapshot_cache:
            return self._snapshot_cache[key]
        records = []
        for project, slug in self._list_repos():
            for pr in self._list_merged_prs(project, slug, since, until):
                acts = self._activities(project, slug, pr["id"])
                comments = _activities_to_comments(acts)
                meta = _pr_meta(pr, project, self._base_url)
                meta["repo"] = slug
                meta["node_id"] = f"{project}/{slug}/{pr['id']}"
                records.append({
                    "meta": meta, "raw": pr, "slug": slug, "project": project,
                    "comments": comments, "reviews": _activities_to_reviews(acts),
                    "is_qodo": find_qodo_comment(comments) is not None,
                })
        snap = {"records": records}
        self._snapshot_cache[key] = snap
        return snap

    def search_merged_prs(self, org, since, until=None, chunk_days=None,
                          repos=None, total_prs=None, qodo_only=True):
        snap = self._snapshot(since, until)
        for rec in snap["records"]:
            if qodo_only and not rec["is_qodo"]:
                continue
            yield dict(rec["meta"])

    def _pr_data(self, rec, comments_limit=20):
        """Build the per-PR data dict (commits, CI, LOC) for one snapshot record."""
        project, slug, pr = rec["project"], rec["slug"], rec["raw"]
        pid = pr["id"]
        base = f"/rest/api/1.0/projects/{project}/repos/{slug}/pull-requests/{pid}"
        commits = [
            {"commit": {"committedDate": _iso(cm.get("authorTimestamp")),
                        "message": cm.get("message", "")}}
            for cm in self._client.paginate(f"{base}/commits")
        ]
        additions = deletions = 0
        if self._loc_mode != "off":
            try:
                diff = self._client.get_json(f"{base}/diff", {"contextLines": 0})
                additions, deletions = _loc_from_diff(diff)
            except BitbucketHttpError:
                pass
        ci_status = self._ci_status(project, slug, (pr.get("fromRef") or {}).get("latestCommit"))
        return {
            "comments": rec["comments"][:comments_limit] if comments_limit else rec["comments"],
            "additions": additions, "deletions": deletions,
            "body": pr.get("description", "") or "",
            "labels": [],  # Bitbucket DC has no PR labels
            "reviews": rec["reviews"],
            "ci_status": ci_status,
            "commits": commits,
        }

    def _ci_status(self, project, slug, commit):
        if not commit:
            return None
        try:
            stats = self._client.get_json(
                f"/rest/build-status/1.0/commits/stats/{commit}")
        except BitbucketHttpError:
            return None
        if stats.get("failed", 0) > 0:
            return "FAILURE"
        if stats.get("inProgress", 0) > 0:
            return "PENDING"
        if stats.get("successful", 0) > 0:
            return "SUCCESS"
        return None

    def fetch_pr_data_batch(self, prs, batch_size=50, raise_on_5xx=False,
                            comments_first=20):
        by_node = {}
        snap_records = {r["meta"]["node_id"]: r for s in self._snapshot_cache.values()
                        for r in s["records"]}
        for pr in prs:
            rec = snap_records.get(pr.get("node_id"))
            if rec is None:
                continue
            by_node[pr["node_id"]] = self._pr_data(rec, comments_limit=comments_first)
        return by_node

    def fetch_pr_data(self, owner, repo, number, comments_limit=20):
        for snap in self._snapshot_cache.values():
            for rec in snap["records"]:
                if rec["slug"] == repo and rec["raw"]["id"] == number:
                    return self._pr_data(rec, comments_limit=comments_limit)
        raise BitbucketHttpError(f"PR not in snapshot: {repo}#{number}")
