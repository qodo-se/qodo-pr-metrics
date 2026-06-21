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
