import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import collectors.bitbucket as bb
import pytest, urllib.error


class _FakeResp:
    def __init__(self, body): self._body = json.dumps(body).encode()
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_paginate_walks_envelope(monkeypatch):
    pages = [
        {"values": [{"id": 1}, {"id": 2}], "isLastPage": False, "nextPageStart": 2},
        {"values": [{"id": 3}], "isLastPage": True},
    ]
    calls = []
    def fake_urlopen(req, context=None, timeout=None):
        calls.append(req.full_url)
        return _FakeResp(pages[len(calls) - 1])
    monkeypatch.setattr(bb.urllib.request, "urlopen", fake_urlopen)
    client = bb._BitbucketClient("https://bb.example.com", "tok")
    out = list(client.paginate("/rest/api/1.0/projects/COD/repos"))
    assert [v["id"] for v in out] == [1, 2, 3]
    assert "start=2" in calls[1]


def test_get_json_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(bb, "_sleep", lambda s: None)
    seq = [urllib.error.HTTPError("u", 503, "busy", {}, None), _FakeResp({"ok": True})]
    def fake_urlopen(req, context=None, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(bb.urllib.request, "urlopen", fake_urlopen)
    client = bb._BitbucketClient("https://bb.example.com", "tok")
    assert client.get_json("/x") == {"ok": True}


def test_iso_converts_epoch_ms():
    assert bb._iso(1704067200000) == "2024-01-01T00:00:00Z"
    assert bb._iso(0) == ""
    assert bb._iso(None) == ""


def test_activities_to_comments_and_reviews():
    acts = [
        {"action": "COMMENTED", "comment": {
            "text": "### Code Review by Qodo\n#### 1. x `🐞 Bug`",
            "createdDate": 1704067200000,
            "author": {"name": "talr"}}},
        {"action": "APPROVED", "createdDate": 1704070800000, "user": {"name": "ravid"}},
    ]
    comments = bb._activities_to_comments(acts)
    assert comments[0]["created_at"] == "2024-01-01T00:00:00Z"
    assert comments[0]["user"]["login"] == "talr"
    assert comments[0]["user"]["type"] == "User"
    assert comments[0]["user_content_edits"] == []
    reviews = bb._activities_to_reviews(acts)
    assert reviews == [{"author": {"login": "ravid"}, "state": "APPROVED",
                        "submittedAt": "2024-01-01T01:00:00Z"}]


def test_pr_meta_mapping():
    pr = {"id": 8, "title": "x", "createdDate": 1704067200000, "closedDate": 1704070800000,
          "author": {"user": {"name": "talr"}},
          "links": {"self": [{"href": "https://bb/x/pull-requests/8"}]}}
    meta = bb._pr_meta(pr, "COD", "https://bb")
    assert meta["owner"] == "COD" and meta["repo"] == "" and meta["number"] == 8
    assert meta["creator"] == "talr"
    assert meta["merged_at"] == "2024-01-01T01:00:00Z"
    assert meta["url"] == "https://bb/x/pull-requests/8"


def test_loc_from_diff_sums_added_removed():
    diff = {"diffs": [{"hunks": [{"segments": [
        {"type": "CONTEXT", "lines": [{}, {}]},
        {"type": "ADDED", "lines": [{}, {}, {}]},
        {"type": "REMOVED", "lines": [{}]},
    ]}]}]}
    assert bb._loc_from_diff(diff) == (3, 1)


def test_loc_from_diff_empty():
    assert bb._loc_from_diff({"diffs": []}) == (0, 0)


class _StubClient:
    """Routes paginate()/get_json() to canned responses keyed by path SUFFIX.

    Suffix (endswith) matching disambiguates nested REST paths — e.g. the repo-list
    path ends with "/repos" while the PR-list path ends with "/pull-requests", even
    though one is a prefix of the other. The path passed to the client never includes
    the query string (params are separate), so suffixes are stable.
    """
    def __init__(self, routes): self.routes = routes
    def _match(self, path):
        # longest suffix wins, so "/5/activities" beats a hypothetical "/activities"
        best = None
        for key, val in self.routes.items():
            if path.endswith(key) and (best is None or len(key) > len(best[0])):
                best = (key, val)
        return best[1] if best else None
    def paginate(self, path, params=None):
        yield from (self._match(path) or [])
    def get_json(self, path, params=None):
        return self._match(path) or {}

QODO_SUMMARY = "### Code Review by Qodo\n#### 1. Bug here `🐞 Bug` `✓ Correctness`"

def _make_collector():
    c = bb.BitbucketCollector("https://bb", "tok", project="COD")
    c._client = _StubClient({
        "/repos": [{"slug": "repo1"}],
        "/5/activities": [
            {"action": "COMMENTED", "comment": {"text": QODO_SUMMARY,
             "createdDate": 1704067200000, "author": {"name": "talr"}}},
        ],
        "/pull-requests": [   # PR list (state=MERGED)
            {"id": 5, "title": "feat: x", "createdDate": 1704060000000,
             "closedDate": 1704067200000, "updatedDate": 1704067200000,
             "author": {"user": {"name": "dev1"}},
             "fromRef": {"displayId": "feature/x", "latestCommit": "abc"},
             "reviewers": [], "links": {"self": [{"href": "https://bb/pr/5"}]}},
        ],
    })
    return c


def test_search_merged_prs_yields_qodo_prs(monkeypatch):
    from datetime import date
    c = _make_collector()
    prs = list(c.search_merged_prs("COD", date(2023, 12, 1)))
    assert len(prs) == 1
    assert prs[0]["repo"] == "repo1"
    assert prs[0]["number"] == 5
    assert prs[0]["node_id"] == "COD/repo1/5"
    assert prs[0]["creator"] == "dev1"


def test_fetch_pr_data_batch_returns_core_shape():
    from datetime import date
    c = _make_collector()
    c._client.routes["/5/commits"] = [
        {"authorTimestamp": 1704067200000, "message": "fix"}]
    c._client.routes["/5/diff"] = {"diffs": [
        {"hunks": [{"segments": [{"type": "ADDED", "lines": [{}, {}]}]}]}]}
    prs = list(c.search_merged_prs("COD", date(2023, 12, 1)))
    data = c.fetch_pr_data_batch(prs)
    d = data["COD/repo1/5"]
    assert d["additions"] == 2
    assert d["labels"] == []
    assert d["commits"][0]["commit"]["message"] == "fix"
    assert any("Code Review by Qodo" in cm["body"] for cm in d["comments"])


def test_aggregate_counts_from_snapshot():
    from datetime import date
    c = _make_collector()
    # add a second (non-Qodo) merged PR and a revert
    c._client.routes["/pull-requests"] = [
        {"id": 5, "title": "feat: x", "createdDate": 1704060000000,
         "closedDate": 1704067200000, "updatedDate": 1704067200000,
         "author": {"user": {"name": "dev1"}},
         "fromRef": {"displayId": "feature/x", "latestCommit": "abc"},
         "reviewers": [], "links": {"self": [{"href": "https://bb/pr/5"}]}},
        {"id": 6, "title": "Revert bad change", "createdDate": 1704060000000,
         "closedDate": 1704067200000, "updatedDate": 1704067200000,
         "author": {"user": {"name": "dev2"}},
         "fromRef": {"displayId": "hotfix/y", "latestCommit": "def"},
         "reviewers": [], "links": {"self": [{"href": "https://bb/pr/6"}]}},
    ]
    since = date(2023, 12, 1)
    assert c.get_org_pr_count("COD", since) == 2
    assert c.get_qodo_pr_count("COD", since) == 1
    assert c.get_org_author_count("COD", since) == 2
    assert c.get_revert_pr_count("COD", since) == 1
    assert c.get_hotfix_pr_count("COD", since) == 1   # branch "hotfix/y"


import importlib

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "bitbucket")


def test_end_to_end_from_recorded_fixtures():
    """Pipeline smoke-test: recorded PR#8 fixtures round-trip through collector helpers."""
    import core

    with open(os.path.join(FIXTURES_DIR, "activities_pr8.json")) as f:
        activities_envelope = json.load(f)
    with open(os.path.join(FIXTURES_DIR, "diff_pr8.json")) as f:
        diff_raw = json.load(f)

    activities = activities_envelope["values"]
    comments = bb._activities_to_comments(activities)

    qodo_comment = core.find_qodo_comment(comments)
    assert qodo_comment is not None, "find_qodo_comment should detect the Qodo summary"

    parsed = core.parse_qodo_comment(qodo_comment["body"])
    assert parsed.total_suggestions >= 1, (
        f"expected >= 1 suggestion, got {parsed.total_suggestions}"
    )

    additions, _deletions = bb._loc_from_diff(diff_raw)
    assert additions > 0, f"expected additions > 0, got {additions}"


def test_cli_builds_bitbucket_collector(monkeypatch):
    monkeypatch.setenv("BITBUCKET_TOKEN", "tok")
    qm = importlib.import_module("qodo_metrics")
    captured = {}
    def fake_get_collector(name, **config):
        captured["name"] = name
        captured["config"] = config
        raise SystemExit(0)  # stop before the network run
    monkeypatch.setattr(qm, "get_collector", fake_get_collector)
    monkeypatch.setattr(sys, "argv", [
        "qodo_metrics.py", "--provider", "bitbucket-dc",
        "--base-url", "https://bb.example.com", "--project", "COD",
        "--loc", "qodo-only", "--since", "2026-01-01"])
    with __import__("pytest").raises(SystemExit):
        qm.main()
    assert captured["name"] == "bitbucket-dc"
    assert captured["config"]["base_url"] == "https://bb.example.com"
    assert captured["config"]["project"] == "COD"
    assert captured["config"]["token"] == "tok"
    assert captured["config"]["loc_mode"] == "qodo-only"
