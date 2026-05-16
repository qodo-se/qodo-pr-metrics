import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from report import aggregate, ReportData


def _row(repo="backend", creator="alice",
         suggestions=4, implemented=2,
         ar_sug=2, ar_imp=1, rr_sug=2, rr_imp=1,
         bugs_sug=1, bugs_imp=1, rule_sug=2, rule_imp=1,
         req_sug=1, req_imp=0):
    return {
        "Repo Name": repo, "PR #": 1,
        "PR URL": "https://github.com/acme/backend/pull/1",
        "PR Creator": creator,
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
    assert agg.prs_with_qodo == 0
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
    assert agg.prs_with_qodo == 3
    assert agg.total_suggestions == 8
    assert agg.total_implemented == 5
    assert agg.overall_impl_rate_pct == 62.5



def test_aggregate_repo_and_dev_stats():
    rows = [
        _row(repo="api", creator="alice", suggestions=4, implemented=2),
        _row(repo="api", creator="alice", suggestions=2, implemented=1),
    ]
    agg = aggregate(rows)
    assert len(agg.by_repo) == 1
    assert agg.by_repo[0]["prs"] == 2
    assert len(agg.by_developer) == 1
    assert agg.by_developer[0]["prs"] == 2


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


def test_aggregate_top_prs_by_implemented_counts_all():
    rows = [
        _row(suggestions=5, implemented=5),
        _row(suggestions=2, implemented=0),
    ]
    agg = aggregate(rows)
    assert len(agg.top_prs_by_implemented) == 2
    assert agg.top_prs_by_implemented[0]["Total Implemented"] == 5


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
        _row(repo="web", creator="bob", suggestions=0, implemented=0),
    ]
    html = generate_html(rows, "acme-corp", date(2025, 1, 1), date(2026, 1, 1), logo_path=None)
    assert "<!DOCTYPE html>" in html
    assert "acme-corp" in html
    assert "Code review impact report" in html
    assert "Adoption" in html
    assert "By severity" in html
    assert "By category" in html
    assert "Most fixes implemented" in html
    # stat values present
    assert 'class="kpi-value">2<' in html   # prs_with_qodo
    assert 'class="kpi-value">5<' in html   # total_suggestions
    assert 'class="kpi-value">3<' in html   # total_implemented


def test_generate_html_includes_top_prs_by_implemented_section():
    from report import generate_html
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        _row(repo="web", creator="bob", suggestions=3, implemented=3),
    ]
    html = generate_html(rows, "acme-corp", date(2025, 1, 1), date(2026, 1, 1), logo_path=None)
    assert "Most fixes implemented in a single PR" in html


import json as _json


def _timing_row(repo="backend", creator="alice",
                suggestions=4, implemented=2,
                qodo_min=8, human_min=270, has_human=True,
                spotlight=None):
    base = _row(repo=repo, creator=creator,
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
        _timing_row(has_human=False),
        _timing_row(has_human=True),
        _timing_row(has_human=False),
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
        _timing_row(creator="alice", implemented=3),
        _timing_row(creator="bob",   implemented=0),
        _timing_row(creator="carol", suggestions=0, implemented=0),
    ]
    agg = aggregate(rows)
    assert agg.developers_with_qodo == 3
    assert agg.developers_engaged == 1   # only alice implemented > 0


def test_aggregate_empty_new_fields():
    agg = aggregate([])
    assert agg.velocity_qodo_median_min is None
    assert agg.velocity_human_median_min is None
    assert agg.pct_no_human_comment == 0.0
    assert agg.spotlight_issues == []
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
    assert "First feedback on a PR" in html
    assert "10m" in html    # median of [8, 12] = 10


def test_generate_html_velocity_sub_minute_shows_lt1m():
    from report import generate_html
    rows = [_timing_row(qodo_min=0, human_min=0, has_human=True)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "&lt;1m" in html
    assert ">0m<" not in html


def test_generate_html_velocity_hidden_when_no_data():
    from report import generate_html
    rows = [_row()]   # _row() has no timing columns → all empty
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "First feedback on a PR" not in html


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
    assert "High-impact findings caught" in html
    assert "Hardcoded API key" in html
    assert "Security" in html


def test_generate_html_spotlight_hidden_when_empty():
    from report import generate_html
    rows = [_timing_row(spotlight=[])]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "High-impact findings caught" not in html


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
        _timing_row(creator="alice", implemented=2),
        _timing_row(creator="bob",   implemented=0),
        _timing_row(creator="carol", suggestions=0, implemented=0),
    ]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "Coverage across the org" in html
    assert "alice" in html
    assert "Developers who implemented at least one Qodo fix" in html


def test_generate_html_adoption_engagement():
    from report import generate_html
    rows = [
        _timing_row(creator="alice", implemented=3),
        _timing_row(creator="bob",   implemented=0),
    ]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "Developers who implemented" in html


def test_spotlight_security_sorted_before_correctness():
    from report import generate_html
    issues = [
        {"title": "Correctness Issue", "category": "bug", "sub_label": "Correctness"},
        {"title": "Security Issue",    "category": "bug", "sub_label": "Security"},
    ]
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert html.index("Security Issue") < html.index("Correctness Issue")


def test_spotlight_50_50_split():
    from report import generate_html, SPOTLIGHT_LIMIT
    # 20 Security + 20 Correctness = 40 total; expect 5 of each shown (10 total)
    issues = (
        [{"title": f"Sec {i}",  "category": "bug", "sub_label": "Security"}    for i in range(20)] +
        [{"title": f"Cor {i}",  "category": "bug", "sub_label": "Correctness"} for i in range(20)]
    )
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert html.count('class="spot-card-title"') == SPOTLIGHT_LIMIT
    assert html.count('<span class="tag tag-security">Security</span>') == 5
    assert html.count('<span class="tag tag-correctness">Correctness</span>') == 5


def test_spotlight_spare_slots_to_correctness_when_security_short():
    from report import generate_html
    # 2 Security + 20 Correctness: Security gets 2 slots, remaining 8 go to Correctness
    issues = (
        [{"title": f"Sec {i}",  "category": "bug", "sub_label": "Security"}    for i in range(2)] +
        [{"title": f"Cor {i}",  "category": "bug", "sub_label": "Correctness"} for i in range(20)]
    )
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert html.count('<span class="tag tag-security">Security</span>') == 2
    assert html.count('<span class="tag tag-correctness">Correctness</span>') == 8


def test_spotlight_spare_slots_to_security_when_correctness_short():
    from report import generate_html
    # 20 Security + 2 Correctness: Correctness gets 2 slots, remaining 8 go to Security
    issues = (
        [{"title": f"Sec {i}",  "category": "bug", "sub_label": "Security"}    for i in range(20)] +
        [{"title": f"Cor {i}",  "category": "bug", "sub_label": "Correctness"} for i in range(2)]
    )
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert html.count('<span class="tag tag-security">Security</span>') == 8
    assert html.count('<span class="tag tag-correctness">Correctness</span>') == 2


def test_spotlight_keyword_promoted_within_security_bucket():
    from report import generate_html
    # 3 keyword Security + 5 plain Security + 6 Correctness = 14 total
    # Security gets 5 slots; keyword issues rank first → all 3 appear, plain 2-4 are cut
    keyword_issues = [
        {"title": f"bypass vuln {i}", "category": "bug", "sub_label": "Security"}
        for i in range(3)
    ]
    plain_issues = [
        {"title": f"plain sec {i}", "category": "bug", "sub_label": "Security"}
        for i in range(5)
    ]
    correctness_issues = [
        {"title": f"cor issue {i}", "category": "bug", "sub_label": "Correctness"}
        for i in range(6)
    ]
    rows = [_timing_row(spotlight=keyword_issues + plain_issues + correctness_issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    for i in range(3):
        assert f"bypass vuln {i}" in html
    assert "plain sec 0" in html
    assert "plain sec 1" in html
    for i in range(2, 5):
        assert f"plain sec {i}" not in html


def test_spotlight_keyword_promoted_within_correctness_bucket():
    from report import generate_html
    # 6 Security + 2 keyword Correctness + 4 plain Correctness = 12 total
    # Correctness gets 5 slots; keyword issues rank first → both appear, plain 3 is cut
    security_issues = [
        {"title": f"sec {i}", "category": "bug", "sub_label": "Security"}
        for i in range(6)
    ]
    keyword_issues = [
        {"title": f"crash fix {i}", "category": "bug", "sub_label": "Correctness"}
        for i in range(2)
    ]
    plain_issues = [
        {"title": f"plain cor {i}", "category": "bug", "sub_label": "Correctness"}
        for i in range(4)
    ]
    rows = [_timing_row(spotlight=security_issues + keyword_issues + plain_issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    for i in range(2):
        assert f"crash fix {i}" in html
    for i in range(3):
        assert f"plain cor {i}" in html
    assert "plain cor 3" not in html


def test_spotlight_other_sub_label_sorted_last():
    from report import generate_html
    issues = [
        {"title": "Unknown Issue",     "category": "bug", "sub_label": "Performance"},
        {"title": "Correctness Issue", "category": "bug", "sub_label": "Correctness"},
        {"title": "Security Issue",    "category": "bug", "sub_label": "Security"},
    ]
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert html.index("Security Issue") < html.index("Correctness Issue") < html.index("Unknown Issue")


def test_spotlight_truncated_at_limit():
    from report import generate_html, SPOTLIGHT_LIMIT
    issues = [
        {"title": f"Issue {i}", "category": "bug", "sub_label": "Security"}
        for i in range(SPOTLIGHT_LIMIT + 3)
    ]
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert html.count('class="spot-card-title"') == SPOTLIGHT_LIMIT


def test_spotlight_no_footer_when_at_or_below_limit():
    from report import generate_html, SPOTLIGHT_LIMIT
    issues = [
        {"title": f"Issue {i}", "category": "bug", "sub_label": "Security"}
        for i in range(SPOTLIGHT_LIMIT)
    ]
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert 'class="spot-more"' not in html


def test_spotlight_footer_shows_remainder_count():
    from report import generate_html, SPOTLIGHT_LIMIT
    issues = [
        {"title": f"Issue {i}", "category": "bug", "sub_label": "Security"}
        for i in range(SPOTLIGHT_LIMIT + 4)
    ]
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert 'class="spot-more"' in html
    assert "+ 4 more" in html


def test_spotlight_footer_breakdown_by_sublabel():
    from report import generate_html, SPOTLIGHT_LIMIT
    # 6 Security + 6 Correctness = 12 total, limit=10
    # 50-50 logic: 5 Security + 5 Correctness shown; 2 hidden total
    issues = (
        [{"title": f"Sec {i}", "category": "bug", "sub_label": "Security"}    for i in range(6)] +
        [{"title": f"Cor {i}", "category": "bug", "sub_label": "Correctness"} for i in range(6)]
    )
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert 'class="spot-more"' in html
    assert "+ 2 more" in html


def test_spotlight_footer_other_sub_label():
    from report import generate_html, SPOTLIGHT_LIMIT
    # 10 Security + 1 unknown = 11 total; 1 "Performance" issue hidden
    issues = (
        [{"title": f"Sec {i}", "category": "bug", "sub_label": "Security"}      for i in range(10)] +
        [{"title": "Perf Issue", "category": "bug", "sub_label": "Performance"}]
    )
    rows = [_timing_row(spotlight=issues)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert 'class="spot-more"' in html
    assert "+ 1 more" in html


def test_spotlight_section_before_top_prs():
    from report import generate_html
    issue = {"title": "Security issue", "category": "bug", "sub_label": "Security"}
    rows = [
        _timing_row(spotlight=[issue], suggestions=5, implemented=3),
        _timing_row(spotlight=[],      suggestions=3, implemented=3),
    ]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    spotlight_pos    = html.index("High-impact findings caught")
    top_prs_impl_pos = html.index("Most fixes implemented")
    assert spotlight_pos < top_prs_impl_pos


def test_generate_html_requirement_gaps_hidden_when_zero():
    from report import generate_html
    rows = [_row(req_sug=0, req_imp=0)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "Requirement gaps" not in html


def test_generate_html_requirement_gaps_shown_when_nonzero():
    from report import generate_html
    rows = [_row(req_sug=1, req_imp=0)]
    html = generate_html(rows, "acme", date(2025,1,1), date(2026,1,1), logo_path=None)
    assert "Requirement gaps" in html


def test_aggregate_rows_without_qodo_flag_treated_as_reviewed():
    # Matches production input shape: github.py no longer emits Has Qodo Review
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        _row(repo="web", creator="bob",   suggestions=2, implemented=1),
    ]
    assert "Has Qodo Review" not in rows[0]
    agg = aggregate(rows)
    assert agg.prs_with_qodo == 2
    assert len(agg.by_repo) == 2
    assert len(agg.by_developer) == 2
    assert len(agg.top_prs) == 2
    assert len(agg.top_prs_by_implemented) == 2
    assert agg.developers_with_qodo == 2


def test_aggregate_explicit_false_qodo_flag_excludes_row():
    # Explicit False excludes the row from breakdowns; absent key is treated as True
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        {**_row(repo="web", creator="bob"), "Has Qodo Review": False},
    ]
    agg = aggregate(rows)
    assert agg.prs_with_qodo == 1
    assert len(agg.by_repo) == 1
    assert agg.by_repo[0]["repo"] == "api"
    assert agg.developers_with_qodo == 1
    assert len(agg.top_prs) == 1


def _extras_row(repo="backend", creator="alice",
                suggestions=4, implemented=2,
                is_ai=False, ai_type="", reviewer_count=2,
                had_request_changes=False, approver="bob",
                ci_status="SUCCESS", commits_after=3, speed_min=30,
                qodo_min=8, human_min=270):
    base = _timing_row(repo=repo, creator=creator,
                       suggestions=suggestions, implemented=implemented,
                       qodo_min=qodo_min, human_min=human_min)
    base["Is AI Authored"] = is_ai
    base["AI Author Type"] = ai_type
    base["Reviewer Count"] = reviewer_count
    base["Had Request Changes"] = had_request_changes
    base["Final Approver"] = approver
    base["CI Status"] = ci_status
    base["Commits After Qodo"] = commits_after
    base["Speed to First Fix (min)"] = speed_min
    return base


def test_aggregate_ai_authored_count():
    rows = [
        _extras_row(is_ai=True, ai_type="copilot"),
        _extras_row(is_ai=False),
        _extras_row(is_ai=True, ai_type="cursor"),
    ]
    agg = aggregate(rows)
    assert agg.ai_authored_count == 2


def test_aggregate_ai_authored_impl_rate():
    rows = [
        _extras_row(is_ai=True, suggestions=4, implemented=4),
        _extras_row(is_ai=True, suggestions=4, implemented=0),
    ]
    agg = aggregate(rows)
    assert agg.ai_authored_impl_rate_pct == 50.0


def test_aggregate_avg_reviewer_count():
    rows = [
        _extras_row(reviewer_count=3),
        _extras_row(reviewer_count=1),
    ]
    agg = aggregate(rows)
    assert agg.avg_reviewer_count == 2.0


def test_aggregate_pct_had_request_changes():
    rows = [
        _extras_row(had_request_changes=True),
        _extras_row(had_request_changes=False),
        _extras_row(had_request_changes=False),
    ]
    agg = aggregate(rows)
    assert agg.pct_had_request_changes == round(100 / 3, 1)


def test_aggregate_ci_pass_rate():
    rows = [
        _extras_row(ci_status="SUCCESS"),
        _extras_row(ci_status="SUCCESS"),
        _extras_row(ci_status="FAILURE"),
        _extras_row(ci_status=""),    # no CI data
    ]
    agg = aggregate(rows)
    assert agg.ci_pass_rate_pct == round(200 / 3, 1)


def test_aggregate_ci_pass_rate_none_when_no_ci_data():
    rows = [_extras_row(ci_status=""), _extras_row(ci_status="")]
    agg = aggregate(rows)
    assert agg.ci_pass_rate_pct is None


def test_aggregate_speed_to_fix_median():
    rows = [
        _extras_row(speed_min=10),
        _extras_row(speed_min=20),
        _extras_row(speed_min=30),
    ]
    agg = aggregate(rows)
    assert agg.speed_to_fix_median_min == 20.0


def test_aggregate_speed_to_fix_none_when_empty():
    rows = [_extras_row(speed_min="")]
    agg = aggregate(rows)
    assert agg.speed_to_fix_median_min is None


def test_aggregate_weekly_coverage_passthrough():
    weekly = [{"week_start": "2026-05-11", "total": 10, "qodo": 5}]
    agg = aggregate([], weekly_coverage=weekly)
    assert agg.weekly_coverage == weekly


def test_aggregate_revert_hotfix_passthrough():
    agg = aggregate([], revert_count=3, hotfix_count=1)
    assert agg.revert_count == 3
    assert agg.hotfix_count == 1


def test_aggregate_new_fields_default_when_empty():
    agg = aggregate([])
    assert agg.ai_authored_count == 0
    assert agg.ai_authored_impl_rate_pct == 0.0
    assert agg.avg_reviewer_count == 0.0
    assert agg.pct_had_request_changes == 0.0
    assert agg.ci_pass_rate_pct is None
    assert agg.speed_to_fix_median_min is None
    assert agg.weekly_coverage == []
    assert agg.revert_count is None
    assert agg.hotfix_count is None
