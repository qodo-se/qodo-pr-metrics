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
