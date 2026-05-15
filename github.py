#!/usr/bin/env python3
"""
Count Qodo code-review suggestions and implementation rate across merged PRs
in a GitHub org.

For each merged PR in the lookback window, this finds Qodo's review comment
(identified by the "Code Review by Qodo" marker, since the bot account name
varies between deployments) and counts:
  - Total suggestions
  - Suggestions implemented (strikethrough on the suggestion title)

Authenticates via the `gh` CLI — no token handling needed. Just make sure
`gh auth status` shows you logged in with access to the org's repos.

Usage:
  # Inspect ONE real Qodo comment to verify the parser matches your output
  ./qodo_pr_stats.py --org acme-corp --inspect

  # Full run, default 365-day lookback
  ./qodo_pr_stats.py --org acme-corp

  # Custom window
  ./qodo_pr_stats.py --org acme-corp --since 2025-05-12
  ./qodo_pr_stats.py --org acme-corp --days 90

  # Per-PR detail
  ./qodo_pr_stats.py --org acme-corp --verbose
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import report


# Stable marker in Qodo Merge's review comment, independent of bot account name.
QODO_MARKER = re.compile(r"Code Review by Qodo", re.IGNORECASE)

# A numbered suggestion line. Qodo wraps each in <details><summary>...</summary>,
# but we also match raw "N. ..." lines as a fallback in case the format changes.
# The captured group is the suggestion title + trailing labels.
SUGGESTION_LINE = re.compile(
    r"(?:<summary>|^)\s*\d+\.\s+(.+?)(?:</summary>|$)",
    re.MULTILINE,
)

# Indicators that a suggestion was implemented:
#   - markdown strikethrough (~~text~~)
#   - HTML strikethrough (<s>, <del>, <strike>)
#   - the ballot-box-check emoji Qodo adds next to fixed items
IMPLEMENTED_MARKERS = ("~~", "<s>", "<del>", "<strike>", "\u2611")  # ☑

# Section header patterns — match both markdown headings (##, **) and the
# <img> tags Qodo currently uses for section banners (e.g. action-required.png).
# Anchored via .match() so suggestion titles containing these words don't
# accidentally reset the section counter.
_SECTION_ACTION = re.compile(
    r"^(?:(?:#+\s*|\*{1,2}\s*)action\s+required"
    r"|<img\b[^>]*action-required\.png)",
    re.IGNORECASE,
)
_SECTION_REVIEW = re.compile(
    r"^(?:(?:#+\s*|\*{1,2}\s*)(?:review|remediation)\s+recommended"
    r"|<img\b[^>]*review-recommended\.png)",
    re.IGNORECASE,
)

# Category label patterns (matched against each suggestion line).
# _CAT_RULE is checked before _CAT_BUG because "Rule violation" would not
# match \bBug\b anyway, but ordering is explicit to prevent future regressions.
_CAT_BUG  = re.compile(r"\bBug\b", re.IGNORECASE)
_CAT_RULE = re.compile(r"\bRule\s+violation", re.IGNORECASE)
_CAT_REQ  = re.compile(r"\bRequirement\s+gap", re.IGNORECASE)
_CAT_SECURITY    = re.compile(r"\bSecurity\b",    re.IGNORECASE)
_CAT_CORRECTNESS = re.compile(r"\bCorrectness\b", re.IGNORECASE)
_HTML_TAG        = re.compile(r"<[^>]+>")
_STRIKETHROUGH   = re.compile(r"~~(.+?)~~")


@dataclass
class QodoStats:
    action_required_total: int = 0
    action_required_implemented: int = 0
    review_recommended_total: int = 0
    review_recommended_implemented: int = 0
    bugs_suggested: int = 0
    bugs_implemented: int = 0
    rule_violations_suggested: int = 0
    rule_violations_implemented: int = 0
    requirement_gaps_suggested: int = 0
    requirement_gaps_implemented: int = 0
    # Direct totals — count ALL items regardless of section so PRs with
    # no section headers still produce correct Total Suggestions counts.
    total_suggestions: int = 0
    total_implemented: int = 0
    security_suggested: int = 0
    security_implemented: int = 0
    correctness_suggested: int = 0
    correctness_implemented: int = 0
    spotlight_issues: list = field(default_factory=list)


def _rate_limit_reset_epoch():
    """Return the Unix timestamp when the primary GitHub rate limit resets."""
    try:
        out = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".rate.reset"],
            capture_output=True, text=True, timeout=15,
        )
        return int(out.stdout.strip())
    except Exception:
        return None


def run_gh(args, paginate=False):
    """Run `gh` and return stdout. Retries once after waiting on rate limit."""
    cmd = ["gh"] + args
    if paginate and "--paginate" not in cmd:
        cmd.append("--paginate")
    for attempt in range(2):
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode == 0:
            return result.stdout
        if "rate limit" in result.stderr.lower() and attempt == 0:
            reset = _rate_limit_reset_epoch()
            if reset:
                wait = max(0, reset - int(time.time())) + 5
            else:
                wait = 60
            print(
                f"\n  Rate limit hit — waiting {wait}s before retry...",
                file=sys.stderr, flush=True,
            )
            time.sleep(wait)
            continue
        sys.exit(f"`{' '.join(cmd)}` failed:\n{result.stderr}")
    sys.exit(f"`{' '.join(cmd)}` failed after rate-limit retry:\n{result.stderr}")


def search_merged_prs(org, since, chunk_days=30, repos=None):
    """Yield a dict of PR metadata for every merged PR in the window.

    Each yielded dict contains: owner, repo, number, url, creator,
    created_at, merged_at.

    Chunked by date range to stay under the search API's 1000-result cap.
    Shrink chunk_days if a single chunk ever exceeds 1000 for your org.
    """
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    today = date.today()
    total_days = max(1, (today - since).days)
    total_chunks = ((total_days + chunk_days - 1) // chunk_days) * len(qualifiers)
    cursor = since
    seen = set()
    call_num = 0
    while cursor <= today:
        chunk_end = min(cursor + timedelta(days=chunk_days), today)
        for qual in qualifiers:
            call_num += 1
            qual_label = qual.split(":", 1)[1]
            print(
                f"  [{call_num}/{total_chunks}] Searching {cursor} .. {chunk_end}"
                f" ({qual_label}) ...",
                end="", file=sys.stderr, flush=True,
            )
            q = (
                f"{qual} is:pr is:merged "
                f'"Code Review by Qodo" in:comments '
                f"merged:{cursor.isoformat()}..{chunk_end.isoformat()}"
            )
            out = run_gh([
                "api", "-X", "GET", "search/issues",
                "-f", f"q={q}",
                "--paginate",
                "--jq", (
                    ".items[] | {"
                    "number: .number, "
                    "repo: .repository_url, "
                    "url: .html_url, "
                    "creator: .user.login, "
                    "created_at: .created_at, "
                    "merged_at: .pull_request.merged_at"
                    "}"
                ),
            ])
            chunk_count = 0
            for line in filter(None, out.split("\n")):
                item = json.loads(line)
                owner_repo = item["repo"].split("/repos/", 1)[1]
                key = (owner_repo, item["number"])
                if key in seen:
                    continue
                seen.add(key)
                chunk_count += 1
                owner, repo = owner_repo.split("/", 1)
                yield {
                    "owner": owner,
                    "repo": repo,
                    "number": item["number"],
                    "url": item.get("url", ""),
                    "creator": item.get("creator", ""),
                    "created_at": item.get("created_at", ""),
                    "merged_at": item.get("merged_at", ""),
                }
            print(f" {chunk_count} PRs", file=sys.stderr)
        cursor = chunk_end + timedelta(days=1)



def fetch_pr_data(owner: str, repo: str, number: int) -> dict:
    """Fetch PR comments and line counts via a single GraphQL query.

    Returns {"comments": [...], "additions": int, "deletions": int}.
    Each comment: {"body": str, "created_at": str, "user": {"login": str}}.
    Caps comments at 100 (sufficient — Qodo reviews are always among the first).
    """
    query = (
        "query($owner:String!,$repo:String!,$number:Int!){"
        "repository(owner:$owner,name:$repo){"
        "pullRequest(number:$number){"
        "additions deletions "
        "comments(first:100){nodes{body createdAt author{login __typename}}}"
        "}}}"
    )
    out = run_gh([
        "api", "graphql",
        "-f", f"query={query}",
        "-f", f"owner={owner}",
        "-f", f"repo={repo}",
        "-F", f"number={number}",
    ])
    data = json.loads(out)
    pr = data["data"]["repository"]["pullRequest"]
    comments = [
        {
            "body": node["body"] or "",
            "created_at": node["createdAt"],
            "user": {
                "login": (node["author"] or {}).get("login", ""),
                "type": (node["author"] or {}).get("__typename", "User"),
            },
        }
        for node in pr["comments"]["nodes"]
    ]
    return {
        "comments": comments,
        "additions": pr["additions"] or 0,
        "deletions": pr["deletions"] or 0,
    }


def find_qodo_comment(comments):
    """Return the Qodo review comment, or None."""
    for c in comments:
        if QODO_MARKER.search(c.get("body", "") or ""):
            return c
    return None


def parse_qodo_comment(body: str) -> "QodoStats":
    """Parse a Qodo review comment body into structured QodoStats."""
    stats = QodoStats()
    if not body:
        return stats

    # Track which section we're currently in (None = preamble/unknown)
    section = None  # "action_required" | "review_recommended" | None

    for line in body.splitlines():
        # Detect section transitions — check before suggestion matching so
        # heading lines are never mis-counted as suggestion items.
        if _SECTION_ACTION.match(line.strip()):
            section = "action_required"
            continue
        if _SECTION_REVIEW.match(line.strip()):
            section = "review_recommended"
            continue

        # Match all numbered suggestion patterns on this line
        for m in SUGGESTION_LINE.finditer(line):
            title = m.group(1)
            is_implemented = any(marker in title for marker in IMPLEMENTED_MARKERS)
            cat = _classify_category(title)

            # Always increment global totals (covers PRs with no section headers)
            stats.total_suggestions += 1
            if is_implemented:
                stats.total_implemented += 1

            # Section counts
            if section == "action_required":
                stats.action_required_total += 1
                if is_implemented:
                    stats.action_required_implemented += 1
            elif section == "review_recommended":
                stats.review_recommended_total += 1
                if is_implemented:
                    stats.review_recommended_implemented += 1

            # Category counts
            if cat == "bug":
                stats.bugs_suggested += 1
                if is_implemented:
                    stats.bugs_implemented += 1
            elif cat == "rule_violation":
                stats.rule_violations_suggested += 1
                if is_implemented:
                    stats.rule_violations_implemented += 1
            elif cat == "requirement_gap":
                stats.requirement_gaps_suggested += 1
                if is_implemented:
                    stats.requirement_gaps_implemented += 1

            # Sub-label counts (Security / Correctness)
            sub_label = _classify_sublabel(title)

            if sub_label == "Security":
                stats.security_suggested += 1
                if is_implemented:
                    stats.security_implemented += 1
            elif sub_label == "Correctness":
                stats.correctness_suggested += 1
                if is_implemented:
                    stats.correctness_implemented += 1

            if section == "action_required" and is_implemented and sub_label:
                stats.spotlight_issues.append({
                    "title": _clean_title(title),
                    "category": cat,
                    "sub_label": sub_label,
                })

    return stats


def _classify_category(title: str) -> str:
    """Return 'bug', 'rule_violation', 'requirement_gap', or 'unknown'."""
    if _CAT_RULE.search(title):
        return "rule_violation"
    if _CAT_REQ.search(title):
        return "requirement_gap"
    if _CAT_BUG.search(title):
        return "bug"
    return "unknown"


def _classify_sublabel(title: str) -> Optional[str]:
    """Return 'Security', 'Correctness', or None."""
    if _CAT_SECURITY.search(title):
        return "Security"
    if _CAT_CORRECTNESS.search(title):
        return "Correctness"
    return None


def _clean_title(title: str) -> str:
    """Strip HTML tags and normalise whitespace for display."""
    text = _HTML_TAG.sub("", title)
    m = _STRIKETHROUGH.search(text)
    if m:
        return m.group(1).strip()
    # No strikethrough — strip emoji/label suffixes
    text = re.sub(r'\s*[☑✓].*$', '', text)       # strip ☑ and after
    text = re.sub(r'\s*[^\x00-\x7F].*$', '', text)  # strip emoji and after
    return text.strip()


CSV_COLUMNS = [
    "Repo Name", "PR #", "PR URL", "PR Creation Date", "PR Merge Date",
    "Hours to Merge", "PR Creator", "Lines Changed", "Has Qodo Review",
    "Action Required Suggestions", "Action Required Implemented",
    "Review Recommended Suggestions", "Review Recommended Implemented",
    "Bugs Suggested", "Bugs Implemented",
    "Rule Violations Suggested", "Rule Violations Implemented",
    "Requirement Gaps Suggested", "Requirement Gaps Implemented",
    "Total Suggestions", "Total Implemented",
    "Implementation Rate (%)", "Suggestions per 100 Lines",
    "Time to First Qodo Comment (min)", "Time to First Human Comment (min)",
    "Has Human Comment", "Spotlight Issues",
]


def _hours_between(iso_start: str, iso_end: str) -> int:
    """Return whole hours between two ISO-8601 timestamps. Returns 0 on error."""
    try:
        # Normalise GitHub's timestamps — strip trailing Z and any fractional
        # seconds so strptime works regardless of sub-second precision.
        def _parse(ts):
            ts = ts.rstrip("Z").split(".")[0]
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        delta = _parse(iso_end) - _parse(iso_start)
        return max(0, int(delta.total_seconds() // 3600))
    except (ValueError, TypeError, AttributeError):
        return 0


def _minutes_between(iso_start: str, iso_end: str) -> Optional[int]:
    """Return whole minutes between two ISO-8601 timestamps, or None on error."""
    try:
        def _parse(ts):
            ts = ts.rstrip("Z").split(".")[0]
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        delta = _parse(iso_end) - _parse(iso_start)
        return max(0, int(delta.total_seconds() // 60))
    except (ValueError, TypeError, AttributeError):
        return None


def compute_timing(pr: dict, comments: list) -> dict:
    """Return timing metrics derived from PR metadata and its comment list.

    Returns {"qodo_min": int|None, "human_min": int|None, "has_human": bool}.
    """
    pr_created = pr.get("created_at", "")
    qodo_comment = find_qodo_comment(comments)
    qodo_min = (
        _minutes_between(pr_created, qodo_comment["created_at"])
        if qodo_comment else None
    )
    qodo_login = qodo_comment.get("user", {}).get("login") if qodo_comment else None
    human_comments = [
        c for c in comments
        if not QODO_MARKER.search(c.get("body", "") or "")
        and c.get("user", {}).get("type") != "Bot"
        and c.get("user", {}).get("login") != qodo_login
    ]
    has_human = bool(human_comments)
    human_min = None
    if human_comments:
        first = min(human_comments, key=lambda c: c.get("created_at", ""))
        human_min = _minutes_between(pr_created, first["created_at"])
    return {"qodo_min": qodo_min, "human_min": human_min, "has_human": has_human}


def _output_stem(org: str, since: date, until: date, repos: Optional[List[str]] = None) -> str:
    """Return the base filename (no extension) for output files."""
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", org)
    if repos:
        n = len(repos)
        repo_segment = f"{n}-repo" if n == 1 else f"{n}-repos"
        return f"{safe_org}_{repo_segment}_{since.isoformat()}_{until.isoformat()}"
    return f"{safe_org}_{since.isoformat()}_{until.isoformat()}"


def build_csv_row(pr: dict, lines_changed: int, stats: Optional["QodoStats"],
                  timing: Optional[dict] = None) -> dict:
    has_qodo = stats is not None
    total = stats.total_suggestions if has_qodo else 0
    implemented = stats.total_implemented if has_qodo else 0
    timing = timing or {}

    impl_rate = f"{100 * implemented / total:.1f}" if total > 0 else ""
    per_100 = (
        f"{100 * total / lines_changed:.1f}" if lines_changed > 0 and total > 0 else ""
    )

    qodo_min = timing.get("qodo_min")
    human_min = timing.get("human_min")

    return {
        "Repo Name":                            pr["repo"],
        "PR #":                                 pr["number"],
        "PR URL":                               pr.get("url", ""),
        "PR Creation Date":                     pr.get("created_at", ""),
        "PR Merge Date":                        pr.get("merged_at", ""),
        "Hours to Merge":                       _hours_between(
                                                    pr.get("created_at", ""),
                                                    pr.get("merged_at", ""),
                                                ),
        "PR Creator":                           pr.get("creator", ""),
        "Lines Changed":                        lines_changed,
        "Has Qodo Review":                      has_qodo,
        "Action Required Suggestions":          stats.action_required_total if has_qodo else 0,
        "Action Required Implemented":          stats.action_required_implemented if has_qodo else 0,
        "Review Recommended Suggestions":       stats.review_recommended_total if has_qodo else 0,
        "Review Recommended Implemented":       stats.review_recommended_implemented if has_qodo else 0,
        "Bugs Suggested":                       stats.bugs_suggested if has_qodo else 0,
        "Bugs Implemented":                     stats.bugs_implemented if has_qodo else 0,
        "Rule Violations Suggested":            stats.rule_violations_suggested if has_qodo else 0,
        "Rule Violations Implemented":          stats.rule_violations_implemented if has_qodo else 0,
        "Requirement Gaps Suggested":           stats.requirement_gaps_suggested if has_qodo else 0,
        "Requirement Gaps Implemented":         stats.requirement_gaps_implemented if has_qodo else 0,
        "Total Suggestions":                    total,
        "Total Implemented":                    implemented,
        "Implementation Rate (%)":              impl_rate,
        "Suggestions per 100 Lines":            per_100,
        "Time to First Qodo Comment (min)":     qodo_min if qodo_min is not None else "",
        "Time to First Human Comment (min)":    human_min if human_min is not None else "",
        "Has Human Comment":                    timing.get("has_human", False),
        "Spotlight Issues":                     json.dumps(stats.spotlight_issues if has_qodo else []),
    }


def cmd_inspect(args):
    """Print the first Qodo comment we find so you can sanity-check the parser."""
    for pr in search_merged_prs(args.org, args.since, repos=args.repos):
        owner, repo, number = pr["owner"], pr["repo"], pr["number"]
        print(f"\r  Checking {owner}/{repo}#{number}...{' ' * 20}", end="", file=sys.stderr, flush=True)
        pr_data = fetch_pr_data(owner, repo, number)
        comments = pr_data["comments"]
        qodo = find_qodo_comment(comments)
        if not qodo:
            continue
        print(file=sys.stderr)
        stats = parse_qodo_comment(qodo["body"])
        print(f"=== {owner}/{repo}#{number} ===")
        print(f"Parser found: {stats.total_implemented}/{stats.total_suggestions} implemented\n")
        print("--- RAW COMMENT BODY ---")
        print(qodo["body"])
        return
    print(file=sys.stderr)
    print("No Qodo comments found in window.", file=sys.stderr)


def checkpoint_path(org):
    return Path(f"{org}-checkpoint.json")


def load_checkpoint(org):
    p = checkpoint_path(org)
    if p.exists():
        data = json.loads(p.read_text())
        return data
    return None


def save_checkpoint(org, state):
    checkpoint_path(org).write_text(json.dumps(state, indent=2))



def cmd_count(args):
    cp_path = checkpoint_path(args.org)
    processed = set()
    pr_total = 0
    suggestions_total = 0
    suggestions_implemented = 0
    rows: List[dict] = []

    if args.resume:
        data = load_checkpoint(args.org)
        if data:
            stored_repos = data.get("repos")
            current_repos = sorted(args.repos) if args.repos else None
            normalized_stored = sorted(stored_repos) if stored_repos else None
            if normalized_stored != current_repos:
                print(
                    "  Warning: checkpoint was created with different --repos scope"
                    " — starting fresh.",
                    file=sys.stderr,
                )
            else:
                pr_total = data["pr_total"]
                suggestions_total = data["suggestions_total"]
                suggestions_implemented = data["suggestions_implemented"]
                processed = {tuple(x) for x in data["processed"]}
                rows = data.get("rows", [])
                since_str = data.get("since", args.since.isoformat())
                args.since = date.fromisoformat(since_str)
                print(
                    f"  Resuming from checkpoint: {pr_total} PRs already processed.",
                    file=sys.stderr,
                )
        else:
            print("  No checkpoint found — starting fresh.", file=sys.stderr)

    for pr in search_merged_prs(args.org, args.since, repos=args.repos):
        owner, repo, number = pr["owner"], pr["repo"], pr["number"]
        if (owner, repo, str(number)) in processed or (owner, repo, number) in processed:
            continue
        pr_total += 1
        if not args.verbose:
            print(
                f"\r  [{pr_total} PRs | "
                f"{suggestions_implemented}/{suggestions_total} suggestions] "
                f"{owner}/{repo}#{number}{' ' * 10}",
                end="", file=sys.stderr, flush=True,
            )

        pr_data = fetch_pr_data(owner, repo, number)
        comments = pr_data["comments"]
        lines_changed = pr_data["additions"] + pr_data["deletions"]
        qodo = find_qodo_comment(comments)
        timing = compute_timing(pr, comments)

        if not qodo:
            continue  # rare false positive from in:comments search; skip

        stats = parse_qodo_comment(qodo["body"])
        suggestions_total += stats.total_suggestions
        suggestions_implemented += stats.total_implemented
        if args.verbose:
            print(
                f"{owner}/{repo}#{number}: "
                f"{stats.total_implemented}/{stats.total_suggestions} implemented"
            )

        rows.append(build_csv_row(pr, lines_changed, stats, timing))

        processed.add((owner, repo, str(number)))
        save_checkpoint(args.org, {
            "since": args.since.isoformat(),
            "pr_total": pr_total,
            "suggestions_total": suggestions_total,
            "suggestions_implemented": suggestions_implemented,
            "processed": list(processed),
            "rows": rows,
            "repos": sorted(args.repos) if args.repos else None,
        })
    if not args.verbose:
        print(file=sys.stderr)  # end the rolling status line

    today = date.today()
    stem = _output_stem(args.org, args.since, today, repos=args.repos)
    base = Path.cwd()

    csv_path = base / f"{stem}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, restval="")
        writer.writeheader()
        writer.writerows(rows)

    html_path = None
    try:
        html_path = base / f"{stem}.html"
        html_path.write_text(
            report.generate_html(rows, args.org, args.since, today, "logo.svg"),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"\n  Warning: HTML report not written: {exc}", file=sys.stderr)
        html_path = None

    if cp_path.exists():
        cp_path.unlink()

    print()
    if args.repos:
        print(f"Repos in scope:              {' '.join(args.repos)}")
    print(f"Window:                      {args.since} → {today}")
    print(f"Merged PRs in window:        {pr_total}")
    print(f"Total Qodo suggestions:      {suggestions_total}")
    print(f"Implemented suggestions:     {suggestions_implemented}")
    if suggestions_total:
        rate = 100 * suggestions_implemented / suggestions_total
        print(f"Implementation rate:         {rate:.1f}%")

    print(f"\nReports written:")
    print(f"  CSV:  {csv_path}")
    if html_path:
        print(f"  HTML: {html_path}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--org", required=True, help="GitHub org login (e.g., acme-corp)")
    window = p.add_mutually_exclusive_group()
    window.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD")
    window.add_argument("--days", type=int, default=365,
                        help="Lookback in days (default: 365)")
    p.add_argument("--inspect", action="store_true",
                   help="Print the first Qodo comment found and exit")
    p.add_argument("--verbose", action="store_true",
                   help="Print per-PR results")
    p.add_argument("--resume", action="store_true",
                   help="Resume from a previous checkpoint (ORG-checkpoint.json)")
    p.add_argument(
        "--repos", nargs="+", metavar="REPO",
        help="Limit to specific repos (e.g. --repos frontend-app backend-api)",
    )
    args = p.parse_args()

    if not args.since:
        args.since = date.today() - timedelta(days=args.days)

    if args.repos:
        args.repos = list(dict.fromkeys(args.repos))

    if args.inspect:
        cmd_inspect(args)
    else:
        cmd_count(args)


if __name__ == "__main__":
    main()