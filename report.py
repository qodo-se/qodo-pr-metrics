from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional
import base64


def _rate(implemented: int, total: int) -> float:
    return round(100 * implemented / total, 1) if total > 0 else 0.0


@dataclass
class ReportData:
    total_prs: int
    prs_with_qodo: int
    qodo_coverage_pct: float
    total_suggestions: int
    total_implemented: int
    overall_impl_rate_pct: float
    action_required_suggested: int
    action_required_implemented: int
    action_required_rate_pct: float
    review_recommended_suggested: int
    review_recommended_implemented: int
    review_recommended_rate_pct: float
    bugs_suggested: int
    bugs_implemented: int
    bugs_rate_pct: float
    rule_violations_suggested: int
    rule_violations_implemented: int
    rule_violations_rate_pct: float
    requirement_gaps_suggested: int
    requirement_gaps_implemented: int
    requirement_gaps_rate_pct: float
    by_repo: list
    by_developer: list
    top_prs: list


def aggregate(rows: list) -> ReportData:
    total_prs = len(rows)
    prs_with_qodo = sum(1 for r in rows if r.get("Has Qodo Review"))

    total_sug = sum(r.get("Total Suggestions", 0) for r in rows)
    total_imp = sum(r.get("Total Implemented", 0) for r in rows)

    ar_sug = sum(r.get("Action Required Suggestions", 0) for r in rows)
    ar_imp = sum(r.get("Action Required Implemented", 0) for r in rows)
    rr_sug = sum(r.get("Review Recommended Suggestions", 0) for r in rows)
    rr_imp = sum(r.get("Review Recommended Implemented", 0) for r in rows)
    bugs_sug = sum(r.get("Bugs Suggested", 0) for r in rows)
    bugs_imp = sum(r.get("Bugs Implemented", 0) for r in rows)
    rule_sug = sum(r.get("Rule Violations Suggested", 0) for r in rows)
    rule_imp = sum(r.get("Rule Violations Implemented", 0) for r in rows)
    req_sug = sum(r.get("Requirement Gaps Suggested", 0) for r in rows)
    req_imp = sum(r.get("Requirement Gaps Implemented", 0) for r in rows)

    repo_acc: dict = defaultdict(lambda: {"prs": 0, "suggestions": 0, "implemented": 0})
    dev_acc: dict = defaultdict(lambda: {"prs": 0, "suggestions": 0, "implemented": 0})
    for r in rows:
        if not r.get("Has Qodo Review"):
            continue
        repo_acc[r["Repo Name"]]["prs"] += 1
        repo_acc[r["Repo Name"]]["suggestions"] += r.get("Total Suggestions", 0)
        repo_acc[r["Repo Name"]]["implemented"] += r.get("Total Implemented", 0)
        dev_acc[r["PR Creator"]]["prs"] += 1
        dev_acc[r["PR Creator"]]["suggestions"] += r.get("Total Suggestions", 0)
        dev_acc[r["PR Creator"]]["implemented"] += r.get("Total Implemented", 0)

    by_repo = sorted(
        [{"repo": k, **v} for k, v in repo_acc.items()],
        key=lambda x: x["prs"], reverse=True,
    )
    by_developer = sorted(
        [{"developer": k, **v} for k, v in dev_acc.items()],
        key=lambda x: x["prs"], reverse=True,
    )[:10]
    top_prs = sorted(
        [r for r in rows if r.get("Has Qodo Review")],
        key=lambda r: r.get("Total Suggestions", 0), reverse=True,
    )[:5]

    return ReportData(
        total_prs=total_prs,
        prs_with_qodo=prs_with_qodo,
        qodo_coverage_pct=_rate(prs_with_qodo, total_prs),
        total_suggestions=total_sug,
        total_implemented=total_imp,
        overall_impl_rate_pct=_rate(total_imp, total_sug),
        action_required_suggested=ar_sug,
        action_required_implemented=ar_imp,
        action_required_rate_pct=_rate(ar_imp, ar_sug),
        review_recommended_suggested=rr_sug,
        review_recommended_implemented=rr_imp,
        review_recommended_rate_pct=_rate(rr_imp, rr_sug),
        bugs_suggested=bugs_sug,
        bugs_implemented=bugs_imp,
        bugs_rate_pct=_rate(bugs_imp, bugs_sug),
        rule_violations_suggested=rule_sug,
        rule_violations_implemented=rule_imp,
        rule_violations_rate_pct=_rate(rule_imp, rule_sug),
        requirement_gaps_suggested=req_sug,
        requirement_gaps_implemented=req_imp,
        requirement_gaps_rate_pct=_rate(req_imp, req_sug),
        by_repo=by_repo,
        by_developer=by_developer,
        top_prs=top_prs,
    )
