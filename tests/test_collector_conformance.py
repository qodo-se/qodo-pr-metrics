import inspect
import pytest

from collectors.base import Collector, get_collector
from collectors.github import GitHubCollector


def test_github_collector_satisfies_protocol():
    # runtime_checkable verifies method NAMES are present.
    assert isinstance(GitHubCollector(), Collector)


def test_github_collector_has_every_protocol_method():
    protocol_methods = {
        name for name in dir(Collector)
        if not name.startswith("_") or name == "search_merged_prs"
    }
    expected = {
        "search_merged_prs", "fetch_pr_data", "fetch_pr_data_batch",
        "get_org_pr_count", "get_org_author_count", "get_org_repo_count",
        "get_qodo_pr_count", "get_all_pr_loc", "get_revert_pr_count",
        "get_hotfix_pr_count", "get_weekly_pr_counts",
    }
    for name in expected:
        assert callable(getattr(GitHubCollector, name, None)), f"missing {name}"


def test_get_collector_returns_github():
    assert isinstance(get_collector("github"), GitHubCollector)


def test_get_collector_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_collector("bitbucket")
