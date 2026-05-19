import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import _build_anon_maps


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
