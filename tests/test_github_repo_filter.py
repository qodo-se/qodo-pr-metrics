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


def _make_fake_run_gh(captured_queries, pr_lines=None):
    """Returns a fake run_gh that records q= args and returns pr_lines as JSON."""
    pr_lines = pr_lines or []
    def fake(args, **kw):
        q_args = [a for a in args if a.startswith("q=")]
        captured_queries.extend(q_args)
        return "\n".join(pr_lines)
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
    import json
    pr_json = json.dumps({
        "number": 1,
        "repo": "https://api.github.com/repos/acme/frontend",
        "url": "https://github.com/acme/frontend/pull/1",
        "creator": "alice",
        "created_at": "2026-01-01T10:00:00Z",
        "merged_at": "2026-01-02T10:00:00Z",
    })
    call_count = [0]
    def fake_run_gh(args, **kw):
        call_count[0] += 1
        return pr_json  # same PR returned from both queries
    monkeypatch.setattr("github.run_gh", fake_run_gh)
    results = list(search_merged_prs("acme", date.today() - timedelta(days=1), repos=["frontend", "frontend"]))
    assert len(results) == 1  # deduped


def test_search_merged_prs_yields_node_id(monkeypatch):
    import json
    pr_json = json.dumps({
        "number": 1,
        "node_id": "PR_kwDOA_test",
        "repo": "https://api.github.com/repos/acme/frontend",
        "url": "https://github.com/acme/frontend/pull/1",
        "creator": "alice",
        "created_at": "2026-01-01T10:00:00Z",
        "merged_at": "2026-01-02T10:00:00Z",
    })
    monkeypatch.setattr("github.run_gh", lambda args, **kw: pr_json)
    results = list(search_merged_prs("acme", date.today() - timedelta(days=1)))
    assert results[0]["node_id"] == "PR_kwDOA_test"


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
