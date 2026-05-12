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
.report-header img { height: 48px; flex-shrink: 0; }
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
"""


def _embed_logo(logo_path: Optional[str]) -> str:
    if not logo_path:
        return ""
    p = Path(logo_path)
    if not p.exists():
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b64}" alt="Qodo" height="48">'


def _rate_str(implemented: int, total: int) -> str:
    return f"{100 * implemented / total:.1f}%" if total > 0 else "—"


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
        _stat_card("PRs Reviewed by Qodo", str(agg.prs_with_qodo)),
        _stat_card("Qodo Coverage", f"{agg.qodo_coverage_pct:.1f}%"),
        _stat_card("Total Issues Caught", str(agg.total_suggestions)),
        _stat_card("Issues Implemented", str(agg.total_implemented)),
        _stat_card("Implementation Rate", f"{agg.overall_impl_rate_pct:.1f}%"),
    ])
    return f'<section><h2>Executive Summary</h2><div class="stat-grid">{cards}</div></section>'


def _table(headers: list, rows_html: str) -> str:
    ths = "".join(f"<th>{h}</th>" for h in headers)
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{rows_html}</tbody></table>"


def _section_adoption(agg: ReportData) -> str:
    repo_rows = "".join(
        f"<tr><td>{r['repo']}</td><td>{r['prs']}</td><td>{r['suggestions']}</td>"
        f"<td>{_rate_str(r['implemented'], r['suggestions'])}</td></tr>"
        for r in agg.by_repo
    )
    dev_rows = "".join(
        f"<tr><td>{r['developer']}</td><td>{r['prs']}</td><td>{r['suggestions']}</td>"
        f"<td>{_rate_str(r['implemented'], r['suggestions'])}</td></tr>"
        for r in agg.by_developer
    )
    repo_table = _table(["Repository", "PRs", "Issues", "Impl. Rate"], repo_rows)
    dev_table = _table(["Developer", "PRs", "Issues", "Impl. Rate"], dev_rows)
    return (
        f'<section><h2>Adoption</h2>'
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
        pr_ref = f'<a href="{url}">#{r["PR #"]}</a>' if url else f'#{r["PR #"]}'
        pr_rows += (
            f'<tr><td>{r["Repo Name"]}</td><td>{pr_ref}</td>'
            f'<td>{r["PR Creator"]}</td>'
            f'<td>{r["Total Suggestions"]}</td><td>{r["Total Implemented"]}</td>'
            f'<td>{r.get("Implementation Rate (%)", "—")}</td></tr>'
        )
    table = _table(["Repo", "PR", "Creator", "Issues", "Implemented", "Rate"], pr_rows)
    return f'<section><h2>Top 5 PRs by Issues Found</h2>{table}</section>'


def generate_html(
    rows: list,
    org: str,
    since: "date",
    until: "date",
    logo_path: Optional[str] = "logo.png",
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
  <title>Qodo Impact Report &mdash; {org}</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="page">
  <header class="report-header">
    {logo_tag}
    <div>
      <h1>Qodo Code Review &mdash; Impact Report</h1>
      <p class="subtitle">{org} &middot; {since_fmt} &ndash; {until_fmt} &middot; Generated {generated}</p>
    </div>
  </header>
  {_section_exec_summary(agg)}
  {_section_adoption(agg)}
  {_section_severity(agg)}
  {_section_categories(agg)}
  {_section_top_prs(agg)}
</div>
</body>
</html>"""
