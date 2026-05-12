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


from github import build_csv_row, QodoStats

def _pr(overrides=None):
    base = {
        "owner": "acme", "repo": "backend", "number": 99,
        "url": "https://github.com/acme/backend/pull/99",
        "creator": "alice",
        "created_at": "2026-01-01T10:00:00Z",
        "merged_at":  "2026-01-03T10:00:00Z",
    }
    if overrides:
        base.update(overrides)
    return base

def test_build_csv_row_no_qodo():
    row = build_csv_row(_pr(), lines_changed=200, stats=None)
    assert row["Repo Name"] == "backend"
    assert row["PR #"] == 99
    assert row["PR Creator"] == "alice"
    assert row["Lines Changed"] == 200
    assert row["Days to Merge"] == 2
    assert row["Has Qodo Review"] is False
    assert row["Total Suggestions"] == 0
    assert row["Implementation Rate (%)"] == ""
    assert row["Suggestions per 100 Lines"] == ""

def test_build_csv_row_with_qodo():
    stats = QodoStats(
        action_required_total=3, action_required_implemented=2,
        review_recommended_total=1, review_recommended_implemented=1,
        bugs_suggested=3, bugs_implemented=2,
        rule_violations_suggested=1, rule_violations_implemented=1,
        total_suggestions=4, total_implemented=3,
    )
    row = build_csv_row(_pr(), lines_changed=400, stats=stats)
    assert row["Has Qodo Review"] is True
    assert row["Total Suggestions"] == 4
    assert row["Total Implemented"] == 3
    assert row["Implementation Rate (%)"] == "75.0"
    assert row["Suggestions per 100 Lines"] == "1.0"

def test_build_csv_row_zero_lines():
    """No division by zero when lines_changed is 0."""
    stats = QodoStats(action_required_total=2, action_required_implemented=1,
                      total_suggestions=2, total_implemented=1)
    row = build_csv_row(_pr(), lines_changed=0, stats=stats)
    assert row["Suggestions per 100 Lines"] == ""
