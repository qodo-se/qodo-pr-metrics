import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import _build_anon_maps, _apply_anonymization


def test_build_anon_maps_users_sorted_alphabetically():
    rows = [
        {"PR Creator": "charlie", "Final Approver": "alice", "Repo Name": "backend"},
        {"PR Creator": "bob", "Final Approver": "alice", "Repo Name": "frontend"},
    ]
    user_map, _ = _build_anon_maps(rows)
    assert user_map == {"alice": "User 1", "bob": "User 2", "charlie": "User 3"}


def test_build_anon_maps_repos_sorted_alphabetically():
    rows = [
        {"PR Creator": "alice", "Final Approver": "", "Repo Name": "zebra-svc"},
        {"PR Creator": "bob", "Final Approver": "", "Repo Name": "alpha-api"},
    ]
    _, repo_map = _build_anon_maps(rows)
    assert repo_map == {"alpha-api": "Repo 1", "zebra-svc": "Repo 2"}


def test_build_anon_maps_blank_approver_excluded():
    rows = [
        {"PR Creator": "alice", "Final Approver": "", "Repo Name": "backend"},
    ]
    user_map, _ = _build_anon_maps(rows)
    assert user_map == {"alice": "User 1"}
    assert "" not in user_map


def test_build_anon_maps_blank_creator_excluded():
    rows = [{"PR Creator": "", "Final Approver": "alice", "Repo Name": "backend"}]
    user_map, _ = _build_anon_maps(rows)
    assert "" not in user_map
    assert user_map == {"alice": "User 1"}


def test_build_anon_maps_approver_only_user_included():
    # Someone who only ever appears as approver (never as PR Creator) still gets a label
    rows = [
        {"PR Creator": "alice", "Final Approver": "reviewer-only", "Repo Name": "backend"},
    ]
    user_map, _ = _build_anon_maps(rows)
    assert "reviewer-only" in user_map


def test_build_anon_maps_missing_final_approver_key():
    # Rows that lack the Final Approver key entirely don't crash
    rows = [
        {"PR Creator": "alice", "Repo Name": "backend"},
    ]
    user_map, repo_map = _build_anon_maps(rows)
    assert user_map == {"alice": "User 1"}
    assert repo_map == {"backend": "Repo 1"}


def test_build_anon_maps_empty_rows():
    user_map, repo_map = _build_anon_maps([])
    assert user_map == {}
    assert repo_map == {}


def test_apply_anonymization_replaces_creator_and_repo():
    rows = [
        {
            "PR Creator": "alice", "Final Approver": "bob",
            "Repo Name": "frontend", "PR URL": "https://github.com/org/frontend/pull/42",
            "PR #": 42,
        }
    ]
    user_map = {"alice": "User 1", "bob": "User 2"}
    repo_map = {"frontend": "Repo 1"}
    _apply_anonymization(rows, user_map, repo_map)
    assert rows[0]["PR Creator"] == "User 1"
    assert rows[0]["Final Approver"] == "User 2"
    assert rows[0]["Repo Name"] == "Repo 1"


def test_apply_anonymization_strips_pr_url():
    rows = [
        {
            "PR Creator": "alice", "Final Approver": "",
            "Repo Name": "backend", "PR URL": "https://github.com/org/backend/pull/7",
            "PR #": 7,
        }
    ]
    _apply_anonymization(rows, {"alice": "User 1"}, {"backend": "Repo 1"})
    assert rows[0]["PR URL"] == "#PR-7"


def test_apply_anonymization_empty_approver_stays_empty():
    rows = [
        {
            "PR Creator": "alice", "Final Approver": "",
            "Repo Name": "backend", "PR URL": "https://github.com/org/backend/pull/1",
            "PR #": 1,
        }
    ]
    _apply_anonymization(rows, {"alice": "User 1"}, {"backend": "Repo 1"})
    assert rows[0]["Final Approver"] == ""


def test_apply_anonymization_scope_users_only():
    rows = [{"PR Creator": "alice", "Final Approver": "bob",
             "Repo Name": "frontend", "PR URL": "https://github.com/org/frontend/pull/1", "PR #": 1}]
    user_map = {"alice": "User 1", "bob": "User 2"}
    repo_map = {"frontend": "Repo 1"}
    _apply_anonymization(rows, user_map, repo_map, scope="users")
    assert rows[0]["PR Creator"] == "User 1"
    assert rows[0]["Final Approver"] == "User 2"
    assert rows[0]["Repo Name"] == "frontend"
    assert rows[0]["PR URL"] == "https://github.com/org/frontend/pull/1"


def test_apply_anonymization_scope_repos_only():
    rows = [{"PR Creator": "alice", "Final Approver": "bob",
             "Repo Name": "frontend", "PR URL": "https://github.com/org/frontend/pull/1", "PR #": 1}]
    user_map = {"alice": "User 1", "bob": "User 2"}
    repo_map = {"frontend": "Repo 1"}
    _apply_anonymization(rows, user_map, repo_map, scope="repos")
    assert rows[0]["PR Creator"] == "alice"
    assert rows[0]["Final Approver"] == "bob"
    assert rows[0]["Repo Name"] == "Repo 1"
    assert rows[0]["PR URL"] == "#PR-1"


def test_apply_anonymization_scope_all_equivalent_to_default():
    rows_default = [{"PR Creator": "alice", "Final Approver": "",
                     "Repo Name": "api", "PR URL": "https://github.com/org/api/pull/5", "PR #": 5}]
    rows_all = [r.copy() for r in rows_default]
    user_map = {"alice": "User 1"}
    repo_map = {"api": "Repo 1"}
    _apply_anonymization(rows_default, user_map, repo_map)
    _apply_anonymization(rows_all, user_map, repo_map, scope="all")
    assert rows_default == rows_all


def test_apply_anonymization_mutates_all_rows():
    rows = [
        {"PR Creator": "alice", "Final Approver": "", "Repo Name": "api",
         "PR URL": "https://github.com/org/api/pull/1", "PR #": 1},
        {"PR Creator": "bob", "Final Approver": "alice", "Repo Name": "ui",
         "PR URL": "https://github.com/org/ui/pull/2", "PR #": 2},
    ]
    user_map = {"alice": "User 1", "bob": "User 2"}
    repo_map = {"api": "Repo 1", "ui": "Repo 2"}
    _apply_anonymization(rows, user_map, repo_map)
    assert rows[0]["PR Creator"] == "User 1"
    assert rows[1]["PR Creator"] == "User 2"
    assert rows[1]["Final Approver"] == "User 1"
    assert rows[0]["PR URL"] == "#PR-1"
    assert rows[1]["PR URL"] == "#PR-2"
