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
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


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

# Section header patterns — anchored to heading-like lines (##, ###, **...**)
# to prevent suggestion titles containing "action required" from resetting the
# section counter mid-parse.
_SECTION_ACTION = re.compile(
    r"^(?:#+\s*|\*{1,2}\s*)action\s+required", re.IGNORECASE
)
_SECTION_REVIEW = re.compile(
    r"^(?:#+\s*|\*{1,2}\s*)review\s+recommended", re.IGNORECASE
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


def search_merged_prs(org, since, chunk_days=30):
    """Yield (owner, repo, number) for every merged PR in the window.

    Chunked by date range to stay under the search API's 1000-result cap.
    Shrink chunk_days if a single chunk ever exceeds 1000 for your org.
    """
    today = date.today()
    total_days = max(1, (today - since).days)
    total_chunks = (total_days + chunk_days - 1) // chunk_days
    cursor = since
    seen = set()
    chunk_num = 0
    while cursor <= today:
        chunk_end = min(cursor + timedelta(days=chunk_days), today)
        chunk_num += 1
        print(
            f"  [{chunk_num}/{total_chunks}] Searching {cursor} .. {chunk_end} ...",
            end="", file=sys.stderr, flush=True,
        )
        q = (
            f"org:{org} is:pr is:merged "
            f"merged:{cursor.isoformat()}..{chunk_end.isoformat()}"
        )
        out = run_gh([
            "api", "-X", "GET", "search/issues",
            "-f", f"q={q}",
            "--paginate",
            "--jq", ".items[] | {number: .number, repo: .repository_url}",
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
            yield owner, repo, item["number"]
        print(f" {chunk_count} PRs", file=sys.stderr)
        cursor = chunk_end + timedelta(days=1)


def fetch_comments(owner, repo, number):
    """All issue comments on a PR. (PRs use the issues comments endpoint.)"""
    out = run_gh(
        ["api", f"repos/{owner}/{repo}/issues/{number}/comments", "--paginate"],
    )
    # --paginate concatenates pages; each page is a JSON array. The simplest
    # robust parse: ask gh to flatten via jq.
    out = run_gh([
        "api", f"repos/{owner}/{repo}/issues/{number}/comments",
        "--paginate", "--jq", ".[]",
    ])
    comments = []
    for line in filter(None, out.split("\n")):
        comments.append(json.loads(line))
    return comments


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

        # Match numbered suggestion lines
        m = SUGGESTION_LINE.search(line)
        if not m:
            continue

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


def cmd_inspect(args):
    """Print the first Qodo comment we find so you can sanity-check the parser."""
    for owner, repo, number in search_merged_prs(args.org, args.since):
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


def get_total_pr_count(org, since):
    """Return total merged PRs in the window from search API (approximate)."""
    today = date.today()
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

    if args.resume:
        data = load_checkpoint(args.org)
        if data:
            pr_total = data["pr_total"]
            prs_with_qodo = data["prs_with_qodo"]
            suggestions_total = data["suggestions_total"]
            suggestions_implemented = data["suggestions_implemented"]
            processed = {tuple(x) for x in data["processed"]}
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

    for owner, repo, number in search_merged_prs(args.org, args.since):
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
        else:
            prs_with_qodo += 1
            stats = parse_qodo_comment(qodo["body"])
            suggestions_total += stats.total_suggestions
            suggestions_implemented += stats.total_implemented
            if args.verbose:
                print(f"{owner}/{repo}#{number}: {stats.total_implemented}/{stats.total_suggestions} implemented")

        processed.add((owner, repo, str(number)))
        save_checkpoint(args.org, {
            "since": args.since.isoformat(),
            "pr_total": pr_total,
            "prs_with_qodo": prs_with_qodo,
            "suggestions_total": suggestions_total,
            "suggestions_implemented": suggestions_implemented,
            "processed": list(processed),
        })

    if not args.verbose:
        print(file=sys.stderr)  # end the rolling status line

    if cp_path.exists():
        cp_path.unlink()

    print()
    print(f"Window:                      {args.since} → {date.today()}")
    print(f"Merged PRs in window:        {pr_total}")
    print(f"PRs with a Qodo review:      {prs_with_qodo}")
    print(f"Total Qodo suggestions:      {suggestions_total}")
    print(f"Implemented suggestions:     {suggestions_implemented}")
    if suggestions_total:
        rate = 100 * suggestions_implemented / suggestions_total
        print(f"Implementation rate:         {rate:.1f}%")


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