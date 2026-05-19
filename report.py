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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape as _h
from pathlib import Path
from typing import Optional
import base64
import json
import math
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
    findings_by_week: list
    # ── funnel (Prototype 02) ─────────────────────────────────────────
    merged_prs_total: int = 0
    prs_with_findings: int = 0
    prs_with_any_fix: int = 0
    prs_with_spotlight_fix: int = 0
    # ── hours saved (Prototype 03) ──────────────────────────
    total_loc: int = 0                 # Σ Lines Changed across all rows
    total_loc_trimmed: int = 0         # Σ Lines Changed after dropping the top 5% of PRs by size
    trimmed_pr_count: int = 0          # PRs kept after the same trim
    unique_developers: int = 0         # distinct PR Creators (matches the prototype's EVAL_DEVS)
    # Per-trim variants for the live tunable controls in the Hours-Saved section.
    # Each entry: {"prs": int, "loc": int, "label": str, "cutoff": int}.
    loc_trim_variants: dict = field(default_factory=dict)
    # ── velocity distribution (Prototype 04) ──────────────────────────
    # P90s for the same series whose medians already live above.
    qodo_p90_min: Optional[float] = None
    human_p90_min: Optional[float] = None
    # Sample sizes used for the density chart legend.
    qodo_sample_count: int = 0
    human_sample_count: int = 0
    # Absolute count behind ``pct_no_human_comment`` — PRs Qodo reviewed
    # that merged without any human comment.
    sole_reviewer_count: int = 0
    # Smoothed histogram counts over a log10(minutes) axis spanning
    # 1 minute → 3 days. Used to draw the density curves; consumers
    # should rescale against ``max(bins)``.
    qodo_density_bins: list = field(default_factory=list)
    human_density_bins: list = field(default_factory=list)
    # ── adoption matrix (Prototype 05) ─────────────────────────
    # One entry per author who received ≥1 finding. Each is a dict with:
    #   user, prs, totalSug, totalImp, actReqSug, actReqImp, repos
    # The section function adds derived rate / actRate / quad based on the
    # window length (so thresholds can scale with the date range).
    adoption_devs: list = field(default_factory=list)
    # Author headcount: anyone who merged a PR in the window, including
    # those who received zero findings (kept for the page-meta headline).
    adoption_authors_with_findings: int = 0
    adoption_authors_total: int = 0
    # ── Spotlight leaderboard (embedded inside Spotlight section) ─────
    # Top PRs ranked by spotlight (security/correctness) findings caught.
    # Each entry is the raw row dict augmented with `spot_count`.
    spotlight_pr_leaderboard: list = field(default_factory=list)


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

    # Spotlight leaderboard — ranked by spotlight findings caught per PR.
    # Built after spotlight_issues exists; populated below.

    qodo_times = [r["Time to First Qodo Comment (min)"] for r in rows
                  if r.get("Time to First Qodo Comment (min)") not in ("", None)]
    human_times = [r["Time to First Human Comment (min)"] for r in rows
                   if r.get("Time to First Human Comment (min)") not in ("", None)]
    no_human_qodo = sum(1 for r in rows
                       if r.get("Has Qodo Review", True) and not r.get("Has Human Comment"))
    velocity_qodo_median = _median(qodo_times)
    velocity_human_median = _median(human_times)
    pct_no_human = _rate(no_human_qodo, prs_with_qodo) if prs_with_qodo else 0.0

    # ── Velocity distribution (Prototype 04) ─────────────────────────
    # P90s give the right tail; density bins drive the curve shape.
    qodo_p90 = _percentile(qodo_times, 90)
    human_p90 = _percentile(human_times, 90)
    qodo_density = _log_density_bins(qodo_times)
    human_density = _log_density_bins(human_times)

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

    findings_by_week = _aggregate_findings_by_week(rows)

    # Funnel (Prototype 02) — conditional cuts from merged → high-impact fix
    merged_prs_total = org_prs_total if org_prs_total is not None else len(rows)
    prs_with_findings = sum(
        1 for r in rows
        if r.get("Has Qodo Review", True) and r.get("Total Suggestions", 0) > 0
    )
    prs_with_any_fix = sum(
        1 for r in rows
        if r.get("Has Qodo Review", True) and r.get("Total Implemented", 0) > 0
    )
    prs_with_spotlight_fix = len({
        (i.get("repo"), i.get("pr_num"))
        for i in spotlight_issues
        if i.get("pr_num") is not None
    })

    # Spotlight leaderboard — top PRs by spotlight findings caught.
    # Tie-break by Total Implemented (so the team that actually fixed them ranks higher).
    spot_counts: dict = {}
    for i in spotlight_issues:
        key = (i.get("repo"), i.get("pr_num"))
        if key[1] is None:
            continue
        spot_counts[key] = spot_counts.get(key, 0) + 1
    row_by_key = {(r.get("Repo Name"), r.get("PR #")): r for r in rows}
    spotlight_pr_leaderboard = []
    for key, count in spot_counts.items():
        row = row_by_key.get(key)
        if not row:
            continue
        spotlight_pr_leaderboard.append({**row, "spot_count": count})
    spotlight_pr_leaderboard.sort(
        key=lambda r: (r["spot_count"], r.get("Total Implemented", 0)),
        reverse=True,
    )
    spotlight_pr_leaderboard = spotlight_pr_leaderboard[:5]

    repos_with_qodo = len({r["Repo Name"] for r in rows if r.get("Repo Name")})

    # ── Hours-Saved (Prototype 03) inputs ──────────────────────────────────
    # Pull per-PR LOC into a list so we can compute the headline (Σ) and the
    # outlier-trimmed variant the section actually quotes. The trim matches
    # the prototype: keep PRs with Lines Changed ≤ the 95th-percentile cutoff.
    loc_per_pr: list = []
    for r in rows:
        v = r.get("Lines Changed", 0)
        try:
            loc_per_pr.append(int(v) if v not in (None, "") else 0)
        except (TypeError, ValueError):
            loc_per_pr.append(0)
    total_loc = sum(loc_per_pr)
    loc_trim_variants: dict = {}
    if loc_per_pr:
        sorted_loc = sorted(loc_per_pr)
        n = len(sorted_loc)

        def _trim(p: int, label_pct: Optional[int]) -> dict:
            # ceil-based index ensures at least one element is excluded per trim level.
            idx = max(0, math.ceil(n * p / 100) - 1)
            cutoff = sorted_loc[idx]
            kept_locs = [v for v in loc_per_pr if v <= cutoff]
            if label_pct is None:
                label = "All PRs"
            else:
                label = f"Trim top {label_pct}% (PRs > {cutoff:,} LOC)"
            return {"prs": len(kept_locs), "loc": sum(kept_locs),
                    "label": label, "cutoff": cutoff}

        loc_trim_variants = {
            "none": {"prs": len(loc_per_pr), "loc": total_loc,
                     "label": "All PRs", "cutoff": sorted_loc[-1] if sorted_loc else 0},
            "p99":  _trim(99, 1),
            "p95":  _trim(95, 5),
        }
        kept = [v for v in loc_per_pr if v <= sorted_loc[max(0, math.ceil(n * 95 / 100) - 1)]]
    else:
        kept = []
    total_loc_trimmed = sum(kept)
    trimmed_pr_count = len(kept)
    unique_developers = len({r["PR Creator"] for r in rows if r.get("PR Creator")})
    all_devs = {r["PR Creator"] for r in rows if r.get("PR Creator")}
    devs_with_qodo = {r["PR Creator"] for r in rows
                      if r.get("Has Qodo Review", True) and r.get("PR Creator")}
    devs_engaged = {r["PR Creator"] for r in rows
                    if r.get("Total Implemented", 0) > 0 and r.get("PR Creator")}

    # ── Adoption matrix (Prototype 05) ─────────────────────────────────
    # Aggregate per-author totals with Action-Required broken out and a
    # distinct-repo count. Authors with zero findings are tracked separately
    # so the section can quote the full headcount but skip them on the chart.
    matrix_acc: dict = defaultdict(
        lambda: {"prs": 0, "totalSug": 0, "totalImp": 0,
                 "actReqSug": 0, "actReqImp": 0, "repos": set()}
    )
    for r in rows:
        u = r.get("PR Creator")
        if not u:
            continue
        d = matrix_acc[u]
        d["prs"]       += 1
        d["totalSug"]  += r.get("Total Suggestions", 0) or 0
        d["totalImp"]  += r.get("Total Implemented", 0) or 0
        d["actReqSug"] += r.get("Action Required Suggestions", 0) or 0
        d["actReqImp"] += r.get("Action Required Implemented", 0) or 0
        if r.get("Repo Name"):
            d["repos"].add(r["Repo Name"])
    adoption_devs = sorted(
        [
            {"user": u, **{k: v for k, v in d.items() if k != "repos"},
             "repos": len(d["repos"])}
            for u, d in matrix_acc.items()
            if d["totalSug"] > 0
        ],
        key=lambda x: x["totalSug"], reverse=True,
    )
    adoption_authors_total = len(matrix_acc)
    adoption_authors_with_findings = len(adoption_devs)

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
        spotlight_pr_leaderboard=spotlight_pr_leaderboard,
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
        findings_by_week=findings_by_week,
        merged_prs_total=merged_prs_total,
        prs_with_findings=prs_with_findings,
        prs_with_any_fix=prs_with_any_fix,
        prs_with_spotlight_fix=prs_with_spotlight_fix,
        total_loc=total_loc,
        total_loc_trimmed=total_loc_trimmed,
        trimmed_pr_count=trimmed_pr_count,
        unique_developers=unique_developers,
        loc_trim_variants=loc_trim_variants,
        qodo_p90_min=qodo_p90,
        human_p90_min=human_p90,
        qodo_sample_count=len(qodo_times),
        human_sample_count=len(human_times),
        sole_reviewer_count=no_human_qodo,
        qodo_density_bins=qodo_density,
        human_density_bins=human_density,
        adoption_devs=adoption_devs,
        adoption_authors_with_findings=adoption_authors_with_findings,
        adoption_authors_total=adoption_authors_total,
    )


# ─── general helpers ───────────────────────────────────────────────────

def _median(values: list) -> Optional[float]:
    if not values:
        return None
    return statistics.median(float(v) for v in values)


def _percentile(values: list, p: float) -> Optional[float]:
    """Linear-interpolated percentile (NumPy default ``linear`` method).

    Returns ``None`` for an empty input. Accepts mixed ints/floats.
    """
    if not values:
        return None
    s = sorted(float(v) for v in values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


# Velocity density chart (Prototype 04) — axis spans 1 min → 3 days on log10.
# Anything below the floor is pinned to bin 0; anything above to the last bin.
_DIST_LO_MIN = 1.0          # 1 minute
_DIST_HI_MIN = 4320.0       # 3 days
_DIST_N_BINS = 36


def _log_density_bins(values: list,
                      lo_min: float = _DIST_LO_MIN,
                      hi_min: float = _DIST_HI_MIN,
                      n_bins: int = _DIST_N_BINS) -> list:
    """Histogram of ``values`` (in minutes) on a log10 axis, smoothed.

    Values outside [lo_min, hi_min] are clipped into the nearest edge bin
    so the curve still hugs the baseline rather than leaving a hole.
    The result is twice-smoothed with a 1-2-1 kernel for a pleasant curve;
    bin units are arbitrary (consumer rescales against ``max(bins)``).
    """
    if not values:
        return []
    lo = math.log10(lo_min)
    hi = math.log10(hi_min)
    span = hi - lo
    if span <= 0:
        return []
    bins = [0.0] * n_bins
    for v in values:
        if v is None or v == "":
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            idx = 0
        else:
            lv = math.log10(fv)
            if lv <= lo:
                idx = 0
            elif lv >= hi:
                idx = n_bins - 1
            else:
                idx = int((lv - lo) / span * n_bins)
                if idx >= n_bins:
                    idx = n_bins - 1
        bins[idx] += 1.0
    # Smooth twice with a 1-2-1 kernel.
    for _ in range(2):
        sm = list(bins)
        for i in range(n_bins):
            left  = bins[i - 1] if i > 0 else 0.0
            right = bins[i + 1] if i < n_bins - 1 else 0.0
            sm[i] = (left + 2.0 * bins[i] + right) / 4.0
        bins = sm
    return bins


def _week_start(date_str: str) -> Optional[date]:
    """Monday of the ISO-week of `date_str` (e.g. '2026-05-12T16:04:48Z').

    Returns ``None`` if the input is missing or unparseable.
    """
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    d = dt.date()
    return d - timedelta(days=d.weekday())


def _aggregate_findings_by_week(rows: list) -> list:
    """Bin caught / fixed counts by the Monday-of-week of each PR's merge date.

    Returns a list of dicts ``{"week_start": "YYYY-MM-DD", "caught": int,
    "fixed": int, "prs": int}`` sorted ascending by ``week_start``. Empty
    intermediate weeks are filled with zeros so the line chart is continuous.
    """
    bins: dict = defaultdict(lambda: {"caught": 0, "fixed": 0, "prs": 0})
    for r in rows:
        wk = _week_start(r.get("PR Merge Date", ""))
        if wk is None:
            continue
        bins[wk]["caught"] += r.get("Total Suggestions", 0)
        bins[wk]["fixed"]  += r.get("Total Implemented", 0)
        if r.get("Has Qodo Review", True):
            bins[wk]["prs"] += 1
    if not bins:
        return []
    first, last = min(bins), max(bins)
    out, cur = [], first
    while cur <= last:
        v = bins.get(cur, {"caught": 0, "fixed": 0, "prs": 0})
        out.append({"week_start": cur.isoformat(), **v})
        cur += timedelta(days=7)
    return out


def _nice_ceiling(value: int) -> int:
    """Round `value` up to a clean axis maximum (e.g. 1375 -> 1500)."""
    if value <= 0:
        return 10
    # find the magnitude (100, 1000, 10000, ...)
    magnitude = 10 ** max(0, len(str(value)) - 2)
    step = magnitude
    n = (value // step) + 1
    # snap to .5 of magnitude when that's enough headroom
    half = n * step
    return int(half)


def _format_week_label(week_start: str, fmt: str = "%b %-d") -> str:
    """'2026-05-11' -> 'May 11'. Falls back to %b %d on Windows."""
    try:
        d = date.fromisoformat(week_start)
    except (ValueError, TypeError):
        return week_start
    try:
        return d.strftime(fmt)
    except ValueError:
        return d.strftime("%b %d")


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

/* velocity — distribution (Prototype 04) */
.dist-wrap{display:grid;grid-template-columns:1fr 280px;gap:24px;align-items:stretch}
.dist-chart{background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);padding:20px 24px 12px;min-height:260px}
.dist-svg{width:100%;height:240px;display:block}
.dist-legend{display:flex;justify-content:space-between;align-items:center;
  margin-bottom:10px;font-size:11px;color:var(--fg-muted)}
.dist-legend .left{display:flex;gap:16px;flex-wrap:wrap}
.dist-legend .sw{display:inline-block;width:10px;height:10px;border-radius:2px;
  margin-right:6px;vertical-align:middle}
.dist-legend .sw.qodo{background:var(--p-400)}
.dist-legend .sw.human{background:var(--pm-400)}
.dist-legend b{color:var(--fg-default);font-weight:500}
.dist-legend .axis{font-size:10px;letter-spacing:.04em;color:var(--fg-subtle);text-transform:uppercase}
.dist-side{display:flex;flex-direction:column;justify-content:center;gap:14px}
.dist-card{background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-md);padding:16px 18px}
.dist-card-label{font-size:10.5px;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;color:var(--fg-muted)}
.dist-card-val{font-size:26px;font-weight:700;color:var(--fg-strong);line-height:1;
  margin-top:8px;letter-spacing:-.01em}
.dist-card-val .pct{font-size:16px;color:var(--fg-muted);font-weight:600;margin-left:2px}
.dist-card-sub{font-size:12px;color:var(--fg-muted);margin-top:8px;line-height:1.55;text-wrap:pretty}
.dist-card-sub b{color:var(--fg-default);font-weight:500}
.dist-card.highlight{border-color:rgba(121,104,250,.30);background:var(--p-tint-10)}
.dist-card.highlight .dist-card-label{color:var(--p-300)}
.dist-card.highlight .dist-card-val{color:var(--p-200)}
@media (max-width:900px){.dist-wrap{grid-template-columns:1fr}}

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

/* four-part table of contents */
.toc{padding:34px 36px 30px;background:var(--bg-inset);border-bottom:1px solid var(--border-default)}
.toc-head{display:flex;align-items:center;gap:14px;margin-bottom:18px}
.toc-label{font-size:10.5px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--p-300);white-space:nowrap}
.toc-line{flex:1;height:1px;background:var(--border-default)}
.toc-hint{font-size:10.5px;color:var(--fg-subtle);font-family:'IBM Plex Mono',monospace;letter-spacing:.04em;white-space:nowrap}
.acts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;background:transparent;border:none}
.act{padding:16px 18px;border:1px solid var(--border-default);border-radius:var(--radius-md);background:var(--bg-canvas);display:flex;flex-direction:column;gap:8px;text-decoration:none;color:inherit;transition:border-color 120ms ease,background 120ms ease,transform 120ms ease}
.act:hover{border-color:var(--p-400);background:rgba(121,104,250,.05);text-decoration:none;transform:translateY(-1px)}
.act-num{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--p-300);display:flex;align-items:center;gap:8px}
.act-num::before{content:'';width:18px;height:1px;background:var(--p-400)}
.act-title{font-size:14.5px;font-weight:600;color:var(--fg-strong);letter-spacing:-.005em;line-height:1.25}
.act-list{font-size:11.5px;color:var(--fg-muted);line-height:1.5;text-wrap:pretty}
@media (max-width:900px){.acts{grid-template-columns:1fr 1fr}.toc-hint{display:none}}
@media (max-width:560px){.acts{grid-template-columns:1fr}}

/* embedded leaderboard inside Spotlight */
.spot-divider{display:flex;align-items:center;gap:14px;margin:32px 0 18px}
.spot-divider .label{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--p-300);white-space:nowrap}
.spot-divider .line{flex:1;height:1px;background:var(--border-default)}
.spot-leaderboard-deck{font-size:12.5px;color:var(--fg-muted);margin-bottom:14px;max-width:680px;line-height:1.55;text-wrap:pretty}
.spot-leaderboard-deck b{color:var(--fg-default);font-weight:600}
table.top-prs .num.spot{color:var(--warning);font-weight:600}
table.top-prs .mini-bar.purple > i{background:var(--p-400)}

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

/* ── Trend (Prototype 01) ─────────────────────────────────────── */
.trend-wrap{display:grid;grid-template-columns:1fr 240px;gap:24px;align-items:stretch}
.trend-chart{position:relative;background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);padding:20px 22px 14px;min-height:280px}
.trend-legend{display:flex;gap:18px;font-size:11px;color:var(--fg-muted);margin-bottom:14px;flex-wrap:wrap}
.trend-legend > span{white-space:nowrap}
.trend-legend .sw{width:10px;height:10px;border-radius:2px;display:inline-block;
  vertical-align:middle;margin-right:6px}
.trend-legend .sw.caught{background:var(--p-400)}
.trend-legend .sw.fixed{background:var(--g-mint)}
.trend-legend b{color:var(--fg-default);font-weight:500}
.trend-svg{width:100%;height:220px;display:block}
.trend-side{display:flex;flex-direction:column;justify-content:flex-start;gap:14px}
.trend-stat{background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);padding:18px 20px}
.trend-stat-label{font-size:10.5px;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;color:var(--fg-muted)}
.trend-stat-val{font-size:30px;font-weight:700;color:var(--fg-strong);line-height:1;
  letter-spacing:-.02em;margin-top:8px}
.trend-stat-val .delta{font-size:13px;font-weight:600;margin-left:6px;vertical-align:middle}
.trend-stat-val .delta.up{color:var(--success)}
.trend-stat-val .delta.down{color:var(--danger)}
.trend-stat-sub{font-size:12px;color:var(--fg-muted);margin-top:8px;line-height:1.5}
.trend-stat-sub b{color:var(--fg-default);font-weight:500}
@media (max-width: 900px){.trend-wrap{grid-template-columns:1fr}}

/* ── Funnel (Prototype 02) ───────────────────────────────────── */
.funnel{display:flex;flex-direction:column;
  background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);padding:6px 24px}
.funnel-row{display:grid;grid-template-columns:220px 1fr 140px;gap:18px;align-items:center;
  padding:14px 0;border-bottom:1px solid var(--border-subtle)}
.funnel-row:last-child{border-bottom:none}
.funnel-name{font-size:13px;color:var(--fg-default);font-weight:500;line-height:1.35}
.funnel-name .step{font-family:'IBM Plex Mono',monospace;font-size:10px;
  color:var(--fg-subtle);display:block;margin-bottom:3px;letter-spacing:.08em;
  text-transform:uppercase;font-weight:600}
.funnel-bar{position:relative;height:34px;background:var(--bg-inset);
  border-radius:var(--radius-sm);overflow:hidden}
.funnel-bar i{display:flex;height:100%;
  background:linear-gradient(90deg,var(--p-500),var(--p-400));
  border-radius:var(--radius-sm);align-items:center;padding-left:14px;
  font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#fff;
  font-style:normal;white-space:nowrap;min-width:fit-content}
.funnel-bar.gold i{background:var(--brand-gradient)}
.funnel-bar.muted i{background:var(--pm-500);color:var(--fg-default)}
.funnel-val{text-align:right;font-family:'IBM Plex Mono',monospace;font-size:14px;
  color:var(--fg-strong);font-weight:600;line-height:1.2}
.funnel-val .pct{display:block;font-size:11px;color:var(--fg-muted);
  margin-top:4px;font-weight:400;letter-spacing:0}
.funnel-foot{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;margin-top:16px;
  background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-lg);overflow:hidden}
.funnel-foot-cell{font-size:12.5px;color:var(--fg-muted);line-height:1.55;
  text-wrap:pretty;padding:20px 22px;border-right:1px solid var(--border-default)}
.funnel-foot-cell:last-child{border-right:none}
.funnel-foot-cell b{color:var(--fg-strong);font-weight:700;display:block;
  font-size:22px;letter-spacing:-.015em;margin-bottom:6px;line-height:1.1}
.funnel-foot-cell.gold b{
  background:var(--brand-gradient);-webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent}
@media (max-width: 900px){
  .funnel-row{grid-template-columns:130px 1fr 100px;gap:10px}
  .funnel-foot{grid-template-columns:1fr}
  .funnel-foot-cell{border-right:none;border-bottom:1px solid var(--border-default)}
  .funnel-foot-cell:last-child{border-bottom:none}
}

/* ── Hours Saved (Prototype 03) ────────────────────────────── */
.saved-grid{display:grid;grid-template-columns:1fr 320px;gap:18px;align-items:start}
.observed-col{display:flex;flex-direction:column;gap:16px;min-width:0}
.saved-hero{background:linear-gradient(135deg,rgba(121,104,250,.08),rgba(6,228,174,.05));
  border:1px solid var(--border-default);border-radius:var(--radius-lg);padding:24px 28px;
  display:flex;flex-direction:column;gap:14px}
.saved-eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:var(--g-mint);line-height:1.4;text-wrap:balance}
.saved-eyebrow .sep{color:var(--fg-subtle);margin:0 7px}
.saved-big{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.saved-big .n{font-size:60px;font-weight:700;line-height:1;letter-spacing:-.03em;
  background:var(--brand-gradient);-webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;font-variant-numeric:tabular-nums}
.saved-big .u{font-size:16px;color:var(--fg-muted);font-weight:500;letter-spacing:-.005em}
.saved-lede{font-size:13px;color:var(--fg-default);line-height:1.65;text-wrap:pretty}
.saved-lede b{color:var(--fg-strong);font-weight:600}
.saved-formula{font-size:11.5px;color:var(--fg-subtle);font-family:'IBM Plex Mono',monospace;
  padding:11px 13px;background:var(--bg-inset);border:1px dashed var(--border-default);
  border-radius:var(--radius-sm);line-height:1.7;font-variant-numeric:tabular-nums}
.saved-formula b{color:var(--fg-default);font-weight:500}
.saved-formula .op{color:var(--fg-subtle)}
.scale-col{background:linear-gradient(180deg,rgba(121,104,250,.10),rgba(15,15,25,.4));
  border:1px solid var(--p-tint-20);border-radius:var(--radius-lg);
  padding:22px 22px 20px;display:flex;flex-direction:column;gap:16px;align-self:start}
.scale-eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:var(--p-300);display:flex;align-items:center;gap:8px}
.scale-eyebrow .live-dot{width:5px;height:5px;border-radius:50%;background:var(--p-400);
  box-shadow:0 0 6px var(--p-400)}
.scale-headline{display:flex;flex-direction:column;gap:2px}
.scale-amount{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap;font-variant-numeric:tabular-nums}
.scale-amount .v{font-size:36px;font-weight:700;color:var(--fg-strong);letter-spacing:-.025em;line-height:1.05}
.scale-amount .yr{font-size:14px;color:var(--fg-muted);font-weight:500;letter-spacing:-.01em}
.scale-sub{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--fg-muted);
  font-variant-numeric:tabular-nums;letter-spacing:-.005em}
.scale-context{font-size:12.5px;color:var(--fg-default);line-height:1.55;text-wrap:pretty}
.scale-context b{color:var(--fg-strong);font-weight:600}
.scale-3yr{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
  padding:10px 14px;background:rgba(6,228,174,.06);border:1px solid rgba(6,228,174,.20);
  border-radius:var(--radius-md)}
.scale-3yr .lbl{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-muted)}
.scale-3yr .val{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:var(--g-mint);
  font-variant-numeric:tabular-nums}
.saved-breakdown{display:flex;flex-direction:column;gap:10px}
.sbr-row{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center;
  padding:14px 16px;background:var(--bg-inset);border:1px solid var(--border-default);
  border-radius:var(--radius-md)}
.sbr-tag{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-muted);
  padding:3px 9px;border-radius:var(--radius-full);background:var(--bg-surface);
  border:1px solid var(--border-default);white-space:nowrap}
.sbr-tag.warn{color:var(--warning);border-color:rgba(245,181,68,.20);background:rgba(245,181,68,.07)}
.sbr-tag.norm{color:var(--p-300);border-color:var(--p-tint-20);background:var(--p-tint-10)}
.sbr-tag.lg{font-size:11px;padding:5px 11px}
.sbr-mid{font-size:12.5px;color:var(--fg-default);line-height:1.5;min-width:0}
.sbr-mid .top{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap}
.sbr-mid .top b{color:var(--fg-strong);font-weight:600;font-variant-numeric:tabular-nums}
.sbr-mid span.sub{display:block;font-size:11px;color:var(--fg-muted);margin-top:3px;line-height:1.45}
.sbr-val{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600;color:var(--fg-strong);
  text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.sbr-val .u{font-size:11px;color:var(--fg-muted);font-weight:500;margin-left:2px}
.sbr-row.big{padding:18px 20px;grid-template-columns:1fr auto;
  grid-template-areas:"tag val" "mid mid";row-gap:12px;align-items:center}
.sbr-row.big > .sbr-tag{grid-area:tag;justify-self:start}
.sbr-row.big > .sbr-mid{grid-area:mid}
.sbr-row.big > .sbr-val{grid-area:val;justify-self:end}
.sbr-row.big .sbr-mid{font-size:13px}
.sbr-row.big .sbr-mid .top b{font-size:18px;letter-spacing:-.01em}
.sbr-row.big .sbr-val{font-size:18px}
.sbr-row.bonus{background:transparent;border-style:dashed;
  grid-template-columns:1fr auto;grid-template-areas:"tag val" "mid mid";row-gap:10px;align-items:center}
.sbr-row.bonus > .sbr-tag{grid-area:tag;justify-self:start;
  color:var(--g-mint);border-color:rgba(6,228,174,.25);background:rgba(6,228,174,.07)}
.sbr-row.bonus > .sbr-mid{grid-area:mid}
.sbr-row.bonus > .sbr-val{grid-area:val;justify-self:end;color:var(--g-mint)}
.sbr-total{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center;
  padding:14px 16px;margin-top:4px;
  background:linear-gradient(135deg,rgba(121,104,250,.10),rgba(6,228,174,.06));
  border:1px solid rgba(168,161,253,.25);border-radius:var(--radius-md)}
.sbr-total .lbl{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--p-200)}
.sbr-total .mid{font-size:11.5px;color:var(--fg-muted);line-height:1.4}
.sbr-total .mid b{color:var(--fg-default);font-weight:500;font-variant-numeric:tabular-nums}
.sbr-total .v{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;
  color:var(--fg-strong);text-align:right;font-variant-numeric:tabular-nums}
@media (max-width:920px){.saved-grid{grid-template-columns:1fr}}

/* ── Hours Saved · interactive bits (live chips, sliders, segs) ── */
.hs-live{display:inline-flex;align-items:baseline;position:relative;
  color:var(--fg-strong);font-weight:600;font-variant-numeric:tabular-nums;
  padding:1px 4px;margin:0 -1px;border-radius:4px;cursor:text;
  border-bottom:1px dashed rgba(168,161,253,.35);
  transition:background-color 120ms ease, color 120ms ease, box-shadow 120ms ease}
.hs-live:hover{background:var(--p-tint-10);color:var(--p-200);border-bottom-color:var(--p-300)}
.hs-live:focus-within,.hs-live.is-focused{background:var(--p-tint-10);color:var(--p-200);
  border-bottom-color:var(--p-400);box-shadow:0 0 0 1px var(--p-400)}
.hs-live input{font:inherit;color:inherit;letter-spacing:inherit;
  background:transparent;border:none;outline:none;padding:0;margin:0;
  width:var(--w,3ch);text-align:center;font-variant-numeric:tabular-nums;
  -moz-appearance:textfield}
.hs-live input::-webkit-outer-spin-button,
.hs-live input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.hs-live .prefix,.hs-live .suffix{color:var(--fg-muted);font-weight:500;pointer-events:none}
.hs-live:hover .prefix,.hs-live:hover .suffix,
.hs-live:focus-within .prefix,.hs-live:focus-within .suffix{color:var(--p-300)}
.hs-live-cycle{cursor:pointer !important;outline:none;user-select:none}
.hs-live-cycle::after{content:'\21bb';font-size:.85em;margin-left:6px;color:var(--p-300);opacity:.7;
  font-weight:400;transition:transform 200ms ease, opacity 120ms ease;display:inline-block}
.hs-live-cycle:hover::after{opacity:1;transform:rotate(180deg)}
.hs-live-cycle:focus-visible{box-shadow:0 0 0 1px var(--p-400);background:var(--p-tint-10)}

.hs-scale-card{padding:12px 14px;background:rgba(0,0,0,.22);
  border:1px solid var(--border-default);border-radius:var(--radius-md);
  display:flex;flex-direction:column;gap:6px}
.hs-scale-card-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.hs-scale-card-label{font-size:10.5px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--fg-muted)}
.hs-scale-card-val{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;
  color:var(--fg-strong);font-variant-numeric:tabular-nums}
.hs-scale-card-val .u{font-size:10.5px;color:var(--fg-muted);font-weight:500;margin-left:2px}
.hs-scale-card-range{position:relative;width:100%}
.hs-scale-card-range input[type=range]{-webkit-appearance:none;appearance:none;
  width:100%;height:18px;background:transparent;cursor:pointer;margin:0;display:block}
.hs-scale-card-range input[type=range]::-webkit-slider-runnable-track{
  height:4px;background:var(--bg-canvas);border:1px solid var(--border-default);
  border-radius:var(--radius-full)}
.hs-scale-card-range input[type=range]::-moz-range-track{
  height:4px;background:var(--bg-canvas);border:1px solid var(--border-default);
  border-radius:var(--radius-full)}
.hs-scale-card-range input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;
  appearance:none;width:14px;height:14px;border-radius:50%;background:var(--p-400);
  border:2px solid var(--bg-inset);box-shadow:0 0 0 1px var(--p-400);margin-top:-6px;cursor:grab}
.hs-scale-card-range input[type=range]::-moz-range-thumb{
  width:14px;height:14px;border-radius:50%;background:var(--p-400);
  border:2px solid var(--bg-inset);box-shadow:0 0 0 1px var(--p-400);cursor:grab}
.hs-scale-card-foot{display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;
  font-size:10px;color:var(--fg-subtle);margin-top:2px}

.hs-seg{display:flex;background:var(--bg-surface);border:1px solid var(--border-default);
  border-radius:var(--radius-sm);padding:3px;gap:2px}
.hs-seg button{flex:1;font:inherit;font-size:11px;font-weight:600;color:var(--fg-muted);
  background:transparent;border:none;cursor:pointer;padding:6px 8px;border-radius:4px;
  white-space:nowrap;transition:background 100ms ease,color 100ms ease}
.hs-seg button:hover{color:var(--fg-default)}
.hs-seg button.on{background:var(--p-tint-10);color:var(--p-200)}

.r-footer{padding:24px 36px 28px;text-align:center;font-size:12px;color:var(--fg-subtle);
  background:var(--bg-inset);border-top:1px solid var(--border-default)}
.r-footer .mono{color:var(--fg-muted)}

/* ── Adoption matrix (Prototype 05) ────────────────────────────── */
.p05-hero{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}
.p05-stat{display:grid;grid-template-columns:110px 1fr;gap:14px;align-items:center;
  padding:14px 16px;background:rgba(0,0,0,.22);border:1px solid var(--border-default);border-radius:var(--radius-md)}
.p05-stat .v{font-size:22px;font-weight:700;color:var(--fg-strong);font-variant-numeric:tabular-nums;letter-spacing:-.02em;line-height:1;white-space:nowrap}
.p05-stat .v.mint{color:var(--g-mint)}
.p05-stat .v.warn{color:var(--warning)}
.p05-stat .v.danger{color:var(--danger)}
.p05-stat .lbl{font-size:11.5px;color:var(--fg-muted);line-height:1.45;text-wrap:pretty}
.p05-stat .lbl b{color:var(--fg-default);font-weight:600}
.p05-chart-card{background:var(--bg-surface);border:1px solid var(--border-default);border-radius:var(--radius-lg);overflow:hidden;margin-bottom:18px}
.p05-svg-wrap{position:relative;padding:16px 18px}
.p05-scatter{display:block;width:100%;height:auto;font-family:'Inter',sans-serif;font-variant-numeric:tabular-nums;user-select:none}
.p05-quad-bg-power{fill:rgba(6,228,174,.045)}
.p05-quad-bg-coach{fill:rgba(245,181,68,.045)}
.p05-quad-bg-curious{fill:rgba(168,161,253,.035)}
.p05-quad-bg-absent{fill:rgba(229,72,77,.045)}
.p05-bubble{cursor:pointer;transition:opacity 120ms ease,filter 120ms ease}
.p05-scatter.has-hover .p05-bubble:not(.is-hovered){opacity:.25}
.p05-bubble.is-hovered{filter:drop-shadow(0 0 6px rgba(255,255,255,.15))}
.p05-bubble-label{font-size:10px;font-weight:600;fill:var(--fg-strong);pointer-events:none;
  paint-order:stroke;stroke:var(--bg-surface);stroke-width:3px;stroke-linejoin:round}
.p05-bubble-sub{font-family:'IBM Plex Mono',monospace;font-size:8.5px;font-weight:500;pointer-events:none;
  paint-order:stroke;stroke:var(--bg-surface);stroke-width:3px;stroke-linejoin:round}
.p05-tip{position:absolute;pointer-events:none;background:var(--bg-canvas);
  border:1px solid var(--p-tint-20);border-radius:var(--radius-sm);
  padding:10px 12px;font-size:11.5px;color:var(--fg-default);line-height:1.5;
  box-shadow:0 8px 24px rgba(0,0,0,.55);min-width:200px;z-index:10;
  opacity:0;transition:opacity 80ms ease;font-variant-numeric:tabular-nums}
.p05-tip.show{opacity:1}
.p05-tip .name{font-size:13px;color:var(--fg-strong);font-weight:600;margin-bottom:2px;display:flex;align-items:center;gap:8px}
.p05-tip .name .badge{font-size:9px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 6px;border-radius:var(--radius-full);white-space:nowrap}
.p05-tip .name .badge.power{color:var(--g-mint);background:rgba(6,228,174,.10);border:1px solid rgba(6,228,174,.25)}
.p05-tip .name .badge.coach{color:var(--warning);background:rgba(245,181,68,.10);border:1px solid rgba(245,181,68,.25)}
.p05-tip .name .badge.curious{color:var(--p-200);background:var(--p-tint-10);border:1px solid var(--p-tint-20)}
.p05-tip .name .badge.absent{color:var(--danger);background:rgba(229,72,77,.10);border:1px solid rgba(229,72,77,.25)}
.p05-tip .meta{font-size:10.5px;color:var(--fg-subtle);margin-bottom:8px}
.p05-tip .row{display:flex;justify-content:space-between;gap:14px;color:var(--fg-muted);font-size:11px;padding:1.5px 0}
.p05-tip .row b{color:var(--fg-default);font-weight:500;font-family:'IBM Plex Mono',monospace}
.p05-tip .row b.mint{color:var(--g-mint)}
.p05-tip .row b.warn{color:var(--warning)}
.p05-tip .row b.danger{color:var(--danger)}
.p05-tip .divider{height:1px;background:var(--border-default);margin:7px 0 6px}
.p05-legend{display:flex;flex-wrap:wrap;gap:10px 18px;padding:10px 18px 14px;border-top:1px solid var(--border-default);background:var(--bg-inset);font-size:10.5px;color:var(--fg-muted)}
.p05-legend .it{display:flex;align-items:center;gap:7px}
.p05-legend .sw{width:11px;height:11px;border-radius:50%;flex-shrink:0;border:1.5px solid}
.p05-legend .sw.power{background:rgba(6,228,174,.18);border-color:#06E4AE}
.p05-legend .sw.coach{background:rgba(245,181,68,.18);border-color:#F5B544}
.p05-legend .sw.curious{background:rgba(168,161,253,.18);border-color:#A8A1FD}
.p05-legend .sw.absent{background:rgba(229,72,77,.18);border-color:#E5484D}
.p05-legend .dash{width:18px;border-top:1px dashed rgba(255,255,255,.25)}
.p05-quad-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.p05-quad-card{background:var(--bg-surface);border:1px solid var(--border-default);border-radius:var(--radius-md);overflow:hidden;display:flex;flex-direction:column}
.p05-quad-card.power{border-color:rgba(6,228,174,.28);background:linear-gradient(180deg,rgba(6,228,174,.04),transparent 50%)}
.p05-quad-card.coach{border-color:rgba(245,181,68,.28);background:linear-gradient(180deg,rgba(245,181,68,.04),transparent 50%)}
.p05-quad-card.curious{border-color:rgba(168,161,253,.22);background:linear-gradient(180deg,rgba(168,161,253,.03),transparent 50%)}
.p05-quad-card.absent{border-color:rgba(229,72,77,.28);background:linear-gradient(180deg,rgba(229,72,77,.04),transparent 50%)}
.p05-qc-head{padding:14px 16px 10px;display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.p05-qc-head-l{display:flex;flex-direction:column;gap:3px;min-width:0}
.p05-qc-eyebrow{font-size:9.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--fg-muted);display:flex;align-items:center;gap:7px}
.p05-qc-eyebrow .dot{width:6px;height:6px;border-radius:50%}
.p05-quad-card.power .p05-qc-eyebrow{color:var(--g-mint)}
.p05-quad-card.power .p05-qc-eyebrow .dot{background:var(--g-mint)}
.p05-quad-card.coach .p05-qc-eyebrow{color:var(--warning)}
.p05-quad-card.coach .p05-qc-eyebrow .dot{background:var(--warning)}
.p05-quad-card.curious .p05-qc-eyebrow{color:var(--p-200)}
.p05-quad-card.curious .p05-qc-eyebrow .dot{background:var(--p-200)}
.p05-quad-card.absent .p05-qc-eyebrow{color:var(--danger)}
.p05-quad-card.absent .p05-qc-eyebrow .dot{background:var(--danger)}
.p05-qc-title{font-size:15px;font-weight:600;color:var(--fg-strong);letter-spacing:-.005em}
.p05-qc-deck{font-size:11px;color:var(--fg-muted);margin-top:4px;line-height:1.5;text-wrap:pretty}
.p05-qc-deck b{color:var(--fg-default);font-weight:600}
.p05-qc-stat{display:flex;flex-direction:column;align-items:flex-end;flex-shrink:0;text-align:right}
.p05-qc-stat .v{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:var(--fg-strong);letter-spacing:-.02em;line-height:1}
.p05-qc-stat .u{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--fg-subtle);margin-top:3px}
.p05-qc-body{padding:0 16px 14px;flex:1;display:flex;flex-direction:column;gap:8px}
.p05-qc-action{font-size:10.5px;color:var(--fg-default);padding:8px 10px;background:rgba(0,0,0,.18);border:1px solid var(--border-default);border-radius:var(--radius-sm);line-height:1.5}
.p05-qc-action b{color:var(--fg-strong);font-weight:600}
.p05-qc-list{display:flex;flex-direction:column;gap:1px}
.p05-qc-row{display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center;padding:5px 4px;font-size:11.5px;border-radius:4px;transition:background 80ms ease;cursor:pointer}
.p05-qc-row:hover{background:rgba(255,255,255,.025)}
.p05-qc-row.is-hovered{background:rgba(255,255,255,.04)}
.p05-qc-row .name{font-weight:500;color:var(--fg-strong);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.p05-qc-row .findings{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--fg-muted);text-align:right;white-space:nowrap}
.p05-qc-row .rate{font-family:'IBM Plex Mono',monospace;font-size:10.5px;font-weight:600;text-align:right;width:48px;white-space:nowrap}
.p05-quad-card.power .rate{color:var(--g-mint)}
.p05-quad-card.coach .rate{color:var(--warning)}
.p05-quad-card.curious .rate{color:var(--p-200)}
.p05-quad-card.absent .rate{color:var(--danger)}
@media (max-width:900px){.p05-hero,.p05-quad-grid{grid-template-columns:1fr}}

@media print{
  body{background:#fff;color:#1c1c1c}
  .shell{max-width:100%;padding:0}
  .report{border:none;border-radius:0;background:#fff}
  section.r-section{break-inside:avoid;background:#fff}
}
"""


# ─── Hours Saved JS bundle ─────────────────────────────────────────────
# Lifted from `Prototype 03 - Hours Saved.html`. Reads its constants from
# the JSON blob emitted as <script id="hs-data"> inside the section. All
# DOM IDs are prefixed `hs-` so this can co-exist with other report sections.

_HOURS_SAVED_JS = r"""
(function(){
  const root = document.getElementById('hs-root');
  if(!root) return;
  const data = JSON.parse(document.getElementById('hs-data').textContent);
  const WINDOW_DAYS  = data.WINDOW_DAYS;
  const WINDOW_LABEL = data.WINDOW_LABEL;
  const EVAL_DEVS    = data.EVAL_DEVS;
  const TOTALS       = data.TOTALS;

  const EFFORT_PRESETS = {
    thorough: { lph: 300,  label: 'Thorough', cite: 'Cisco / SmartBear formal-inspection rate' },
    moderate: { lph: 600,  label: 'Moderate', cite: 'Modern team norm for non-safety-critical app code' },
    quick:    { lph: 1200, label: 'Quick',    cite: 'Skim / LGTM pace — AI-codegen, refactors, vendored libraries' },
  };
  const DEFAULTS = {
    rate: 180000,
    totalDevs: EVAL_DEVS,
    totalLoc: TOTALS.p95.loc,
    locPerHr: 600,
    trim: 'p95',
    reviewEffort: 'moderate',
    offloadPct: 50,
  };
  const state = {...DEFAULTS};
  const HRS_PER_WEEK = 40;
  const HRS_PER_YEAR = 1920;

  const $ = id => document.getElementById('hs-' + id);
  const fmtInt = n => Math.round(n).toLocaleString('en-US');
  const fmt1   = n => (Math.round(n*10)/10).toLocaleString('en-US', {minimumFractionDigits:1, maximumFractionDigits:1});
  const fmtMoney = n => {
    if(n >= 1e6) return '~$' + (Math.round(n/1e4)/100).toFixed(2).replace(/\.?0+$/,'') + 'M';
    if(n >= 1e4) return '~$' + Math.round(n/1000) + 'k';
    return '~$' + (Math.round(n/100)*100).toLocaleString('en-US');
  };
  const fmtRate = n => '$' + (Math.round(n/HRS_PER_YEAR * 100)/100) + '/h';

  function compute(){
    const annualize = 365 / WINDOW_DAYS;
    const devMult   = state.totalDevs / EVAL_DEVS;
    const scale     = annualize * devMult;
    const baselineH = state.totalLoc / state.locPerHr;
    const observedH = baselineH * (state.offloadPct / 100);
    const projectedH = observedH * scale;
    const hourly = state.rate / HRS_PER_YEAR;
    return {
      baselineH, observedH,
      observedDollars: observedH * hourly,
      observedWeeks:   observedH / HRS_PER_WEEK,
      projectedH,
      projectedDollars: projectedH * hourly,
      projectedFTE:     projectedH / HRS_PER_YEAR,
      hourly, scale,
    };
  }

  function makeRateChip(){
    return `<span class="hs-live" data-live="rate" data-min="60000" data-max="500000" data-step="5000" style="--w:5ch">`
         + `<span class="prefix">$</span><input type="text" value="${state.rate}" inputmode="numeric"><span class="suffix">/yr</span>`
         + `</span>`;
  }
  function makeOffloadChip(){
    return `<span class="hs-live" data-live="offloadPct" data-min="5" data-max="95" data-step="5" style="--w:2.5ch">`
         + `<input type="text" value="${state.offloadPct}" inputmode="numeric"><span class="suffix">%</span></span>`;
  }
  function makeProjectionChip(){
    return `<span class="hs-live" data-live="totalDevs" data-min="1" data-max="10000" data-step="1" style="--w:3ch">`
         + `<input type="text" value="${state.totalDevs}" inputmode="numeric"><span class="suffix">&nbsp;engineers</span></span>`;
  }
  function makeEffortChip(){
    const eff = EFFORT_PRESETS[state.reviewEffort];
    return `<span class="hs-live hs-live-cycle" data-cycle="reviewEffort" tabindex="0" role="button" `
         + `title="Click to cycle: Thorough → Moderate → Quick. Qodo always reviews thoroughly; this is your team's counterfactual." `
         + `style="cursor:pointer;padding:1px 8px 1px 8px">`
         + `<b style="color:inherit">${eff.label}</b>`
         + `</span>`;
  }

  function render(){
    // Preserve focus across innerHTML re-injection of lede/scaleContext.
    const _activeEl    = document.activeElement;
    const _activeContainer = _activeEl && _activeEl.closest && _activeEl.closest('#hs-ledeText, #hs-scaleContext');
    const _restoreKey  = _activeContainer ? _activeEl.closest('.hs-live')?.dataset?.live : null;
    const _restoreStart = _activeEl && _activeEl.selectionStart;
    const _restoreEnd   = _activeEl && _activeEl.selectionEnd;
    const _restoreContainerId = _activeContainer && _activeContainer.id;

    const c = compute();
    $('heroHours').textContent = fmtInt(c.observedH);
    $('observedEyebrow').innerHTML =
      `Observed <span style="color:var(--fg-subtle);margin:0 7px">&middot;</span> `
      + `<b style="color:var(--fg-default);font-weight:700">${WINDOW_LABEL}</b> `
      + `<span style="color:var(--fg-subtle);font-weight:500;letter-spacing:.04em">(${WINDOW_DAYS}&nbsp;days)</span>`;

    const eff = EFFORT_PRESETS[state.reviewEffort];
    const lede = `Qodo thoroughly reviewed <b>${fmtInt(state.totalLoc)}&nbsp;lines of code (LOC)</b> from <b>${EVAL_DEVS}&nbsp;engineers</b>. To match that volume in-house at `
               + makeEffortChip()
               + ` pace (<b>${eff.lph}&nbsp;LOC/h</b>), a senior engineer would have spent <b>~${fmtInt(c.baselineH)}&nbsp;hours</b>. Assuming Qodo offloads `
               + makeOffloadChip()
               + ` of the human code review effort, that&rsquo;s <b>~${fmtInt(c.observedH)}&nbsp;hours</b> returned, worth <b>${fmtMoney(c.observedDollars)}</b> at a fully-loaded cost of `
               + makeRateChip()
               + ` per engineer performing code reviews.`;
    $('ledeText').innerHTML = lede;

    $('scaleAmount').textContent  = fmtMoney(c.projectedDollars);
    $('scaleSub').textContent     = `${fmtInt(c.projectedH)} h/yr · ${(Math.round(c.projectedFTE*100)/100).toFixed(2)} FTE`;
    $('scaleContext').innerHTML   = `if sustained across ${makeProjectionChip()} for a full year at a fully-loaded cost of ${makeRateChip()} per engineer.`;
    $('scaleDevsVal').textContent = state.totalDevs;
    $('rateRangeVal').textContent = state.rate.toLocaleString('en-US');
    $('scale3yrVal').textContent  = fmtMoney(c.projectedDollars * 3);

    document.querySelectorAll('#hs-ledeText .hs-live, #hs-scaleContext .hs-live').forEach(bindLiveSpan);
    document.querySelectorAll('#hs-ledeText .hs-live-cycle, #hs-scaleContext .hs-live-cycle').forEach(bindCycleChip);

    if(_restoreKey){
      const newInput = document.querySelector(`#${_restoreContainerId} [data-live="${_restoreKey}"] input`);
      if(newInput && document.activeElement !== newInput){
        newInput.focus();
        const len = newInput.value.length;
        const s = Math.min(_restoreStart ?? len, len), e = Math.min(_restoreEnd ?? len, len);
        try { newInput.setSelectionRange(s, e); } catch(err){}
      }
    }

    $('locTotalHrs').innerHTML = fmtInt(c.observedH) + '<span class="u">h</span>';
    $('totalHrsLine').textContent   = fmtInt(c.observedH) + ' h';
    $('totalWeeksLine').textContent = fmt1(c.observedWeeks) + ' wks';
    $('totalRateLine').textContent  = fmtRate(state.rate);
    $('totalDollars').textContent   = fmtMoney(c.observedDollars);

    $('effortLabel').innerHTML = `<b>${eff.label}</b> &middot; <span style="font-family:'IBM Plex Mono',monospace;color:var(--fg-default);font-weight:500">${eff.lph}&nbsp;LOC/h</span>`;
    $('effortCite').textContent = eff.cite;
    document.querySelectorAll('#hs-effortSeg button').forEach(b =>
      b.classList.toggle('on', b.dataset.effort === state.reviewEffort));
    document.querySelectorAll('#hs-trimSeg button').forEach(b =>
      b.classList.toggle('on', b.dataset.trim === state.trim));

    const t = TOTALS[state.trim];
    const total = TOTALS.none;
    $('trimLabel').textContent = t.label;
    $('trimStats').innerHTML =
        `<b style="color:var(--fg-default);font-weight:600">${fmtInt(t.prs)}</b> of ${fmtInt(total.prs)} PRs`
      + ` <span style="color:var(--fg-subtle);margin:0 6px">&middot;</span> `
      + `<b style="color:var(--fg-default);font-weight:600">${fmtInt(t.loc)}</b> of ${fmtInt(total.loc)} LOC`;

    $('formula').innerHTML =
        `<b>observed</b> <span style="color:var(--fg-subtle)">=</span> (total_loc &divide; loc_per_hr) &times; offload_pct<br>`
      + `<span style="color:var(--fg-subtle)">&rarr;</span> (<b>${fmtInt(state.totalLoc)}</b> &divide; <b>${state.locPerHr}</b>) &times; <b>${state.offloadPct}%</b> <span style="color:var(--fg-subtle)">=</span> <b>${fmt1(c.observedH)}&nbsp;h</b><br>`
      + `<b>projected</b> <span style="color:var(--fg-subtle)">=</span> observed <span style="color:var(--fg-subtle)">&times;</span> <b>(365/${WINDOW_DAYS})</b> <span style="color:var(--fg-subtle)">&times;</span> <b>(${state.totalDevs}/${EVAL_DEVS})</b> <span style="color:var(--fg-subtle)">=</span> <b>${fmtInt(c.projectedH)}&nbsp;h/yr</b>`;
  }

  function sizeInput(input){
    if(!input || input.type === 'range') return;
    const v = input.value || input.placeholder || '';
    input.style.width = Math.max(String(v).length, 1) + 0.5 + 'ch';
  }
  function setValue(key, v){
    if(state[key] === v) return;
    state[key] = v;
    render();
    syncSiblings(key, v);
  }
  function syncSiblings(key, v){
    const targets = [];
    if(key === 'rate'){
      const r = document.getElementById('hs-rateRange'); if(r) targets.push(r);
      document.querySelectorAll('[data-live="rate"] input').forEach(el => targets.push(el));
    } else if(key === 'totalDevs'){
      const r = document.getElementById('hs-totalDevsRange'); if(r) targets.push(r);
      document.querySelectorAll('[data-live="totalDevs"] input').forEach(el => targets.push(el));
    } else if(key === 'offloadPct'){
      document.querySelectorAll('[data-live="offloadPct"] input').forEach(el => targets.push(el));
    }
    targets.forEach(el => {
      if(el && String(el.value) !== String(v)){
        el.value = v;
        if(el.type === 'number') sizeInput(el);
      }
    });
  }
  function bindNumber(key, input, opts){
    opts = opts || {};
    const min = opts.min ?? -Infinity, max = opts.max ?? Infinity, step = opts.step ?? 1;
    const onInput = e => {
      let v = parseFloat(e.target.value);
      if(!isFinite(v)) return;
      setValue(key, v);
      sizeInput(e.target);
    };
    const onChange = e => {
      let v = parseFloat(e.target.value);
      if(!isFinite(v)) v = DEFAULTS[key];
      v = Math.min(max, Math.max(min, v));
      e.target.value = v;
      sizeInput(e.target);
      setValue(key, v);
    };
    input.addEventListener('input', onInput);
    input.addEventListener('change', onChange);
    input.addEventListener('keydown', e => {
      if(e.key === 'Enter'){ e.preventDefault(); e.target.blur(); return; }
      if(input.type === 'text' && (e.key === 'ArrowUp' || e.key === 'ArrowDown')){
        e.preventDefault();
        const cur = parseFloat(input.value) || 0;
        const d = e.key === 'ArrowUp' ? step : -step;
        const v = Math.min(max, Math.max(min, cur + d));
        input.value = v;
        setValue(key, v);
        sizeInput(input);
      }
    });
    sizeInput(input);
  }
  function bindLiveSpan(wrap){
    if(!wrap || wrap.dataset.bound) return;
    wrap.dataset.bound = '1';
    const key  = wrap.dataset.live;
    const min  = wrap.dataset.min  ? +wrap.dataset.min  : undefined;
    const max  = wrap.dataset.max  ? +wrap.dataset.max  : undefined;
    const step = wrap.dataset.step ? +wrap.dataset.step : 1;
    const input = wrap.querySelector('input');
    if(!input) return;
    bindNumber(key, input, {min, max, step});
    input.addEventListener('focus', () => wrap.classList.add('is-focused'));
    input.addEventListener('blur',  () => wrap.classList.remove('is-focused'));
    wrap.addEventListener('click', e => { if(e.target !== input) input.focus(); });
  }
  function bindCycleChip(wrap){
    if(!wrap || wrap.dataset.bound) return;
    wrap.dataset.bound = '1';
    const cycle = (dir) => {
      const order = ['thorough','moderate','quick'];
      const i = order.indexOf(state.reviewEffort);
      setReviewEffort(order[(i + dir + order.length) % order.length]);
    };
    wrap.addEventListener('click', () => cycle(+1));
    wrap.addEventListener('keydown', e => {
      if(e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowRight' || e.key === 'ArrowDown'){
        e.preventDefault(); cycle(+1);
      } else if(e.key === 'ArrowLeft' || e.key === 'ArrowUp'){
        e.preventDefault(); cycle(-1);
      }
    });
  }
  function setReviewEffort(name){
    const preset = EFFORT_PRESETS[name];
    if(!preset) return;
    state.reviewEffort = name;
    state.locPerHr     = preset.lph;
    render();
  }
  function setTrim(name){
    const totals = TOTALS[name];
    if(!totals) return;
    state.trim = name;
    state.totalLoc = totals.loc;
    render();
  }

  // ── range bindings (rate, totalDevs in the scale-col) ──
  bindNumber('rate',      document.getElementById('hs-rateRange'),      {min:60000, max:500000});
  bindNumber('totalDevs', document.getElementById('hs-totalDevsRange'), {min:1,     max:10000});

  // ── effort + trim segmented controls ──
  document.querySelectorAll('#hs-effortSeg button').forEach(b => {
    b.addEventListener('click', () => setReviewEffort(b.dataset.effort));
  });
  document.querySelectorAll('#hs-trimSeg button').forEach(b => {
    b.addEventListener('click', () => setTrim(b.dataset.trim));
  });

  render();
})();
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


def _section_acts(show_trend: bool = True) -> str:
    """Four-part outline — sets expectations for what follows.

    Sits between the hero and the KPI strip. Wrapped in a labeled panel so
    it reads clearly as a table of contents distinct from the metric strip
    below.
    """
    trend_act = (
        '<a class="act" href="#sec-trend">'
        '<div class="act-num">Part I</div>'
        '<div class="act-title">At a glance</div>'
        '<div class="act-list">The headline numbers and how they’re trending.</div>'
        '</a>'
    ) if show_trend else ''
    return (
        '<div class="toc">'
        '<div class="toc-head">'
        '<span class="toc-label">In this report</span>'
        '<span class="toc-line"></span>'
        '<span class="toc-hint">Click any part to jump</span>'
        '</div>'
        '<div class="acts">'
        + trend_act +
        '<a class="act" href="#sec-funnel">'
        '<div class="act-num">Part II</div>'
        '<div class="act-title">Code quality impact</div>'
        '<div class="act-list">Where findings landed, with named examples.</div>'
        '</a>'
        '<a class="act" href="#sec-velocity">'
        '<div class="act-num">Part III</div>'
        '<div class="act-title">Engineering velocity</div>'
        '<div class="act-list">Speed to feedback and developer engagement.</div>'
        '</a>'
        '<a class="act" href="#hs-root">'
        '<div class="act-num">Part IV</div>'
        '<div class="act-title">Return on investment</div>'
        '<div class="act-list">Senior-engineer hours offloaded.</div>'
        '</a>'
        '</div>'
        '</div>'
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


def _section_trend(agg: ReportData) -> str:
    """Prototype 01 — weekly cadence of caught & fixed findings."""
    weeks = agg.findings_by_week
    if len(weeks) < 2:
        return ""

    # Drop leading/trailing all-zero weeks so the chart frames the active range.
    def _is_zero(w: dict) -> bool:
        return w["caught"] == 0 and w["fixed"] == 0
    start, end = 0, len(weeks)
    while start < end and _is_zero(weeks[start]):
        start += 1
    while end > start and _is_zero(weeks[end - 1]):
        end -= 1
    weeks = weeks[start:end]
    if len(weeks) < 2:
        return ""

    max_val = max(max(w["caught"], w["fixed"]) for w in weeks)
    if max_val <= 0:
        return ""
    y_max = _nice_ceiling(max_val)

    # SVG geometry (matches Brainstorm v1 mockup)
    vb_w, vb_h = 700, 220
    pad_l, pad_r, pad_t, pad_b = 40, 10, 20, 25
    plot_l, plot_r = pad_l, vb_w - pad_r
    plot_t, plot_b = pad_t, vb_h - pad_b
    plot_w = plot_r - plot_l
    plot_h = plot_b - plot_t

    n = len(weeks)
    xs = [plot_l + (plot_w * i / (n - 1)) for i in range(n)]

    def _y(v: int) -> float:
        return plot_b - (v / y_max) * plot_h

    caught_pts = [(xs[i], _y(weeks[i]["caught"])) for i in range(n)]
    fixed_pts  = [(xs[i], _y(weeks[i]["fixed"]))  for i in range(n)]

    def _line(pts) -> str:
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    def _area(pts) -> str:
        line = _line(pts)
        return (f"{line} L{pts[-1][0]:.1f},{plot_b} "
                f"L{pts[0][0]:.1f},{plot_b} Z")

    # Gridlines + y-axis labels (5 rows: 0, 25%, 50%, 75%, 100%)
    grid, y_labels = [], []
    for i in range(5):
        y = plot_t + plot_h * i / 4
        val = int(round(y_max * (1 - i / 4)))
        grid.append(
            f'<line x1="{plot_l}" y1="{y:.1f}" x2="{plot_r}" y2="{y:.1f}"></line>'
        )
        y_labels.append(
            f'<text x="{plot_l - 6}" y="{y + 3:.1f}" text-anchor="end">{_fmt_int(val)}</text>'
        )

    # X-axis labels — show all weeks if <= 10, otherwise step to keep ~9 labels.
    label_step = 1 if n <= 10 else max(1, (n + 8) // 9)
    x_labels = []
    for i, w in enumerate(weeks):
        if i % label_step == 0 or i == n - 1:
            x_labels.append(
                f'<text x="{xs[i]:.1f}" y="{plot_b + 16:.0f}" '
                f'text-anchor="middle">{_h(_format_week_label(w["week_start"]))}</text>'
            )

    caught_markers = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5"></circle>'
        for x, y in caught_pts
    )
    fixed_markers = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5"></circle>'
        for x, y in fixed_pts
    )

    svg = (
        f'<svg class="trend-svg" viewBox="0 0 {vb_w} {vb_h}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        f'<defs>'
        f'<linearGradient id="caughtGrad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#7968FA" stop-opacity="0.22"></stop>'
        f'<stop offset="100%" stop-color="#7968FA" stop-opacity="0"></stop>'
        f'</linearGradient>'
        f'<linearGradient id="fixedGrad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#06E4AE" stop-opacity="0.22"></stop>'
        f'<stop offset="100%" stop-color="#06E4AE" stop-opacity="0"></stop>'
        f'</linearGradient>'
        f'</defs>'
        f'<g stroke="rgba(255,255,255,0.04)" stroke-width="1">{"".join(grid)}</g>'
        f'<g font-family="IBM Plex Mono" font-size="9" fill="#A09CB6">{"".join(y_labels)}</g>'
        f'<g font-family="IBM Plex Mono" font-size="9" fill="#A09CB6">{"".join(x_labels)}</g>'
        f'<path d="{_area(caught_pts)}" fill="url(#caughtGrad)"></path>'
        f'<path d="{_line(caught_pts)}" stroke="#7968FA" stroke-width="2" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"></path>'
        f'<path d="{_area(fixed_pts)}"  fill="url(#fixedGrad)"></path>'
        f'<path d="{_line(fixed_pts)}"  stroke="#06E4AE" stroke-width="2" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"></path>'
        f'<g fill="#7968FA">{caught_markers}</g>'
        f'<g fill="#06E4AE">{fixed_markers}</g>'
        f'</svg>'
    )

    # ── Side stats ─────────────────────────────────────────────────
    # Trajectory: compare the average of the first half vs second half of
    # the window. More stable than first-week-vs-last-week with noisy data.
    half = max(1, n // 2)
    avg_first = sum(w["fixed"] for w in weeks[:half]) / half
    avg_last  = sum(w["fixed"] for w in weeks[-half:]) / half
    if avg_first > 0:
        delta_pct = int(round((avg_last - avg_first) / avg_first * 100))
    else:
        delta_pct = 100 if avg_last > 0 else 0
    up = delta_pct >= 0
    delta_html = (
        f'<span class="delta {"up" if up else "down"}">'
        f'{"▲" if up else "▼"}</span>'
    )

    # Caught growth for the supporting copy
    avg_caught_first = sum(w["caught"] for w in weeks[:half]) / half
    avg_caught_last  = sum(w["caught"] for w in weeks[-half:]) / half
    caught_delta_pct = (
        int(round((avg_caught_last - avg_caught_first) / avg_caught_first * 100))
        if avg_caught_first > 0 else 0
    )

    direction_word = "rose" if up else "fell"
    # Within 5pp we don't crown a winner — the lines are tracking each other.
    if avg_caught_first > 0:
        gap = delta_pct - caught_delta_pct
        if abs(gap) <= 5:
            comparison = (
                f"&mdash; caught grew {abs(caught_delta_pct)}% over the same window. "
                f"Acceptance is keeping pace with what Qodo surfaces."
            )
        elif gap > 0:
            comparison = (
                f"&mdash; while caught {'grew' if caught_delta_pct >= 0 else 'fell'} "
                f"{abs(caught_delta_pct)}%. Authors are acting on a larger share of what Qodo flags."
            )
        else:
            comparison = (
                f"&mdash; while caught {'grew' if caught_delta_pct >= 0 else 'fell'} "
                f"{abs(caught_delta_pct)}%. Caught grew faster than fixed &mdash; acceptance is the bottleneck."
            )
    else:
        comparison = ""

    trajectory_card = (
        f'<div class="trend-stat">'
        f'<div class="trend-stat-label">Trajectory &middot; fixes per week</div>'
        f'<div class="trend-stat-val">{"+" if up else ""}{delta_pct}%{delta_html}</div>'
        f'<div class="trend-stat-sub">'
        f'Findings <b>fixed per week</b> {direction_word} from '
        f'<b>~{int(round(avg_first))}</b> to <b>~{int(round(avg_last))}</b> '
        f'{comparison}'
        f'</div>'
        f'</div>'
    )

    # Best week
    best = max(weeks, key=lambda w: w["fixed"])
    best_label = _format_week_label(best["week_start"], fmt="%b %-d")
    best_card = (
        f'<div class="trend-stat">'
        f'<div class="trend-stat-label">Best week</div>'
        f'<div class="trend-stat-val">{_h(best_label)}</div>'
        f'<div class="trend-stat-sub">'
        f'<b>{_fmt_int(best["caught"])} caught</b> &middot; '
        f'<b>{_fmt_int(best["fixed"])} fixed</b> &mdash; '
        f'the highest single-week impact in the window.'
        f'</div>'
        f'</div>'
    )

    return (
        f'<section class="r-section" id="sec-trend">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Trend</div>'
        f'<div class="r-section-title">Findings caught &amp; fixed, week over week</div>'
        f'<div class="r-section-deck">'
        f'The snapshot above proves Qodo is finding issues. This view answers the harder question: '
        f'<b>is the team\'s engagement growing?</b>'
        f'</div>'
        f'</div></div>'
        f'<div class="trend-wrap">'
        f'<div class="trend-chart">'
        f'<div class="trend-legend">'
        f'<span><span class="sw caught"></span><b>Findings caught</b></span>'
        f'<span><span class="sw fixed"></span><b>Findings fixed before merge</b></span>'
        f'</div>'
        f'{svg}'
        f'</div>'
        f'<div class="trend-side">{trajectory_card}{best_card}</div>'
        f'</div>'
        f'</section>'
    )


def _section_funnel(agg: ReportData) -> str:
    """Prototype 02 — adoption funnel from merged PR to high-impact fix."""
    s1 = agg.merged_prs_total
    s2 = agg.prs_with_qodo
    s3 = agg.prs_with_findings
    s4 = agg.prs_with_any_fix
    s5 = agg.prs_with_spotlight_fix

    if s1 == 0:
        return ""

    def _pct(n: int, d: int) -> float:
        return (100.0 * n / d) if d > 0 else 0.0

    s2_pct = _pct(s2, s1)            # of merged
    s3_pct_of_rev = _pct(s3, s2)     # of reviewed
    s4_pct_of_find = _pct(s4, s3)    # of PRs with findings
    s5_pct_of_merged = _pct(s5, s1)  # of merged

    # Bar widths are all relative to the baseline (Step 1)
    def _w(n: int) -> float:
        return _pct(n, s1)

    spotlight_count = len(agg.spotlight_issues)
    spotlight_per_pr = (spotlight_count / s1) if s1 else 0.0
    one_in = f"1 in {s1 / s5:.1f}" if s5 > 0 else "&mdash;"

    rows_html = (
        f'<div class="funnel-row">'
        f'<div class="funnel-name"><span class="step">Step 1</span>Merged PRs in window</div>'
        f'<div class="funnel-bar muted"><i style="width:100%">{_fmt_int(s1)}</i></div>'
        f'<div class="funnel-val">{_fmt_int(s1)}<span class="pct">baseline</span></div>'
        f'</div>'
        f'<div class="funnel-row">'
        f'<div class="funnel-name"><span class="step">Step 2</span>Reviewed by Qodo</div>'
        f'<div class="funnel-bar"><i style="width:{_w(s2):.1f}%">{_fmt_int(s2)}</i></div>'
        f'<div class="funnel-val">{_fmt_int(s2)}<span class="pct">{s2_pct:.1f}% of merged</span></div>'
        f'</div>'
        f'<div class="funnel-row">'
        f'<div class="funnel-name"><span class="step">Step 3</span>Qodo found &ge;1 finding</div>'
        f'<div class="funnel-bar"><i style="width:{_w(s3):.1f}%">{_fmt_int(s3)}</i></div>'
        f'<div class="funnel-val">{_fmt_int(s3)}<span class="pct">{s3_pct_of_rev:.1f}% of reviewed</span></div>'
        f'</div>'
        f'<div class="funnel-row">'
        f'<div class="funnel-name"><span class="step">Step 4</span>Author fixed &ge;1 before merge</div>'
        f'<div class="funnel-bar gold"><i style="width:{_w(s4):.1f}%">{_fmt_int(s4)}</i></div>'
        f'<div class="funnel-val">{_fmt_int(s4)}<span class="pct">{s4_pct_of_find:.1f}% of PRs with findings</span></div>'
        f'</div>'
        f'<div class="funnel-row">'
        f'<div class="funnel-name"><span class="step">Step 5</span>High-impact fix landed</div>'
        f'<div class="funnel-bar gold"><i style="width:{_w(s5):.1f}%">{_fmt_int(s5)}</i></div>'
        f'<div class="funnel-val">{_fmt_int(s5)}<span class="pct">{s5_pct_of_merged:.1f}% of merged PRs</span></div>'
        f'</div>'
    )

    foot = (
        f'<div class="funnel-foot">'
        f'<div class="funnel-foot-cell gold">'
        f'<b>{one_in}</b>'
        f'merged PRs had a Qodo&#8209;flagged security or correctness issue caught and fixed before merge.'
        f'</div>'
        f'<div class="funnel-foot-cell">'
        f'<b>{s4_pct_of_find:.0f}%</b>'
        f'of authors who received a finding acted on at least one of them &mdash; a strong adoption signal.'
        f'</div>'
        f'<div class="funnel-foot-cell">'
        f'<b>{_fmt_int(spotlight_count)} spotlight</b>'
        f'issues implemented (security / correctness). Average '
        f'<b style="display:inline;font-size:inherit;color:var(--fg-default);font-weight:600">{spotlight_per_pr:.2f}</b> '
        f'high-impact fixes per merged PR.'
        f'</div>'
        f'</div>'
    )

    return (
        f'<section class="r-section" id="sec-funnel">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Adoption funnel</div>'
        f'<div class="r-section-title">From merged PR to high-impact fix</div>'
        f'<div class="r-section-deck">'
        f'Conditional cuts at each step &mdash; each row is the share of the prior. '
        f'The bottom two rows are golden: PRs where Qodo measurably prevented something bad from shipping.'
        f'</div>'
        f'</div></div>'
        f'<div class="funnel">{rows_html}</div>'
        f'{foot}'
        f'</section>'
    )


def _section_hours_saved(agg: ReportData, span_days: int,
                          since: "date", until: "date") -> str:
    """Prototype 03 — senior-engineer hours offloaded (interactive).

    Emits the full LOC-based model as a tunable mini-app: viewers can toggle
    Thorough / Moderate / Quick pace, the outlier trim, offload %, rate, and
    project to a different org size. All state lives in the embedded JS;
    no server round-trip required.
    """
    if (agg.total_loc <= 0 or agg.unique_developers == 0 or span_days <= 0
            or not agg.loc_trim_variants):
        return ""

    # Friendly window label, e.g. "Mar 17 – May 16, 2026"
    try:
        window_label = (since.strftime("%b %-d") + " &ndash; "
                        + until.strftime("%b %-d, %Y"))
    except ValueError:                                     # Windows %-d quirk
        window_label = (since.strftime("%b %d").replace(" 0", " ")
                        + " &ndash; "
                        + until.strftime("%b %d, %Y").replace(" 0", " "))

    # Data the JS needs. Kept small — it only carries the per-trim totals and
    # the contextual labels; everything else is computed live in the browser.
    data_blob = json.dumps({
        "WINDOW_DAYS":  span_days,
        "WINDOW_LABEL": window_label,
        "EVAL_DEVS":    agg.unique_developers,
        "TOTALS":       agg.loc_trim_variants,
    })

    return (
        '<section class="r-section" id="hs-root">'
        '<div class="r-section-head"><div>'
        '<div class="r-section-eyebrow">Hours Saved</div>'
        '<div class="r-section-title">Senior&#8209;engineer hours offloaded</div>'
        '<div class="r-section-deck">'
        'How much human review labor Qodo absorbed across the window. '
        'Tunable — toggle your team&rsquo;s pace, the outlier trim, or scale '
        'to a larger org to see how the claim changes.'
        '</div>'
        '</div></div>'

        '<div class="saved-grid">'
        '<div class="observed-col">'

        # ── hero ─────────────────────────────────────────────────
        '<div class="saved-hero">'
        '<div class="saved-eyebrow" id="hs-observedEyebrow">Observed</div>'
        '<div class="saved-big" style="margin-top:2px">'
        '<span class="n" id="hs-heroHours">&mdash;</span>'
        '<span class="u" style="display:inline-flex;flex-direction:column;'
        'line-height:1.15;gap:2px;align-self:flex-end;padding-bottom:6px">'
        '<span>senior engineer hours</span>'
        '<span style="font-size:11px;color:var(--fg-muted);font-weight:600;'
        'letter-spacing:.06em;text-transform:uppercase">offloaded by Qodo</span>'
        '</span>'
        '</div>'
        '<p class="saved-lede" id="hs-ledeText"></p>'
        '</div>'

        # ── breakdown cards ──────────────────────────────────────
        '<div class="saved-breakdown">'

        # Team's review pace card (segmented control)
        '<div class="sbr-row big">'
        '<span class="sbr-tag lg norm">Team&rsquo;s review pace</span>'
        '<div class="sbr-mid">'
        '<div class="top"><b id="hs-effortLabel"></b></div>'
        '<span class="sub" id="hs-effortCite" style="color:var(--fg-subtle)"></span>'
        '<span class="sub" style="margin-top:10px;display:block;color:var(--fg-muted)">'
        '<b style="color:var(--p-200);font-weight:600">'
        'Qodo always performs a thorough review.</b> This pace is '
        '<em>your team&rsquo;s</em> counterfactual &mdash; how thoroughly '
        'you&rsquo;d have reviewed the same code in&#8209;house.'
        '</span>'
        '</div>'
        '<div class="sbr-val" style="font-size:12px;color:var(--fg-muted);font-weight:500">'
        '<div class="hs-seg" id="hs-effortSeg" style="margin-top:0">'
        '<button type="button" data-effort="thorough">Thorough</button>'
        '<button type="button" data-effort="moderate" class="on">Moderate</button>'
        '<button type="button" data-effort="quick">Quick</button>'
        '</div>'
        '</div>'
        '</div>'

        # Outlier trim card
        '<div class="sbr-row big">'
        '<span class="sbr-tag lg warn">Outlier trim</span>'
        '<div class="sbr-mid">'
        '<div class="top"><b id="hs-trimLabel"></b></div>'
        '<span class="sub" id="hs-trimStats" style="margin-top:6px;display:block;'
        'font-variant-numeric:tabular-nums"></span>'
        '<span class="sub" style="margin-top:8px;display:block">'
        'Some PRs are huge but require little review &mdash; vendored libraries, '
        'multimedia &amp; static assets, formatter sweeps, lockfile &amp; '
        'snapshot updates. Trim them so the headline stays defensible.'
        '</span>'
        '</div>'
        '<div class="sbr-val" style="font-size:12px;color:var(--fg-muted);font-weight:500">'
        '<div class="hs-seg" id="hs-trimSeg" style="margin-top:0">'
        '<button type="button" data-trim="none">All</button>'
        '<button type="button" data-trim="p99">Top&nbsp;1%</button>'
        '<button type="button" data-trim="p95" class="on">Top&nbsp;5%</button>'
        '</div>'
        '</div>'
        '</div>'

        # Defensibility row
        '<div class="sbr-row bonus">'
        '<span class="sbr-tag">Defensibility</span>'
        '<div class="sbr-mid">'
        '<div class="top"><b>Skeptics get a heavier number; honest teams '
        'get a smaller one.</b></div>'
        '<span class="sub">A team that already does thorough review gets the '
        'biggest savings; a team that skims still gets the thorough review '
        'they weren&rsquo;t doing. Either way, Qodo&rsquo;s output is the same.'
        '</span>'
        '</div>'
        '<div class="sbr-val">&mdash;</div>'
        '</div>'

        '<div class="sbr-row big" style="display:none">'
        '<span class="sbr-tag lg norm">LOC total</span>'
        '<div class="sbr-mid"><div class="top">'
        '<b id="hs-locTotalHrs">&mdash;</b></div></div>'
        '<div class="sbr-val">&mdash;</div>'
        '</div>'

        '</div>'  # /saved-breakdown

        # ── observed totals bar ─────────────────────────────────
        '<div class="sbr-total" aria-live="polite">'
        '<span class="lbl">Observed</span>'
        '<div class="mid">'
        '<b id="hs-totalHrsLine">&mdash;</b> &middot; '
        '<b id="hs-totalWeeksLine">&mdash;</b> &middot; '
        'at <b id="hs-totalRateLine">&mdash;</b>'
        '</div>'
        '<div class="v"><span id="hs-totalDollars">&mdash;</span></div>'
        '</div>'

        # ── formula ──────────────────────────────────────────────
        '<div class="saved-formula" id="hs-formula"></div>'

        '</div>'  # /observed-col

        # ── Sustain & Scale panel ───────────────────────────────
        '<div class="scale-col">'
        '<div class="scale-eyebrow"><span class="live-dot"></span>'
        'Sustain &amp; Scale</div>'

        '<div class="scale-headline">'
        '<div class="scale-amount">'
        '<span class="v" id="hs-scaleAmount">&mdash;</span>'
        '<span class="yr">/ yr</span>'
        '</div>'
        '<div class="scale-sub" id="hs-scaleSub">&mdash;</div>'
        '</div>'

        '<p class="scale-context" id="hs-scaleContext"></p>'

        '<div class="hs-scale-card">'
        '<div class="hs-scale-card-head">'
        '<span class="hs-scale-card-label">Project to N engineers</span>'
        '<span class="hs-scale-card-val">'
        '<span id="hs-scaleDevsVal">&mdash;</span>'
        '<span class="u">devs</span>'
        '</span>'
        '</div>'
        '<div class="hs-scale-card-range">'
        f'<input type="range" id="hs-totalDevsRange" min="1" max="5000" step="1" '
        f'value="{agg.unique_developers}" '
        'aria-label="Number of engineers in your target org">'
        '</div>'
        '<div class="hs-scale-card-foot"><span>1</span><span>5,000</span></div>'
        '</div>'

        '<div class="hs-scale-card">'
        '<div class="hs-scale-card-head">'
        '<span class="hs-scale-card-label">Fully-loaded engineer cost</span>'
        '<span class="hs-scale-card-val">'
        '$<span id="hs-rateRangeVal">180,000</span>'
        '<span class="u">/yr</span>'
        '</span>'
        '</div>'
        '<div class="hs-scale-card-range">'
        '<input type="range" id="hs-rateRange" min="60000" max="500000" step="5000" '
        'value="180000" aria-label="Fully-loaded annual cost per engineer">'
        '</div>'
        '<div class="hs-scale-card-foot"><span>$60k</span><span>$500k</span></div>'
        '</div>'

        '<div class="scale-3yr">'
        '<span class="lbl">3-year value</span>'
        '<span class="val" id="hs-scale3yrVal">&mdash;</span>'
        '</div>'

        '</div>'  # /scale-col
        '</div>'  # /saved-grid

        # ── data blob + JS bundle ───────────────────────────────
        f'<script type="application/json" id="hs-data">{data_blob}</script>'
        f'<script>{_HOURS_SAVED_JS}</script>'
        '</section>'
    )



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

    # Embedded leaderboard: Top PRs by spotlight catches.
    leaderboard = _spotlight_leaderboard_html(agg.spotlight_pr_leaderboard)

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
        f'<section class="r-section" id="sec-spotlight">'
        f'<div class="r-section-head"><div>'
        f'<div class="r-section-eyebrow">Spotlight</div>'
        f'<div class="r-section-title">High-impact findings caught &amp; fixed</div>'
        f'<div class="r-section-deck">Action-required findings flagged as Security or Correctness — '
        f'the findings most likely to have caused an incident if they reached production.</div>'
        f'</div></div>'
        f'{summary}'
        f'<div class="spot-grid">{"".join(cards)}</div>'
        f'{footer}'
        f'{leaderboard}'
        f'</section>'
    )


def _spotlight_leaderboard_html(leaderboard: list) -> str:
    """Render the embedded Top-PRs-by-spotlight-catches leaderboard.

    Lives inside the Spotlight section. Ranks PRs by spotlight findings caught
    (security + correctness), tie-broken by Total Implemented. Avoids the
    “big PRs win” failure mode of ranking by raw fix count.
    """
    if not leaderboard:
        return ""

    rows_html = []
    for r in leaderboard:
        url = r.get("PR URL", "")
        safe_url = url if url.startswith(("https://", "http://")) else ""
        pr_num = _h(str(r.get("PR #", "")))
        pr_link = (
            f'<a href="{_h(safe_url)}">#{pr_num}</a>' if safe_url else f'#{pr_num}'
        )
        creator = r.get("PR Creator", "") or "—"
        initials = _h((creator[:2] or "--").upper())
        sug = r.get("Total Suggestions", 0)
        imp = r.get("Total Implemented", 0)
        rate = (imp / sug * 100.0) if sug else 0.0
        rate_bar = max(0.0, min(100.0, rate))
        rate_label = f'{rate:.1f}%' if sug else '—'
        rows_html.append(
            f'<tr>'
            f'<td class="repo">{_h(r.get("Repo Name", ""))}</td>'
            f'<td class="pr-link">{pr_link}</td>'
            f'<td><div class="creator">'
            f'<span class="dev-avatar" style="width:18px;height:18px;font-size:9px">{initials}</span>'
            f'{_h(creator)}</div></td>'
            f'<td class="num spot">{r["spot_count"]}</td>'
            f'<td class="num">{imp} / {sug}</td>'
            f'<td><span class="mini-bar"><i style="width:{rate_bar:.1f}%"></i></span> {rate_label}</td>'
            f'</tr>'
        )

    return (
        f'<div class="spot-divider">'
        f'<span class="label">Top PRs by spotlight catches</span>'
        f'<span class="line"></span>'
        f'</div>'
        f'<div class="spot-leaderboard-deck">'
        f'The PRs where Qodo caught the most <b>security &amp; correctness</b> findings during review. '
        f'Ranked by spotlight findings caught, not raw fix count — this avoids rewarding huge churn PRs '
        f'where Qodo just had more surface area.'
        f'</div>'
        f'<table class="top-prs">'
        f'<thead><tr>'
        f'<th>Repository</th><th>PR</th><th>Author</th>'
        f'<th class="num">Spotlight caught</th>'
        f'<th class="num">All findings</th>'
        f'<th>PR implementation rate</th>'
        f'</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        f'</table>'
    )


def _section_velocity(agg: ReportData) -> str:
    """Prototype 04 — Time-to-first-feedback density distribution.

    Shows Qodo's tight curve vs the human reviewer's broader, long-tailed
    curve on a log-scale axis (1m → 3d). Side cards quote the p90s and
    the sole-reviewer share (PRs merged with no human comment).
    """
    if agg.velocity_qodo_median_min is None and agg.velocity_human_median_min is None:
        return ""

    q = agg.velocity_qodo_median_min
    h = agg.velocity_human_median_min
    q_time = _format_duration(q)
    h_time = _format_duration(h)
    q_p90 = _format_duration(agg.qodo_p90_min)
    h_p90 = _format_duration(agg.human_p90_min)

    # ── chart geometry (mirrors the prototype's viewBox) ──
    VB_W, VB_H = 700, 220
    X0, X1 = 40, 690                # axis line endpoints
    Y_BASE, Y_TOP = 195, 20
    TICK_LO, TICK_HI = _DIST_LO_MIN, _DIST_HI_MIN    # 1m, 3d

    def _x(minutes: float) -> float:
        if minutes is None or minutes <= 0:
            return float(X0)
        lo, hi = math.log10(TICK_LO), math.log10(TICK_HI)
        lv = math.log10(max(minutes, TICK_LO))
        if lv <= lo:
            return float(X0)
        if lv >= hi:
            return float(X1)
        return X0 + (lv - lo) / (hi - lo) * (X1 - X0)

    def _bins_path(bins, peak, close=True) -> str:
        if not bins or peak <= 0:
            return ""
        n = len(bins)
        usable_h = Y_BASE - Y_TOP
        pts = []
        for i, b in enumerate(bins):
            # centre each bin between TICK_LO and TICK_HI on the log axis
            frac = (i + 0.5) / n
            lo, hi = math.log10(TICK_LO), math.log10(TICK_HI)
            x = X0 + ((lo + frac * (hi - lo)) - lo) / (hi - lo) * (X1 - X0)
            y = Y_BASE - (b / peak) * usable_h
            pts.append(f"{x:.1f},{y:.1f}")
        body = " L".join(pts)
        if close:
            return f"M{X0},{Y_BASE} L{body} L{X1},{Y_BASE} Z"
        return f"M{X0},{Y_BASE} L{body} L{X1},{Y_BASE}"

    # Independent peaks so the smaller series isn't drowned out.
    q_bins = agg.qodo_density_bins
    h_bins = agg.human_density_bins
    q_peak = max(q_bins) if q_bins else 0
    h_peak = max(h_bins) if h_bins else 0
    q_fill = _bins_path(q_bins, q_peak, close=True)
    q_stroke = _bins_path(q_bins, q_peak, close=False)
    h_fill = _bins_path(h_bins, h_peak, close=True)
    h_stroke = _bins_path(h_bins, h_peak, close=False)

    # Tick labels along the bottom: 1m, 5m, 30m, 2h, 12h, 3d.
    ticks = [(1, "1m"), (5, "5m"), (30, "30m"),
             (120, "2h"), (720, "12h"), (4320, "3d")]
    tick_text = "".join(
        f'<text x="{_x(m):.1f}" y="212" text-anchor="middle">{lbl}</text>'
        for m, lbl in ticks
    )
    tick_grid = "".join(
        f'<line x1="{_x(m):.1f}" y1="{Y_TOP}" x2="{_x(m):.1f}" y2="{Y_BASE}"></line>'
        for m, _lbl in ticks[1:-1]   # skip the bookends
    )

    # Long-wait zone — PRs that wait > 1 day.
    long_wait_x = _x(1440)   # 24h on the axis
    long_wait_w = X1 - long_wait_x
    long_wait_mid = long_wait_x + long_wait_w / 2

    # Median markers (only when defined).
    median_lines = []
    if q is not None:
        qx = _x(q)
        median_lines.append(
            f'<line x1="{qx:.1f}" y1="{Y_TOP}" x2="{qx:.1f}" y2="{Y_BASE}" '
            f'stroke="#A8A1FD" stroke-width="1.5" stroke-dasharray="3 3"></line>'
            f'<text x="{qx + 4:.1f}" y="18" font-family="Inter" font-size="10" '
            f'font-weight="600" fill="#A8A1FD">Qodo p50 &middot; {q_time}</text>'
        )
    if h is not None:
        hx = _x(h)
        # Nudge the human label up-left if it would crowd the Qodo label.
        anchor = "start"
        text_x = hx + 4
        if q is not None and abs(hx - _x(q)) < 110:
            anchor = "end"
            text_x = hx - 4
        median_lines.append(
            f'<line x1="{hx:.1f}" y1="{Y_TOP}" x2="{hx:.1f}" y2="{Y_BASE}" '
            f'stroke="#A09CB6" stroke-width="1.5" stroke-dasharray="3 3"></line>'
            f'<text x="{text_x:.1f}" y="18" font-family="Inter" font-size="10" '
            f'font-weight="600" fill="#DFDFDF" text-anchor="{anchor}">'
            f'Human p50 &middot; {h_time}</text>'
        )
    median_html = "".join(median_lines)

    # ── side cards ──
    side_cards = []
    if agg.qodo_p90_min is not None:
        side_cards.append(
            '<div class="dist-card">'
            '<div class="dist-card-label">Qodo p90</div>'
            f'<div class="dist-card-val">{q_p90}</div>'
            '<div class="dist-card-sub">'
            f'90% of Qodo-reviewed PRs received initial feedback within '
            f'<b>{q_p90}</b> of opening.'
            '</div>'
            '</div>'
        )
    if agg.human_p90_min is not None and agg.human_sample_count > 0:
        side_cards.append(
            '<div class="dist-card">'
            '<div class="dist-card-label">Human p90</div>'
            f'<div class="dist-card-val">{h_p90}</div>'
            '<div class="dist-card-sub">'
            f'Of the PRs that received any human comment, the slowest 10% '
            f'waited over <b>{h_p90}</b>.'
            '</div>'
            '</div>'
        )
    if agg.pct_no_human_comment > 0 and agg.sole_reviewer_count > 0:
        # Descriptive copy: count + share, no editorialising about whether
        # the number is large or small.
        side_cards.append(
            '<div class="dist-card highlight">'
            '<div class="dist-card-label">No human comment</div>'
            f'<div class="dist-card-val">{agg.pct_no_human_comment:.0f}'
            '<span class="pct">%</span></div>'
            '<div class="dist-card-sub">'
            f'<b>{_fmt_int(agg.sole_reviewer_count)} of {_fmt_int(agg.prs_with_qodo)}</b> '
            'Qodo-reviewed PRs merged without any human reviewer comment &mdash; '
            'Qodo provided the sole feedback before merge.'
            '</div>'
            '</div>'
        )
    side_html = "".join(side_cards)

    # ── legend ──
    legend = (
        '<div class="dist-legend">'
        '<div class="left">'
        f'<span><span class="sw qodo"></span><b>Qodo</b> &middot; n={_fmt_int(agg.qodo_sample_count)}</span>'
        f'<span><span class="sw human"></span><b>First human reviewer</b> &middot; n={_fmt_int(agg.human_sample_count)}</span>'
        '</div>'
        '<span class="axis">log-scale &middot; time to first comment</span>'
        '</div>'
    )

    # ── deck copy reacts to whether we have a human baseline ──
    # Stay descriptive — let the number speak for itself rather than
    # editorialising about how striking it is.
    if h is not None and q is not None:
        deck = (
            f'Density plot shows the shape of the wait. Qodo posts in '
            f'<b>{q_time}</b> (median); the first human takes <b>{h_time}</b>.'
        )
        if agg.pct_no_human_comment > 0:
            deck += (
                f' Across the window, <b>{agg.pct_no_human_comment:.0f}% of '
                f'Qodo-reviewed PRs merged with no human comment</b>.'
            )
    else:
        deck = (
            f'Density plot shows the shape of the wait. Qodo posts initial '
            f'feedback in <b>{q_time}</b> (median) from when a PR opens.'
        )

    chart_svg = (
        f'<svg class="dist-svg" viewBox="0 0 {VB_W} {VB_H}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        # baseline axis
        f'<line x1="{X0}" y1="{Y_BASE}" x2="{X1}" y2="{Y_BASE}" '
        f'stroke="#2C2C2C" stroke-width="1"></line>'
        # gridlines + ticks
        f'<g stroke="rgba(255,255,255,0.04)" stroke-width="1">{tick_grid}</g>'
        f'<g font-family="IBM Plex Mono" font-size="9" fill="#A09CB6">{tick_text}</g>'
        # long-wait zone
        f'<rect x="{long_wait_x:.1f}" y="{Y_TOP}" width="{long_wait_w:.1f}" '
        f'height="{Y_BASE - Y_TOP}" fill="rgba(229,72,77,0.06)"></rect>'
        f'<text x="{long_wait_mid:.1f}" y="35" font-family="Inter" font-size="10" '
        f'font-weight="600" fill="#E5484D" text-anchor="middle">long wait zone</text>'
        f'<text x="{long_wait_mid:.1f}" y="48" font-family="Inter" font-size="9" '
        f'fill="#A09CB6" text-anchor="middle">PRs sitting &gt; 1 day</text>'
        # gradient defs
        '<defs>'
          '<linearGradient id="qodoFill" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0%" stop-color="#7968FA" stop-opacity="0.45"></stop>'
            '<stop offset="100%" stop-color="#7968FA" stop-opacity="0"></stop>'
          '</linearGradient>'
          '<linearGradient id="humanFill" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0%" stop-color="#6E6E6E" stop-opacity="0.30"></stop>'
            '<stop offset="100%" stop-color="#6E6E6E" stop-opacity="0"></stop>'
          '</linearGradient>'
        '</defs>'
        # human curve drawn first so Qodo sits on top
        f'<path d="{h_fill}" fill="url(#humanFill)"></path>'
        f'<path d="{h_stroke}" stroke="#A09CB6" stroke-width="2" fill="none" stroke-linejoin="round"></path>'
        f'<path d="{q_fill}" fill="url(#qodoFill)"></path>'
        f'<path d="{q_stroke}" stroke="#7968FA" stroke-width="2" fill="none" stroke-linejoin="round"></path>'
        # median markers
        f'{median_html}'
        f'</svg>'
    )

    chart_html = (
        '<div class="dist-chart">'
        f'{legend}'
        f'{chart_svg}'
        '</div>'
    )

    body = (
        f'<div class="dist-wrap">{chart_html}'
        f'<div class="dist-side">{side_html}</div></div>'
        if side_html else chart_html
    )

    return (
        '<section class="r-section" id="sec-velocity">'
        '<div class="r-section-head"><div>'
        '<div class="r-section-eyebrow">Velocity</div>'
        '<div class="r-section-title">First feedback on a PR &mdash; the gap, not just the medians</div>'
        f'<div class="r-section-deck">{deck}</div>'
        '</div></div>'
        f'{body}'
        '</section>'
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


def _section_adoption_matrix(agg: ReportData, span_days: int) -> str:
    """Prototype 05 — Developer adoption matrix.

    Scatter of every author by action-required findings received (X, log
    scale) vs AR implementation rate (Y). Four quadrants map to four
    personas: Power users, Coach, Curious / new, and Low usage.

    Thresholds scale with the window length: 25 AR findings is the anchor
    for a 60-day window; we recompute for any other ``span_days``.
    """
    if not agg.adoption_devs:
        return ""

    # ── Window-scaled thresholds ──
    days = max(1, span_days or 60)
    FINDINGS_CUT = max(5, round(25 * days / 60))
    NOTABLE_FCUT = max(3, round(8 * days / 60))
    RATE_CUT      = 30
    NOTABLE_RCUT  = 15

    # ── Derive AR rate / quad per dev — only devs with AR findings ──
    source = [d for d in agg.adoption_devs if d["actReqSug"] > 0]
    devs = []
    for d in source:
        rate = round(100 * d["actReqImp"] / d["actReqSug"], 1) if d["actReqSug"] else 0.0
        hv   = d["actReqSug"] >= FINDINGS_CUT
        actRate = rate  # rate is already AR rate; kept for tooltip parity
        hr = rate >= RATE_CUT
        quad = "power" if hv and hr else "coach" if hv else "curious" if hr else "absent"
        devs.append({**d, "rate": rate, "actRate": actRate, "quad": quad})

    if not devs:
        return '<p style="padding:1rem;color:var(--fg-muted)">No action-required findings in this window.</p>'

    # ── Org-wide action-required rate (the hero topline) ──
    tot_ar_sug = sum(d["actReqSug"] for d in devs)
    tot_ar_imp = sum(d["actReqImp"] for d in devs)
    ar_rate = round(100 * tot_ar_imp / tot_ar_sug, 1) if tot_ar_sug else 0.0

    # ── Quadrant groupings ──
    by_quad = {k: [d for d in devs if d["quad"] == k]
               for k in ("power", "coach", "curious", "absent")}
    _vol = lambda d: d["actReqSug"]
    for k in by_quad:
        by_quad[k].sort(key=_vol, reverse=True)

    notable = sorted(
        [d for d in devs if _vol(d) >= NOTABLE_FCUT and d["rate"] < NOTABLE_RCUT],
        key=_vol, reverse=True,
    )

    # ── Hero stat cards (3 cards, conditional copy) ──
    stats_html = []

    # Card 1: org-wide action-required rate (always shown)
    stats_html.append(
        f'<div class="p05-stat">'
        f'<div class="v mint">{ar_rate:.1f}%</div>'
        f'<div class="lbl"><b>{_fmt_int(tot_ar_imp)} of {_fmt_int(tot_ar_sug)}</b> '
        f'action&#8209;required findings implemented before merge &mdash; Qodo\'s '
        f'must-fix grade. The org-wide acceptance signal.</div>'
        f'</div>'
    )

    # Card 2: coach quadrant, or fallback to power-user cohort
    if by_quad["coach"]:
        coach_devs = by_quad["coach"]
        named = ", ".join(f'<b>{_h(d["user"])}</b>' for d in coach_devs[:3])
        more = (f', plus {len(coach_devs) - 3} more'
                if len(coach_devs) > 3 else "")
        ceiling = math.ceil(max(d["rate"] for d in coach_devs))
        stats_html.append(
            f'<div class="p05-stat">'
            f'<div class="v warn">{len(coach_devs)} dev{"s" if len(coach_devs) != 1 else ""}</div>'
            f'<div class="lbl">High&#8209;volume, low&#8209;adoption: {named}{more}. '
            f'Receiving findings but accepting under {ceiling}%.</div>'
            f'</div>'
        )
    elif by_quad["power"]:
        pc = len(by_quad["power"])
        p_findings = sum(_vol(d) for d in by_quad["power"])
        tot_findings = sum(_vol(d) for d in devs)
        p_share = round(100 * p_findings / tot_findings) if tot_findings else 0
        stats_html.append(
            f'<div class="p05-stat">'
            f'<div class="v mint">{pc} dev{"s" if pc != 1 else ""}</div>'
            f'<div class="lbl"><b>Power users</b> &mdash; high volume, high acceptance. '
            f'Together they account for <b>{p_share}%</b> of all findings the org received.</div>'
            f'</div>'
        )
    else:
        stats_html.append(
            f'<div class="p05-stat">'
            f'<div class="v">{len(devs)}</div>'
            f'<div class="lbl"><b>Active authors</b> received at least one Qodo finding '
            f'in the window. Distribution is flat across the team.</div>'
            f'</div>'
        )

    # Card 3: low-usage outliers, or fallback to curious / concentration
    if notable:
        listed = " and ".join(
            f'<b>{_h(d["user"])}</b> ({_vol(d)} findings, {d["rate"]:.0f}% impl)'
            for d in notable[:2]
        )
        more = f', plus {len(notable) - 2} more' if len(notable) > 2 else ""
        stats_html.append(
            f'<div class="p05-stat">'
            f'<div class="v danger">{len(notable)} dev{"s" if len(notable) != 1 else ""}</div>'
            f'<div class="lbl"><b>Lowest usage:</b> {listed}{more}. Receiving real '
            f'volume (&ge;{NOTABLE_FCUT} findings) but implementing under '
            f'{NOTABLE_RCUT}% &mdash; worth a check-in to confirm Qodo is configured '
            f'the way they need.</div>'
            f'</div>'
        )
    elif by_quad["curious"]:
        cc = len(by_quad["curious"])
        stats_html.append(
            f'<div class="p05-stat">'
            f'<div class="v">{cc}</div>'
            f'<div class="lbl"><b>{cc} curious contributor{"s" if cc != 1 else ""}</b> '
            f'&mdash; light volume but high acceptance. Recent onboards or occasional '
            f'PR authors. No active concern in the data.</div>'
            f'</div>'
        )
    else:
        top3 = devs[:3]
        tot_findings = sum(_vol(d) for d in devs)
        top3_sum = sum(_vol(d) for d in top3)
        top3_pct = round(100 * top3_sum / tot_findings) if tot_findings else 0
        stats_html.append(
            f'<div class="p05-stat">'
            f'<div class="v">{top3_pct}%</div>'
            f'<div class="lbl">Of all <b>{_fmt_int(tot_findings)}</b> findings, '
            f'<b>{top3_pct}%</b> went to the top 3 authors by volume.</div>'
            f'</div>'
        )

    # ── Scatter chart ──
    W, H = 1180, 600
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 30, 50, 60
    PW, PH = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    X_MIN, X_MAX = 1, max(200, max(_vol(d) for d in devs) * 1.1)

    def _x(v: float) -> float:
        lo, hi = math.log10(X_MIN), math.log10(X_MAX)
        return PAD_L + PW * (math.log10(max(v, X_MIN)) - lo) / (hi - lo)

    def _y(v: float) -> float:
        return PAD_T + PH * (1 - v / 100.0)

    pr_max = max(d["prs"] for d in devs) or 1
    def _r(prs: int) -> float:
        return 5 + math.sqrt(prs / pr_max) * 28

    cutX, cutY = _x(FINDINGS_CUT), _y(RATE_CUT)
    qc = {
        "power":   ("#06E4AE", "rgba(6,228,174,.18)",   "Power user"),
        "coach":   ("#F5B544", "rgba(245,181,68,.18)",  "Coach"),
        "curious": ("#A8A1FD", "rgba(168,161,253,.18)", "Curious / new"),
        "absent":  ("#E5484D", "rgba(229,72,77,.18)",   "Low usage"),
    }

    grid_x_vals = [1, 5, 10, 50, 100, 500]
    grid_y_vals = [0, 25, 50, 75, 100]

    # Build bubble + label markup, biggest first (so small ones land on top)
    pts = sorted(devs, key=_vol, reverse=True)
    bubbles_html = []
    for d in pts:
        stroke, fill, _label = qc[d["quad"]]
        px, py, r = _x(_vol(d)), _y(d["rate"]), _r(d["prs"])
        bubbles_html.append(
            f'<g class="p05-bubble" data-user="{_h(d["user"])}" '
            f'transform="translate({px:.1f} {py:.1f})">'
            f'<circle r="{r:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"></circle>'
            f'<circle r="2" fill="{stroke}"></circle>'
            f'</g>'
        )
        # Inline labels for the upper-volume bubbles.
        if _vol(d) >= max(5, FINDINGS_CUT // 2):
            label_y = py - r - 6
            bubbles_html.append(
                f'<text class="p05-bubble-label" x="{px:.1f}" y="{label_y:.1f}" '
                f'text-anchor="middle">{_h(d["user"])}</text>'
                f'<text class="p05-bubble-sub" x="{px:.1f}" y="{label_y + 11:.1f}" '
                f'text-anchor="middle" fill="{stroke}">{_vol(d)} \u00b7 {d["rate"]:.0f}%</text>'
            )

    grid_x_lines = "".join(
        f'<line x1="{_x(v):.1f}" y1="{PAD_T}" x2="{_x(v):.1f}" y2="{H - PAD_B}" '
        f'stroke="rgba(255,255,255,.04)" stroke-width="1"></line>'
        for v in grid_x_vals
    )
    grid_y_lines = "".join(
        f'<line x1="{PAD_L}" y1="{_y(v):.1f}" x2="{W - PAD_R}" y2="{_y(v):.1f}" '
        f'stroke="rgba(255,255,255,.04)" stroke-width="1"></line>'
        for v in grid_y_vals
    )
    x_ticks = "".join(
        f'<text x="{_x(v):.1f}" y="{H - PAD_B + 18}" text-anchor="middle" '
        f'font-family="IBM Plex Mono" font-size="10" fill="#A09CB6">{v}</text>'
        for v in grid_x_vals
    )
    y_ticks = "".join(
        f'<text x="{PAD_L - 10}" y="{_y(v) + 4:.1f}" text-anchor="end" '
        f'font-family="IBM Plex Mono" font-size="10" fill="#A09CB6">{v}%</text>'
        for v in grid_y_vals
    )

    svg = (
        f'<svg class="p05-scatter" id="p05-scatter" '
        f'viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">'
        # Quadrant tints
        f'<rect class="p05-quad-bg-curious" x="{PAD_L}" y="{PAD_T}" '
        f'width="{cutX - PAD_L:.1f}" height="{cutY - PAD_T:.1f}"></rect>'
        f'<rect class="p05-quad-bg-power" x="{cutX:.1f}" y="{PAD_T}" '
        f'width="{W - PAD_R - cutX:.1f}" height="{cutY - PAD_T:.1f}"></rect>'
        f'<rect class="p05-quad-bg-absent" x="{PAD_L}" y="{cutY:.1f}" '
        f'width="{cutX - PAD_L:.1f}" height="{H - PAD_B - cutY:.1f}"></rect>'
        f'<rect class="p05-quad-bg-coach" x="{cutX:.1f}" y="{cutY:.1f}" '
        f'width="{W - PAD_R - cutX:.1f}" height="{H - PAD_B - cutY:.1f}"></rect>'
        # Grid
        f'{grid_x_lines}{grid_y_lines}'
        # Thresholds
        f'<line x1="{cutX:.1f}" y1="{PAD_T}" x2="{cutX:.1f}" y2="{H - PAD_B}" '
        f'stroke="rgba(255,255,255,.22)" stroke-width="1" stroke-dasharray="4 5"></line>'
        f'<line x1="{PAD_L}" y1="{cutY:.1f}" x2="{W - PAD_R}" y2="{cutY:.1f}" '
        f'stroke="rgba(255,255,255,.22)" stroke-width="1" stroke-dasharray="4 5"></line>'
        # Axes
        f'<line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{H - PAD_B}" '
        f'stroke="#2C2C2C" stroke-width="1"></line>'
        f'<line x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - PAD_R}" y2="{H - PAD_B}" '
        f'stroke="#2C2C2C" stroke-width="1"></line>'
        f'{x_ticks}{y_ticks}'
        # Axis labels
        f'<text x="{PAD_L + PW/2:.1f}" y="{H - 12}" text-anchor="middle" '
        f'font-family="Inter" font-size="11" fill="#6E6E6E">Action-required findings &nbsp;(log scale)</text>'
        f'<text x="22" y="{PAD_T + PH/2:.1f}" text-anchor="middle" '
        f'font-family="Inter" font-size="11" fill="#6E6E6E" '
        f'transform="rotate(-90 22 {PAD_T + PH/2:.1f})">Implementation rate</text>'
        # Quadrant labels — positioned in the outermost corners so bubbles can't block them
        f'<text x="{W - PAD_R - 12:.1f}" y="{PAD_T + 22}" text-anchor="end" font-family="Inter" font-size="11" '
        f'font-weight="700" fill="rgba(6,228,174,.55)" letter-spacing=".06em">POWER USERS</text>'
        f'<text x="{PAD_L + 12:.1f}" y="{PAD_T + 22}" text-anchor="start" font-family="Inter" '
        f'font-size="11" font-weight="700" fill="rgba(168,161,253,.55)" letter-spacing=".06em">CURIOUS / NEW</text>'
        f'<text x="{W - PAD_R - 12:.1f}" y="{H - PAD_B - 14}" text-anchor="end" font-family="Inter" font-size="11" '
        f'font-weight="700" fill="rgba(245,181,68,.65)" letter-spacing=".06em">COACH</text>'
        f'<text x="{PAD_L + 12:.1f}" y="{H - PAD_B - 14}" text-anchor="start" font-family="Inter" '
        f'font-size="11" font-weight="700" fill="rgba(229,72,77,.55)" letter-spacing=".06em">LOW USAGE</text>'
        + "".join(bubbles_html) +
        f'</svg>'
    )

    legend = (
        '<div class="p05-legend">'
        '<div class="it"><span class="sw power"></span><b style="color:var(--fg-default)">Power</b> '
        f'&middot; &ge;{FINDINGS_CUT} / &ge;{RATE_CUT}%</div>'
        '<div class="it"><span class="sw coach"></span><b style="color:var(--fg-default)">Coach</b> '
        f'&middot; &ge;{FINDINGS_CUT} / &lt;{RATE_CUT}%</div>'
        '<div class="it"><span class="sw curious"></span><b style="color:var(--fg-default)">Curious</b> '
        f'&middot; &lt;{FINDINGS_CUT} / &ge;{RATE_CUT}%</div>'
        '<div class="it"><span class="sw absent"></span><b style="color:var(--fg-default)">Low usage</b> '
        f'&middot; &lt;{FINDINGS_CUT} / &lt;{RATE_CUT}%</div>'
        f'<div class="it"><span class="dash"></span>Quadrant threshold ({FINDINGS_CUT} / {RATE_CUT}%)</div>'
        '</div>'
    )

    chart_card = (
        '<div class="p05-chart-card">'
        f'<div class="p05-svg-wrap">{svg}<div class="p05-tip" id="p05-tip"></div></div>'
        f'{legend}'
        '</div>'
    )

    # ── Quadrant cards ──
    quad_meta = {
        "power": (
            "Power users", "Celebrate &amp; study", "DO",
            "<b>High volume, high acceptance.</b> Generating the most Qodo-reviewed code "
            "and implementing what comes back. The workflow to case-study and replicate.",
            "<b>Action:</b> Document one of their reviewed-PR flows as a reference.",
        ),
        "coach": (
            "Coach", "Likely fixable", "ASK",
            "<b>High volume, low acceptance.</b> Qodo is reviewing their code but they're "
            "not acting on most of it. The interesting question is <em>why</em> &mdash; "
            "noisy rules, time pressure, or real disagreement.",
            "<b>Action:</b> 1:1 conversation, not an automated nudge.",
        ),
        "curious": (
            "Curious / new", "Observe", "WAIT",
            "<b>Light usage, high acceptance.</b> Recent onboards, low-PR-volume teammates, "
            "or contributors to repos Qodo isn't deeply tuned for.",
            "<b>Action:</b> Nothing at this time. Expect them to shift quadrants in the "
            "next report once their PR volume settles.",
        ),
        "absent": (
            "Low usage", "Re-engage", "FIX",
            "<b>Engaged but not implementing.</b> Some authors here receive findings "
            f"regularly (&ge;{NOTABLE_FCUT}) but implement under {NOTABLE_RCUT}%.",
            "<b>Action:</b> A direct check-in confirms whether Qodo is configured for "
            "their repos and whether they know how to act on findings.",
        ),
    }

    def _quad_card(k: str) -> str:
        title, eyebrow, _icon, deck, action = quad_meta[k]
        ds = by_quad[k]
        rows = "".join(
            f'<div class="p05-qc-row {k}" data-user="{_h(d["user"])}">'
            f'<span class="name">{_h(d["user"])}</span>'
            f'<span class="findings">{_vol(d)} findings &middot; {d["prs"]} PR'
            f'{"s" if d["prs"] != 1 else ""}</span>'
            f'<span class="rate">{d["rate"]:.0f}%</span>'
            f'</div>'
            for d in ds
        )
        return (
            f'<div class="p05-quad-card {k}">'
            f'<div class="p05-qc-head">'
            f'<div class="p05-qc-head-l">'
            f'<div class="p05-qc-eyebrow"><span class="dot"></span>{eyebrow}</div>'
            f'<div class="p05-qc-title">{title}</div>'
            f'<div class="p05-qc-deck">{deck}</div>'
            f'</div>'
            f'<div class="p05-qc-stat"><div class="v">{len(ds)}</div>'
            f'<div class="u">{"author" if len(ds) == 1 else "authors"}</div></div>'
            f'</div>'
            f'<div class="p05-qc-body">'
            f'<div class="p05-qc-action">{action}</div>'
            f'<div class="p05-qc-list">{rows}</div>'
            f'</div>'
            f'</div>'
        )

    quad_grid = (
        '<div class="p05-quad-grid" id="p05-quad-grid">'
        + "".join(_quad_card(k) for k in ("power", "coach", "curious", "absent"))
        + '</div>'
    )

    # ── Hover tooltip script (small inline IIFE) ──
    # Serialise per-dev data the tooltip needs.
    dev_payload = json.dumps([
        {"user": d["user"], "prs": d["prs"], "repos": d["repos"],
         "totalSug": d["totalSug"], "totalImp": d["totalImp"],
         "actReqSug": d["actReqSug"], "actReqImp": d["actReqImp"],
         "rate": d["rate"], "actRate": d["actRate"], "quad": d["quad"]}
        for d in devs
    ])
    quad_labels_payload = json.dumps({k: v[2] for k, v in qc.items()})
    tip_script = (
        '<script>(function(){'
        f'const D = {dev_payload};'
        f'const QL = {quad_labels_payload};'
        'const M = Object.fromEntries(D.map(d => [d.user, d]));'
        'const svg = document.getElementById("p05-scatter");'
        'const tip = document.getElementById("p05-tip");'
        'if (!svg || !tip) return;'
        'function escH(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}'
        'function render(d){'
        '  const cls = d.quad==="power"||d.quad==="curious"?"mint":d.quad==="coach"?"warn":"danger";'
        '  tip.innerHTML = '
        '    \'<div class="name">\'+escH(d.user)+\' <span class="badge \'+d.quad+\'">\'+QL[d.quad]+\'</span></div>\''
        '    + \'<div class="meta">\'+d.prs+\' PRs \\u00b7 \'+d.repos+\' repo\'+(d.repos>1?"s":"")+\'</div>\''
        '    + \'<div class="row"><span>Action-required findings</span> <b>\'+d.actReqSug+\'</b></div>\''
        '    + \'<div class="row"><span>Implemented</span> <b>\'+d.actReqImp+\'</b></div>\''
        '    + \'<div class="row"><span>AR impl rate</span> <b class="\'+cls+\'">\'+d.rate.toFixed(1)+\'%</b></div>\';'
        '  tip.classList.add("show");'
        '}'
        'function position(d, evt){'
        '  const wrap = svg.parentElement.getBoundingClientRect();'
        '  const tw = tip.offsetWidth || 220, th = tip.offsetHeight || 200;'
        '  let cx, cy;'
        '  if (evt) { cx = evt.clientX - wrap.left; cy = evt.clientY - wrap.top; }'
        '  else {'
        '    const sb = svg.getBoundingClientRect();'
        '    const g = svg.querySelector(".p05-bubble[data-user=\'"+CSS.escape(d.user)+"\']");'
        '    if (!g) return;'
        '    const r = g.getBoundingClientRect();'
        '    cx = (r.left+r.width/2) - wrap.left;'
        '    cy = (r.top +r.height/2) - wrap.top;'
        '  }'
        '  let tx = cx+16, ty = cy+16;'
        '  if (tx+tw > wrap.width)  tx = cx-tw-12;'
        '  if (ty+th > wrap.height) ty = cy-th-12;'
        '  if (tx < 0) tx = 4; if (ty < 0) ty = 4;'
        '  tip.style.left = tx+"px"; tip.style.top = ty+"px";'
        '}'
        'function focus(user, evt){'
        '  const d = M[user]; if (!d) return;'
        '  svg.classList.add("has-hover");'
        '  svg.querySelectorAll(".p05-bubble").forEach(g => g.classList.toggle("is-hovered", g.dataset.user===user));'
        '  document.querySelectorAll("#p05-quad-grid .p05-qc-row").forEach(r => r.classList.toggle("is-hovered", r.dataset.user===user));'
        '  render(d); position(d, evt);'
        '}'
        'function clearFocus(){'
        '  svg.classList.remove("has-hover");'
        '  document.querySelectorAll(".p05-bubble.is-hovered, .p05-qc-row.is-hovered").forEach(e => e.classList.remove("is-hovered"));'
        '  tip.classList.remove("show");'
        '}'
        'svg.querySelectorAll(".p05-bubble").forEach(g => {'
        '  g.addEventListener("mouseenter", e => focus(g.dataset.user, e));'
        '  g.addEventListener("mousemove",  e => { const d = M[g.dataset.user]; if (d) position(d, e); });'
        '  g.addEventListener("mouseleave", clearFocus);'
        '});'
        'document.querySelectorAll("#p05-quad-grid .p05-qc-row").forEach(r => {'
        '  r.addEventListener("mouseenter", () => focus(r.dataset.user));'
        '  r.addEventListener("mouseleave", clearFocus);'
        '});'
        '})();</script>'
    )

    return (
        '<section class="r-section" id="sec-adoption-matrix">'
        '<div class="r-section-head"><div>'
        '<div class="r-section-eyebrow">Adoption matrix</div>'
        '<div class="r-section-title">Developer adoption matrix &mdash; volume vs implementation rate</div>'
        '<div class="r-section-deck">'
        f'Every author plotted by action-required findings (log scale) and implementation rate. '
        f'Four quadrants surface four different conversations: power users to learn from, '
        f'coach candidates to unblock, curious new joiners to observe, and a low-usage '
        f'group to re-engage. {len(devs)} of {agg.adoption_authors_total} authors received '
        f'at least one action-required finding in this window.'
        '</div>'
        '</div></div>'
        f'<div class="p05-hero">{"".join(stats_html)}</div>'
        f'{chart_card}'
        f'{quad_grid}'
        f'{tip_script}'
        '</section>'
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
        f'<section class="r-section" id="sec-breakdown">'
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
    trend_html = _section_trend(agg)

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
        f'{_section_acts(show_trend=bool(trend_html))}'
        f'{_section_kpis(agg)}'
        f'{trend_html}'
        f'{_section_funnel(agg)}'
        f'{_section_spotlight(agg)}'
        f'{_section_breakdown(agg)}'
        f'{_section_velocity(agg)}'
        f'{_section_adoption_matrix(agg, span_days)}'
        f'{_section_hours_saved(agg, span_days, since, until)}'
        f'{_section_footer(org, span_days)}'
        f'</div></div>\n'
        f'</body>\n</html>'
    )
