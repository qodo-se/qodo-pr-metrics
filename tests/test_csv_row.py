import sys, os, json
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import fetch_pr_lines

def _fake_gh_output(data: dict) -> str:
    return json.dumps(data)

def test_fetch_pr_lines_returns_sum(monkeypatch):
    fake = {"additions": 120, "deletions": 40}
    monkeypatch.setattr(
        "github.run_gh",
        lambda args, **kw: json.dumps(fake),
    )
    assert fetch_pr_lines("acme", "repo", 42) == 160

def test_fetch_pr_lines_zero_on_missing(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: "{}")
    assert fetch_pr_lines("acme", "repo", 42) == 0
