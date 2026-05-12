import sys, os, json
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import fetch_pr_lines, parse_qodo_comment

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
    assert row["Hours to Merge"] == 48
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


# ---------------------------------------------------------------------------
# parse_qodo_comment — section detection with Qodo's HTML <img> banner format
# ---------------------------------------------------------------------------

_IMG_ACTION = '<img src="https://www.qodo.ai/wp-content/uploads/2026/01/action-required.png" height="20" alt="Action required">'
_IMG_REVIEW = '<img src="https://www.qodo.ai/wp-content/uploads/2026/01/review-recommended.png" height="20" alt="Remediation recommended">'

def _qodo_body(action_items=(), review_items=()):
    """Build a minimal Qodo comment body with <img> section banners."""
    lines = ["<h3>Code Review by Qodo</h3>", ""]
    if action_items:
        lines += [_IMG_ACTION, ""]
        for i, title in enumerate(action_items, 1):
            lines += [
                "<details>",
                f"<summary>  {i}.  {title}</summary>",
                "</details>",
            ]
    if review_items:
        lines += ["", _IMG_REVIEW, ""]
        for i, title in enumerate(review_items, 1):
            lines += [
                "<details>",
                f"<summary>  {i}.  {title}</summary>",
                "</details>",
            ]
    return "\n".join(lines)


def test_parse_img_section_action_required():
    body = _qodo_body(action_items=["Fix null pointer <code>🐞 Bug</code>"])
    s = parse_qodo_comment(body)
    assert s.action_required_total == 1
    assert s.review_recommended_total == 0
    assert s.total_suggestions == 1


def test_parse_img_section_review_recommended():
    body = _qodo_body(review_items=["Rename variable <code>📘 Rule violation</code>"])
    s = parse_qodo_comment(body)
    assert s.review_recommended_total == 1
    assert s.action_required_total == 0
    assert s.total_suggestions == 1


def test_parse_img_both_sections():
    body = _qodo_body(
        action_items=["Critical issue <code>🐞 Bug</code>", "Another issue <code>📘 Rule violation</code>"],
        review_items=["Style nit <code>📘 Rule violation</code>"],
    )
    s = parse_qodo_comment(body)
    assert s.action_required_total == 2
    assert s.review_recommended_total == 1
    assert s.total_suggestions == 3
    assert s.bugs_suggested == 1
    assert s.rule_violations_suggested == 2
