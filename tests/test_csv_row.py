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


# ---------------------------------------------------------------------------
# fetch_pr_data — new fields: body, labels, reviews, ci_status, commits
# ---------------------------------------------------------------------------

def _graphql_response_extended(
    body="PR description", labels=None, reviews=None,
    ci_state="SUCCESS", commits=None,
    additions=120, deletions=40
):
    return json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "additions": additions,
                    "deletions": deletions,
                    "body": body,
                    "labels": {"nodes": [{"name": n} for n in (labels or [])]},
                    "reviews": {"nodes": reviews or []},
                    "lastCommit": {
                        "nodes": [{"commit": {"statusCheckRollup": {"state": ci_state}}}]
                    },
                    "allCommits": {
                        "nodes": commits or []
                    },
                    "comments": {"nodes": []},
                }
            }
        }
    })


def test_fetch_pr_data_returns_body(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _graphql_response_extended(body="My PR"))
    result = fetch_pr_data("acme", "repo", 42)
    assert result["body"] == "My PR"


def test_fetch_pr_data_returns_labels(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _graphql_response_extended(labels=["copilot", "bug"]))
    result = fetch_pr_data("acme", "repo", 42)
    assert result["labels"] == ["copilot", "bug"]


def test_fetch_pr_data_returns_reviews(monkeypatch):
    reviews = [{"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-01T12:00:00Z"}]
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _graphql_response_extended(reviews=reviews))
    result = fetch_pr_data("acme", "repo", 42)
    assert len(result["reviews"]) == 1
    assert result["reviews"][0]["state"] == "APPROVED"


def test_fetch_pr_data_returns_ci_status(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _graphql_response_extended(ci_state="FAILURE"))
    result = fetch_pr_data("acme", "repo", 42)
    assert result["ci_status"] == "FAILURE"


def test_fetch_pr_data_returns_commits(monkeypatch):
    commits = [{"commit": {"committedDate": "2026-01-01T11:00:00Z", "message": "fix: address review"}}]
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _graphql_response_extended(commits=commits))
    result = fetch_pr_data("acme", "repo", 42)
    assert len(result["commits"]) == 1
    assert result["commits"][0]["commit"]["committedDate"] == "2026-01-01T11:00:00Z"


def test_fetch_pr_data_ci_status_none_when_missing(monkeypatch):
    payload = json.dumps({
        "data": {"repository": {"pullRequest": {
            "additions": 0, "deletions": 0,
            "body": "", "labels": {"nodes": []}, "reviews": {"nodes": []},
            "lastCommit": {"nodes": []}, "allCommits": {"nodes": []},
            "comments": {"nodes": []},
        }}}
    })
    monkeypatch.setattr("github.run_gh", lambda args, **kw: payload)
    result = fetch_pr_data("acme", "repo", 1)
    assert result["ci_status"] is None


# ---------------------------------------------------------------------------
# detect_ai_authored — identify AI-generated PRs
# ---------------------------------------------------------------------------

from github import detect_ai_authored

def test_detect_ai_copilot_coauthor():
    body = "Co-authored-by: github-copilot[bot] <175728472+github-copilot[bot]@users.noreply.github.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "copilot"

def test_detect_ai_copilot_swe_agent():
    body = "Co-authored-by: copilot-swe-agent[bot] <198982749+Copilot@users.noreply.github.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "copilot"

def test_detect_ai_copilot_plain():
    body = "Co-authored-by: Copilot <copilot@github.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "copilot"

def test_detect_ai_copilot_label():
    is_ai, ai_type = detect_ai_authored("", ["copilot", "bug"])
    assert is_ai is True
    assert ai_type == "copilot"

def test_detect_ai_cursor_coauthor():
    body = "Co-authored-by: Cursor <cursoragent@cursor.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "cursor"

def test_detect_ai_cursor_body():
    body = "<sub>Generated with Cursor</sub>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "cursor"

def test_detect_ai_claude_coauthor():
    body = "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "claude"

def test_detect_ai_claude_url():
    body = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "claude"

def test_detect_ai_codex_coauthor():
    body = "Co-authored-by: Codex <codex@openai.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "codex"

def test_detect_ai_codex_openai_email():
    body = "Co-authored-by: OpenAI Codex GPT-5 <noreply@openai.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "codex"

def test_detect_ai_kiro_coauthor():
    body = "Co-authored-by: Kiro AI <kiro@amazon.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "kiro"

def test_detect_ai_kiro_body():
    body = "This pull request was generated by @kiro-agent :ghost:"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "kiro"

def test_detect_ai_gemini_coauthor():
    body = "Co-authored-by: gemini-code-assist[bot] <176961590+gemini-code-assist[bot]@users.noreply.github.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "gemini"

def test_detect_ai_windsurf_coauthor():
    body = "Co-authored-by: Windsurf Cascade <cascade@windsurf.ai>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "windsurf"

def test_detect_ai_windsurf_codeium_email():
    body = "Co-Authored-By: Cascade (Windsurf) <noreply@codeium.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "windsurf"

def test_detect_ai_windsurf_label():
    is_ai, ai_type = detect_ai_authored("", ["windsurf"])
    assert is_ai is True
    assert ai_type == "windsurf"

def test_detect_ai_codeium_label():
    is_ai, ai_type = detect_ai_authored("", ["codeium"])
    assert is_ai is True
    assert ai_type == "windsurf"

def test_detect_ai_devin_coauthor():
    body = "Co-Authored-By: Devin <158243242+devin-ai-integration[bot]@users.noreply.github.com>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "devin"

def test_detect_ai_devin_session_url():
    body = "Link to Devin session: https://app.devin.ai/sessions/abc123"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "devin"

def test_detect_ai_aider_coauthor():
    body = "Co-authored-by: aider (openai/gpt-4o) <aider@aider.chat>"
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "aider"

def test_detect_ai_amazon_q_prose():
    body = "Co-authored by Amazon Q using Claude Sonnet 4.6."
    is_ai, ai_type = detect_ai_authored(body, [])
    assert is_ai is True
    assert ai_type == "amazon-q"

def test_detect_ai_label_ai_generated():
    is_ai, ai_type = detect_ai_authored("", ["ai-generated"])
    assert is_ai is True

def test_detect_ai_not_triggered_by_normal_pr():
    is_ai, ai_type = detect_ai_authored("Fix the login bug", ["bug", "backend"])
    assert is_ai is False
    assert ai_type == ""

def test_detect_ai_empty_inputs():
    is_ai, ai_type = detect_ai_authored("", [])
    assert is_ai is False
    assert ai_type == ""


# ---------------------------------------------------------------------------
# parse_reviews — reviewer count, request-changes flag, and approver
# ---------------------------------------------------------------------------

from github import parse_reviews

def test_parse_reviews_approved():
    reviews = [{"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-01T12:00:00Z"}]
    result = parse_reviews(reviews)
    assert result["reviewer_count"] == 1
    assert result["had_request_changes"] is False
    assert result["approver"] == "alice"

def test_parse_reviews_request_changes():
    reviews = [
        {"author": {"login": "bob"}, "state": "CHANGES_REQUESTED", "submittedAt": "2026-01-01T10:00:00Z"},
        {"author": {"login": "bob"}, "state": "APPROVED", "submittedAt": "2026-01-01T11:00:00Z"},
    ]
    result = parse_reviews(reviews)
    assert result["had_request_changes"] is True
    assert result["reviewer_count"] == 1
    assert result["approver"] == "bob"

def test_parse_reviews_multiple_reviewers():
    reviews = [
        {"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-01T12:00:00Z"},
        {"author": {"login": "bob"}, "state": "COMMENTED", "submittedAt": "2026-01-01T11:00:00Z"},
    ]
    result = parse_reviews(reviews)
    assert result["reviewer_count"] == 2

def test_parse_reviews_empty():
    result = parse_reviews([])
    assert result["reviewer_count"] == 0
    assert result["had_request_changes"] is False
    assert result["approver"] == ""

def test_parse_reviews_null_author_skipped():
    reviews = [{"author": None, "state": "APPROVED", "submittedAt": "2026-01-01T12:00:00Z"}]
    result = parse_reviews(reviews)
    assert result["reviewer_count"] == 0
    assert result["approver"] == ""


# ---------------------------------------------------------------------------
# compute_speed_to_fix — commits pushed after Qodo review
# ---------------------------------------------------------------------------

from github import compute_speed_to_fix

def test_speed_to_fix_basic():
    commits = [
        {"commit": {"committedDate": "2026-01-01T10:30:00Z", "message": "fix: address review"}},
        {"commit": {"committedDate": "2026-01-01T11:00:00Z", "message": "chore: cleanup"}},
    ]
    result = compute_speed_to_fix("2026-01-01T10:00:00Z", commits)
    assert result["commits_after_qodo"] == 2
    assert result["speed_to_fix_min"] == 30

def test_speed_to_fix_no_commits_after():
    commits = [{"commit": {"committedDate": "2026-01-01T09:00:00Z", "message": "initial"}}]
    result = compute_speed_to_fix("2026-01-01T10:00:00Z", commits)
    assert result["commits_after_qodo"] == 0
    assert result["speed_to_fix_min"] is None

def test_speed_to_fix_no_qodo_ts():
    result = compute_speed_to_fix(None, [{"commit": {"committedDate": "2026-01-01T10:00:00Z", "message": "fix"}}])
    assert result["commits_after_qodo"] == 0
    assert result["speed_to_fix_min"] is None

def test_speed_to_fix_empty_commits():
    result = compute_speed_to_fix("2026-01-01T10:00:00Z", [])
    assert result["commits_after_qodo"] == 0
    assert result["speed_to_fix_min"] is None

def test_speed_to_fix_picks_earliest_commit():
    commits = [
        {"commit": {"committedDate": "2026-01-01T11:00:00Z", "message": "second"}},
        {"commit": {"committedDate": "2026-01-01T10:15:00Z", "message": "first"}},
    ]
    result = compute_speed_to_fix("2026-01-01T10:00:00Z", commits)
    assert result["speed_to_fix_min"] == 15


# ---------------------------------------------------------------------------
# build_csv_row — new extras columns for AI authorship, reviews, CI, speed
# ---------------------------------------------------------------------------

def test_build_csv_row_extras_populated():
    extras = {
        "is_ai_authored": True,
        "ai_author_type": "copilot",
        "reviewer_count": 2,
        "had_request_changes": True,
        "approver": "alice",
        "ci_status": "SUCCESS",
        "commits_after_qodo": 3,
        "speed_to_fix_min": 45,
    }
    row = build_csv_row(_pr(), lines_changed=100, stats=None, extras=extras)
    assert row["Is AI Authored"] is True
    assert row["AI Author Type"] == "copilot"
    assert row["Reviewer Count"] == 2
    assert row["Had Request Changes"] is True
    assert row["Final Approver"] == "alice"
    assert row["CI Status"] == "SUCCESS"
    assert row["Commits After Qodo"] == 3
    assert row["Speed to First Fix (min)"] == 45


def test_build_csv_row_extras_none_defaults():
    row = build_csv_row(_pr(), lines_changed=100, stats=None)
    assert row["Is AI Authored"] is False
    assert row["AI Author Type"] == ""
    assert row["Reviewer Count"] == 0
    assert row["Had Request Changes"] is False
    assert row["Final Approver"] == ""
    assert row["CI Status"] == ""
    assert row["Commits After Qodo"] == 0
    assert row["Speed to First Fix (min)"] == ""


def test_build_csv_row_speed_to_fix_none_becomes_empty():
    extras = {"speed_to_fix_min": None, "ci_status": None}
    row = build_csv_row(_pr(), lines_changed=100, stats=None, extras=extras)
    assert row["Speed to First Fix (min)"] == ""
    assert row["CI Status"] == ""


# ---------------------------------------------------------------------------
# build_csv_row — dismissed columns
# ---------------------------------------------------------------------------

def test_build_csv_row_dismissed_columns_present():
    """New dismissed columns must appear in the output row."""
    stats = QodoStats(
        action_required_total=3,
        action_required_implemented=1,
        action_required_dismissed=1,
        review_recommended_total=1,
        review_recommended_implemented=0,
        review_recommended_dismissed=1,
        total_suggestions=4,
        total_implemented=1,
        total_dismissed=2,
    )
    row = build_csv_row(_pr(), lines_changed=400, stats=stats)
    assert row["Action Required Dismissed"] == 1
    assert row["Review Recommended Dismissed"] == 1
    assert row["Total Dismissed"] == 2

def test_build_csv_row_dismissed_zero_when_no_qodo():
    row = build_csv_row(_pr(), lines_changed=100, stats=None)
    assert row["Action Required Dismissed"] == 0
    assert row["Review Recommended Dismissed"] == 0
    assert row["Total Dismissed"] == 0

def test_build_csv_row_impl_rate_excludes_dismissed():
    """Implementation rate must only count fixed items, not dismissed."""
    stats = QodoStats(
        total_suggestions=4,
        total_implemented=1,   # 1 fixed
        total_dismissed=2,     # 2 dismissed
    )
    row = build_csv_row(_pr(), lines_changed=400, stats=stats)
    assert row["Implementation Rate (%)"] == "25.0"   # 1/4, not 3/4
