import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import date
from github import _output_stem

_SINCE = date(2025, 5, 12)
_UNTIL = date(2026, 5, 12)


def test_output_stem_no_repos():
    assert _output_stem("acme-corp", _SINCE, _UNTIL) == "acme-corp_2025-05-12_2026-05-12"


def test_output_stem_single_repo_uses_singular():
    result = _output_stem("acme-corp", _SINCE, _UNTIL, repos=["frontend-app"])
    assert result == "acme-corp_1-repo_2025-05-12_2026-05-12"


def test_output_stem_multiple_repos_uses_plural():
    result = _output_stem("acme-corp", _SINCE, _UNTIL, repos=["frontend-app", "backend-api", "shared"])
    assert result == "acme-corp_3-repos_2025-05-12_2026-05-12"


def test_output_stem_two_repos():
    result = _output_stem("acme-corp", _SINCE, _UNTIL, repos=["a", "b"])
    assert result == "acme-corp_2-repos_2025-05-12_2026-05-12"


def test_output_stem_empty_list_treated_as_no_repos():
    assert _output_stem("acme-corp", _SINCE, _UNTIL, repos=[]) == "acme-corp_2025-05-12_2026-05-12"



from github import get_qodo_pr_count


def test_get_qodo_pr_count_uses_qodo_filter(monkeypatch):
    captured = []
    def fake_run_gh(args, **kw):
        captured.extend(args)
        return "42\n"
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    result = get_qodo_pr_count("acme", date(2025, 1, 1))
    assert result == 42
    q_arg = next(a for a in captured if a.startswith("q="))
    assert '"Code Review by Qodo" in:comments' in q_arg
    assert "org:acme" in q_arg


def test_get_qodo_pr_count_with_repos_sums_per_repo(monkeypatch):
    call_count = [0]
    def fake_run_gh(args, **kw):
        call_count[0] += 1
        return "5\n"
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    result = get_qodo_pr_count("acme", date(2025, 1, 1), repos=["frontend", "backend"])
    assert result == 10
    assert call_count[0] == 2


def test_get_qodo_pr_count_returns_none_on_bad_response(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: "not-a-number\n")
    result = get_qodo_pr_count("acme", date(2025, 1, 1))
    assert result is None


from datetime import timedelta
from github import search_merged_prs


def _gql_search_response(nodes=None):
    """Return a GraphQL search response JSON string with the given PR nodes."""
    return _json.dumps({
        "data": {
            "search": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": nodes or [],
            }
        }
    })


def _make_fake_run_gh(captured_queries, nodes=None):
    """Returns a fake run_gh that records q= args and returns a GraphQL response."""
    def fake(args, **kw):
        q_args = [a for a in args if a.startswith("q=")]
        captured_queries.extend(q_args)
        return _gql_search_response(nodes)
    return fake


def test_search_merged_prs_no_repos_uses_org_qualifier(monkeypatch):
    captured = []
    monkeypatch.setattr("github.run_gh", _make_fake_run_gh(captured))
    list(search_merged_prs("acme", date.today() - timedelta(days=1)))
    assert any("org:acme" in q for q in captured)
    assert not any("repo:" in q for q in captured)


def test_search_merged_prs_with_repos_uses_repo_qualifiers(monkeypatch):
    captured = []
    monkeypatch.setattr("github.run_gh", _make_fake_run_gh(captured))
    list(search_merged_prs("acme", date.today() - timedelta(days=1), repos=["frontend", "backend"]))
    assert any("repo:acme/frontend" in q for q in captured)
    assert any("repo:acme/backend" in q for q in captured)
    assert not any("org:acme" in q for q in captured)


def test_search_merged_prs_includes_qodo_comment_filter(monkeypatch):
    captured = []
    monkeypatch.setattr("github.run_gh", _make_fake_run_gh(captured))
    list(search_merged_prs("acme", date.today() - timedelta(days=1)))
    assert any('"Code Review by Qodo" in:comments' in q for q in captured)


def test_search_merged_prs_deduplicates_when_same_repo_listed_twice(monkeypatch):
    node = {
        "number": 1,
        "id": "PR_kwDOA_dedup",
        "repository": {"nameWithOwner": "acme/frontend"},
        "url": "https://github.com/acme/frontend/pull/1",
        "author": {"login": "alice"},
        "createdAt": "2026-01-01T10:00:00Z",
        "mergedAt": "2026-01-02T10:00:00Z",
    }
    call_count = [0]
    def fake_run_gh(args, **kw):
        call_count[0] += 1
        return _gql_search_response([node])  # same PR returned from both queries
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    results = list(search_merged_prs("acme", date.today() - timedelta(days=1), repos=["frontend", "frontend"]))
    assert len(results) == 1  # deduped


def test_search_merged_prs_yields_node_id(monkeypatch):
    node = {
        "number": 1,
        "id": "PR_kwDOA_test",
        "repository": {"nameWithOwner": "acme/frontend"},
        "url": "https://github.com/acme/frontend/pull/1",
        "author": {"login": "alice"},
        "createdAt": "2026-01-01T10:00:00Z",
        "mergedAt": "2026-01-02T10:00:00Z",
    }
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _gql_search_response([node]))
    results = list(search_merged_prs("acme", date.today() - timedelta(days=1)))
    assert results[0]["node_id"] == "PR_kwDOA_test"


def test_search_merged_prs_follows_pagination_cursor(monkeypatch):
    """Verify that when hasNextPage is True, a second request is made with the endCursor."""
    page1_node = {
        "number": 1,
        "id": "PR_page1",
        "repository": {"nameWithOwner": "acme/frontend"},
        "url": "https://github.com/acme/frontend/pull/1",
        "author": {"login": "alice"},
        "createdAt": "2026-01-01T10:00:00Z",
        "mergedAt": "2026-01-02T10:00:00Z",
    }
    page2_node = {
        "number": 2,
        "id": "PR_page2",
        "repository": {"nameWithOwner": "acme/frontend"},
        "url": "https://github.com/acme/frontend/pull/2",
        "author": {"login": "bob"},
        "createdAt": "2026-01-03T10:00:00Z",
        "mergedAt": "2026-01-04T10:00:00Z",
    }
    import json
    calls = []
    def fake_run_gh(args, **kw):
        calls.append(args)
        if len(calls) == 1:
            return json.dumps({"data": {"search": {
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor_abc"},
                "nodes": [page1_node],
            }}})
        return json.dumps({"data": {"search": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [page2_node],
        }}})
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    results = list(search_merged_prs("acme", date.today() - timedelta(days=1)))
    assert len(results) == 2
    assert results[0]["node_id"] == "PR_page1"
    assert results[1]["node_id"] == "PR_page2"
    # Second call must include the cursor from page 1
    second_call_args = calls[1]
    assert any(a == "cursor=cursor_abc" for a in second_call_args)


import json as _json
from github import save_checkpoint, load_checkpoint, checkpoint_path


def test_checkpoint_stores_and_loads_repos(monkeypatch, tmp_path):
    monkeypatch.setattr("github.checkpoint_path", lambda org: tmp_path / f"{org}-checkpoint.json")
    save_checkpoint("acme", {
        "since": "2025-01-01",
        "pr_total": 0,
        "suggestions_total": 0,
        "suggestions_implemented": 0,
        "processed": [],
        "rows": [],
        "repos": ["frontend", "backend"],
    })
    data = load_checkpoint("acme")
    assert data["repos"] == ["frontend", "backend"]


def test_checkpoint_repos_none_when_not_filtered(monkeypatch, tmp_path):
    monkeypatch.setattr("github.checkpoint_path", lambda org: tmp_path / f"{org}-checkpoint.json")
    save_checkpoint("acme", {
        "since": "2025-01-01",
        "pr_total": 0,
        "suggestions_total": 0,
        "suggestions_implemented": 0,
        "processed": [],
        "rows": [],
        "repos": None,
    })
    data = load_checkpoint("acme")
    assert data["repos"] is None


def _mismatch(stored_repos, current_repos_arg):
    """Replicate the resume mismatch detection logic from cmd_count."""
    normalized_stored = sorted(stored_repos) if stored_repos else None
    current_repos = sorted(current_repos_arg) if current_repos_arg else None
    return normalized_stored != current_repos


def test_resume_mismatch_detected_when_repos_differ():
    assert _mismatch(["frontend"], ["backend"])


def test_resume_mismatch_not_triggered_when_repos_match():
    assert not _mismatch(["frontend", "backend"], ["backend", "frontend"])


def test_resume_mismatch_not_triggered_when_both_none():
    assert not _mismatch(None, None)


def test_resume_mismatch_detected_when_stored_none_current_set():
    assert _mismatch(None, ["frontend"])


def test_resume_mismatch_detected_when_stored_set_current_none():
    assert _mismatch(["frontend"], None)


from github import fetch_pr_data_batch


def _batch_response(nodes):
    return _json.dumps({"data": {"nodes": nodes}})


def test_fetch_pr_data_batch_returns_keyed_by_node_id(monkeypatch):
    node = {
        "id": "PR_abc",
        "additions": 10,
        "deletions": 5,
        "comments": {"nodes": [
            {"body": "hello", "createdAt": "2026-01-01T00:00:00Z",
             "author": {"login": "alice", "__typename": "User"}},
        ]},
    }
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _batch_response([node]))
    prs = [{"node_id": "PR_abc", "owner": "acme", "repo": "frontend", "number": 1}]
    result = fetch_pr_data_batch(prs)
    assert "PR_abc" in result
    assert result["PR_abc"]["additions"] == 10
    assert result["PR_abc"]["deletions"] == 5
    assert len(result["PR_abc"]["comments"]) == 1
    assert result["PR_abc"]["comments"][0]["user"]["login"] == "alice"


def test_fetch_pr_data_batch_uses_nodes_query(monkeypatch):
    captured = []
    def fake_run_gh(args, **kw):
        captured.extend(args)
        return _batch_response([])
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    prs = [{"node_id": "PR_xyz", "owner": "acme", "repo": "r", "number": 1}]
    fetch_pr_data_batch(prs)
    query_arg = next(a for a in captured if a.startswith("query="))
    assert "nodes(ids:" in query_arg
    assert "PR_xyz" in query_arg


def test_fetch_pr_data_batch_splits_into_batches(monkeypatch):
    call_count = [0]
    def fake_run_gh(args, **kw):
        call_count[0] += 1
        return _batch_response([])
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    prs = [{"node_id": f"PR_{i}", "owner": "a", "repo": "r", "number": i} for i in range(5)]
    fetch_pr_data_batch(prs, batch_size=2)
    assert call_count[0] == 3  # ceil(5/2) = 3


def test_fetch_pr_data_batch_returns_body_and_labels(monkeypatch):
    node = {
        "id": "PR_abc",
        "additions": 0, "deletions": 0,
        "body": "Co-Authored-By: github-copilot",
        "labels": {"nodes": [{"name": "copilot"}, {"name": "bug"}]},
        "reviews": {"nodes": []},
        "lastCommit": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "allCommits": {"nodes": []},
        "comments": {"nodes": []},
    }
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _batch_response([node]))
    prs = [{"node_id": "PR_abc", "owner": "acme", "repo": "r", "number": 1}]
    result = fetch_pr_data_batch(prs)
    assert result["PR_abc"]["body"] == "Co-Authored-By: github-copilot"
    assert result["PR_abc"]["labels"] == ["copilot", "bug"]


def test_fetch_pr_data_batch_returns_reviews(monkeypatch):
    node = {
        "id": "PR_def",
        "additions": 0, "deletions": 0,
        "body": "",
        "labels": {"nodes": []},
        "reviews": {"nodes": [{"author": {"login": "alice"}, "state": "APPROVED", "submittedAt": "2026-01-01T12:00:00Z"}]},
        "lastCommit": {"nodes": []},
        "allCommits": {"nodes": []},
        "comments": {"nodes": []},
    }
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _batch_response([node]))
    prs = [{"node_id": "PR_def", "owner": "acme", "repo": "r", "number": 2}]
    result = fetch_pr_data_batch(prs)
    assert result["PR_def"]["reviews"][0]["state"] == "APPROVED"
    assert result["PR_def"]["ci_status"] is None


def test_fetch_pr_data_batch_returns_ci_status_and_commits(monkeypatch):
    node = {
        "id": "PR_ghi",
        "additions": 0, "deletions": 0,
        "body": "",
        "labels": {"nodes": []},
        "reviews": {"nodes": []},
        "lastCommit": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]},
        "allCommits": {"nodes": [{"committedDate": "2026-01-01T11:00:00Z", "message": "fix: resolve review"}]},
        "comments": {"nodes": []},
    }
    monkeypatch.setattr("github.run_gh", lambda args, **kw: _batch_response([node]))
    prs = [{"node_id": "PR_ghi", "owner": "acme", "repo": "r", "number": 3}]
    result = fetch_pr_data_batch(prs)
    assert result["PR_ghi"]["ci_status"] == "FAILURE"
    assert len(result["PR_ghi"]["commits"]) == 1
    assert result["PR_ghi"]["commits"][0]["committedDate"] == "2026-01-01T11:00:00Z"


from github import get_revert_pr_count, get_hotfix_pr_count


def test_get_revert_pr_count_includes_revert_in_title(monkeypatch):
    captured = []
    def fake_run_gh(args, **kw):
        captured.extend(args)
        return "7\n"
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    result = get_revert_pr_count("acme", date(2026, 1, 1))
    assert result == 7
    q_args = [a for a in captured if a.startswith("q=")]
    assert any("revert in:title" in q for q in q_args)


def test_get_hotfix_pr_count_uses_head_qualifier(monkeypatch):
    captured = []
    def fake_run_gh(args, **kw):
        captured.extend(args)
        return "3\n"
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    result = get_hotfix_pr_count("acme", date(2026, 1, 1))
    assert result == 3
    q_args = [a for a in captured if a.startswith("q=")]
    assert any("head:hotfix" in q for q in q_args)


def test_get_revert_pr_count_with_repos_sums(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: "4\n")
    result = get_revert_pr_count("acme", date(2026, 1, 1), repos=["a", "b"])
    assert result == 8


def test_get_revert_pr_count_returns_none_on_error(monkeypatch):
    def bad_run_gh(args, **kw):
        raise Exception("network error")
    monkeypatch.setattr("github.run_gh", bad_run_gh)
    result = get_revert_pr_count("acme", date(2026, 1, 1))
    assert result is None


def test_get_hotfix_pr_count_with_repos_sums(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: "4\n")
    result = get_hotfix_pr_count("acme", date(2026, 1, 1), repos=["a", "b"])
    assert result == 8


def test_get_hotfix_pr_count_returns_none_on_error(monkeypatch):
    def bad_run_gh(args, **kw):
        raise Exception("network error")
    monkeypatch.setattr("github.run_gh", bad_run_gh)
    result = get_hotfix_pr_count("acme", date(2026, 1, 1))
    assert result is None


from github import get_weekly_pr_counts


def test_get_weekly_pr_counts_returns_list(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: "5\n")
    result = get_weekly_pr_counts("acme", date(2026, 5, 11))  # Monday
    assert isinstance(result, list)
    assert len(result) >= 1
    assert "week_start" in result[0]
    assert "total" in result[0]
    assert "qodo" in result[0]


def test_get_weekly_pr_counts_week_start_is_monday(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda args, **kw: "0\n")
    result = get_weekly_pr_counts("acme", date(2026, 5, 13))  # Wednesday
    # First week_start must be the Monday of that week
    from datetime import date as d
    first_week = d.fromisoformat(result[0]["week_start"])
    assert first_week.weekday() == 0  # Monday


def test_get_weekly_pr_counts_calls_search_once_per_week(monkeypatch):
    call_count = [0]
    def fake_run_gh(args, **kw):
        call_count[0] += 1
        return "3\n"
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    # Force exactly one week by passing since = today - 1 day
    from datetime import date as d, timedelta
    result = get_weekly_pr_counts("acme", d.today() - timedelta(days=1))
    # One week → 1 call (total only; qodo counts come from _qodo_counts_by_week)
    assert call_count[0] == 1


def test_get_weekly_pr_counts_with_repos_uses_repo_qualifier(monkeypatch):
    captured = []
    def fake_run_gh(args, **kw):
        captured.extend(args)
        return "1\n"
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    from datetime import date as d, timedelta
    get_weekly_pr_counts("acme", d.today() - timedelta(days=1), repos=["frontend"])
    q_args = [a for a in captured if a.startswith("q=")]
    assert any("repo:acme/frontend" in q for q in q_args)
