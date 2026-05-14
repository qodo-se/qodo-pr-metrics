import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from report import aggregate, ReportData


def _row(repo="backend", creator="alice", has_qodo=True,
         suggestions=4, implemented=2,
         ar_sug=2, ar_imp=1, rr_sug=2, rr_imp=1,
         bugs_sug=1, bugs_imp=1, rule_sug=2, rule_imp=1,
         req_sug=1, req_imp=0):
    return {
        "Repo Name": repo, "PR #": 1,
        "PR URL": "https://github.com/acme/backend/pull/1",
        "PR Creator": creator,
        "Has Qodo Review": has_qodo,
        "Total Suggestions": suggestions,
        "Total Implemented": implemented,
        "Action Required Suggestions": ar_sug,
        "Action Required Implemented": ar_imp,
        "Review Recommended Suggestions": rr_sug,
        "Review Recommended Implemented": rr_imp,
        "Bugs Suggested": bugs_sug,
        "Bugs Implemented": bugs_imp,
        "Rule Violations Suggested": rule_sug,
        "Rule Violations Implemented": rule_imp,
        "Requirement Gaps Suggested": req_sug,
        "Requirement Gaps Implemented": req_imp,
        "Implementation Rate (%)": f"{100 * implemented / suggestions:.1f}" if suggestions else "",
    }


def test_aggregate_empty():
    agg = aggregate([])
    assert agg.total_prs == 0
    assert agg.prs_with_qodo == 0
    assert agg.qodo_coverage_pct == 0.0
    assert agg.total_suggestions == 0
    assert agg.by_repo == []
    assert agg.by_developer == []
    assert agg.top_prs == []
    assert agg.top_prs_by_implemented == []


def test_aggregate_basic_counts():
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        _row(repo="api", creator="bob", suggestions=2, implemented=2),
        _row(repo="web", creator="alice", suggestions=1, implemented=0),
    ]
    agg = aggregate(rows)
    assert agg.total_prs == 3
    assert agg.prs_with_qodo == 3
    assert agg.total_suggestions == 8
    assert agg.total_implemented == 5
    assert agg.overall_impl_rate_pct == 62.5


def test_aggregate_coverage_excludes_non_qodo():
    rows = [
        _row(has_qodo=True),
        _row(has_qodo=False, suggestions=0, implemented=0),
        _row(has_qodo=False, suggestions=0, implemented=0),
    ]
    agg = aggregate(rows)
    assert agg.total_prs == 3
    assert agg.prs_with_qodo == 1
    assert agg.qodo_coverage_pct == round(100 / 3, 1)


def test_aggregate_non_qodo_excluded_from_repo_and_dev_stats():
    rows = [
        _row(repo="api", creator="alice", has_qodo=True, suggestions=4, implemented=2),
        _row(repo="api", creator="alice", has_qodo=False, suggestions=0, implemented=0),
    ]
    agg = aggregate(rows)
    assert len(agg.by_repo) == 1
    assert agg.by_repo[0]["prs"] == 1
    assert len(agg.by_developer) == 1
    assert agg.by_developer[0]["prs"] == 1


def test_aggregate_by_repo_sorted_by_prs_desc():
    rows = [
        _row(repo="api"),
        _row(repo="web"),
        _row(repo="api"),
    ]
    agg = aggregate(rows)
    assert agg.by_repo[0]["repo"] == "api"
    assert agg.by_repo[0]["prs"] == 2


def test_aggregate_by_developer_capped_at_10():
    rows = [_row(creator=f"dev{i}") for i in range(15)]
    agg = aggregate(rows)
    assert len(agg.by_developer) == 10


def test_aggregate_top_prs_returns_top_5_by_suggestions():
    rows = [_row(suggestions=i, implemented=0) for i in range(10, 0, -1)]
    agg = aggregate(rows)
    assert len(agg.top_prs) == 5
    assert agg.top_prs[0]["Total Suggestions"] == 10


def test_aggregate_top_prs_by_implemented_returns_top_5_by_implemented():
    rows = [_row(suggestions=10, implemented=i) for i in range(10, 0, -1)]
    agg = aggregate(rows)
    assert len(agg.top_prs_by_implemented) == 5
    assert agg.top_prs_by_implemented[0]["Total Implemented"] == 10


def test_aggregate_top_prs_by_implemented_excludes_non_qodo():
    rows = [
        _row(has_qodo=True, suggestions=5, implemented=5),
        _row(has_qodo=False, suggestions=0, implemented=0),
    ]
    agg = aggregate(rows)
    assert len(agg.top_prs_by_implemented) == 1


def test_aggregate_severity_totals():
    rows = [
        _row(ar_sug=3, ar_imp=2, rr_sug=1, rr_imp=1),
        _row(ar_sug=1, ar_imp=0, rr_sug=2, rr_imp=1),
    ]
    agg = aggregate(rows)
    assert agg.action_required_suggested == 4
    assert agg.action_required_implemented == 2
    assert agg.review_recommended_suggested == 3
    assert agg.review_recommended_implemented == 2


def test_aggregate_category_totals():
    rows = [
        _row(bugs_sug=2, bugs_imp=1, rule_sug=1, rule_imp=0, req_sug=1, req_imp=1),
        _row(bugs_sug=1, bugs_imp=1, rule_sug=3, rule_imp=2, req_sug=0, req_imp=0),
    ]
    agg = aggregate(rows)
    assert agg.bugs_suggested == 3
    assert agg.bugs_implemented == 2
    assert agg.rule_violations_suggested == 4
    assert agg.rule_violations_implemented == 2
    assert agg.requirement_gaps_suggested == 1
    assert agg.requirement_gaps_implemented == 1


def test_aggregate_zero_suggestions_rates_are_zero():
    rows = [_row(ar_sug=0, ar_imp=0, rr_sug=0, rr_imp=0,
                 bugs_sug=0, bugs_imp=0, rule_sug=0, rule_imp=0,
                 req_sug=0, req_imp=0, suggestions=0, implemented=0)]
    agg = aggregate(rows)
    assert agg.action_required_rate_pct == 0.0
    assert agg.review_recommended_rate_pct == 0.0
    assert agg.bugs_rate_pct == 0.0
    assert agg.rule_violations_rate_pct == 0.0
    assert agg.requirement_gaps_rate_pct == 0.0
    assert agg.overall_impl_rate_pct == 0.0


from datetime import date


def test_generate_html_smoke():
    from report import generate_html
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        _row(repo="web", creator="bob", has_qodo=False, suggestions=0, implemented=0),
    ]
    html = generate_html(rows, "acme-corp", date(2025, 1, 1), date(2026, 1, 1), logo_path=None)
    assert "<!DOCTYPE html>" in html
    assert "acme-corp" in html
    assert "Qodo Code Review" in html
    assert "Executive Summary" in html
    assert "Adoption" in html
    assert "Impact by Severity" in html
    assert "Impact by Category" in html
    assert "Top 5 Merged PRs" in html
    # stat values present
    assert 'class="stat-value">1<' in html   # prs_with_qodo
    assert 'class="stat-value">5<' in html   # total_suggestions
    assert 'class="stat-value">3<' in html   # total_implemented


def test_generate_html_includes_top_prs_by_implemented_section():
    from report import generate_html
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        _row(repo="web", creator="bob", suggestions=3, implemented=3),
    ]
    html = generate_html(rows, "acme-corp", date(2025, 1, 1), date(2026, 1, 1), logo_path=None)
    assert "Top 5 Merged PRs by Implemented Suggestions" in html


import json as _json


def _timing_row(repo="backend", creator="alice", has_qodo=True,
                suggestions=4, implemented=2,
                qodo_min=8, human_min=270, has_human=True,
                spotlight=None):
    base = _row(repo=repo, creator=creator, has_qodo=has_qodo,
                suggestions=suggestions, implemented=implemented)
    base["Time to First Qodo Comment (min)"] = qodo_min if qodo_min is not None else ""
    base["Time to First Human Comment (min)"] = human_min if human_min is not None else ""
    base["Has Human Comment"] = has_human
    base["Spotlight Issues"] = _json.dumps(spotlight or [])
    return base


def test_aggregate_velocity_median():
    rows = [
        _timing_row(qodo_min=6, human_min=200),
        _timing_row(qodo_min=10, human_min=300),
        _timing_row(qodo_min=8, human_min=None, has_human=False),
    ]
    agg = aggregate(rows)
    assert agg.velocity_qodo_median_min == 8.0      # median of [6, 8, 10]
    assert agg.velocity_human_median_min == 250.0   # median of [200, 300]


def test_aggregate_velocity_none_when_no_data():
    rows = [_timing_row(qodo_min=None, human_min=None, has_human=False)]
    agg = aggregate(rows)
    assert agg.velocity_qodo_median_min is None
    assert agg.velocity_human_median_min is None


def test_aggregate_pct_no_human_comment():
    rows = [
        _timing_row(has_qodo=True, has_human=False),
        _timing_row(has_qodo=True, has_human=True),
        _timing_row(has_qodo=True, has_human=False),
    ]
    agg = aggregate(rows)
    assert agg.pct_no_human_comment == round(200 / 3, 1)


def test_aggregate_spotlight_issues_collected():
    issue = {"title": "API key leak", "category": "bug", "sub_label": "Security"}
    rows = [
        _timing_row(repo="api", spotlight=[issue]),
        _timing_row(repo="web", spotlight=[]),
    ]
    agg = aggregate(rows)
    assert len(agg.spotlight_issues) == 1
    assert agg.spotlight_issues[0]["repo"] == "api"
    assert agg.spotlight_issues[0]["title"] == "API key leak"
    assert agg.spotlight_issues[0]["pr_num"] == 1


def test_aggregate_developer_metrics():
    rows = [
        _timing_row(creator="alice", has_qodo=True, implemented=3),
        _timing_row(creator="bob",   has_qodo=True, implemented=0),
        _timing_row(creator="carol", has_qodo=False, suggestions=0, implemented=0),
    ]
    agg = aggregate(rows)
    assert agg.developers_total == 3
    assert agg.developers_with_qodo == 2
    assert agg.developers_engaged == 1   # only alice implemented > 0


def test_aggregate_empty_new_fields():
    agg = aggregate([])
    assert agg.velocity_qodo_median_min is None
    assert agg.velocity_human_median_min is None
    assert agg.pct_no_human_comment == 0.0
    assert agg.spotlight_issues == []
    assert agg.developers_total == 0
    assert agg.developers_with_qodo == 0
    assert agg.developers_engaged == 0


def test_generate_html_includes_velocity_section():
    from report import generate_html
    rows = [
        _timing_row(qodo_min=8, human_min=270, has_human=True),
        _timing_row(qodo_min=12, human_min=300, has_human=True),
    ]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "Velocity" in html
    assert "Time to First Feedback" in html
    assert "10m" in html    # median of [8, 12] = 10


def test_generate_html_velocity_hidden_when_no_data():
    from report import generate_html
    rows = [_row()]   # _row() has no timing columns → all empty
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "Time to First Feedback" not in html


def test_generate_html_no_human_comment_insight():
    from report import generate_html
    rows = [
        _timing_row(qodo_min=8, human_min=None, has_human=False),
        _timing_row(qodo_min=8, human_min=None, has_human=False),
    ]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "sole feedback" in html


def test_generate_html_includes_spotlight_section():
    from report import generate_html
    issue = {"title": "Hardcoded API key", "category": "bug", "sub_label": "Security"}
    rows = [_timing_row(spotlight=[issue])]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "High-Impact Issues" in html
    assert "Hardcoded API key" in html
    assert "Security" in html


def test_generate_html_spotlight_hidden_when_empty():
    from report import generate_html
    rows = [_timing_row(spotlight=[])]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "High-Impact Issues" not in html


def test_generate_html_spotlight_links_pr():
    from report import generate_html
    issue = {"title": "SQL injection risk", "category": "bug", "sub_label": "Security"}
    rows = [_timing_row(spotlight=[issue])]
    rows[0]["PR URL"] = "https://github.com/acme/repo/pull/42"
    rows[0]["PR #"] = 42
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "PR #42" in html
    assert "https://github.com/acme/repo/pull/42" in html


def test_generate_html_adoption_developer_breadth():
    from report import generate_html
    rows = [
        _timing_row(creator="alice", has_qodo=True, implemented=2),
        _timing_row(creator="bob",   has_qodo=True, implemented=0),
        _timing_row(creator="carol", has_qodo=False, suggestions=0, implemented=0),
    ]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    # "2 of 3 developers participated"
    assert "2" in html
    assert "of 3 developers" in html


def test_generate_html_adoption_engagement():
    from report import generate_html
    rows = [
        _timing_row(creator="alice", implemented=3),
        _timing_row(creator="bob",   implemented=0),
    ]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "developers implemented" in html
