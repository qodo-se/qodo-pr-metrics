import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from user_impact import (
    aggregate_user_impact,
    generate_user_html,
    _humanize_login,
    _parse_iso_date,
)


# 10-day window: valid day offsets are 0..9 (Jan 1 through Jan 10 inclusive).
SINCE = "2025-01-01"
UNTIL = "2025-01-10"


def _row(creator="alice", created="2025-01-02", has_qodo=True, lines=100,
         ar_sug=2, ar_imp=1):
    """Build a per-PR row dict with the fields aggregate_user_impact reads."""
    return {
        "PR Creator": creator,
        "PR Creation Date": f"{created}T10:00:00Z",
        "Has Qodo Review": has_qodo,
        "Lines Added": lines,
        "Action Required Suggestions": ar_sug,
        "Action Required Implemented": ar_imp,
    }


def _by_login(data):
    """Index aggregated users by their login for order-independent lookups."""
    return {u["login"]: u for u in data.users}


def test_aggregate_empty():
    """Empty rows yield no users, no PR records, but a valid window."""
    data = aggregate_user_impact([], SINCE, UNTIL)
    assert data.users == []
    assert data.pr_records == []
    assert data.window == {"start": SINCE, "end": UNTIL, "total_days": 10}


def test_aggregate_known_totals():
    """Per-user totals match hand-calculated sums over the window."""
    rows = [
        _row("alice", "2025-01-02", lines=100, ar_sug=4, ar_imp=2),
        _row("alice", "2025-01-05", lines=50, ar_sug=2, ar_imp=1),
        _row("bob", "2025-01-03", lines=30, ar_sug=1, ar_imp=0),
    ]
    data = aggregate_user_impact(rows, SINCE, UNTIL)
    users = _by_login(data)

    assert set(users) == {"alice", "bob"}
    assert len(data.pr_records) == 3

    alice = users["alice"]
    assert alice["prs"] == 2
    assert alice["reviewed"] == 2
    assert alice["locTotal"] == 150
    assert alice["locReviewed"] == 150
    assert alice["arSugg"] == 6
    assert alice["arAcc"] == 3

    bob = users["bob"]
    assert bob["prs"] == 1
    assert bob["reviewed"] == 1
    assert bob["locTotal"] == 30
    assert bob["arSugg"] == 1
    assert bob["arAcc"] == 0


def test_day_offset_filtering():
    """PRs created before `since` or after `until` are dropped from records and totals."""
    rows = [
        _row("carol", "2024-12-31"),                 # offset -1 -> excluded
        _row("carol", "2025-01-06", lines=80),       # offset 5  -> kept
        _row("carol", "2025-01-11"),                 # offset 10 -> excluded
    ]
    data = aggregate_user_impact(rows, SINCE, UNTIL)

    assert len(data.pr_records) == 1
    rec = data.pr_records[0]
    assert rec["user"] == "carol"
    assert rec["dayOffset"] == 5

    carol = _by_login(data)["carol"]
    assert carol["prs"] == 1
    assert carol["locTotal"] == 80


def test_unreviewed_pr_counts_toward_total_not_reviewed():
    """A non-Qodo-reviewed PR adds to total PRs/LOC but not to reviewed/AR figures."""
    rows = [
        _row("dave", "2025-01-02", has_qodo=True, lines=100, ar_sug=3, ar_imp=2),
        # Unreviewed PR: its AR fields must be ignored even though they are non-zero.
        _row("dave", "2025-01-03", has_qodo=False, lines=40, ar_sug=5, ar_imp=5),
    ]
    data = aggregate_user_impact(rows, SINCE, UNTIL)
    dave = _by_login(data)["dave"]

    assert dave["prs"] == 2
    assert dave["reviewed"] == 1
    assert dave["locTotal"] == 140
    assert dave["locReviewed"] == 100
    assert dave["arSugg"] == 3
    assert dave["arAcc"] == 2

    # The unreviewed record zeroes its reviewed-only fields.
    unreviewed = [r for r in data.pr_records if r["reviewed"] == 0]
    assert len(unreviewed) == 1
    assert unreviewed[0]["locReviewed"] == 0
    assert unreviewed[0]["arSugg"] == 0
    assert unreviewed[0]["arAcc"] == 0
    assert unreviewed[0]["locTotal"] == 40


def test_user_with_no_in_window_prs_is_excluded():
    """A user whose only PR falls outside the window does not appear in users.

    Inactive ("No activity") rows are a client-side slider concept seeded from
    the full-window baseline; the Python aggregate only emits in-window users.
    """
    rows = [
        _row("active", "2025-01-04"),
        _row("ghost", "2024-06-01"),  # far outside the window
    ]
    data = aggregate_user_impact(rows, SINCE, UNTIL)
    logins = {u["login"] for u in data.users}
    assert "active" in logins
    assert "ghost" not in logins


def test_invalid_window_dates():
    """Unparseable since/until collapse to a single-day window with no data."""
    data = aggregate_user_impact([_row()], "not-a-date", UNTIL)
    assert data.users == []
    assert data.pr_records == []
    assert data.window["total_days"] == 1


def test_generate_user_html_json_blocks_round_trip():
    """The three embedded application/json blocks parse cleanly and stay consistent."""
    rows = [
        _row("alice", "2025-01-02", lines=100, ar_sug=4, ar_imp=2),
        _row("alice", "2025-01-05", lines=50, ar_sug=2, ar_imp=1),
        _row("bob", "2025-01-03", lines=30, ar_sug=1, ar_imp=0),
    ]
    html = generate_user_html(rows, "acme", SINCE, UNTIL)
    assert html.lstrip().startswith("<!DOCTYPE html>")

    blocks = dict(
        re.findall(
            r'<script type="application/json" id="([^"]+)">(.*?)</script>',
            html,
            re.S,
        )
    )
    assert set(blocks) == {"ui-records", "ui-baseline", "ui-window"}

    records = json.loads(blocks["ui-records"])
    baseline = json.loads(blocks["ui-baseline"])
    window = json.loads(blocks["ui-window"])

    assert window == {"start": SINCE, "end": UNTIL, "total_days": 10}
    assert len(records) == 3
    assert {r["user"] for r in records} == {"alice", "bob"}
    assert {b["login"] for b in baseline} == {"alice", "bob"}


def test_humanize_login():
    """Logins are split on separators and title-cased, falling back to the input."""
    assert _humanize_login("ofer-sabo") == "Ofer Sabo"
    assert _humanize_login("alice") == "Alice"
    assert _humanize_login("dev_jane.doe") == "Dev Jane Doe"
    assert _humanize_login("") == ""


def test_parse_iso_date_formats():
    """Both timestamped and date-only ISO strings parse; junk returns None."""
    assert _parse_iso_date("2025-01-02T10:00:00Z").isoformat() == "2025-01-02"
    assert _parse_iso_date("2025-01-02").isoformat() == "2025-01-02"
    assert _parse_iso_date("nonsense") is None
    assert _parse_iso_date("") is None
