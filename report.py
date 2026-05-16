"""HTML report generator for Qodo PR metrics — v2 (redesigned).

Drop-in replacement for ``report.py``. Public API is unchanged:

    aggregate(rows, ...) -> ReportData
    generate_html(rows, org, since, until, logo_path=...) -> str

What's new compared to v1:
  - Opens with a narrative headline + 4-cell KPI strip
  - Promotes the high-impact security / correctness spotlight to the top of
    the body
  - Renders repo and developer breakdowns as horizontal bar charts
  - Shows velocity as a single positional scale (Qodo vs first human)
  - Uses the Qodo dark-mode brand palette (canvas #171518, accent #7968FA)

External runtime dependencies: NONE. The output ``<head>`` references Google
Fonts (Inter + IBM Plex Mono) for visual fidelity but falls back to system
fonts if the viewer is offline.

NOTE: The HTML strings emitted here are a substantial visual rewrite. The
existing ``tests/test_report.py`` asserts against the v1 markup
(section titles, CSS class names, copy fragments) and WILL need to be
updated — see the bundled ``tests/test_report_v2.py`` patch alongside this
file for the corresponding fixture changes.
"""

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


# ─── data layer (unchanged from v1) ────────────────────────────────────

SPOTLIGHT_LIMIT = 10
SPOTLIGHT_PRIORITY_KEYWORDS = [
    "injection", "bypass", "unauthenticated", "hardcoded", "exfiltrat",
    "csrf", "silent", "crash", "discard", "corrupt",
]


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
    org_prs_total: Optional[int]
    org_pr_authors_total: Optional[int]
    repos_with_qodo: int
    ai_authored_count: int
    ai_authored_impl_rate_pct: float
    avg_reviewer_count: float
    pct_had_request_changes: float
    ci_pass_rate_pct: Optional[float]
    speed_to_fix_median_min: Optional[float]
    weekly_coverage: list
    revert_count: Optional[int]
    hotfix_count: Optional[int]


def aggregate(rows: list, org_prs_total: Optional[int] = None,
              org_pr_authors_total: Optional[int] = None,
              weekly_coverage: Optional[list] = None,
              revert_count: Optional[int] = None,
              hotfix_count: Optional[int] = None) -> ReportData:
    prs_with_qodo = sum(1 for r in rows if r.get("Has Qodo Review", True))

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
        if not r.get("Has Qodo Review", True):
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
        [r for r in rows if r.get("Has Qodo Review", True)],
        key=lambda r: r.get("Total Suggestions", 0), reverse=True,
    )[:5]
    top_prs_by_implemented = sorted(
        [r for r in rows if r.get("Has Qodo Review", True)],
        key=lambda r: r.get("Total Implemented", 0), reverse=True,
    )[:5]

    qodo_times = [r["Time to First Qodo Comment (min)"] for r in rows
                  if r.get("Time to First Qodo Comment (min)") not in ("", None)]
    human_times = [r["Time to First Human Comment (min)"] for r in rows
                   if r.get("Time to First Human Comment (min)") not in ("", None)]
    no_human_qodo = sum(1 for r in rows
                       if r.get("Has Qodo Review", True) and not r.get("Has Human Comment"))
    velocity_qodo_median = _median(qodo_times)
    velocity_human_median = _median(human_times)
    pct_no_human = _rate(no_human_qodo, prs_with_qodo) if prs_with_qodo else 0.0

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

    # AI-authored stats
    ai_rows = [r for r in rows if r.get("Is AI Authored")]
    ai_authored_count = len(ai_rows)
    ai_sug = sum(r.get("Total Suggestions", 0) for r in ai_rows)
    ai_imp = sum(r.get("Total Implemented", 0) for r in ai_rows)

    # Reviewer stats
    rev_counts = [r["Reviewer Count"] for r in rows
                  if isinstance(r.get("Reviewer Count"), int)]
    avg_reviewer_count = round(sum(rev_counts) / len(rev_counts), 1) if rev_counts else 0.0
    had_changes_count = sum(1 for r in rows if r.get("Had Request Changes"))
    pct_had_changes = _rate(had_changes_count, len(rows)) if rows else 0.0

    # CI pass rate (only count rows with a non-empty CI Status)
    ci_rows = [r for r in rows if r.get("CI Status") not in ("", None)]
    ci_pass = sum(1 for r in ci_rows if r.get("CI Status") == "SUCCESS")
    ci_pass_rate_pct = _rate(ci_pass, len(ci_rows)) if ci_rows else None

    # Speed to fix median
    fix_times = [r["Speed to First Fix (min)"] for r in rows
                 if r.get("Speed to First Fix (min)") not in ("", None)]
    speed_to_fix_median = _median(fix_times)

    repos_with_qodo = len({r["Repo Name"] for r in rows if r.get("Repo Name")})
    all_devs = {r["PR Creator"] for r in rows if r.get("PR Creator")}
    devs_with_qodo = {r["PR Creator"] for r in rows
                      if r.get("Has Qodo Review", True) and r.get("PR Creator")}
    devs_engaged = {r["PR Creator"] for r in rows
                    if r.get("Total Implemented", 0) > 0 and r.get("PR Creator")}

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
        org_prs_total=org_prs_total,
        org_pr_authors_total=org_pr_authors_total,
        repos_with_qodo=repos_with_qodo,
        ai_authored_count=ai_authored_count,
        ai_authored_impl_rate_pct=_rate(ai_imp, ai_sug),
        avg_reviewer_count=avg_reviewer_count,
        pct_had_request_changes=pct_had_changes,
        ci_pass_rate_pct=ci_pass_rate_pct,
        speed_to_fix_median_min=speed_to_fix_median,
        weekly_coverage=weekly_coverage or [],
        revert_count=revert_count,
        hotfix_count=hotfix_count,
    )


# ─── general helpers ───────────────────────────────────────────────────

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


def _fmt_int(n: int) -> str:
    """1574 -> '1,574'"""
    return f"{n:,}"


def _initials(name: str) -> str:
    """'sagi-medina' -> 'SM'. Falls back to first two letters."""
    if not name:
        return "—"
    parts = [p for p in re.split(r"[-_\s.]+", name.strip()) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


def _rate_class(rate_pct: float) -> str:
    """Bar color tier from implementation rate."""
    if rate_pct is None:
        return ""
    if rate_pct < 40:
        return "low"
    if rate_pct < 70:
        return "med"
    return "hi"


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
        text = re.sub(r"(<svg\b)", r'\1 height="36"', text, count=1)
        return text
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:{mime};base64,{b64}" alt="Qodo" height="36">'


# ─── spotlight selection (unchanged from v1) ───────────────────────────

def _keyword_score(issue: dict) -> int:
    title = issue.get("title", "").lower()
    return 0 if any(kw in title for kw in SPOTLIGHT_PRIORITY_KEYWORDS) else 1


def _select_spotlight(issues: list, limit: int) -> tuple:
    half = limit // 2
    security    = sorted([i for i in issues if i.get("sub_label") == "Security"],    key=_keyword_score)
    correctness = sorted([i for i in issues if i.get("sub_label") == "Correctness"], key=_keyword_score)
    other       = sorted([i for i in issues if i.get("sub_label") not in ("Security", "Correctness")], key=_keyword_score)

    sec_take = min(half, len(security))
    cor_take = min(half, len(correctness))
    remaining = limit - sec_take - cor_take

    sec_avail = len(security) - sec_take
    cor_avail = len(correctness) - cor_take
    if sec_avail >= cor_avail:
        extra = min(remaining, sec_avail); sec_take += extra; remaining -= extra
        extra = min(remaining, cor_avail); cor_take += extra; remaining -= extra
    else:
        extra = min(remaining, cor_avail); cor_take += extra; remaining -= extra
        extra = min(remaining, sec_avail); sec_take += extra; remaining -= extra

    other_take = min(remaining, len(other))
    displayed = security[:sec_take] + correctness[:cor_take] + other[:other_take]
    hidden    = security[sec_take:] + correctness[cor_take:] + other[other_take:]
    return displayed, hidden


_CATEGORY_DISPLAY = {
    "bug": "Bug",
    "rule_violation": "Rule violation",
    "requirement_gap": "Requirement gap",
    "unknown": "Finding",
}


# ─── styles ────────────────────────────────────────────────────────────

_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root{
  --pm-100:#F4F4F4;--pm-200:#DFDFDF;--pm-400:#6E6E6E;--pm-500:#3D3D3D;
  --pm-700:#1C1C1C;--pm-750:#171518;--pm-800:#141414;
  --p-200:#A8A1FD;--p-300:#9084FC;--p-400:#7968FA;--p-500:#634FD1;
  --p-tint-10:rgba(174,161,241,0.10);--p-tint-20:rgba(174,161,241,0.20);
  --g-mint:#06E4AE;--success:#57E3C0;--danger:#E5484D;--warning:#F5B544;
  --bg-canvas:var(--pm-750);--bg-surface:var(--pm-700);--bg-inset:var(--pm-800);
  --border-default:#2C2C2C;--border-subtle:rgba(255,255,255,0.06);
  --fg-default:var(--pm-200);--fg-strong:var(--pm-100);--fg-muted:#A09CB6;--fg-subtle:var(--pm-400);
  --brand-gradient:linear-gradient(135deg,#684BFE 0%,#06E4AE 100%);
  --radius-sm:6px;--radius-md:8px;--radius-lg:12px;--radius-xl:16px;--radius-full:999px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg-canvas);color:var(--fg-default);
  font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;}
.mono{font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace}
a{color:var(--p-300);text-decoration:none}
a:hover{color:var(--p-200);text-decoration:underline}

.shell{max-width:1080px;margin:0 auto;padding:32px 24px 96px}
.report{background:var(--bg-canvas);border:1px solid var(--border-default);
  border-radius:var(--radius-xl);overflow:hidden}

.r-header{display:flex;align-items:center;justify-content:space-between;
  padding:28px 36px 24px;border-bottom:1px solid var(--border-default)}
.r-brand{display:flex;align-items:center;gap:14px}
.r-brand svg{height:36px;width:auto}
.r-meta{text-align:right;font-size:12px;color:var(--fg-muted);line-height:1.6;
  white-space:nowrap}
.r-meta .r-meta-title{color:var(--fg-strong);font-weight:500;font-size:13px;margin-bottom:2px}
.r-meta .mono{color:var(--p-300);font-size:12px}

.hero{padding:56px 36px 48px;
  background:radial-gradient(80% 60% at 15% 0%,rgba(121,104,250,.10),transparent 70%),
             radial-gradient(60% 50% at 95% 100%,rgba(6,228,174,.05),transparent 70%);
  border-bottom:1px solid var(--border-default)}
.hero-kicker{display:inline-flex;align-items:center;gap:8px;
  font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;
  color:var(--p-300);margin-bottom:20px;padding:6px 12px;border-radius:var(--radius-full);
  background:var(--p-tint-10);border:1px solid var(--p-tint-20)}
.hero-kicker .dot{width:6px;height:6px;border-radius:50%;background:var(--g-mint);box-shadow:0 0 8px var(--g-mint)}
.hero h1{font-size:38px;line-height:1.15;font-weight:600;letter-spacing:-.015em;
  color:var(--fg-strong);max-width:820px;text-wrap:balance;margin-bottom:14px}
.hero h1 .num{background:var(--brand-gradient);-webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;font-weight:700}
.hero-lede{font-size:15px;color:var(--fg-muted);max-width:680px;line-height:1.55;text-wrap:pretty}
.hero-lede b{color:var(--fg-strong);font-weight:600}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--border-default)}
.kpi{padding:24px 28px;border-right:1px solid var(--border-default);
  display:flex;flex-direction:column;gap:6px}
.kpi:last-child{border-right:none}
.kpi-label{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-muted)}
.kpi-value{font-size:36px;font-weight:700;line-height:1;color:var(--fg-strong);letter-spacing:-.02em;margin-top:4px}
.kpi-sub{font-size:12px;color:var(--fg-muted);margin-top:4px}
.kpi-sub b{color:var(--p-200);font-weight:500}

section.r-section{padding:40px 36px;border-bottom:1px solid var(--border-default)}
section.r-section:last-of-type{border-bottom:none}
.r-section-head{margin-bottom:24px}
.r-section-eyebrow{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--p-300);margin-bottom:6px}
.r-section-title{font-size:22px;font-weight:600;letter-spacing:-.01em;color:var(--fg-strong)}
.r-section-deck{font-size:13px;color:var(--fg-muted);margin-top:4px;max-width:580px;line-height:1.55;text-wrap:pretty}

/* spotlight */
.spotlight-summary{display:grid;grid-template-columns:auto 1fr auto;align-items:center;
  gap:24px;padding:22px 24px;margin-bottom:24px;
  background:linear-gradient(135deg,rgba(229,72,77,.06) 0%,rgba(121,104,250,.06) 100%);
  border:1px solid var(--border-default);border-radius:var(--radius-lg)}
.spot-big{font-size:48px;font-weight:700;line-height:1;color:var(--fg-strong);letter-spacing:-.02em}
.spot-mid{font-size:14px;color:var(--fg-default);line-height:1.55;text-wrap:pretty}
.spot-mid b{color:var(--fg-strong);font-weight:600}
.spot-split{display:flex;gap:24px;font-size:12px;color:var(--fg-muted)}
.spot-split-cell{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.spot-split-cell b{font-size:22px;font-weight:700;color:var(--fg-strong);line-height:1}
.spot-split-cell.security b{color:var(--warning)}
.spot-split-cell.correctness b{color:var(--p-300)}

.spot-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.spot-card{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:center;
  padding:14px 16px;background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-md);border-left:3px solid var(--danger)}
.spot-card.correctness{border-left-color:var(--p-400)}
.spot-card-title{font-size:14px;font-weight:500;color:var(--fg-strong);margin-bottom:6px;line-height:1.35}
.spot-card-tags{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.spot-card-right{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:4px}
.spot-card-repo{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--fg-muted)}
.spot-card-pr a{font-size:12px;font-weight:500}
.tag{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:var(--radius-sm);
  font-size:11px;font-weight:500;line-height:1.4}
.tag-bug{background:rgba(229,72,77,.10);color:#E55C83}
.tag-security{background:rgba(245,181,68,.12);color:var(--warning)}
.tag-correctness{background:var(--p-tint-10);color:var(--p-200)}
.tag-check{background:rgba(87,227,192,.10);color:var(--success);padding:2px 7px}
.tag-check::before{content:'✓';margin-right:2px;font-weight:700}
.spot-more{margin-top:14px;text-align:center;font-size:12px;color:var(--fg-muted);
  padding:10px;border:1px dashed var(--border-default);border-radius:var(--radius-md)}

/* velocity */
.vel-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:24px}
.vel-scale{background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);padding:28px 28px 24px}
.vel-axis{position:relative;height:80px;margin:24px 0 8px}
.vel-track{position:absolute;left:0;right:0;top:50%;height:2px;
  background:linear-gradient(90deg,var(--p-400) 0%,var(--p-400) var(--qodo-pct),
    var(--pm-500) var(--qodo-pct),var(--pm-500) 100%);
  transform:translateY(-50%);border-radius:999px}
.vel-marker{position:absolute;top:50%;transform:translate(-50%,-50%);
  display:flex;flex-direction:column;align-items:center;gap:10px}
.vel-marker .pt{width:14px;height:14px;border-radius:50%;background:var(--p-400);
  box-shadow:0 0 0 4px var(--bg-surface),0 0 0 5px var(--p-400)}
.vel-marker.human .pt{background:var(--pm-400);box-shadow:0 0 0 4px var(--bg-surface),0 0 0 5px var(--pm-400)}
.vel-label-top,.vel-label-bot{position:absolute;left:50%;transform:translateX(-50%);
  white-space:nowrap;text-align:center}
.vel-label-top{bottom:calc(50% + 18px)}
.vel-label-bot{top:calc(50% + 18px)}
.vel-marker.human .vel-label-bot{left:auto;right:0;transform:none;text-align:right}
.vel-name{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-muted);font-weight:600;margin-bottom:4px}
.vel-name.qodo{color:var(--p-300)}
.vel-time{font-size:28px;font-weight:700;color:var(--fg-strong);line-height:1;letter-spacing:-.01em}
.vel-time.qodo{color:var(--p-200)}
.vel-headline{margin-top:36px;font-size:14px;color:var(--fg-default);line-height:1.6;text-wrap:pretty}
.vel-headline b{color:var(--fg-strong);font-weight:600}
.vel-pill{display:inline-block;padding:3px 10px;border-radius:var(--radius-full);
  background:var(--p-tint-10);color:var(--p-200);font-weight:600;font-size:12px;white-space:nowrap}
.vel-aside{background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);padding:24px 28px;
  display:flex;flex-direction:column;justify-content:space-between;gap:16px}
.vel-stat-big{font-size:64px;line-height:1;font-weight:700;color:var(--fg-strong);letter-spacing:-.03em}
.vel-stat-big .pct{font-size:32px;color:var(--fg-muted);font-weight:600;margin-left:2px}
.vel-stat-label{font-size:13px;color:var(--fg-muted);line-height:1.55;text-wrap:pretty}
.vel-stat-label b{color:var(--fg-strong);font-weight:600}
.vel-aside .vel-foot{font-size:12px;color:var(--fg-subtle);padding-top:14px;border-top:1px solid var(--border-default)}

/* adoption */
.coverage-strip{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--border-default);
  border-radius:var(--radius-lg);background:var(--bg-surface);margin-bottom:28px}
.cov-cell{padding:18px 22px;border-right:1px solid var(--border-default)}
.cov-cell:last-child{border-right:none}
.cov-cell-val{font-size:24px;font-weight:700;color:var(--fg-strong);line-height:1;letter-spacing:-.01em}
.cov-cell-val .of{color:var(--fg-muted);font-weight:500;font-size:18px}
.cov-cell-label{font-size:11px;color:var(--fg-muted);text-transform:uppercase;letter-spacing:.05em;margin-top:8px;font-weight:500}
.cov-cell-bar{margin-top:10px;height:4px;background:var(--bg-inset);border-radius:999px;overflow:hidden}
.cov-cell-bar > i{display:block;height:100%;background:var(--p-400);border-radius:999px}

.bar-grid{display:grid;grid-template-columns:1fr 1fr;gap:32px}
.bar-head{display:flex;justify-content:space-between;align-items:baseline;
  margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border-default)}
.bar-head h4{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--fg-muted);margin:0}
.bar-head .axis{font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-subtle)}

.bar-row{display:grid;grid-template-columns:1fr auto;align-items:center;gap:6px 14px;
  padding:10px 0;border-bottom:1px solid var(--border-subtle)}
.bar-row:last-of-type{border-bottom:none}
.bar-row-name{display:flex;flex-direction:column;gap:2px;font-size:13px;color:var(--fg-default);min-width:0}
.bar-row-name .nm{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--fg-default);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.bar-row-name .pr-ct{font-size:11px;color:var(--fg-subtle);white-space:nowrap}
.bar-row-bar{position:relative;height:8px;background:var(--bg-inset);border-radius:999px;overflow:hidden}
.bar-row-bar > i{display:block;height:100%;background:var(--p-400);border-radius:999px}
.bar-row-bar.low > i{background:var(--pm-500)}
.bar-row-bar.med > i{background:var(--p-500)}
.bar-row-bar.hi > i{background:var(--success)}
.bar-row-val{font-family:'IBM Plex Mono',monospace;font-size:12px;text-align:right;color:var(--fg-default);font-weight:500}
.bar-row-val.low{color:var(--fg-subtle)}
.bar-row-val.hi{color:var(--success)}
.bar-row-name.dev{display:grid;grid-template-columns:24px 1fr;align-items:center;gap:4px 10px}
.bar-row-name.dev .nm{grid-column:2}
.bar-row-name.dev .pr-ct{grid-column:2}
.bar-row-name.dev .dev-avatar{grid-row:1/3;align-self:center}
.dev-avatar{width:22px;height:22px;border-radius:50%;background:var(--brand-gradient);flex-shrink:0;
  font-size:10px;font-weight:600;color:#fff;display:flex;align-items:center;justify-content:center;line-height:1}
.bar-tail{font-size:12px;color:var(--fg-subtle);margin-top:12px;text-align:center}

/* breakdown (severity + category) */
.breakdown{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.bd-block{background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);padding:22px 24px}
.bd-block h4{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--fg-muted);margin-bottom:16px}
.bd-item{margin-bottom:18px}
.bd-item:last-child{margin-bottom:0}
.bd-item-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.bd-item-name{font-size:14px;font-weight:600;color:var(--fg-strong);display:flex;align-items:center;gap:8px}
.bd-item-name .swatch{width:8px;height:8px;border-radius:2px;background:var(--p-400)}
.bd-item-rate{font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:500;color:var(--fg-strong)}
.bd-item-rate.muted{color:var(--fg-muted)}
.bd-item-bar{position:relative;height:22px;background:var(--bg-inset);border-radius:var(--radius-sm);overflow:hidden}
.bd-item-bar .filled{position:absolute;top:0;left:0;bottom:0;background:var(--p-400);
  display:flex;align-items:center;padding-left:10px;
  font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:#fff}
.bd-item-bar .total{position:absolute;top:0;right:0;bottom:0;
  display:flex;align-items:center;padding-right:10px;
  font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--fg-muted)}
.bd-item-foot{font-size:12px;color:var(--fg-muted);margin-top:6px}
.bd-item-foot b{color:var(--fg-default);font-weight:500}
.bd-item.severity-action .swatch{background:var(--danger)}
.bd-item.severity-action .filled{background:var(--danger)}
.bd-item.severity-review .swatch{background:var(--warning)}
.bd-item.severity-review .filled{background:var(--warning);color:#1c1c1c}
.bd-item.cat-bugs .swatch{background:var(--danger)}
.bd-item.cat-bugs .filled{background:var(--danger)}
.bd-item.cat-rule .swatch{background:var(--warning)}
.bd-item.cat-rule .filled{background:var(--warning);color:#1c1c1c}
.bd-item.cat-req .swatch{background:var(--p-400)}
.bd-item.cat-req .filled{background:var(--p-400)}

/* top PRs */
table.top-prs{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
table.top-prs th{text-align:left;padding:10px 14px;font-size:11px;font-weight:600;
  text-transform:uppercase;letter-spacing:.05em;color:var(--fg-muted);
  border-bottom:1px solid var(--border-default)}
table.top-prs th.num,table.top-prs td.num{text-align:right;font-family:'IBM Plex Mono',monospace}
table.top-prs td{padding:14px;border-bottom:1px solid var(--border-subtle);color:var(--fg-default)}
table.top-prs tr:last-child td{border-bottom:none}
table.top-prs .repo{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--fg-default)}
table.top-prs .pr-link a{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--p-300)}
table.top-prs .creator{display:flex;align-items:center;gap:8px}
table.top-prs .mini-bar{position:relative;width:80px;height:6px;background:var(--bg-inset);
  border-radius:999px;overflow:hidden;display:inline-block;margin-right:8px;vertical-align:middle}
table.top-prs .mini-bar > i{display:block;height:100%;background:var(--success);border-radius:999px}

.vel-fix-row { display:flex; align-items:baseline; gap:12px; margin-top:16px;
               padding-top:16px; border-top:1px solid rgba(255,255,255,.08); }
.vel-fix-label { font-size:.8rem; color:#9992b4; min-width:140px; }
.vel-fix-value { font-size:1.4rem; font-weight:600; color:#e2dffe; font-family:'IBM Plex Mono',monospace; }
.vel-fix-sub { font-size:.75rem; color:#6b6585; }

.ai-stat { display:flex; align-items:center; gap:16px; margin-top:16px;
           padding:12px 16px; border-radius:8px; background:rgba(121,104,250,.08);
           border:1px solid rgba(121,104,250,.2); }
.ai-count { font-size:1.6rem; font-weight:700; color:#7968FA; font-family:'IBM Plex Mono',monospace; }
.ai-label { font-size:.85rem; color:#9992b4; flex:1; }
.ai-rate  { font-size:.85rem; color:#b8b2d4; }

.quality-signal { margin-bottom:40px; }
.quality-grid { display:flex; gap:24px; }
.quality-cell { flex:1; padding:20px; border-radius:10px;
                background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08); }
.quality-value { font-size:2rem; font-weight:700; color:#e2dffe;
                 font-family:'IBM Plex Mono',monospace; }
.quality-label { font-size:.8rem; color:#9992b4; margin-top:4px; }

.r-footer{padding:24px 36px 28px;text-align:center;font-size:12px;color:var(--fg-subtle);
  background:var(--bg-inset);border-top:1px solid var(--border-default)}
.r-footer .mono{color:var(--fg-muted)}

@media print{
  body{background:#fff;color:#1c1c1c}
  .shell{max-width:100%;padding:0}
  .report{border:none;border-radius:0;background:#fff}
  section.r-section{break-inside:avoid;background:#fff}
}
"""


# ─── section emitters ──────────────────────────────────────────────────

def _section_header(org: str, since: date, until: date, logo_tag: str) -> str:
    since_fmt = since.strftime("%b %d, %Y")
    until_fmt = until.strftime("%b %d, %Y")
    span_days = (until - since).days
    return (
        f'<header class="r-header">'
        f'<div class="r-brand">{logo_tag}</div>'
        f'<div class="r-meta">'
        f'<div class="r-meta-title">Code review impact report</div>'
        f'<div><span class="mono">{_h(org)}</span> &middot; {since_fmt} &ndash; {until_fmt} &middot; {span_days} days</div>'
        f'<div style="color:var(--fg-subtle);font-size:11px">Generated {date.today().strftime("%b %d, %Y")}</div>'
        f'</div>'
        f'</header>'
    )


def _section_hero(agg: ReportData, span_days: int) -> str:
    kicker_text = f"Last {span_days} days" if span_days > 0 else "This period"

    headline = (
        f'Qodo caught <span class="num">{_fmt_int(agg.total_suggestions)}</span> findings across '
        f'<span class="num">{_fmt_int(agg.prs_with_qodo)}</span> merged PRs.<br>'
        f'Your team fixed <span class="num">{_fmt_int(agg.total_implemented)}</span> of them before merge.'
    )

    lede_parts = []
    n_spot = len(agg.spotlight_issues)
    if n_spot > 0:
        lede_parts.append(
            f'Including <b>{_fmt_int(n_spot)} high-impact security and correctness findings</b> '
            f'that were implemented before merge.'
        )

    mult = _speed_multiplier(agg)
    if mult is not None:
        lede_parts.append(
            f'Qodo also delivered first feedback on these PRs <b>{mult}&times; faster</b> than the first human reviewer.'
        )

    lede_html = (
        f'<p class="hero-lede">{" ".join(lede_parts)}</p>'
        if lede_parts else ""
    )

    return (
        f'<section class="hero">'
        f'<div class="hero-kicker"><span class="dot"></span>{_h(kicker_text)}</div>'
        f'<h1>{headline}</h1>'
        f'{lede_html}'
        f'</section>'
    )


def _section_kpis(agg: ReportData) -> str:
    repo_sub = (
        f'across <b>{agg.repos_with_qodo} repositories</b>'
        if agg.repos_with_qodo else
        f'<b>{agg.developers_with_qodo} developers</b>'
    )

    rate_sub = (
        f'<b>{agg.overall_impl_rate_pct:.1f}%</b> implementation rate'
        if agg.total_suggestions > 0 else ""
    )

    spot_count = len(agg.spotlight_issues)
    spot_sub = "security &amp; correctness &middot; <b>implemented before merge</b>" if spot_count else ""

    cells = [
        ("PRs reviewed",      _fmt_int(agg.prs_with_qodo),         f"across <b>{agg.repos_with_qodo} repositories</b>"),
        ("Findings caught",   _fmt_int(agg.total_suggestions),     f"<b>{agg.developers_with_qodo} developers</b> involved"),
        ("Fixed before merge", _fmt_int(agg.total_implemented),    rate_sub),
        ("High-impact fixes",  _fmt_int(spot_count),               spot_sub),
    ]

    html = ['<div class="kpis">']
    for label, value, sub in cells:
        html.append(
            f'<div class="kpi">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-sub">{sub}</div>'
            f'</div>'
        )
    html.append('</div>')
    return "".join(html)


def _speed_multiplier(agg: ReportData) -> Optional[int]:
    """Integer multiplier if Qodo is faster; None otherwise."""
    q, h = agg.velocity_qodo_median_min, agg.velocity_human_median_min
    if q is None or h is None or q <= 0 or h <= q:
        return None
    return int(round(h / q))


def _section_spotlight(agg: ReportData) -> str:
    if not agg.spotlight_issues:
        return ""

    displayed, hidden = _select_spotlight(agg.spotlight_issues, SPOTLIGHT_LIMIT)
    sec_total = sum(1 for i in agg.spotlight_issues if i.get("sub_label") == "Security")
    cor_total = sum(1 for i in agg.spotlight_issues if i.get("sub_label") == "Correctness")
    n = len(agg.spotlight_issues)
    spot_repos = len({i.get("repo") for i in agg.spotlight_issues if i.get("repo")})

    cards = []
    for issue in displayed:
        url = issue.get("pr_url", "")
        safe_url = url if url.startswith(("https://", "http://")) else ""
        pr_num_safe = _h(str(issue.get("pr_num", "")))
        pr_ref = (
            f'<a href="{_h(safe_url)}">PR #{pr_num_safe} &#8599;</a>'
            if safe_url else f'PR #{pr_num_safe}'
        )

        sub_label = issue.get("sub_label", "")
        is_correctness = sub_label == "Correctness"
        cat = _CATEGORY_DISPLAY.get(issue.get("category", ""), "Finding")

        sub_tag_class = {"Security": "tag-security", "Correctness": "tag-correctness"}.get(sub_label, "tag-correctness")
        border_class = " correctness" if is_correctness else ""

        sub_tag = (
            f'<span class="tag {sub_tag_class}">{_h(sub_label)}</span>'
            if sub_label else ""
        )

        cards.append(
            f'<div class="spot-card{border_class}">'
            f'<div>'
            f'<div class="spot-card-title">{_h(issue.get("title", ""))}</div>'
            f'<div class="spot-card-tags">'
            f'<span class="tag tag-bug">{_h(cat)}</span>'
            f'{sub_tag}'
            f'<span class="tag tag-check">Fixed</span>'
            f'</div>'
            f'</div>'
            f'<div class="spot-card-right">'
            f'<div class="spot-card-repo">{_h(issue.get("repo", ""))}</div>'
            f'<div class="spot-card-pr">{pr_ref}</div>'
            f'</div>'
            f'</div>'
        )

    footer = ""
    if hidden:
        footer = f'<div class="spot-more">+ {len(hidden)} more high-impact fixes implemented before merge</div>'

    repo_str = f"{spot_repos} {'repositories' if spot_repos != 1 else 'repository'}"

    summary = (
        f'<div class="spotlight-summary">'
        f'<div class="spot-big">{n}</div>'
        f'<div class="spot-mid">'
        f'<b>Security &amp; correctness findings implemented before merge</b>'
        f'<div style="color:var(--fg-muted);margin-top:4px;font-size:13px">'
        f'Action-required findings flagged as Security or Correctness that the team caught and resolved. '
        f'Across {repo_str}.'
        f'</div>'
        f'</div>'
        f'<div class="spot-split">'
        f'<div class="spot-split-cell security"><b>{sec_total}</b><span>Security</span></div>'
        f'<div class="spot-split-cell correctness"><b>{cor_total}</b><span>Correctness</span></div>'
        f'</div>'
        f'</div>'
    )

    return (
        f'<section class="r-section">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Spotlight</div>'
        f'<div class="r-section-title">High-impact findings caught &amp; fixed</div>'
        f'<div class="r-section-deck">Action-required findings flagged as Security or Correctness — '
        f'the findings most likely to have caused an incident if they reached production.</div>'
        f'</div></div>'
        f'{summary}'
        f'<div class="spot-grid">{"".join(cards)}</div>'
        f'{footer}'
        f'</section>'
    )


def _section_velocity(agg: ReportData) -> str:
    if agg.velocity_qodo_median_min is None and agg.velocity_human_median_min is None:
        return ""

    q = agg.velocity_qodo_median_min
    h = agg.velocity_human_median_min
    q_time = _format_duration(q)
    h_time = _format_duration(h)
    mult = _speed_multiplier(agg)

    # Position Qodo marker along the scale based on its share of human time.
    # If human time is unknown or Qodo is slower, fall back to a static position.
    if q is not None and h is not None and h > 0 and q < h:
        qodo_pct = max(8, min(50, int(round(q / h * 100))))
    else:
        qodo_pct = 50

    scale_html = (
        f'<div class="vel-scale">'
        f'<div class="vel-axis" style="--qodo-pct:{qodo_pct}%">'
        f'<div class="vel-track"></div>'
        f'<div class="vel-marker qodo" style="left:{qodo_pct}%">'
        f'<div class="vel-label-top">'
        f'<div class="vel-name qodo">Qodo</div>'
        f'<div class="vel-time qodo">{q_time}</div>'
        f'</div>'
        f'<div class="pt"></div>'
        f'</div>'
        f'<div class="vel-marker human" style="left:100%">'
        f'<div class="pt"></div>'
        f'<div class="vel-label-bot">'
        f'<div class="vel-time" style="text-align:right">{h_time}</div>'
        f'<div class="vel-name" style="margin-top:4px;text-align:right">First human reviewer</div>'
        f'</div>'
        f'</div>'
        f'</div>'
    )

    if mult is not None:
        scale_html += (
            f'<div class="vel-headline">'
            f'Qodo posts initial feedback in <b>{q_time}</b> — the first human reviewer takes <b>{h_time}</b>. '
            f'That\'s a <span class="vel-pill">{mult}&times; speed-up</span> on the feedback loop.'
            f'</div>'
        )
    elif q is not None:
        scale_html += (
            f'<div class="vel-headline">'
            f'Qodo posts initial feedback in <b>{q_time}</b> from when a PR opens.'
            f'</div>'
        )

    if agg.speed_to_fix_median_min is not None:
        fix_time = _format_duration(agg.speed_to_fix_median_min)
        scale_html += (
            f'<div class="vel-fix-row">'
            f'<span class="vel-fix-label">Speed to first fix</span>'
            f'<span class="vel-fix-value">{fix_time}</span>'
            f'<span class="vel-fix-sub">median time from Qodo comment to first fix commit</span>'
            f'</div>'
        )

    scale_html += "</div>"

    aside_html = ""
    if agg.pct_no_human_comment > 0:
        aside_html = (
            f'<div class="vel-aside">'
            f'<div>'
            f'<div class="vel-stat-big">{agg.pct_no_human_comment:.0f}<span class="pct">%</span></div>'
            f'<div class="vel-stat-label" style="margin-top:10px">'
            f'of Qodo-reviewed PRs received <b>no human comment</b> before merge — Qodo provided the sole feedback before merge.'
            f'</div>'
            f'</div>'
            f'<div class="vel-foot">Share of Qodo-reviewed PRs where no human reviewer commented before merge.</div>'
            f'</div>'
        )

    layout = (
        f'<div class="vel-grid">{scale_html}{aside_html}</div>'
        if aside_html else scale_html
    )

    return (
        f'<section class="r-section">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Velocity</div>'
        f'<div class="r-section-title">First feedback on a PR</div>'
        f'<div class="r-section-deck">Median time from PR open to the first review comment, compared to the first human reviewer.</div>'
        f'</div></div>'
        f'{layout}'
        f'</section>'
    )


def _bar_row_repo(row: dict) -> str:
    rate = _rate(row["implemented"], row["suggestions"])
    cls = _rate_class(rate)
    rate_label = _rate_str(row["implemented"], row["suggestions"])
    return (
        f'<div class="bar-row">'
        f'<div class="bar-row-name">'
        f'<span class="nm">{_h(row["repo"])}</span>'
        f'<span class="pr-ct">{row["prs"]} PRs &middot; {row["suggestions"]} findings</span>'
        f'</div>'
        f'<div class="bar-row-val {cls}">{rate_label}</div>'
        f'<div style="grid-column:1/-1"><div class="bar-row-bar {cls}"><i style="width:{rate}%"></i></div></div>'
        f'</div>'
    )


def _bar_row_dev(row: dict) -> str:
    rate = _rate(row["implemented"], row["suggestions"])
    cls = _rate_class(rate)
    rate_label = _rate_str(row["implemented"], row["suggestions"])
    return (
        f'<div class="bar-row">'
        f'<div class="bar-row-name dev">'
        f'<span class="dev-avatar">{_h(_initials(row["developer"]))}</span>'
        f'<span class="nm">{_h(row["developer"])}</span>'
        f'<span class="pr-ct">{row["prs"]} PRs</span>'
        f'</div>'
        f'<div class="bar-row-val {cls}">{rate_label}</div>'
        f'<div style="grid-column:1/-1"><div class="bar-row-bar {cls}"><i style="width:{rate}%"></i></div></div>'
        f'</div>'
    )


def _section_adoption(agg: ReportData) -> str:
    top_repos = agg.by_repo[:10]
    remainder_repos = agg.by_repo[10:]
    rem_repo_prs = sum(r["prs"] for r in remainder_repos)
    rem_repo_findings = sum(r["suggestions"] for r in remainder_repos)

    repo_rows = "".join(_bar_row_repo(r) for r in top_repos)
    repo_tail = (
        f'<div class="bar-tail">+ {len(remainder_repos)} more repos &middot; '
        f'{rem_repo_prs} PRs &middot; {rem_repo_findings} findings</div>'
        if remainder_repos else ""
    )

    dev_rows = "".join(_bar_row_dev(r) for r in agg.by_developer)
    rem_devs = max(0, agg.developers_with_qodo - len(agg.by_developer))
    dev_tail = (
        f'<div class="bar-tail">+ {rem_devs} more developers</div>'
        if rem_devs else ""
    )

    dev_rate_pct = _rate(agg.developers_engaged, agg.developers_with_qodo) if agg.developers_with_qodo else 0.0

    ai_stat_html = ""
    if agg.ai_authored_count > 0:
        ai_rate_label = f"{agg.ai_authored_impl_rate_pct:.1f}% implementation rate" if agg.ai_authored_count > 0 else ""
        ai_stat_html = (
            f'<div class="ai-stat">'
            f'<span class="ai-count">{agg.ai_authored_count}</span>'
            f'<span class="ai-label">AI-authored PRs reviewed by Qodo this period</span>'
            f'<span class="ai-rate">{ai_rate_label}</span>'
            f'</div>'
        )

    return (
        f'<section class="r-section">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Adoption</div>'
        f'<div class="r-section-title">Coverage across the org</div>'
        f'<div class="r-section-deck">How Qodo\'s reach splits across repositories and developers this period.</div>'
        f'</div></div>'

        f'<div class="coverage-strip">'
        f'<div class="cov-cell">'
        f'<div class="cov-cell-val">{agg.repos_with_qodo}</div>'
        f'<div class="cov-cell-label">Repos with Qodo-reviewed PRs</div>'
        f'<div class="cov-cell-bar"><i style="width:100%"></i></div>'
        f'</div>'
        f'<div class="cov-cell">'
        f'<div class="cov-cell-val">{agg.developers_engaged}<span class="of"> / {agg.developers_with_qodo}</span></div>'
        f'<div class="cov-cell-label">Developers who implemented at least one Qodo fix</div>'
        f'<div class="cov-cell-bar"><i style="width:{dev_rate_pct}%"></i></div>'
        f'</div>'
        f'</div>'
        f'{ai_stat_html}'

        f'<div class="bar-grid">'
        f'<div>'
        f'<div class="bar-head"><h4>By repository &middot; top 10 by volume</h4>'
        f'<span class="axis">% findings implemented &rarr;</span></div>'
        f'{repo_rows}{repo_tail}'
        f'</div>'
        f'<div>'
        f'<div class="bar-head"><h4>By developer &middot; top 10 by volume</h4>'
        f'<span class="axis">% findings implemented &rarr;</span></div>'
        f'{dev_rows}{dev_tail}'
        f'</div>'
        f'</div>'
        f'</section>'
    )


def _bd_item(klass: str, name: str, total: int, implemented: int) -> str:
    rate = _rate(implemented, total)
    rate_label = f"{rate:.1f}%" if total > 0 else "—"
    rate_cls = " muted" if total == 0 else ""
    filled_label = str(implemented) if total > 0 else ""
    foot = (
        f'<b>{_fmt_int(total)} caught, {_fmt_int(implemented)} fixed</b> before merge.'
        if total > 0 else
        f'<b>{_fmt_int(total)} caught, {_fmt_int(implemented)} fixed.</b>'
    )
    foot_color = ' style="color:var(--fg-subtle)"' if total <= 1 else ''
    return (
        f'<div class="bd-item {klass}">'
        f'<div class="bd-item-head">'
        f'<div class="bd-item-name"><span class="swatch"></span>{_h(name)}</div>'
        f'<div class="bd-item-rate{rate_cls}">{rate_label}</div>'
        f'</div>'
        f'<div class="bd-item-bar">'
        f'<div class="filled" style="width:{rate}%">{filled_label}</div>'
        f'<div class="total">/ {_fmt_int(total)}</div>'
        f'</div>'
        f'<div class="bd-item-foot"{foot_color}>{foot}</div>'
        f'</div>'
    )


def _section_breakdown(agg: ReportData) -> str:
    severity = (
        _bd_item("severity-action", "Action required",
                 agg.action_required_suggested, agg.action_required_implemented) +
        _bd_item("severity-review", "Review recommended",
                 agg.review_recommended_suggested, agg.review_recommended_implemented)
    )
    category = (
        _bd_item("cat-bugs", "Bugs",
                 agg.bugs_suggested, agg.bugs_implemented) +
        _bd_item("cat-rule", "Rule violations",
                 agg.rule_violations_suggested, agg.rule_violations_implemented) +
        (
            _bd_item("cat-req", "Requirement gaps",
                     agg.requirement_gaps_suggested, agg.requirement_gaps_implemented)
            if agg.requirement_gaps_suggested > 0 else ""
        )
    )

    return (
        f'<section class="r-section">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Breakdown</div>'
        f'<div class="r-section-title">What Qodo flagged &amp; how it landed</div>'
        f'<div class="r-section-deck">Bars show fixed (filled) vs total caught — the percentage is the implementation rate.</div>'
        f'</div></div>'
        f'<div class="breakdown">'
        f'<div class="bd-block"><h4>By severity</h4>{severity}</div>'
        f'<div class="bd-block"><h4>By category</h4>{category}</div>'
        f'</div>'
        f'</section>'
    )


def _section_quality_signal(agg: ReportData) -> str:
    revert_absent = agg.revert_count is None or agg.revert_count == 0
    hotfix_absent = agg.hotfix_count is None or agg.hotfix_count == 0
    if revert_absent and hotfix_absent:
        return ""
    revert_val = str(agg.revert_count) if agg.revert_count is not None else "—"
    hotfix_val = str(agg.hotfix_count) if agg.hotfix_count else "—"
    return (
        f'<section class="r-section">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Quality Signal</div>'
        f'<div class="r-section-title">Reverts &amp; hotfixes</div>'
        f'<div class="r-section-deck">Indicators of post-merge quality issues in the period.</div>'
        f'</div></div>'
        f'<div class="quality-signal">'
        f'<div class="quality-grid">'
        f'<div class="quality-cell">'
        f'<div class="quality-value">{revert_val}</div>'
        f'<div class="quality-label">Reverts</div>'
        f'</div>'
        f'<div class="quality-cell">'
        f'<div class="quality-value">{hotfix_val}</div>'
        f'<div class="quality-label">Hotfixes</div>'
        f'</div>'
        f'</div>'
        f'</div>'
        f'</section>'
    )


def _section_top_prs(agg: ReportData) -> str:
    rows = []
    for r in agg.top_prs_by_implemented:
        url = r.get("PR URL", "")
        safe_url = url if url.startswith(("https://", "http://")) else ""
        pr_ref = f'<a href="{_h(safe_url)}">#{_h(str(r["PR #"]))}</a>' if safe_url else f'#{_h(str(r["PR #"]))}'
        rate_raw = r.get("Implementation Rate (%)", "")
        try:
            rate_val = float(rate_raw) if rate_raw not in (None, "") else None
        except (TypeError, ValueError):
            rate_val = None
        rate_pct_label = f'{rate_val:.1f}%' if rate_val is not None else '—'
        rate_pct_bar = rate_val if rate_val is not None else 0
        creator = r.get("PR Creator", "")

        rows.append(
            f'<tr>'
            f'<td class="repo">{_h(r["Repo Name"])}</td>'
            f'<td class="pr-link">{pr_ref}</td>'
            f'<td><div class="creator">'
            f'<span class="dev-avatar" style="width:18px;height:18px;font-size:9px">{_h(_initials(creator))}</span>'
            f'{_h(creator)}'
            f'</div></td>'
            f'<td class="num">{r.get("Total Suggestions", 0)}</td>'
            f'<td class="num">{r.get("Total Implemented", 0)}</td>'
            f'<td><span class="mini-bar"><i style="width:{rate_pct_bar}%"></i></span> {rate_pct_label}</td>'
            f'</tr>'
        )

    if not rows:
        return ""

    return (
        f'<section class="r-section">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Top PRs</div>'
        f'<div class="r-section-title">Most fixes implemented in a single PR</div>'
        f'<div class="r-section-deck">PRs with the highest count of implemented Qodo findings.</div>'
        f'</div></div>'
        f'<table class="top-prs">'
        f'<thead><tr>'
        f'<th>Repository</th><th>PR</th><th>Author</th>'
        f'<th class="num">Caught</th><th class="num">Fixed</th>'
        f'<th>Implementation rate</th>'
        f'</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        f'</table>'
        f'</section>'
    )


def _section_footer(org: str, span_days: int) -> str:
    return (
        f'<footer class="r-footer">'
        f'Generated <span class="mono">{date.today().strftime("%b %d, %Y")}</span> &middot; '
        f'<span class="mono">{_h(org)}</span> &middot; '
        f'{span_days}-day lookback'
        f'</footer>'
    )


# ─── public entry point ────────────────────────────────────────────────

def generate_html(
    rows: list,
    org: str,
    since: "date",
    until: "date",
    logo_path: Optional[str] = "logo.svg",
    org_pr_count: Optional[int] = None,
    org_author_count: Optional[int] = None,
    weekly_coverage: Optional[list] = None,
    revert_count: Optional[int] = None,
    hotfix_count: Optional[int] = None,
) -> str:
    agg = aggregate(rows, org_prs_total=org_pr_count, org_pr_authors_total=org_author_count,
                    weekly_coverage=weekly_coverage, revert_count=revert_count,
                    hotfix_count=hotfix_count)
    logo_tag = _embed_logo(logo_path)
    span_days = (until - since).days

    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="en">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<title>Qodo impact report &mdash; {_h(org)}</title>\n'
        f'<style>{_CSS}</style>\n'
        f'</head>\n<body>\n'
        f'<div class="shell"><div class="report">'
        f'{_section_header(org, since, until, logo_tag)}'
        f'{_section_hero(agg, span_days)}'
        f'{_section_kpis(agg)}'
        f'{_section_spotlight(agg)}'
        f'{_section_velocity(agg)}'
        f'{_section_adoption(agg)}'
        f'{_section_breakdown(agg)}'
        f'{_section_quality_signal(agg)}'
        f'{_section_top_prs(agg)}'
        f'{_section_footer(org, span_days)}'
        f'</div></div>\n'
        f'</body>\n</html>'
    )
