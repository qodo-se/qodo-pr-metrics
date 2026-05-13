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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

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
        result = subprocess.run(cmd, capture_output=True, text=True)
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


def fetch_comments(owner, repo, number):
    """All issue comments on a PR. (PRs use the issues comments endpoint.)"""
    out = run_gh([
        "api", f"repos/{owner}/{repo}/issues/{number}/comments",
        "--paginate", "--jq", ".[]",
    ])
    comments = []
    for line in filter(None, out.split("\n")):
        comments.append(json.loads(line))
    return comments


def fetch_pr_lines(owner: str, repo: str, number: int) -> int:
    """Return additions + deletions for a PR. Returns 0 on any error."""
    out = run_gh([
        "api", f"repos/{owner}/{repo}/pulls/{number}",
        "--jq", "{additions: .additions, deletions: .deletions}",
    ])
    try:
        data = json.loads(out.strip())
        return (data.get("additions") or 0) + (data.get("deletions") or 0)
    except (json.JSONDecodeError, ValueError):
        return 0


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


def _output_stem(org: str, since: date, until: date, repos: Optional[list[str]] = None) -> str:
    """Return the base filename (no extension) for output files."""
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", org)
    if repos:
        n = len(repos)
        repo_segment = f"{n}-repo" if n == 1 else f"{n}-repos"
        return f"{safe_org}_{repo_segment}_{since.isoformat()}_{until.isoformat()}"
    return f"{safe_org}_{since.isoformat()}_{until.isoformat()}"


def build_csv_row(pr: dict, lines_changed: int, stats: Optional["QodoStats"]) -> dict:
    has_qodo = stats is not None
    total = stats.total_suggestions if has_qodo else 0
    implemented = stats.total_implemented if has_qodo else 0

    impl_rate = f"{100 * implemented / total:.1f}" if total > 0 else ""
    per_100 = (
        f"{100 * total / lines_changed:.1f}" if lines_changed > 0 and total > 0 else ""
    )

    return {
        "Repo Name":                        pr["repo"],
        "PR #":                             pr["number"],
        "PR URL":                           pr.get("url", ""),
        "PR Creation Date":                 pr.get("created_at", ""),
        "PR Merge Date":                    pr.get("merged_at", ""),
        "Hours to Merge":                   _hours_between(
                                                pr.get("created_at", ""),
                                                pr.get("merged_at", ""),
                                            ),
        "PR Creator":                       pr.get("creator", ""),
        "Lines Changed":                    lines_changed,
        "Has Qodo Review":                  has_qodo,
        "Action Required Suggestions":      stats.action_required_total if has_qodo else 0,
        "Action Required Implemented":      stats.action_required_implemented if has_qodo else 0,
        "Review Recommended Suggestions":   stats.review_recommended_total if has_qodo else 0,
        "Review Recommended Implemented":   stats.review_recommended_implemented if has_qodo else 0,
        "Bugs Suggested":                   stats.bugs_suggested if has_qodo else 0,
        "Bugs Implemented":                 stats.bugs_implemented if has_qodo else 0,
        "Rule Violations Suggested":        stats.rule_violations_suggested if has_qodo else 0,
        "Rule Violations Implemented":      stats.rule_violations_implemented if has_qodo else 0,
        "Requirement Gaps Suggested":       stats.requirement_gaps_suggested if has_qodo else 0,
        "Requirement Gaps Implemented":     stats.requirement_gaps_implemented if has_qodo else 0,
        "Total Suggestions":                total,
        "Total Implemented":                implemented,
        "Implementation Rate (%)":          impl_rate,
        "Suggestions per 100 Lines":        per_100,
    }


def cmd_inspect(args):
    """Print the first Qodo comment we find so you can sanity-check the parser."""
    for pr in search_merged_prs(args.org, args.since):
        owner, repo, number = pr["owner"], pr["repo"], pr["number"]
        print(f"\r  Checking {owner}/{repo}#{number}...{' ' * 20}", end="", file=sys.stderr, flush=True)
        comments = fetch_comments(owner, repo, number)
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


def get_total_pr_count(org: str, since: date, repos: Optional[list[str]] = None) -> Optional[int]:
    """Return total merged PRs in the window from search API (approximate)."""
    today = date.today()
    if repos:
        total = 0
        all_failed = True
        for repo in repos:
            q = (
                f"repo:{org}/{repo} is:pr is:merged "
                f"merged:{since.isoformat()}..{today.isoformat()}"
            )
            out = run_gh([
                "api", "-X", "GET", "search/issues",
                "-f", f"q={q}",
                "--jq", ".total_count",
            ])
            try:
                total += int(out.strip())
                all_failed = False
            except ValueError:
                pass
        return None if all_failed else total
    q = (
        f"org:{org} is:pr is:merged "
        f"merged:{since.isoformat()}..{today.isoformat()}"
    )
    out = run_gh([
        "api", "-X", "GET", "search/issues",
        "-f", f"q={q}",
        "--jq", ".total_count",
    ])
    try:
        return int(out.strip())
    except ValueError:
        return None


def cmd_count(args):
    cp_path = checkpoint_path(args.org)
    processed = set()
    pr_total = 0
    prs_with_qodo = 0
    suggestions_total = 0
    suggestions_implemented = 0
    rows: list[dict] = []

    if args.resume:
        data = load_checkpoint(args.org)
        if data:
            stored_repos = data.get("repos")
            _repos = getattr(args, "repos", None)
            current_repos = sorted(_repos) if _repos else None
            normalized_stored = sorted(stored_repos) if stored_repos else None
            if normalized_stored != current_repos:
                print(
                    "  Warning: checkpoint was created with different --repos scope"
                    " — starting fresh.",
                    file=sys.stderr,
                )
            else:
                pr_total = data["pr_total"]
                prs_with_qodo = data["prs_with_qodo"]
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

    print("  Fetching total PR count...", end="", file=sys.stderr, flush=True)
    total_prs = get_total_pr_count(args.org, args.since)
    print(f" {total_prs}" if total_prs is not None else " (unavailable)", file=sys.stderr)

    for pr in search_merged_prs(args.org, args.since):
        owner, repo, number = pr["owner"], pr["repo"], pr["number"]
        if (owner, repo, str(number)) in processed or (owner, repo, number) in processed:
            continue
        pr_total += 1
        if not args.verbose:
            total_str = f"/{total_prs}" if total_prs is not None else ""
            print(
                f"\r  [{pr_total}{total_str} PRs | {prs_with_qodo} with Qodo | "
                f"{suggestions_implemented}/{suggestions_total} suggestions] "
                f"{owner}/{repo}#{number}{' ' * 10}",
                end="", file=sys.stderr, flush=True,
            )

        comments = fetch_comments(owner, repo, number)
        qodo = find_qodo_comment(comments)

        if not qodo:
            if args.verbose:
                print(f"{owner}/{repo}#{number}: (no Qodo comment)")
            rows.append(build_csv_row(pr, lines_changed=0, stats=None))
            processed.add((owner, repo, str(number)))
            save_checkpoint(args.org, {
                "since": args.since.isoformat(),
                "pr_total": pr_total,
                "prs_with_qodo": prs_with_qodo,
                "suggestions_total": suggestions_total,
                "suggestions_implemented": suggestions_implemented,
                "processed": list(processed),
                "rows": rows,
                "repos": sorted(getattr(args, "repos", None) or []) or None,
            })
            continue

        prs_with_qodo += 1
        stats = parse_qodo_comment(qodo["body"])
        suggestions_total += stats.total_suggestions
        suggestions_implemented += stats.total_implemented
        if args.verbose:
            print(
                f"{owner}/{repo}#{number}: "
                f"{stats.total_implemented}/{stats.total_suggestions} implemented"
            )

        lines_changed = fetch_pr_lines(owner, repo, number)
        rows.append(build_csv_row(pr, lines_changed, stats))

        processed.add((owner, repo, str(number)))
        save_checkpoint(args.org, {
            "since": args.since.isoformat(),
            "pr_total": pr_total,
            "prs_with_qodo": prs_with_qodo,
            "suggestions_total": suggestions_total,
            "suggestions_implemented": suggestions_implemented,
            "processed": list(processed),
            "rows": rows,
            "repos": sorted(getattr(args, "repos", None) or []) or None,
        })
    if not args.verbose:
        print(file=sys.stderr)  # end the rolling status line

    today = date.today()
    stem = _output_stem(args.org, args.since, today)
    base = Path.cwd()

    csv_path = base / f"{stem}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    html_path = None
    try:
        html_path = base / f"{stem}.html"
        html_path.write_text(
            report.generate_html(rows, args.org, args.since, today, "logo.png"),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"\n  Warning: HTML report not written: {exc}", file=sys.stderr)
        html_path = None

    if cp_path.exists():
        cp_path.unlink()

    print()
    print(f"Window:                      {args.since} → {today}")
    print(f"Merged PRs in window:        {pr_total}")
    print(f"PRs with a Qodo review:      {prs_with_qodo}")
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
    args = p.parse_args()

    if not args.since:
        args.since = date.today() - timedelta(days=args.days)

    if args.inspect:
        cmd_inspect(args)
    else:
        cmd_count(args)


if __name__ == "__main__":
    main()