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

  # Full run, default 90-day lookback
  ./qodo_pr_stats.py --org acme-corp

  # Custom window
  ./qodo_pr_stats.py --org acme-corp --since 2025-05-12
  ./qodo_pr_stats.py --org acme-corp --days 90

  # Per-PR detail
  ./qodo_pr_stats.py --org acme-corp --verbose
"""

import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import report
import user_impact

from core import (
    QODO_MARKER, SUGGESTION_LINE, IMPLEMENTED_MARKERS, DISMISSED_MARKER,
    CSV_COLUMNS, QodoStats,
    find_qodo_comment, parse_qodo_comment,
    detect_ai_authored, parse_reviews, compute_speed_to_fix,
    compute_timing, build_csv_row,
    _hours_between, _minutes_between, _output_stem,
    _build_anon_maps, _apply_anonymization, _qodo_counts_by_week,
    checkpoint_path, load_checkpoint, save_checkpoint,
)

from collectors import get_collector
from collectors.github import (
    _LOC_PAGE_SIZE_DEFAULT, _LOC_PAGE_SIZE_MIN, _LOC_PAGE_SIZE_MAX,
    _PR_BATCH_SIZE_DEFAULT, _PR_BATCH_SIZE_MIN, _PR_BATCH_SIZE_MAX,
    TransientHttpError, run_gh,
)


def cmd_inspect(args, collector):
    """Print the first Qodo comment we find so you can sanity-check the parser."""
    for pr in collector.search_merged_prs(args.org, args.since, repos=args.repos):
        owner, repo, number = pr["owner"], pr["repo"], pr["number"]
        print(f"\r  Checking {owner}/{repo}#{number}...{' ' * 20}", end="", file=sys.stderr, flush=True)
        pr_data = collector.fetch_pr_data(owner, repo, number)
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


def cmd_test_hotfix_signals(args, collector):
    """Print hotfix detection counts per signal and combined for smoke-testing."""
    today = date.today()
    qualifiers = [f"repo:{args.org}/{r}" for r in args.repos] if args.repos else [f"org:{args.org}"]
    signals = {
        "title":    "hotfix in:title",
        "label":    "label:hotfix",
        "branch":   "head:hotfix",
        "combined": "(hotfix in:title OR label:hotfix OR head:hotfix)",
    }
    results = {}
    had_failures = False
    for name, signal in signals.items():
        total = 0
        failed = False
        for qual in qualifiers:
            q = (f"{qual} is:pr is:merged {signal} "
                 f"merged:{args.since.isoformat()}..{today.isoformat()}")
            try:
                out = run_gh(["api", "-X", "GET", "search/issues",
                              "-f", f"q={q}", "--jq", ".total_count"])
                total += int(out.strip())
            except (SystemExit, ValueError, Exception) as e:
                print(f"  WARNING: {name}/{qual} failed: {e}", file=sys.stderr)
                failed = True
                had_failures = True
        if not failed:
            results[name] = total
        label = name.ljust(10)
        suffix = " (ERROR)" if failed else ""
        print(f"  {label} {total}{suffix}")
    if len(results) == 4:
        signal_sum = results["title"] + results["label"] + results["branch"]
        combined = results["combined"]
        if combined <= signal_sum:
            print(f"\n  OK: combined ({combined}) <= sum of signals ({signal_sum}) — OR deduplication confirmed")
        else:
            print(f"\n  WARNING: combined ({combined}) > sum ({signal_sum}) — check GitHub Search OR behavior")
    if had_failures:
        sys.exit(1)


def cmd_count(args, collector):
    start_time = time.monotonic()
    cp_path = checkpoint_path(args.org)
    processed = set()
    pr_total = 0
    skipped_prs = 0
    suggestions_total = 0
    suggestions_implemented = 0
    qodo_loc_total = 0
    graphql_nodes = 0
    rows: List[dict] = []
    all_pr_loc: Optional[int] = None

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
                qodo_loc_total = data.get("qodo_loc_total", 0)
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

    print("  Fetching total org PR count...", end="", file=sys.stderr, flush=True)
    org_pr_count = collector.get_org_pr_count(args.org, args.since, repos=args.repos)
    print(f" {org_pr_count}" if org_pr_count is not None else " (unavailable)", file=sys.stderr)

    print("  Fetching weekly PR counts...", end="", file=sys.stderr, flush=True)
    weekly_coverage = collector.get_weekly_pr_counts(args.org, args.since, repos=args.repos)
    print(f" {len(weekly_coverage)} weeks", file=sys.stderr)

    print("  Fetching revert/hotfix counts...", end="", file=sys.stderr, flush=True)
    revert_count = collector.get_revert_pr_count(args.org, args.since, repos=args.repos)
    hotfix_count = collector.get_hotfix_pr_count(args.org, args.since, repos=args.repos)
    print(f" {revert_count} reverts, {hotfix_count} hotfixes", file=sys.stderr)

    all_pr_loc = collector.get_all_pr_loc(args.org, args.since, repos=args.repos, total_prs=org_pr_count, page_size=args.loc_page_size)
    loc_str = f"{all_pr_loc:,}" if all_pr_loc is not None else "(unavailable)"
    print(f"\r  Fetching total org LOC... {loc_str}\033[K", file=sys.stderr)

    all_qodo_prs = list(collector.search_merged_prs(args.org, args.since, repos=args.repos, total_prs=org_pr_count))
    pending = [
        pr for pr in all_qodo_prs
        if (pr["owner"], pr["repo"], str(pr["number"])) not in processed
        and (pr["owner"], pr["repo"], pr["number"]) not in processed
    ]
    qodo_total = len(pending)
    org_author_count = len({pr["creator"] for pr in all_qodo_prs if pr.get("creator")})
    qodo_by_week = _qodo_counts_by_week(all_qodo_prs)
    for week in weekly_coverage:
        week["qodo"] = qodo_by_week.get(week["week_start"], 0)

    pr_batch_size = args.pr_batch_size if args.pr_batch_size is not None else _PR_BATCH_SIZE_DEFAULT
    i = 0
    while i < len(pending):
        batch = pending[i:i + pr_batch_size]
        try:
            pr_data_map = collector.fetch_pr_data_batch(batch, raise_on_5xx=True)
        except TransientHttpError as exc:
            if pr_batch_size <= _PR_BATCH_SIZE_MIN:
                print(
                    f"\n  Persistent API errors on PR-data query at floor "
                    f"batch size {pr_batch_size}: {exc}",
                    file=sys.stderr, flush=True,
                )
                sys.exit(1)
            new_size = max(_PR_BATCH_SIZE_MIN, pr_batch_size // 2)
            print(
                f"\n  Persistent API errors on PR-data query — shrinking batch size "
                f"{pr_batch_size} → {new_size} and retrying the same batch.",
                file=sys.stderr, flush=True,
            )
            pr_batch_size = new_size
            continue
        graphql_nodes += len(batch) * 20
        for pr in batch:
            owner, repo, number = pr["owner"], pr["repo"], pr["number"]
            pr_total += 1
            if not args.verbose:
                print(
                    f"\r  [{pr_total}/{qodo_total} PRs] "
                    f"{owner}/{repo}#{number}\033[K",
                    end="", file=sys.stderr, flush=True,
                )

            pr_data = pr_data_map.get(pr.get("node_id", ""))
            if not pr_data:
                print(
                    f"\n  Warning: no GraphQL data for {owner}/{repo}#{number} "
                    f"(node_id={pr.get('node_id', '')!r}) — skipping",
                    file=sys.stderr,
                )
                skipped_prs += 1
                continue

            comments = pr_data["comments"]
            lines_added = pr_data["additions"]
            qodo = find_qodo_comment(comments)
            timing = compute_timing(pr, comments)

            if not qodo:
                # Qodo comment not in first 20 — re-fetch with higher limit before giving up.
                pr_data = collector.fetch_pr_data(owner, repo, number, comments_limit=100)
                graphql_nodes += 100
                comments = pr_data["comments"]
                lines_added = pr_data["additions"]
                qodo = find_qodo_comment(comments)
                timing = compute_timing(pr, comments)
                if not qodo:
                    print(
                        f"\n  Warning: Qodo comment not found for {owner}/{repo}#{number} — skipping",
                        file=sys.stderr,
                    )
                    continue

            qodo_loc_total += pr_data["additions"]
            stats = parse_qodo_comment(qodo["body"])
            suggestions_total += stats.total_suggestions
            suggestions_implemented += stats.total_implemented
            if args.verbose:
                print(
                    f"{owner}/{repo}#{number}: "
                    f"{stats.total_implemented}/{stats.total_suggestions} implemented"
                )

            reviews_data = pr_data.get("reviews", [])
            review_info = parse_reviews(reviews_data)
            body = pr_data.get("body", "")
            labels = pr_data.get("labels", [])
            is_ai, ai_type = detect_ai_authored(body, labels)
            # Use the real Qodo content timestamp (first real edit if available)
            edits = (qodo.get("user_content_edits") or [])
            qodo_ts = edits[0]["edited_at"] if len(edits) >= 2 else qodo.get("created_at")
            speed_info = compute_speed_to_fix(qodo_ts, pr_data.get("commits", []))
            extras = {
                "is_ai_authored": is_ai,
                "ai_author_type": ai_type,
                "reviewer_count": review_info["reviewer_count"],
                "had_request_changes": review_info["had_request_changes"],
                "approver": review_info["approver"],
                "ci_status": pr_data.get("ci_status"),
                "commits_after_qodo": speed_info["commits_after_qodo"],
                "speed_to_fix_min": speed_info["speed_to_fix_min"],
            }

            rows.append(build_csv_row(pr, lines_added, stats, timing, extras=extras))

            processed.add((owner, repo, str(number)))
            save_checkpoint(args.org, {
                "since": args.since.isoformat(),
                "pr_total": pr_total,
                "suggestions_total": suggestions_total,
                "suggestions_implemented": suggestions_implemented,
                "qodo_loc_total": qodo_loc_total,
                "processed": list(processed),
                "rows": rows,
                "repos": sorted(args.repos) if args.repos else None,
            })
        i += len(batch)
    if not args.verbose:
        print(file=sys.stderr)  # end the rolling status line

    today = date.today()

    if args.anonymize:
        user_map, repo_map = _build_anon_maps(rows)
        _apply_anonymization(rows, user_map, repo_map, scope=args.anonymize)

    stem = _output_stem(args.org, args.since, today, repos=args.repos,
                        anonymize=args.anonymize)
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
            report.generate_html(
                rows, args.org, args.since, today, "logo.svg",
                org_pr_count=org_pr_count,
                org_author_count=org_author_count,
                weekly_coverage=weekly_coverage,
                revert_count=revert_count,
                hotfix_count=hotfix_count,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"\n  Warning: HTML report not written: {exc}", file=sys.stderr)
        html_path = None

    user_html_path = None
    try:
        user_html_path = base / f"{stem}_user.html"
        user_html_path.write_text(
            user_impact.generate_user_html(
                rows, args.org, args.since.isoformat(), today.isoformat(),
                logo_path="logo.svg",
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"\n  Warning: user impact report not written: {exc}", file=sys.stderr)
        user_html_path = None

    if cp_path.exists():
        cp_path.unlink()

    repos_in_results = sorted({r["Repo Name"] for r in rows})

    print()
    if args.repos:
        print(f"Repos in scope:              {' '.join(args.repos)}")
    print(f"Window:                      {args.since} → {today}")
    print(f"Merged PRs in window:        {pr_total - skipped_prs}")
    if skipped_prs:
        print(f"Skipped (no GraphQL data):   {skipped_prs}")
    print(f"GraphQL nodes requested:     {graphql_nodes:,}")
    print(f"Total Qodo suggestions:      {suggestions_total}")
    print(f"Implemented suggestions:     {suggestions_implemented}")
    if suggestions_total:
        rate = 100 * suggestions_implemented / suggestions_total
        print(f"Implementation rate:         {rate:.1f}%")
    if all_pr_loc is not None:
        print(f"Total LOC added (all PRs):   {all_pr_loc:,}")
        if all_pr_loc > 0:
            pct = min(100.0, 100 * qodo_loc_total / all_pr_loc)
            print(f"Qodo-reviewed LOC:           {qodo_loc_total:,}  ({pct:.1f}% of total)")
        else:
            print(f"Qodo-reviewed LOC:           {qodo_loc_total:,}")

    elapsed = time.monotonic() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    elapsed_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"

    print(f"\nRepos in results ({len(repos_in_results)}):")
    for repo in repos_in_results:
        print(f"  {repo}")

    print(f"\nReports written:")
    print(f"  CSV:  {csv_path}")
    if html_path:
        print(f"  HTML: {html_path}")
    if user_html_path:
        print(f"  User: {user_html_path}")
    print(f"\nCompleted in {elapsed_str}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--org", required=True, help="GitHub org login (e.g., acme-corp)")
    window = p.add_mutually_exclusive_group()
    window.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD")
    window.add_argument("--days", type=int, default=90,
                        help="Lookback in days (default: 90)")
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
    p.add_argument("--anonymize", nargs="?", const="all", default=None,
                   choices=["all", "users", "repos"],
                   metavar="SCOPE",
                   help="Replace identifying data with stable pseudonyms. "
                        "SCOPE: 'users' (PR Creator / Final Approver only), "
                        "'repos' (Repo Name / PR URL only), "
                        "or omit SCOPE to anonymize both.")
    p.add_argument("--test-hotfix-signals", action="store_true",
                   help="Smoke-test hotfix detection signals (branch/label/title) and exit")
    p.add_argument(
        "--loc-page-size", type=int, default=None, metavar="N",
        help=(
            f"Starting page size for the org-wide LOC GraphQL query "
            f"(default: {_LOC_PAGE_SIZE_DEFAULT}, range: {_LOC_PAGE_SIZE_MIN}-{_LOC_PAGE_SIZE_MAX}). "
            "Lower it (e.g. 25 or 10) for very large orgs where GitHub returns "
            "persistent 5xx or stream-cancel errors on the LOC fetch."
        ),
    )
    p.add_argument(
        "--pr-batch-size", type=int, default=None, metavar="N",
        help=(
            f"Starting batch size for the per-PR GraphQL data lookup "
            f"(default: {_PR_BATCH_SIZE_DEFAULT}, range: {_PR_BATCH_SIZE_MIN}-{_PR_BATCH_SIZE_MAX}). "
            "Lower it for very large orgs where GitHub returns persistent 5xx "
            "or stream-cancel errors during the main PR walk."
        ),
    )
    args = p.parse_args()

    if args.loc_page_size is not None and not (
        _LOC_PAGE_SIZE_MIN <= args.loc_page_size <= _LOC_PAGE_SIZE_MAX
    ):
        p.error(
            f"--loc-page-size must be between {_LOC_PAGE_SIZE_MIN} and "
            f"{_LOC_PAGE_SIZE_MAX} (got {args.loc_page_size})"
        )

    if args.pr_batch_size is not None and not (
        _PR_BATCH_SIZE_MIN <= args.pr_batch_size <= _PR_BATCH_SIZE_MAX
    ):
        p.error(
            f"--pr-batch-size must be between {_PR_BATCH_SIZE_MIN} and "
            f"{_PR_BATCH_SIZE_MAX} (got {args.pr_batch_size})"
        )

    if not args.since:
        args.since = date.today() - timedelta(days=args.days)

    if args.repos:
        args.repos = list(dict.fromkeys(args.repos))

    collector = get_collector("github")

    if args.inspect:
        cmd_inspect(args, collector)
    elif args.test_hotfix_signals:
        cmd_test_hotfix_signals(args, collector)
    else:
        cmd_count(args, collector)


if __name__ == "__main__":
    main()
