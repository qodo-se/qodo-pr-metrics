from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from html import escape as _h
from pathlib import Path
from typing import Optional
import base64
import json
import mimetypes
import re
import statistics

SPOTLIGHT_LIMIT = 10
_SPOTLIGHT_PRIORITY = {"Security": 0, "Correctness": 1}


def _rate(implemented: int, total: int) -> float:
    return round(100 * implemented / total, 1) if total > 0 else 0.0


@dataclass
class ReportData:
    prs_with_qodo: int
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
    top_prs_by_implemented: list
    velocity_qodo_median_min: Optional[float]
    velocity_human_median_min: Optional[float]
    pct_no_human_comment: float
    spotlight_issues: list
    developers_total: int
    developers_with_qodo: int
    developers_engaged: int


def aggregate(rows: list) -> ReportData:
    prs_with_qodo = len(rows)

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
    top_prs_by_implemented = sorted(
        [r for r in rows if r.get("Has Qodo Review")],
        key=lambda r: r.get("Total Implemented", 0), reverse=True,
    )[:5]

    # Velocity
    qodo_times = [
        r["Time to First Qodo Comment (min)"] for r in rows
        if r.get("Time to First Qodo Comment (min)") not in ("", None)
    ]
    human_times = [
        r["Time to First Human Comment (min)"] for r in rows
        if r.get("Time to First Human Comment (min)") not in ("", None)
    ]
    no_human_qodo = sum(
        1 for r in rows
        if r.get("Has Qodo Review") and not r.get("Has Human Comment")
    )
    velocity_qodo_median = _median(qodo_times)
    velocity_human_median = _median(human_times)
    pct_no_human = _rate(no_human_qodo, prs_with_qodo) if prs_with_qodo else 0.0

    # Spotlight
    spotlight_issues = []
    for r in rows:
        raw = r.get("Spotlight Issues", "[]")
        try:
            issues = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (json.JSONDecodeError, TypeError):
            issues = []
        for issue in issues:
            spotlight_issues.append({
                **issue,
                "repo": r["Repo Name"],
                "pr_num": r["PR #"],
                "pr_url": r.get("PR URL", ""),
            })

    # Developer metrics
    all_devs = {r["PR Creator"] for r in rows if r.get("PR Creator")}
    devs_with_qodo = {
        r["PR Creator"] for r in rows
        if r.get("Has Qodo Review") and r.get("PR Creator")
    }
    devs_engaged = {
        r["PR Creator"] for r in rows
        if r.get("Total Implemented", 0) > 0 and r.get("PR Creator")
    }

    return ReportData(
        prs_with_qodo=prs_with_qodo,
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
        top_prs_by_implemented=top_prs_by_implemented,
        velocity_qodo_median_min=velocity_qodo_median,
        velocity_human_median_min=velocity_human_median,
        pct_no_human_comment=pct_no_human,
        spotlight_issues=spotlight_issues,
        developers_total=len(all_devs),
        developers_with_qodo=len(devs_with_qodo),
        developers_engaged=len(devs_engaged),
    )


_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  background: #f4f4f4;
  color: #1c1c1c;
  font-size: 14px;
  line-height: 1.5;
}
.page { max-width: 900px; margin: 0 auto; padding: 32px 24px; }
.report-header {
  display: flex; align-items: center; gap: 20px;
  background: #ffffff; border-radius: 8px;
  padding: 24px 28px; margin-bottom: 24px;
  border-bottom: 3px solid #634fd1;
}
.report-header img, .report-header svg { height: 48px; flex-shrink: 0; }
.report-header h1 { font-size: 22px; font-weight: 700; color: #634fd1; }
.subtitle { color: #6e6e6e; font-size: 13px; margin-top: 4px; }
section {
  background: #ffffff; border-radius: 8px;
  padding: 24px 28px; margin-bottom: 24px;
}
section h2 {
  font-size: 16px; font-weight: 600; color: #634fd1;
  margin-bottom: 18px; border-bottom: 1px solid #dfdfdf; padding-bottom: 10px;
}
.subsection { font-size: 13px; font-weight: 600; color: #3d3d3d; margin-bottom: 10px; }
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 14px;
}
.stat-card {
  background: #dddcff; border-radius: 6px;
  padding: 18px 16px; text-align: center;
}
.stat-value { font-size: 28px; font-weight: 700; color: #634fd1; line-height: 1; }
.stat-label {
  font-size: 11px; color: #3d3d3d; margin-top: 6px;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.impact-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
}
.impact-card {
  border-radius: 6px; padding: 16px;
  border-left: 4px solid #634fd1; background: #f4f4f4;
}
.impact-card.red  { border-left-color: #e55c83; }
.impact-card.orange { border-left-color: #cca05a; }
.impact-card.purple { border-left-color: #634fd1; }
.impact-card h3 { font-size: 13px; font-weight: 600; color: #3d3d3d; margin-bottom: 10px; }
.impact-row {
  display: flex; justify-content: space-between;
  font-size: 13px; margin-bottom: 4px;
}
.impact-row .val { font-weight: 600; }
.rate-pill {
  display: inline-block; padding: 2px 8px; border-radius: 12px;
  font-size: 12px; font-weight: 600;
  background: #c7f5ea; color: #206b58; margin-top: 8px;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 8px 12px;
  background: #f4f4f4; font-weight: 600; color: #3d3d3d;
  border-bottom: 2px solid #dfdfdf;
}
tbody tr:nth-child(even) { background: #f4f4f4; }
tbody td { padding: 8px 12px; border-bottom: 1px solid #dfdfdf; }
tbody td a { color: #634fd1; text-decoration: none; }
tbody td a:hover { text-decoration: underline; }
@media print {
  body { background: #ffffff; font-size: 11px; }
  .page { max-width: 100%; padding: 0; }
  section { break-inside: avoid; border: 1px solid #dfdfdf; margin-bottom: 16px; }
  .report-header { border: 1px solid #dfdfdf; margin-bottom: 16px; }
  .two-col { grid-template-columns: 1fr 1fr; }
}
.vs-block {
  display: grid; grid-template-columns: 1fr auto 1fr; gap: 16px;
  align-items: center; margin-bottom: 14px;
}
.vs-side {
  border-radius: 8px; padding: 20px; text-align: center;
}
.vs-qodo { background: #dddcff; border: 2px solid #634fd1; }
.vs-human { background: #f9f9f9; border: 2px solid #d0d0d0; }
.vs-name {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; margin-bottom: 8px;
}
.vs-name-qodo { color: #634fd1; }
.vs-name-human { color: #666; }
.vs-time { font-size: 36px; font-weight: 700; line-height: 1; }
.vs-time-qodo { color: #634fd1; }
.vs-time-human { color: #555; }
.vs-sub { font-size: 11px; color: #888; margin-top: 5px; }
.vs-divider { font-size: 20px; font-weight: 700; color: #bbb; text-align: center; }
.insight-box {
  background: #f8f7ff; border-left: 4px solid #634fd1;
  border-radius: 4px; padding: 12px 16px; font-size: 13px; color: #444;
  margin-top: 14px;
}
.spotlight-card {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 12px; border-radius: 6px; padding: 14px 16px; margin-bottom: 10px;
  border: 1px solid #eee; border-left: 4px solid #e55c83;
}
.spotlight-correctness { border-left-color: #634fd1; }
.spotlight-left { flex: 1; }
.spotlight-title { font-size: 13px; font-weight: 500; color: #1c1c1c; margin-bottom: 6px; }
.spotlight-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.spotlight-right { text-align: right; flex-shrink: 0; }
.spotlight-repo { font-size: 12px; color: #666; margin-bottom: 3px; }
.spotlight-pr { font-size: 12px; }
.spotlight-pr a { color: #634fd1; text-decoration: none; }
.spotlight-pr a:hover { text-decoration: underline; }
.spotlight-more {
  font-size: 13px; color: #666; text-align: center;
  padding: 10px 0 4px; border-top: 1px solid #efefef; margin-top: 6px;
}
.tag { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.tag-cat { background: #fdeef3; color: #a02040; }
.tag-sub-security { background: #fff0d6; color: #8a5a00; }
.tag-sub-correctness { background: #e8f4ff; color: #1a5a8a; }
.tag-impl { background: #c7f5ea; color: #206b58; }
"""


def _embed_logo(logo_path: Optional[str]) -> str:
    if not logo_path:
        return ""
    p = Path(logo_path)
    if not p.exists():
        return ""
    if p.suffix.lower() == ".svg":
        text = p.read_text(encoding="utf-8")
        text = re.sub(r"<\?xml[^?]*\?>", "", text).strip()
        text = re.sub(r'\s+width="[^"]*"', "", text, count=1)
        text = re.sub(r'\s+height="[^"]*"', "", text, count=1)
        text = re.sub(r"(<svg\b)", r'\1 height="48"', text, count=1)
        return text
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:{mime};base64,{b64}" alt="Qodo" height="48">'


def _median(values: list) -> Optional[float]:
    if not values:
        return None
    return statistics.median(float(v) for v in values)


def _rate_str(implemented: int, total: int) -> str:
    return f"{100 * implemented / total:.1f}%" if total > 0 else "—"


def _format_duration(minutes: Optional[float]) -> str:
    if minutes is None:
        return "&mdash;"
    if minutes == 0:
        return "&lt;1m"
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _stat_card(label: str, value: str) -> str:
    return (
        f'<div class="stat-card">'
        f'<div class="stat-value">{value}</div>'
        f'<div class="stat-label">{label}</div>'
        f'</div>'
    )


def _impact_card(title: str, color: str, suggested: int, implemented: int) -> str:
    rate = _rate_str(implemented, suggested)
    return (
        f'<div class="impact-card {color}">'
        f'<h3>{title}</h3>'
        f'<div class="impact-row"><span>Suggested</span><span class="val">{suggested}</span></div>'
        f'<div class="impact-row"><span>Implemented</span><span class="val">{implemented}</span></div>'
        f'<div><span class="rate-pill">{rate} implemented</span></div>'
        f'</div>'
    )


def _section_exec_summary(agg: ReportData) -> str:
    cards = "".join([
        _stat_card("Merged PRs Reviewed by Qodo", str(agg.prs_with_qodo)),
        _stat_card("Total Issues Caught", str(agg.total_suggestions)),
        _stat_card("Issues Resolved", str(agg.total_implemented)),
        _stat_card("Overall Implementation Rate", f"{agg.overall_impl_rate_pct:.1f}%"),
    ])
    return f'<section><h2>Executive Summary</h2><div class="stat-grid">{cards}</div></section>'


def _section_velocity(agg: ReportData) -> str:
    if agg.velocity_qodo_median_min is None and agg.velocity_human_median_min is None:
        return ""

    qodo_time = _format_duration(agg.velocity_qodo_median_min)
    human_time = _format_duration(agg.velocity_human_median_min)

    multiplier_html = ""
    if (agg.velocity_qodo_median_min is not None
            and agg.velocity_human_median_min is not None
            and agg.velocity_qodo_median_min > 0):
        mult = agg.velocity_human_median_min / agg.velocity_qodo_median_min
        multiplier_html = (
            f'<div style="text-align:center;margin-top:10px">'
            f'<span class="rate-pill">{mult:.0f}&times; faster initial feedback</span>'
            f'</div>'
        )

    insight_html = ""
    if agg.pct_no_human_comment > 0:
        insight_html = (
            f'<div class="insight-box">'
            f'<strong>{agg.pct_no_human_comment:.0f}%</strong> of Qodo-reviewed PRs '
            f'received no human comment &mdash; Qodo provided the sole feedback before merge.'
            f'</div>'
        )

    vs_block = (
        f'<div class="vs-block">'
        f'<div class="vs-side vs-qodo">'
        f'<div class="vs-name vs-name-qodo">Qodo</div>'
        f'<div class="vs-time vs-time-qodo">{qodo_time}</div>'
        f'<div class="vs-sub">median time to first comment</div>'
        f'</div>'
        f'<div class="vs-divider">vs</div>'
        f'<div class="vs-side vs-human">'
        f'<div class="vs-name vs-name-human">First human commenter</div>'
        f'<div class="vs-time vs-time-human">{human_time}</div>'
        f'<div class="vs-sub">median time to first comment</div>'
        f'</div>'
        f'</div>'
    )

    return (
        f'<section><h2>Velocity &mdash; Time to First Feedback</h2>'
        f'{vs_block}'
        f'{multiplier_html}'
        f'{insight_html}'
        f'</section>'
    )


_CATEGORY_DISPLAY = {
    "bug": "Bug",
    "rule_violation": "Rule Violation",
    "requirement_gap": "Requirement Gap",
    "unknown": "Issue",
}


def _section_bug_spotlight(agg: ReportData) -> str:
    if not agg.spotlight_issues:
        return ""

    sorted_issues = sorted(
        agg.spotlight_issues,
        key=lambda i: _SPOTLIGHT_PRIORITY.get(i.get("sub_label", ""), 2)
    )
    displayed = sorted_issues[:SPOTLIGHT_LIMIT]
    hidden    = sorted_issues[SPOTLIGHT_LIMIT:]

    cards = ""
    for issue in displayed:
        url = issue.get("pr_url", "")
        safe_url = url if url.startswith(("https://", "http://")) else ""
        pr_num_safe = _h(str(issue.get("pr_num", "")))
        pr_ref = (
            f'<a href="{_h(safe_url)}">PR #{pr_num_safe} &#8599;</a>'
            if safe_url else f'PR #{pr_num_safe}'
        )
        cat_display = _CATEGORY_DISPLAY.get(issue.get("category", ""), "Issue")
        sub_label = issue.get("sub_label", "")
        # Allowlist known sub_labels to prevent CSS class injection from untrusted CSV data
        safe_sub_class = {"Security": "tag-sub-security", "Correctness": "tag-sub-correctness"}.get(sub_label, "tag-sub-correctness")
        border_class = "spotlight-correctness" if sub_label == "Correctness" else ""
        sub_label_tag = (
            f'<span class="tag {safe_sub_class}">{_h(sub_label)}</span>'
            if sub_label else ""
        )
        cards += (
            f'<div class="spotlight-card {border_class}">'
            f'<div class="spotlight-left">'
            f'<div class="spotlight-title">{_h(issue.get("title", ""))}</div>'
            f'<div class="spotlight-tags">'
            f'<span class="tag tag-cat">{_h(cat_display)}</span>'
            f'{sub_label_tag}'
            f'<span class="tag tag-impl">✓ Implemented</span>'
            f'</div>'
            f'</div>'
            f'<div class="spotlight-right">'
            f'<div class="spotlight-repo">{_h(issue.get("repo", ""))}</div>'
            f'<div class="spotlight-pr">{pr_ref}</div>'
            f'</div>'
            f'</div>'
        )

    footer = ""
    if hidden:
        sec_count   = sum(1 for i in hidden if i.get("sub_label") == "Security")
        cor_count   = sum(1 for i in hidden if i.get("sub_label") == "Correctness")
        other_count = sum(1 for i in hidden if i.get("sub_label") not in ("Security", "Correctness"))
        parts = []
        if sec_count:
            parts.append(f"{sec_count} {_h('Security')}")
        if cor_count:
            parts.append(f"{cor_count} {_h('Correctness')}")
        if other_count:
            parts.append(f"{other_count} {_h('Other')}")
        breakdown = " &middot; ".join(parts)
        footer = (
            f'<p class="spotlight-more">'
            f'+ {len(hidden)} more &mdash; {breakdown} &mdash; all implemented before merge'
            f'</p>'
        )

    count = len(agg.spotlight_issues)
    plural = "s" if count != 1 else ""
    preview_note = f" Top {SPOTLIGHT_LIMIT} shown below." if hidden else ""
    return (
        f'<section>'
        f'<h2>High-Impact Issues Caught &amp; Resolved</h2>'
        f'<p style="font-size:13px;color:#555;margin-bottom:14px">'
        f'<strong>{count}</strong> Action Required issue{plural} '
        f'flagged as Security or Correctness &mdash; all implemented before merge.{preview_note}</p>'
        f'{cards}'
        f'{footer}'
        f'</section>'
    )


def _table(headers: list, rows_html: str) -> str:
    ths = "".join(f"<th>{h}</th>" for h in headers)
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{rows_html}</tbody></table>"


def _section_adoption(agg: ReportData) -> str:
    dev_cards = (
        _stat_card(
            f"of {agg.developers_total} developers participated",
            str(agg.developers_with_qodo),
        ) +
        _stat_card("developers implemented suggestions", str(agg.developers_engaged))
    )

    repo_rows = "".join(
        f"<tr><td>{_h(r['repo'])}</td><td>{r['prs']}</td><td>{r['suggestions']}</td>"
        f"<td>{_rate_str(r['implemented'], r['suggestions'])}</td></tr>"
        for r in agg.by_repo
    )
    dev_rows = "".join(
        f"<tr><td>{_h(r['developer'])}</td><td>{r['prs']}</td><td>{r['suggestions']}</td>"
        f"<td>{_rate_str(r['implemented'], r['suggestions'])}</td></tr>"
        for r in agg.by_developer
    )
    repo_table = _table(["Repository", "Merged PRs", "Issues", "Impl. Rate"], repo_rows)
    dev_table  = _table(["Developer", "Merged PRs", "Issues", "Impl. Rate"], dev_rows)
    return (
        f'<section><h2>Adoption</h2>'
        f'<div class="stat-grid" style="margin-bottom:20px">{dev_cards}</div>'
        f'<div class="two-col">'
        f'<div><p class="subsection">By Repository</p>{repo_table}</div>'
        f'<div><p class="subsection">By Developer (top 10)</p>{dev_table}</div>'
        f'</div></section>'
    )


def _section_severity(agg: ReportData) -> str:
    cards = (
        _impact_card("Action Required", "red",
                     agg.action_required_suggested, agg.action_required_implemented) +
        _impact_card("Review Recommended", "orange",
                     agg.review_recommended_suggested, agg.review_recommended_implemented)
    )
    return f'<section><h2>Impact by Severity</h2><div class="impact-grid">{cards}</div></section>'


def _section_categories(agg: ReportData) -> str:
    cards = (
        _impact_card("Bugs", "red", agg.bugs_suggested, agg.bugs_implemented) +
        _impact_card("Rule Violations", "orange",
                     agg.rule_violations_suggested, agg.rule_violations_implemented) +
        _impact_card("Requirement Gaps", "purple",
                     agg.requirement_gaps_suggested, agg.requirement_gaps_implemented)
    )
    return f'<section><h2>Impact by Category</h2><div class="impact-grid">{cards}</div></section>'


def _section_top_prs(agg: ReportData) -> str:
    pr_rows = ""
    for r in agg.top_prs:
        url = r.get("PR URL", "")
        safe_url = url if url.startswith(("https://", "http://")) else ""
        pr_ref = f'<a href="{_h(safe_url)}">#{r["PR #"]}</a>' if safe_url else f'#{r["PR #"]}'
        pr_rows += (
            f'<tr><td>{_h(r["Repo Name"])}</td><td>{pr_ref}</td>'
            f'<td>{_h(r["PR Creator"])}</td>'
            f'<td>{r["Total Suggestions"]}</td><td>{r["Total Implemented"]}</td>'
            f'<td>{r.get("Implementation Rate (%)", "—")}</td></tr>'
        )
    table = _table(["Repo", "PR", "Creator", "Issues", "Implemented", "Rate"], pr_rows)
    return f'<section><h2>Top 5 Merged PRs by Issues Found</h2>{table}</section>'


def _section_top_prs_by_implemented(agg: ReportData) -> str:
    pr_rows = ""
    for r in agg.top_prs_by_implemented:
        url = r.get("PR URL", "")
        safe_url = url if url.startswith(("https://", "http://")) else ""
        pr_ref = f'<a href="{_h(safe_url)}">#{r["PR #"]}</a>' if safe_url else f'#{r["PR #"]}'
        pr_rows += (
            f'<tr><td>{_h(r["Repo Name"])}</td><td>{pr_ref}</td>'
            f'<td>{_h(r["PR Creator"])}</td>'
            f'<td>{r["Total Suggestions"]}</td><td>{r["Total Implemented"]}</td>'
            f'<td>{r.get("Implementation Rate (%)", "—")}</td></tr>'
        )
    table = _table(["Repo", "PR", "Creator", "Issues", "Implemented", "Rate"], pr_rows)
    return f'<section><h2>Top 5 Merged PRs by Implemented Suggestions</h2>{table}</section>'


def generate_html(
    rows: list,
    org: str,
    since: "date",
    until: "date",
    logo_path: Optional[str] = "logo.svg",
) -> str:
    agg = aggregate(rows)
    logo_tag = _embed_logo(logo_path)
    since_fmt = since.strftime("%b %d, %Y")
    until_fmt = until.strftime("%b %d, %Y")
    generated = date.today().strftime("%b %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Qodo Impact Report &mdash; {_h(org)}</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="page">
  <header class="report-header">
    {logo_tag}
    <div>
      <h1>Qodo Code Review &mdash; Impact Report</h1>
      <p class="subtitle">{_h(org)} &middot; {since_fmt} &ndash; {until_fmt} &middot; Generated {generated}</p>
    </div>
  </header>
  {_section_exec_summary(agg)}
  {_section_velocity(agg)}
  {_section_adoption(agg)}
  {_section_severity(agg)}
  {_section_categories(agg)}
  {_section_top_prs(agg)}
  {_section_top_prs_by_implemented(agg)}
  {_section_bug_spotlight(agg)}
</div>
</body>
</html>"""
