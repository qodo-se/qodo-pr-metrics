import sys, os, json
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import fetch_pr_data, parse_qodo_comment, _minutes_between, compute_timing, find_qodo_comment

import json

def _graphql_response(body1="comment body", additions=120, deletions=40):
    return json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "additions": additions,
                    "deletions": deletions,
                    "comments": {
                        "nodes": [
                            {
                                "body": body1,
                                "createdAt": "2026-01-01T10:05:00Z",
                                "author": {"login": "qodo-ai"},
                            }
                        ]
                    }
                }
            }
        }
    })


def test_fetch_pr_data_returns_lines(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _graphql_response())
    result = fetch_pr_data("acme", "repo", 42)
    assert result["additions"] == 120
    assert result["deletions"] == 40


def test_fetch_pr_data_returns_comments(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _graphql_response(body1="hello"))
    result = fetch_pr_data("acme", "repo", 42)
    assert len(result["comments"]) == 1
    assert result["comments"][0]["body"] == "hello"
    assert result["comments"][0]["created_at"] == "2026-01-01T10:05:00Z"
    assert result["comments"][0]["user"]["login"] == "qodo-ai"


def test_fetch_pr_data_null_author(monkeypatch):
    payload = json.dumps({
        "data": {"repository": {"pullRequest": {
            "additions": 0, "deletions": 0,
            "comments": {"nodes": [
                {"body": "anon", "createdAt": "2026-01-01T10:00:00Z", "author": None}
            ]}
        }}}
    })
    monkeypatch.setattr("github.run_gh", lambda args, **kw: payload)
    result = fetch_pr_data("acme", "repo", 1)
    assert result["comments"][0]["user"]["login"] == ""


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


# ---------------------------------------------------------------------------
# _minutes_between and compute_timing
# ---------------------------------------------------------------------------

QODO_COMMENT = {
    "body": "Code Review by Qodo — some content",
    "created_at": "2026-01-01T10:08:00Z",
    "user": {"login": "qodo-ai"},
}
HUMAN_COMMENT = {
    "body": "LGTM",
    "created_at": "2026-01-01T14:30:00Z",
    "user": {"login": "alice"},
}

def test_minutes_between_basic():
    assert _minutes_between("2026-01-01T10:00:00Z", "2026-01-01T10:08:00Z") == 8

def test_minutes_between_zero():
    assert _minutes_between("2026-01-01T10:00:00Z", "2026-01-01T10:00:00Z") == 0

def test_minutes_between_bad_input():
    assert _minutes_between("", "2026-01-01T10:00:00Z") is None

def test_compute_timing_with_both():
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [QODO_COMMENT, HUMAN_COMMENT])
    assert timing["qodo_min"] == 8
    assert timing["human_min"] == 270   # 4h30m = 270 min
    assert timing["has_human"] is True

def test_compute_timing_no_human():
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [QODO_COMMENT])
    assert timing["qodo_min"] == 8
    assert timing["human_min"] is None
    assert timing["has_human"] is False

def test_compute_timing_no_qodo():
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [HUMAN_COMMENT])
    assert timing["qodo_min"] is None
    assert timing["human_min"] == 270
    assert timing["has_human"] is True

def test_compute_timing_empty_comments():
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [])
    assert timing["qodo_min"] is None
    assert timing["human_min"] is None
    assert timing["has_human"] is False

def test_compute_timing_bot_comment_excluded():
    bot_comment = {
        "body": "CI passed",
        "created_at": "2026-01-01T10:00:30Z",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [QODO_COMMENT, bot_comment])
    assert timing["human_min"] is None
    assert timing["has_human"] is False

def test_compute_timing_bot_excluded_human_still_counted():
    bot_comment = {
        "body": "CI passed",
        "created_at": "2026-01-01T10:00:30Z",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [QODO_COMMENT, bot_comment, HUMAN_COMMENT])
    assert timing["human_min"] == 270
    assert timing["has_human"] is True

def test_compute_timing_qodo_summary_comment_excluded_from_human():
    qodo_summary = {
        "body": "Here's a summary of the changes in this PR...",
        "created_at": "2026-01-01T10:00:05Z",
        "user": {"login": "qodo-ai"},
    }
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [qodo_summary, QODO_COMMENT, HUMAN_COMMENT])
    assert timing["human_min"] == 270   # summary must not set human_min to 0
    assert timing["has_human"] is True

def test_compute_timing_uses_first_real_edit_when_available():
    # last:2 gives [first_real_edit, creation]; edit[0] is when review content arrived
    qodo_with_edits = {
        "body": "Code Review by Qodo — some content",
        "created_at": "2026-01-01T10:00:30Z",
        "user_content_edits": [
            {"edited_at": "2026-01-01T10:06:00Z"},  # first real edit (5m30s after creation)
            {"edited_at": "2026-01-01T10:00:30Z"},  # creation record
        ],
        "user": {"login": "qodo-ai"},
    }
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [qodo_with_edits])
    assert timing["qodo_min"] == 6   # measured to first real edit, not placeholder creation

def test_compute_timing_falls_back_to_created_at_when_no_real_edit():
    # Only creation edit present (comment was never edited)
    qodo_no_real_edit = {
        "body": "Code Review by Qodo — some content",
        "created_at": "2026-01-01T10:08:00Z",
        "user_content_edits": [
            {"edited_at": "2026-01-01T10:08:00Z"},  # creation only
        ],
        "user": {"login": "qodo-ai"},
    }
    pr = {"created_at": "2026-01-01T10:00:00Z"}
    timing = compute_timing(pr, [qodo_no_real_edit])
    assert timing["qodo_min"] == 8   # falls back to created_at


# ---------------------------------------------------------------------------
# build_csv_row — new timing + spotlight columns
# ---------------------------------------------------------------------------

def test_build_csv_row_timing_columns_populated():
    timing = {"qodo_min": 8, "human_min": 270, "has_human": True}
    row = build_csv_row(_pr(), lines_changed=100, stats=None, timing=timing)
    assert row["Time to First Qodo Comment (min)"] == 8
    assert row["Time to First Human Comment (min)"] == 270
    assert row["Has Human Comment"] is True

def test_build_csv_row_timing_none_becomes_empty():
    timing = {"qodo_min": None, "human_min": None, "has_human": False}
    row = build_csv_row(_pr(), lines_changed=100, stats=None, timing=timing)
    assert row["Time to First Qodo Comment (min)"] == ""
    assert row["Time to First Human Comment (min)"] == ""
    assert row["Has Human Comment"] is False

def test_build_csv_row_no_timing_arg():
    # backward-compatible: timing defaults to None, new columns default to empty
    row = build_csv_row(_pr(), lines_changed=100, stats=None)
    assert row["Time to First Qodo Comment (min)"] == ""
    assert "Spotlight Issues" in row

def test_build_csv_row_spotlight_issues_serialised():
    stats = QodoStats(
        total_suggestions=2, total_implemented=1,
        spotlight_issues=[{"title": "API key leak", "category": "bug", "sub_label": "Security"}],
    )
    row = build_csv_row(_pr(), lines_changed=100, stats=stats)
    issues = json.loads(row["Spotlight Issues"])
    assert len(issues) == 1
    assert issues[0]["title"] == "API key leak"

def test_build_csv_row_no_spotlight_issues_empty_json():
    row = build_csv_row(_pr(), lines_changed=100, stats=None)
    assert row["Spotlight Issues"] == "[]"
