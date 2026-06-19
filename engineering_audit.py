#!/usr/bin/env python3
"""
Engineering Audit — pre-install diagnostic report.

Reads the last N days of merged PRs from a GitHub org (or a scoped list of
repos) and renders a self-contained HTML report that surfaces six measurable
patterns in review depth, reviewer concentration, cycle time, AI-authored
share, and volume scaling.

DESIGNED TO BE RUN BEFORE QODO IS INSTALLED. The script does not read any
Qodo product data — only the GitHub GraphQL API via the `gh` CLI. The
generated HTML can be used to establish a baseline, then compared against a
post-install run to quantify what changed.

Usage:
    python3 engineering_audit.py --org acme-corp
    python3 engineering_audit.py --org acme-corp --since 2026-03-17
    python3 engineering_audit.py --org acme-corp --days 60
    python3 engineering_audit.py --org acme-corp --repos frontend-app api
    python3 engineering_audit.py --org acme-corp --output-dir reports/

Prerequisites:
    - `gh` CLI installed and authenticated (`gh auth status`)
    - `repo` scope on the token if the org has private repos
"""

import argparse
import csv as _csv
import html
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import core
from collectors import get_collector


TEMPLATE_FILENAME = "engineering_audit_template.html"

# Bot-comment detection. GitHub's __typename=Bot catches marketplace bots,
# but plenty of User-typed accounts behave like bots (CI, security scanners,
# the Qodo Merge bot when it's installed as a User app). These are the
# common login patterns we treat as bot signal regardless of __typename.
_BOT_LOGIN_PATTERNS = re.compile(
    r"("
    r"dependabot|renovate|github-actions|codecov|sonarcloud|"
    r"deepsource|netlify|vercel|sentry-io|allcontributors|"
    r"semantic-release|stale|imgbot|pre-commit-ci|"
    r"qodo|codium"
    r")",
    re.IGNORECASE,
)
# Qodo review marker — if a comment carries this string, treat it as a bot
# comment for the purpose of the audit so the report stays clean if Qodo is
# already installed when someone runs the script.
_QODO_BODY_MARKER = re.compile(r"Code Review by Qodo", re.IGNORECASE)


# ── Per-PR row ───────────────────────────────────────────────────────────

def _is_bot_login(login: str) -> bool:
    return bool(_BOT_LOGIN_PATTERNS.search((login or "").lower()))


def _is_bot_comment(comment: dict) -> bool:
    user = comment.get("user") or {}
    if user.get("type") == "Bot":
        return True
    if _is_bot_login(user.get("login") or ""):
        return True
    body = comment.get("body") or ""
    if _QODO_BODY_MARKER.search(body):
        return True
    return False


# Review states that represent a human actually engaging with the PR. A bare
# DISMISSED/PENDING review is not engagement; APPROVED/CHANGES_REQUESTED/
# COMMENTED are.
_HUMAN_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}


def _human_review_timestamps(reviews: list) -> List[str]:
    """submittedAt timestamps of non-bot reviews that signal real engagement.

    The batch fetch doesn't return review-thread comment *bodies*, only review
    state + author + submittedAt, so we use a review in an engaged state as a
    proxy for "a human looked at this PR" even when they left no issue comment.
    """
    stamps = []
    for rv in reviews or []:
        if (rv.get("state") or "").upper() not in _HUMAN_REVIEW_STATES:
            continue
        if _is_bot_login((rv.get("author") or {}).get("login") or ""):
            continue
        ts = rv.get("submittedAt") or ""
        if ts:
            stamps.append(ts)
    return stamps


def build_audit_row(pr: dict, pr_data: dict) -> dict:
    comments = pr_data.get("comments", []) or []
    additions = pr_data.get("additions") or 0
    deletions = pr_data.get("deletions") or 0
    lines = additions
    hours = core.hours_between(
        pr.get("created_at", ""), pr.get("merged_at", "")
    )

    # "Human engagement" = any non-bot issue comment OR any non-bot review in
    # an engaged state (APPROVED / CHANGES_REQUESTED / COMMENTED). Counting
    # reviews matters because a PR can get a thorough review with zero issue
    # comments, and the methodology copy promises review-level signal is used.
    reviews = pr_data.get("reviews", []) or []
    human_comments = [c for c in comments if not _is_bot_comment(c)]
    human_timestamps = [
        c.get("created_at", "") for c in human_comments if c.get("created_at")
    ] + _human_review_timestamps(reviews)
    has_human = bool(human_timestamps)
    ttfc_min: Optional[int] = None
    if has_human:
        ttfc_min = core.minutes_between(
            pr.get("created_at", ""), min(human_timestamps)
        )

    is_ai, ai_type = core.detect_ai_authored(
        pr_data.get("body", "") or "",
        pr_data.get("labels", []) or [],
    )
    review_info = core.parse_reviews(reviews)

    return {
        "owner": pr["owner"],
        "repo": pr["repo"],
        "number": pr["number"],
        "url": pr.get("url", ""),
        "creator": pr.get("creator", ""),
        "created_at": pr.get("created_at", ""),
        "merged_at": pr.get("merged_at", ""),
        "lines": lines,
        "hours": hours,
        "has_human": has_human,
        "ttfc_min": ttfc_min,
        "is_ai": is_ai,
        "ai_type": ai_type,
        "approver": review_info["approver"],
        "reviewer_count": review_info["reviewer_count"],
        "had_request_changes": review_info["had_request_changes"],
    }


# ── Aggregation ──────────────────────────────────────────────────────────

def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _week_start_iso(ts: str) -> Optional[str]:
    """Return the Monday-start ISO date for a GitHub mergedAt timestamp."""
    if not ts:
        return None
    try:
        d = datetime.strptime(
            ts.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S"
        ).date()
    except (ValueError, TypeError):
        return None
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def aggregate_audit(rows: List[dict], org: str, since: date, until: date) -> dict:
    total_prs = len(rows)
    total_repos = len({r["repo"] for r in rows})

    # ── s1_bypass: large + fast + no human comment ─────────────────────
    valid_lines_vals = [r["lines"] for r in rows if r["lines"] > 0]
    median_lines = int(statistics.median(valid_lines_vals)) if valid_lines_vals else 0
    hours_vals = [r["hours"] for r in rows]
    median_hours = int(statistics.median(hours_vals)) if hours_vals else 0
    bypass = [
        r for r in rows
        if r["lines"] > median_lines
        and r["hours"] <= median_hours
        and not r["has_human"]
    ]
    s1_bypass = {
        "bigAndFast": len(bypass),
        "pct": round(100 * len(bypass) / total_prs, 1) if total_prs else 0,
        "medianLines": median_lines,
        "medianHours": median_hours,
        # extras the template uses for the finding card
        "avgLines": int(statistics.mean([r["lines"] for r in bypass])) if bypass else 0,
        "avgHours": (
            round(statistics.mean([r["hours"] for r in bypass]), 1) if bypass else 0
        ),
    }

    # ── s2_ttfc: human comment / no human comment ──────────────────────
    with_human = [r for r in rows if r["has_human"] and r["ttfc_min"] is not None]
    no_human = [r for r in rows if not r["has_human"]]
    ttfc_vals = [r["ttfc_min"] for r in with_human]
    over_24h = sum(1 for v in ttfc_vals if v >= 24 * 60)
    s2_ttfc = {
        "noHumanCommentCount": len(no_human),
        "noHumanCommentPct": 100 * len(no_human) / total_prs if total_prs else 0,
        "medianTtfcMin": int(_percentile(ttfc_vals, 50)) if ttfc_vals else 0,
        "p90TtfcMin": int(_percentile(ttfc_vals, 90)) if ttfc_vals else 0,
        "over24hCount": over_24h,
        "over24hPct": 100 * over_24h / len(with_human) if with_human else 0,
        "withHumanCount": len(with_human),
    }

    # ── s3_reviewers: final approver concentration ─────────────────────
    approvers = [r["approver"] for r in rows if r["approver"]]
    total_approved = len(approvers)
    counter = Counter(approvers)
    top5 = [
        {
            "name": name,
            "count": cnt,
            "pct": 100 * cnt / total_approved if total_approved else 0,
        }
        for name, cnt in counter.most_common(5)
    ]
    s3_reviewers = {
        "uniqueApprovers": len(counter),
        "totalApproved": total_approved,
        "top5": top5,
        "top1Pct": top5[0]["pct"] if top5 else 0,
        "top2Pct": sum(t["pct"] for t in top5[:2]) if len(top5) >= 1 else 0,
        "top3Pct": sum(t["pct"] for t in top5[:3]) if top5 else 0,
    }

    # ── s4_cycle: histogram + percentiles ──────────────────────────────
    bins = [
        ("<1h",   lambda h: h < 1),
        ("1-4h",  lambda h: 1 <= h < 4),
        ("4-24h", lambda h: 4 <= h < 24),
        ("1-3d",  lambda h: 24 <= h < 72),
        ("3-7d",  lambda h: 72 <= h < 168),
        ("1-4w",  lambda h: 168 <= h < 672),
        (">1mo",  lambda h: h >= 672),
    ]
    histogram = [
        {"label": label, "count": sum(1 for h in hours_vals if pred(h))}
        for label, pred in bins
    ]
    over_24h_cnt   = sum(1 for h in hours_vals if h >= 24)
    over_week_cnt  = sum(1 for h in hours_vals if h >= 168)
    over_month_cnt = sum(1 for h in hours_vals if h >= 672)
    s4_cycle = {
        "p50": int(_percentile(hours_vals, 50)) if hours_vals else 0,
        "p90": int(_percentile(hours_vals, 90)) if hours_vals else 0,
        "p99": int(_percentile(hours_vals, 99)) if hours_vals else 0,
        "max": max(hours_vals) if hours_vals else 0,
        "over24h": over_24h_cnt,
        "over1week": over_week_cnt,
        "over1month": over_month_cnt,
        "over24hPct": 100 * over_24h_cnt / total_prs if total_prs else 0,
        "over1weekPct": 100 * over_week_cnt / total_prs if total_prs else 0,
        "over1monthPct": 100 * over_month_cnt / total_prs if total_prs else 0,
        "histogram": histogram,
        "lt1hCount": histogram[0]["count"],
        "lt1hPct": 100 * histogram[0]["count"] / total_prs if total_prs else 0,
    }

    # ── s6_ai: AI-authored share ───────────────────────────────────────
    ai_count = sum(1 for r in rows if r["is_ai"])
    ai_types = Counter(r["ai_type"] for r in rows if r["is_ai"] and r["ai_type"])
    s6_ai = {
        "count": ai_count,
        "pct": 100 * ai_count / total_prs if total_prs else 0,
        "humanCount": total_prs - ai_count,
        "predominantTool": ai_types.most_common(1)[0][0] if ai_types else "",
    }

    # ── s7_volume: weekly merged-PR count ──────────────────────────────
    by_week = defaultdict(int)
    for r in rows:
        w = _week_start_iso(r["merged_at"])
        if w:
            by_week[w] += 1
    weeks_sorted = sorted(by_week.items())
    weeks = [{"week": w, "count": c} for w, c in weeks_sorted]
    first_wk = weeks[0]["count"] if weeks else 0
    last_wk = weeks[-1]["count"] if weeks else 0
    growth = last_wk / first_wk if first_wk else 0
    s7_volume = {
        "weeks": weeks,
        "firstWeek": first_wk,
        "lastWeek": last_wk,
        "growth": growth,
        "firstWeekStart": weeks[0]["week"] if weeks else "",
        "lastWeekStart": weeks[-1]["week"] if weeks else "",
    }

    # ── s7_cost: feeds the interactive cost calculator in section 07 ───
    # The calculator needs three trim modes (no trim / top-1% trimmed /
    # top-5% trimmed) plus the unique-author count to seed the "engineers"
    # input. Trim drops the largest PRs by line count to mute single-commit
    # spikes (vendored deps, generated code, etc.).
    sorted_by_lines = sorted(rows, key=lambda r: r["lines"], reverse=True)
    n = len(rows)

    def _trim_after(drop_pct: float) -> List[dict]:
        k = int(n * drop_pct)
        return sorted_by_lines[k:]

    def _totals(subset: List[dict]) -> Dict[str, int]:
        return {
            "prs": len(subset),
            "loc": sum(r["lines"] for r in subset),
        }

    s7_cost = {
        "totalDevs": len({r["creator"] for r in rows if r["creator"]}),
        "totals": {
            "none": {**_totals(rows),               "label": "All PRs (no trim)"},
            "p99":  {**_totals(_trim_after(0.01)),  "label": "Top 1% of outlier PRs trimmed"},
            "p95":  {**_totals(_trim_after(0.05)),  "label": "Top 5% of outlier PRs trimmed for defensibility"},
        },
    }

    return {
        "org": org,
        "window": {
            "from": since.isoformat(),
            "to": until.isoformat(),
            "days": (until - since).days,
        },
        "totalPRs": total_prs,
        "totalRepos": total_repos,
        "totalPRsWithLines": sum(1 for r in rows if r["lines"] > 0),
        "s1_bypass": s1_bypass,
        "s2_ttfc": s2_ttfc,
        "s3_reviewers": s3_reviewers,
        "s4_cycle": s4_cycle,
        "s6_ai": s6_ai,
        "s7_volume": s7_volume,
        "s7_cost": s7_cost,
    }


def build_scatter(rows: List[dict], aggregates: dict) -> dict:
    """Build the scatter data payload — per-PR points + rubber-stamp outliers.

    Each point is a positional tuple to keep the embedded JSON small:
        [lines, hours, totalImpl, spotlight, isAI, hasHuman, repo, pr, creator]
    `totalImpl` and `spotlight` are always 0 in the audit — they're slots the
    post-install report fills in, kept here so the same chart-rendering JS
    works for both.
    """
    points = [
        [
            r["lines"],
            r["hours"],
            0,                              # totalImpl  (N/A pre-install)
            0,                              # spotlight  (N/A pre-install)
            1 if r["is_ai"] else 0,
            1 if r["has_human"] else 0,
            r["repo"],
            r["number"],
            r["creator"],
        ]
        for r in rows if r["lines"] > 0
    ]
    median_lines = aggregates["s1_bypass"]["medianLines"]
    median_hours = aggregates["s1_bypass"]["medianHours"]
    bypass = sorted(
        [
            r for r in rows
            if r["lines"] > median_lines
            and r["hours"] <= median_hours
            and not r["has_human"]
        ],
        key=lambda r: r["lines"],
        reverse=True,
    )
    rubber_stamps = [
        {"repo": r["repo"], "pr": r["number"], "lines": r["lines"], "hours": r["hours"]}
        for r in bypass[:10]
    ]
    return {
        "generated": datetime.utcnow().isoformat() + "Z",
        "points": points,
        "rubberStamps": rubber_stamps,
    }


# ── HTML rendering ────────────────────────────────────────────────────
#
# As of the v2 template the report is rendered client-side: the HTML carries
# example values plus two <script type="application/json"> blobs, and an
# inline <script> on the page reads those blobs at load time and rewrites
# the DOM (text content, copy variants, chart SVGs) accordingly. Python's
# job is therefore much smaller than it used to be — it computes the
# aggregates, then injects them into the template's JSON slots. The only
# server-side text substitution still needed is <<ORG>> in the <title> tag,
# which the inline JS can't touch.
#
# Historical note: prior versions of this script substituted ~50 named
# <<PLACEHOLDER>> tokens (S1_PCT, S2_NO_HUMAN_COUNT, S3_TOP1_NAME, …) via
# a _displayables() helper. That whole apparatus has been retired; see
# engineering_audit_template_v1.html for the old template if you need to
# diff.



def render_html(agg: dict, scatter: dict, template_path: Path) -> str:
    """Render the report by injecting AGG + scatter JSON into the template.

    The template's inline <script> does the rest — reads the blobs at load
    time and rewrites the DOM. We only do three substitutions here:
      - <<ORG>>          → HTML-escaped org name (used in the <title> tag)
      - <<AGG_JSON>>     → pretty-printed aggregates blob
      - <<SCATTER_JSON>> → compact scatter-points blob

    org comes from --org or, via --from-json/--from-csv, from untrusted files,
    so it's HTML-escaped before going into <title>. The JSON blobs land inside
    <script> tags, so any "</script>" inside them is neutralised to stop an
    early tag close from breaking out into executable markup.
    """
    template = template_path.read_text(encoding="utf-8")
    out = template.replace("<<ORG>>", html.escape(str(agg.get("org", "")), quote=True))
    # Inject the JSON payloads LAST so any stray placeholders inside them
    # don't get rewritten by an earlier substitution.
    out = out.replace("<<AGG_JSON>>", _json_for_script(agg, indent=2))
    out = out.replace("<<SCATTER_JSON>>", _json_for_script(scatter, separators=(",", ":")))
    return out


def _json_for_script(obj, **dumps_kwargs) -> str:
    """json.dumps, but safe to embed inside an inline <script> block."""
    return json.dumps(obj, **dumps_kwargs).replace("</", "<\\/")


# ── Orchestration / CLI ──────────────────────────────────────────────

def _org_from_csv_stem(stem: str) -> str:
    """Infer the org from a report.py CSV filename: ``{org}_{since}_{until}``.

    Splitting on the first underscore breaks for orgs containing underscores
    (e.g. ``my_org_2026-01-01_2026-02-01``), so split on the first
    ``_YYYY-MM-DD`` date segment instead and fall back to the whole stem.
    """
    parts = re.split(r"_\d{4}-\d{2}-\d{2}", stem, maxsplit=1)
    return parts[0] if parts and parts[0] else stem


def rows_from_csv(csv_path: Path) -> List[dict]:
    """Load audit rows from a report.py CSV.

    CAVEAT: report.py only fetches Qodo-reviewed PRs, so the resulting
    aggregates reflect that subset only — not the full merged-PR
    population a true pre-install audit would see. Useful for sanity checks
    against existing data; not a substitute for a fresh `--org` run.
    """
    rows: List[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            ttfc = r.get("Time to First Human Comment (min)") or ""
            try:
                ttfc_min = int(ttfc) if ttfc.strip() else None
            except ValueError:
                ttfc_min = None
            try:
                lines = int(r.get("Lines Added") or 0)
            except ValueError:
                lines = 0
            try:
                hours = int(r.get("Hours to Merge") or 0)
            except ValueError:
                hours = 0
            try:
                number = int(r.get("PR #") or 0)
            except ValueError:
                number = 0
            try:
                reviewer_count = int(r.get("Reviewer Count") or 0)
            except ValueError:
                reviewer_count = 0
            rows.append({
                "owner": "",
                "repo": r.get("Repo Name", ""),
                "number": number,
                "url": r.get("PR URL", ""),
                "creator": r.get("PR Creator", ""),
                "created_at": r.get("PR Creation Date", ""),
                "merged_at": r.get("PR Merge Date", ""),
                "lines": lines,
                "hours": hours,
                "has_human": (r.get("Has Human Comment") or "").strip() == "True",
                "ttfc_min": ttfc_min,
                "is_ai": (r.get("Is AI Authored") or "").strip() == "True",
                "ai_type": r.get("AI Author Type", ""),
                "approver": r.get("Final Approver", ""),
                "reviewer_count": reviewer_count,
                "had_request_changes": (
                    (r.get("Had Request Changes") or "").strip() == "True"
                ),
            })
    return rows


def cmd_audit(args):
    start = time.monotonic()
    until = args.until or date.today()
    since = args.since or (until - timedelta(days=args.days))

    if args.from_csv:
        print(f"Loading rows from CSV: {args.from_csv}", file=sys.stderr)
        rows = rows_from_csv(Path(args.from_csv))
        if not rows:
            sys.exit("CSV produced no rows.")
        # Infer window from the CSV merge dates if --since/--until weren't given.
        if not args.since and not args.until:
            merge_dates = [r["merged_at"][:10] for r in rows if r["merged_at"]]
            merge_dates.sort()
            if merge_dates:
                since = date.fromisoformat(merge_dates[0])
                until = date.fromisoformat(merge_dates[-1])
        org = args.org or _org_from_csv_stem(Path(args.from_csv).stem)
        print(
            f"Loaded {len(rows):,} rows (window {since} → {until}, org {org}).\n"
            "Note: report.py CSVs only contain Qodo-reviewed PRs, so the\n"
            "audit aggregates reflect that subset, not the full PR population.",
            file=sys.stderr,
        )
    else:
        print(f"Fetching merged PRs for {args.org} ({since} → {until}) …",
              file=sys.stderr)

        collector = get_collector("github")
        pr_meta = list(collector.search_merged_prs(
            args.org, since, until=until,
            chunk_days=args.chunk_days, repos=args.repos, qodo_only=False,
        ))
        if not pr_meta:
            sys.exit("No merged PRs found in window. Check --org and date range.")

        print(f"\nFetching PR detail for {len(pr_meta)} PRs in batches …",
              file=sys.stderr)
        rows: List[dict] = []
        batch_size = 25
        for i in range(0, len(pr_meta), batch_size):
            batch = pr_meta[i:i + batch_size]
            # Pull up to 100 comments (vs the report's default 20) so the
            # "no human comment" / TTFC signal isn't skewed by chatty PRs.
            pr_data_map = collector.fetch_pr_data_batch(batch, comments_first=100)
            for pr in batch:
                data = pr_data_map.get(pr.get("node_id", ""))
                if not data:
                    continue
                rows.append(build_audit_row(pr, data))
            print(
                f"\r  [{min(i + batch_size, len(pr_meta))}/{len(pr_meta)}] processed",
                end="", file=sys.stderr, flush=True,
            )
        print(file=sys.stderr)
        org = args.org

    print("Aggregating …", file=sys.stderr)
    agg = aggregate_audit(rows, org, since, until)
    scatter = build_scatter(rows, agg)

    out_dir = Path(args.output_dir) if args.output_dir else core.REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", org)
    stem = f"{safe_org}_audit_{since.isoformat()}_{until.isoformat()}"

    template_path = Path(args.template or TEMPLATE_FILENAME)
    if not template_path.exists():
        sys.exit(
            f"HTML template not found: {template_path}\n"
            f"Pass --template /path/to/engineering_audit_template.html "
            "or place the file alongside this script."
        )

    html_path = out_dir / f"{stem}.html"
    json_path = out_dir / f"{stem}.json"
    html_path.write_text(render_html(agg, scatter, template_path), encoding="utf-8")
    json_path.write_text(json.dumps(agg, indent=2), encoding="utf-8")

    elapsed = int(time.monotonic() - start)
    print(file=sys.stderr)
    print(f"Window:           {since} → {until} ({(until - since).days} days)")
    print(f"PRs analyzed:     {len(rows):,}")
    print(f"Repos:            {agg['totalRepos']}")
    print(f"Bypass zone:      {agg['s1_bypass']['bigAndFast']:,} "
          f"({agg['s1_bypass']['pct']:.1f}%)")
    print(f"No human comment: {agg['s2_ttfc']['noHumanCommentCount']:,} "
          f"({agg['s2_ttfc']['noHumanCommentPct']:.1f}%)")
    print(f"AI-authored:      {agg['s6_ai']['count']:,} "
          f"({agg['s6_ai']['pct']:.1f}%)")
    print()
    print(f"Reports written:")
    print(f"  HTML: {html_path}")
    print(f"  JSON: {json_path}")
    print(f"\nCompleted in {elapsed}s")


def render_from_json(args):
    """Re-render the HTML from a previously saved audit JSON file.

    Useful when you've already paid for the GitHub API calls and just want
    to tweak the template or copy without re-fetching everything.
    """
    json_path = Path(args.from_json)
    if not json_path.exists():
        sys.exit(f"Audit JSON not found: {json_path}")
    try:
        agg = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"Audit JSON is not valid JSON ({json_path}): {e}")
    template_path = Path(args.template or TEMPLATE_FILENAME)
    if not template_path.exists():
        sys.exit(
            f"HTML template not found: {template_path}\n"
            f"Pass --template /path/to/engineering_audit_template.html "
            "or place the file alongside this script."
        )
    # No scatter data on disk — render with an empty payload. The scatter
    # chart will be blank but the rest of the report is intact.
    scatter = {"generated": datetime.utcnow().isoformat() + "Z",
               "points": [], "rubberStamps": []}
    out = Path(args.output or "audit.html")
    out.write_text(render_html(agg, scatter, template_path), encoding="utf-8")
    print(f"Rendered {out}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--org", help="GitHub org login (e.g., acme-corp)")
    p.add_argument("--since", type=date.fromisoformat,
                   help="Start date (YYYY-MM-DD). Defaults to --days back from --until.")
    p.add_argument("--until", type=date.fromisoformat,
                   help="End date (YYYY-MM-DD). Defaults to today.")
    p.add_argument("--days", type=int, default=60,
                   help="Lookback window in days when --since is omitted (default: 60)")
    p.add_argument("--repos", nargs="+", metavar="REPO",
                   help="Limit to specific repos (e.g. --repos frontend api)")
    p.add_argument("--chunk-days", type=int, default=30,
                   help="Date-window size per search query (default: 30). "
                        "Lower it if you hit GitHub's 1000-result search cap.")
    p.add_argument("--output-dir", help="Directory to write reports into (default: reports/)")
    p.add_argument("--template", help=f"Path to HTML template (default: ./{TEMPLATE_FILENAME})")
    p.add_argument("--from-csv",
                   help="Build the audit from a report.py CSV instead of "
                        "fetching from GitHub. Only contains Qodo-reviewed PRs.")
    p.add_argument("--from-json",
                   help="Skip fetching; re-render HTML from a previously saved audit JSON file")
    p.add_argument("--output",
                   help="Output HTML path when used with --from-json")
    args = p.parse_args()

    if args.from_json:
        render_from_json(args)
        return

    if not args.org and not args.from_csv:
        p.error("--org is required (unless using --from-csv or --from-json)")

    cmd_audit(args)


if __name__ == "__main__":
    main()
