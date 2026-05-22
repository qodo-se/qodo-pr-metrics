import sys, os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import _qodo_counts_by_week
from github import get_all_pr_loc
from datetime import date


def test_empty_list_returns_empty_dict():
    assert _qodo_counts_by_week([]) == {}


def test_pr_is_bucketed_to_its_monday():
    # 2026-05-13 is a Wednesday; its Monday is 2026-05-11
    prs = [{"merged_at": "2026-05-13T10:00:00Z"}]
    assert _qodo_counts_by_week(prs) == {"2026-05-11": 1}


def test_multiple_prs_same_week_are_counted():
    prs = [
        {"merged_at": "2026-05-11T00:00:00Z"},  # Monday
        {"merged_at": "2026-05-12T00:00:00Z"},  # Tuesday
        {"merged_at": "2026-05-17T00:00:00Z"},  # Sunday (still same Mon week)
    ]
    assert _qodo_counts_by_week(prs) == {"2026-05-11": 3}


def test_prs_in_different_weeks_are_split():
    prs = [
        {"merged_at": "2026-05-11T00:00:00Z"},  # week of 2026-05-11
        {"merged_at": "2026-05-18T00:00:00Z"},  # week of 2026-05-18
    ]
    result = _qodo_counts_by_week(prs)
    assert result == {"2026-05-11": 1, "2026-05-18": 1}


def test_pr_without_merged_at_is_skipped():
    prs = [{"merged_at": ""}, {"merged_at": None}, {}]
    assert _qodo_counts_by_week(prs) == {}


from github import _parse_search_gql_nodes


_SAMPLE_NODES = [
    {
        "number": 42,
        "id": "PR_abc123",
        "repository": {"nameWithOwner": "acme-corp/frontend"},
        "url": "https://github.com/acme-corp/frontend/pull/42",
        "author": {"login": "alice"},
        "createdAt": "2026-05-01T09:00:00Z",
        "mergedAt": "2026-05-02T11:00:00Z",
    },
    {
        "number": 99,
        "id": "PR_def456",
        "repository": {"nameWithOwner": "acme-corp/backend"},
        "url": "https://github.com/acme-corp/backend/pull/99",
        "author": None,  # bot or deleted user
        "createdAt": "2026-05-03T10:00:00Z",
        "mergedAt": "2026-05-04T12:00:00Z",
    },
]


def test_parse_nodes_extracts_owner_and_repo():
    result = _parse_search_gql_nodes(_SAMPLE_NODES)
    assert result[0]["owner"] == "acme-corp"
    assert result[0]["repo"] == "frontend"
    assert result[1]["repo"] == "backend"


def test_parse_nodes_maps_all_fields():
    result = _parse_search_gql_nodes(_SAMPLE_NODES)
    r = result[0]
    assert r["number"] == 42
    assert r["node_id"] == "PR_abc123"
    assert r["url"] == "https://github.com/acme-corp/frontend/pull/42"
    assert r["creator"] == "alice"
    assert r["created_at"] == "2026-05-01T09:00:00Z"
    assert r["merged_at"] == "2026-05-02T11:00:00Z"


def test_parse_nodes_handles_null_author():
    result = _parse_search_gql_nodes(_SAMPLE_NODES)
    assert result[1]["creator"] == ""


def test_parse_nodes_skips_empty_nodes():
    # GraphQL returns {} for non-PullRequest search hits; skip them
    nodes = [{}] + _SAMPLE_NODES
    result = _parse_search_gql_nodes(nodes)
    assert len(result) == 2


def test_parse_nodes_empty_list():
    assert _parse_search_gql_nodes([]) == []


def _loc_response(additions_list, has_next=False, end_cursor=None):
    nodes = [{"additions": a} for a in additions_list]
    return json.dumps({
        "data": {
            "search": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                "nodes": nodes,
            }
        }
    })


def test_get_all_pr_loc_sums_additions(monkeypatch):
    # Basic test: single chunk with multiple PRs
    monkeypatch.setattr("github.run_gh", lambda _args: _loc_response([100, 200, 50]))
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend"])
    assert result == 350


def test_get_all_pr_loc_sums_across_chunks(monkeypatch):
    # chunk_days=1 with a 2-day window forces two separate API calls (chunks),
    # verifying the outer date loop accumulates correctly across both.
    calls = []

    def mock_gh(_args):
        calls.append(1)
        return _loc_response([100, 50])  # 150 per chunk

    monkeypatch.setattr("github.run_gh", mock_gh)
    # date(2026, 5, 19) to today (2026-05-21) = 2 days → 2 chunks with chunk_days=1
    result = get_all_pr_loc("acme", date(2026, 5, 19), repos=["frontend"], chunk_days=1)
    assert result == 300   # 150 per chunk × 2 chunks
    assert len(calls) == 2


def test_get_all_pr_loc_skips_empty_nodes(monkeypatch):
    response = json.dumps({
        "data": {
            "search": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{}, {"additions": 100}],
            }
        }
    })
    monkeypatch.setattr("github.run_gh", lambda _args: response)
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend"])
    assert result == 100


def test_get_all_pr_loc_handles_null_additions(monkeypatch):
    response = json.dumps({
        "data": {
            "search": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"additions": None}, {"additions": 75}],
            }
        }
    })
    monkeypatch.setattr("github.run_gh", lambda _args: response)
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend"])
    assert result == 75


def test_get_all_pr_loc_handles_pagination(monkeypatch):
    calls = []

    def mock_gh(args):
        calls.append(args)
        if len(calls) == 1:
            return _loc_response([100], has_next=True, end_cursor="abc")
        return _loc_response([200])

    monkeypatch.setattr("github.run_gh", mock_gh)
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend"])
    assert result == 300
    assert len(calls) == 2
    assert any("abc" in str(arg) for arg in calls[1])


def test_get_all_pr_loc_returns_none_on_error(monkeypatch):
    def boom(_args):
        raise RuntimeError("API unavailable")

    monkeypatch.setattr("github.run_gh", boom)
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend"])
    assert result is None


def test_get_all_pr_loc_returns_none_on_malformed_response(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda _args: '{"data": {}}')
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend"])
    assert result is None


def test_get_all_pr_loc_returns_zero_for_empty_window(monkeypatch):
    monkeypatch.setattr("github.run_gh", lambda _args: _loc_response([]))
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend"])
    assert result == 0


def test_get_all_pr_loc_uses_org_qualifier_when_no_repos(monkeypatch):
    captured = []

    def capture_gh(args):
        captured.extend(args)
        return _loc_response([50])

    monkeypatch.setattr("github.run_gh", capture_gh)
    result = get_all_pr_loc("acme", date(2026, 5, 20))
    assert result == 50
    assert any("org:acme" in str(arg) for arg in captured)


def test_get_all_pr_loc_queries_each_repo(monkeypatch):
    captured = []

    def capture_gh(args):
        captured.append(" ".join(str(a) for a in args))
        return _loc_response([10])

    monkeypatch.setattr("github.run_gh", capture_gh)
    result = get_all_pr_loc("acme", date(2026, 5, 20), repos=["frontend", "backend"])
    assert result == 20  # 10 per repo
    assert any("frontend" in call for call in captured)
    assert any("backend" in call for call in captured)
