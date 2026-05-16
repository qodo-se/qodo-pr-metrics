import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import _qodo_counts_by_week


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
