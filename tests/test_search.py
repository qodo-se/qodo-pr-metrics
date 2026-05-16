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
