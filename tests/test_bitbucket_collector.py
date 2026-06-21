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
