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

# Matches gh/GitHub transient failures worth retrying: HTTP 5xx from the REST/GraphQL
# endpoint, and HTTP/2 "stream error ... CANCEL/INTERNAL_ERROR" frames sent by GitHub's
# edge when a GraphQL query is too expensive (seen on large-org LOC fetches).
_TRANSIENT_HTTP = re.compile(r"HTTP 5\d\d|stream error.*(?:CANCEL|INTERNAL_ERROR)")

_GQL_SEARCH_QUERY = (
    "query($q:String!,$cursor:String){"
    "search(query:$q,type:ISSUE,first:100,after:$cursor){"
    "issueCount "
    "pageInfo{hasNextPage endCursor}"
    "nodes{...on PullRequest{"
    "number id "
    "repository{nameWithOwner} "
    "url "
    "author{login} "
    "createdAt mergedAt"
    "}}}}"
)

_LOC_PAGE_SIZE_DEFAULT = 50
_LOC_PAGE_SIZE_MIN = 10
_LOC_PAGE_SIZE_MAX = 100  # GitHub GraphQL `first:` hard cap

# Starting batch size for the per-PR GraphQL `nodes(ids:[...])` lookup that
# pulls comments/reviews/commits. This query is heavier than the LOC search,
# so we keep the default lower and the floor smaller to recover from edge 5xx
# on large orgs.
_PR_BATCH_SIZE_DEFAULT = 25
_PR_BATCH_SIZE_MIN = 5
_PR_BATCH_SIZE_MAX = 50


def _gql_loc_query(page_size: int) -> str:
    return (
        "query($q:String!,$cursor:String){"
        f"search(query:$q,type:ISSUE,first:{page_size},after:$cursor){{"
        "issueCount "
        "pageInfo{hasNextPage endCursor}"
        "nodes{...on PullRequest{"
        "additions"
        "}}}}"
    )


class TransientHttpError(Exception):
    """Raised by run_gh when transient-error retries are exhausted and the caller opted in to recover.

    Covers HTTP 5xx responses and HTTP/2 stream CANCEL/INTERNAL_ERROR frames
    (see _TRANSIENT_HTTP), both of which GitHub's edge can emit for expensive
    GraphQL queries on large orgs.
    """


def _safe_chunk_days(known_count: Optional[int], since: date, target: int = 800) -> int:
    """Compute chunk_days to keep search results safely under GitHub's 1000-result cap.

    Targets `target` results per chunk. Falls back to 30 when count is unknown.
    """
    if not known_count:
        return 30
    span = max(1, (date.today() - since).days)
    daily_rate = known_count / span
    if daily_rate <= 0:
        return 30
    return max(1, min(30, int(target / daily_rate)))


def _rate_limit_reset_epoch():
    """Return the Unix timestamp when the GitHub search rate limit resets."""
    try:
        out = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".resources.search.reset"],
            capture_output=True, text=True, timeout=15,
        )
        return int(out.stdout.strip())
    except Exception:
        return None


def run_gh(args, paginate=False, raise_on_5xx=False):
    """Run `gh` and return stdout. Retries on rate limits and transient HTTP errors.

    Transient errors include HTTP 5xx responses and HTTP/2 stream
    CANCEL/INTERNAL_ERROR frames (see _TRANSIENT_HTTP).

    If `raise_on_5xx` is True, raises TransientHttpError after transient
    retries are exhausted instead of calling sys.exit, so the caller can
    adapt and retry (e.g. by shrinking page size). The kwarg name is kept
    for backward compatibility; it gates the stream-error path too.
    """
    cmd = ["gh"] + args
    if paginate and "--paginate" not in cmd:
        cmd.append("--paginate")
    rate_retried = False
    http_retries = 0
    max_http_retries = 3
    while True:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode == 0:
            return result.stdout
        stderr = result.stderr
        if "rate limit" in stderr.lower() and not rate_retried:
            rate_retried = True
            reset = _rate_limit_reset_epoch()
            wait = max(0, reset - int(time.time())) + 5 if reset else 60
            print(
                f"\n  Rate limit hit — waiting {wait}s before retry...",
                file=sys.stderr, flush=True,
            )
            time.sleep(wait)
            continue
        m = _TRANSIENT_HTTP.search(stderr)
        if m and http_retries < max_http_retries:
            http_retries += 1
            wait = 5 * (3 ** (http_retries - 1))  # 5s, 15s, 45s
            print(
                f"\n  {m.group()} — retrying in {wait}s"
                f" ({http_retries}/{max_http_retries})...",
                file=sys.stderr, flush=True,
            )
            time.sleep(wait)
            continue
        if m and raise_on_5xx:
            raise TransientHttpError(f"`{' '.join(cmd)}` failed:\n{stderr}")
        sys.exit(f"`{' '.join(cmd)}` failed:\n{stderr}")


def _parse_search_gql_nodes(nodes):
    """Parse GraphQL search result nodes into the PR dict format used by search_merged_prs.

    Nodes that are empty dicts (non-PullRequest hits from the search index) are skipped.
    """
    results = []
    for node in nodes:
        if not node.get("number"):
            continue
        owner, repo = node["repository"]["nameWithOwner"].split("/", 1)
        results.append({
            "owner": owner,
            "repo": repo,
            "number": node["number"],
            "node_id": node["id"],
            "url": node.get("url", ""),
            "creator": (node.get("author") or {}).get("login", ""),
            "created_at": node.get("createdAt", ""),
            "merged_at": node.get("mergedAt", ""),
        })
    return results


def search_merged_prs(org, since, chunk_days=None, repos=None, total_prs=None):
    """Yield a dict of PR metadata for every merged PR in the window.

    Each yielded dict contains: owner, repo, number, node_id, url, creator,
    created_at, merged_at.

    Uses GraphQL search (core API, 5000 req/hr) instead of REST search/issues
    (Search API, 30 req/min) to avoid rate limiting on large orgs.

    Chunked by date range to stay under GitHub's 1000-result search query cap.
    Pass total_prs to enable dynamic chunk sizing for high-volume orgs.
    """
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    today = date.today()
    if chunk_days is None:
        chunk_days = _safe_chunk_days(total_prs, since)
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
            chunk_prefix = f"[{call_num}/{total_chunks}] " if total_chunks > 1 else ""
            print(
                f"  {chunk_prefix}Searching {cursor} .. {chunk_end}"
                f" ({qual_label}) ...",
                end="", file=sys.stderr, flush=True,
            )
            q = (
                f"{qual} is:pr is:merged "
                f'"Code Review by Qodo" in:comments '
                f"merged:{cursor.isoformat()}..{chunk_end.isoformat()}"
            )
            end_cursor = None
            chunk_count = 0
            issue_count = 0
            while True:
                gh_args = [
                    "api", "graphql",
                    "-f", f"query={_GQL_SEARCH_QUERY}",
                    "-f", f"q={q}",
                ]
                if end_cursor:
                    gh_args += ["-f", f"cursor={end_cursor}"]
                else:
                    gh_args += ["-F", "cursor=null"]
                out = run_gh(gh_args)
                data = json.loads(out)
                search = data["data"]["search"]
                issue_count = search.get("issueCount", 0)
                for pr in _parse_search_gql_nodes(search["nodes"]):
                    key = (pr["owner"], pr["repo"], pr["number"])
                    if key in seen:
                        continue
                    seen.add(key)
                    chunk_count += 1
                    yield pr
                if not search["pageInfo"]["hasNextPage"]:
                    break
                end_cursor = search["pageInfo"]["endCursor"]
            print(f" {chunk_count} PRs", file=sys.stderr)
            if issue_count > chunk_count:
                print(
                    f"  Warning: search cap hit for Qodo PR chunk "
                    f"{cursor}..{chunk_end}: fetched {chunk_count}/{issue_count} PRs"
                    f" — some reviewed PRs may be missing. Try a shorter --since window.",
                    file=sys.stderr,
                )
        cursor = chunk_end + timedelta(days=1)


def _extract_ci_status(last_commit_data: Optional[dict]) -> Optional[str]:
    """Extract the rollup CI state from a lastCommit GraphQL node."""
    if not last_commit_data:
        return None
    nodes = last_commit_data.get("nodes", [])
    if not nodes:
        return None
    rollup = (nodes[0].get("commit") or {}).get("statusCheckRollup")
    if not rollup:
        return None
    return rollup.get("state")


def fetch_pr_data(owner: str, repo: str, number: int, comments_limit: int = 20) -> dict:
    """Fetch PR comments and line counts via a single GraphQL query.

    Returns a dict with keys:
      - comments:  list of comment dicts, each with keys body, created_at, user
      - additions: int
      - deletions: int
      - body:      str (PR description body)
      - labels:    list of str
      - reviews:   list of review nodes
      - ci_status: str or None (GitHub status check rollup state)
      - commits:   list of commit nodes (capped at 100)
    """
    query = (
        "query($owner:String!,$repo:String!,$number:Int!){"
        "repository(owner:$owner,name:$repo){"
        "pullRequest(number:$number){"
        "additions deletions body "
        "labels(first:10){nodes{name}} "
        "reviews(last:100){nodes{author{login} state submittedAt}} "
        "lastCommit:commits(last:1){nodes{commit{statusCheckRollup{state}}}} "
        "allCommits:commits(last:100){nodes{commit{committedDate message}}} "
        f"comments(first:{comments_limit})"
        "{nodes{body createdAt "
        "userContentEdits(last:2){nodes{editedAt}} "
        "author{login __typename}}}"
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
            "user_content_edits": [
                {"edited_at": e["editedAt"]}
                for e in node.get("userContentEdits", {}).get("nodes", [])
            ],
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
        "body": pr.get("body") or "",
        "labels": [n["name"] for n in (pr.get("labels") or {}).get("nodes", [])],
        "reviews": (pr.get("reviews") or {}).get("nodes", []),
        "ci_status": _extract_ci_status(pr.get("lastCommit")),
        "commits": (pr.get("allCommits") or {}).get("nodes", []),
    }


def fetch_pr_data_batch(prs: list, batch_size: int = 50, raise_on_5xx: bool = False,
                        comments_first: int = 20) -> dict:
    """Fetch PR data for multiple PRs in batched GraphQL calls.

    prs: list of dicts with a "node_id" key (from search_merged_prs).
    Returns: dict mapping node_id -> {"comments": [...], "additions": int, "deletions": int}.

    If `raise_on_5xx` is True, transient 5xx / stream-cancel errors that
    survive run_gh's retries propagate as TransientHttpError instead of
    exiting, so the caller can adapt (e.g. shrink batch size and retry).

    `comments_first` caps how many issue comments are pulled per PR (default
    20, matching the impact report). The audit passes a higher value so its
    "no human comment" / TTFC classification isn't fooled by PRs whose first
    20 comments are all bots.
    """
    comments_first = max(1, min(comments_first, 100))  # GitHub `first:` hard cap
    results = {}
    for i in range(0, len(prs), batch_size):
        batch = prs[i:i + batch_size]
        ids_str = ", ".join(f'"{pr["node_id"]}"' for pr in batch)
        query = (
            "{"
            f"nodes(ids:[{ids_str}]){{"
            "... on PullRequest{"
            "id additions deletions body "
            "labels(first:10){nodes{name}} "
            "reviews(last:100){nodes{author{login} state submittedAt}} "
            "lastCommit:commits(last:1){nodes{commit{statusCheckRollup{state}}}} "
            "allCommits:commits(last:100){nodes{commit{committedDate message}}} "
            f"comments(first:{comments_first}){{nodes{{body createdAt "
            "userContentEdits(last:2){nodes{editedAt}} "
            "author{login __typename}}}"
            "}}"
            "}"
        )
        out = run_gh(["api", "graphql", "-f", f"query={query}"], raise_on_5xx=raise_on_5xx)
        data = json.loads(out)
        for node in data["data"]["nodes"]:
            if not node:
                continue
            node_id = node["id"]
            comments = [
                {
                    "body": n["body"] or "",
                    "created_at": n["createdAt"],
                    "user_content_edits": [
                        {"edited_at": e["editedAt"]}
                        for e in n.get("userContentEdits", {}).get("nodes", [])
                    ],
                    "user": {
                        "login": (n["author"] or {}).get("login", ""),
                        "type": (n["author"] or {}).get("__typename", "User"),
                    },
                }
                for n in node["comments"]["nodes"]
            ]
            results[node_id] = {
                "comments": comments,
                "additions": node["additions"] or 0,
                "deletions": node["deletions"] or 0,
                "body": node.get("body") or "",
                "labels": [n["name"] for n in (node.get("labels") or {}).get("nodes", [])],
                "reviews": (node.get("reviews") or {}).get("nodes", []),
                "ci_status": _extract_ci_status(node.get("lastCommit")),
                "commits": (node.get("allCommits") or {}).get("nodes", []),
            }
    return results




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



def get_org_author_count(org: str, since: date, repos: Optional[List[str]] = None, chunk_days: Optional[int] = None, total_prs: Optional[int] = None) -> Optional[int]:
    """Return count of unique PR authors who merged a PR in the window (Qodo or not).

    Uses date chunking to stay under GitHub Search API's 1000-result cap,
    matching the approach used by search_merged_prs.
    Pass total_prs to enable dynamic chunk sizing for high-volume orgs.
    """
    today = date.today()
    if chunk_days is None:
        chunk_days = _safe_chunk_days(total_prs, since)
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    authors: set = set()
    cursor = since
    while cursor <= today:
        chunk_end = min(cursor + timedelta(days=chunk_days), today)
        for qual in qualifiers:
            q = (
                f"{qual} is:pr is:merged "
                f"merged:{cursor.isoformat()}..{chunk_end.isoformat()}"
            )
            try:
                out = run_gh([
                    "api", "-X", "GET", "search/issues",
                    "-f", f"q={q}",
                    "--paginate",
                    "--jq", ".items[].user.login",
                ])
                for line in filter(None, out.split("\n")):
                    authors.add(line.strip().strip('"'))
            except Exception:
                return None
        cursor = chunk_end + timedelta(days=1)
    return len(authors)


def get_org_repo_count(org: str) -> Optional[int]:
    """Return total repository count for the org (public + private)."""
    try:
        out = run_gh(["api", f"orgs/{org}", "--jq", ".public_repos + .total_private_repos"])
        return int(out.strip())
    except Exception:
        return None


def get_org_pr_count(org: str, since: date, repos: Optional[List[str]] = None) -> Optional[int]:
    """Return count of all merged PRs in the window (Qodo or not)."""
    today = date.today()
    if repos:
        total = 0
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
            except ValueError:
                return None
        return total
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


def get_qodo_pr_count(org: str, since: date, repos: Optional[List[str]] = None) -> Optional[int]:
    """Return count of merged PRs with a Qodo review comment in the window."""
    today = date.today()
    if repos:
        total = 0
        for repo in repos:
            q = (
                f"repo:{org}/{repo} is:pr is:merged "
                f'"Code Review by Qodo" in:comments '
                f"merged:{since.isoformat()}..{today.isoformat()}"
            )
            out = run_gh([
                "api", "-X", "GET", "search/issues",
                "-f", f"q={q}",
                "--jq", ".total_count",
            ])
            try:
                total += int(out.strip())
            except ValueError:
                return None
        return total
    q = (
        f"org:{org} is:pr is:merged "
        f'"Code Review by Qodo" in:comments '
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


def get_all_pr_loc(org: str, since: date, repos: Optional[List[str]] = None, chunk_days: Optional[int] = None, total_prs: Optional[int] = None, page_size: Optional[int] = None) -> Optional[int]:
    """Return total additions across all merged PRs in the window.

    Pass total_prs to enable dynamic chunk sizing for high-volume orgs.
    Pass page_size to override the default GraphQL `first:` value (still
    shrinks adaptively on persistent API errors).
    """
    today = date.today()
    if chunk_days is None:
        chunk_days = _safe_chunk_days(total_prs, since)
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    cursor = since
    total = 0
    pr_count = 0
    page_size = page_size if page_size is not None else _LOC_PAGE_SIZE_DEFAULT
    _frac = f"0/{total_prs}" if total_prs is not None else "0"
    print(f"\r  [{_frac} PRs] Fetching total org LOC...\033[K", end="", file=sys.stderr, flush=True)
    try:
        while cursor <= today:
            chunk_end = min(cursor + timedelta(days=chunk_days), today)
            for qual in qualifiers:
                q = (
                    f"{qual} is:pr is:merged "
                    f"merged:{cursor.isoformat()}..{chunk_end.isoformat()}"
                )
                end_cursor = None
                chunk_pr_count = 0
                issue_count = 0
                while True:
                    gh_args = [
                        "api", "graphql",
                        "-f", f"query={_gql_loc_query(page_size)}",
                        "-f", f"q={q}",
                    ]
                    if end_cursor:
                        gh_args += ["-f", f"cursor={end_cursor}"]
                    else:
                        gh_args += ["-F", "cursor=null"]
                    try:
                        out = run_gh(gh_args, raise_on_5xx=True)
                    except TransientHttpError as exc:
                        if page_size <= _LOC_PAGE_SIZE_MIN:
                            raise
                        new_size = max(_LOC_PAGE_SIZE_MIN, page_size // 2)
                        print(
                            f"\n  Persistent API errors on LOC query — shrinking page size "
                            f"{page_size} → {new_size} and retrying the same page.",
                            file=sys.stderr, flush=True,
                        )
                        page_size = new_size
                        continue
                    data = json.loads(out)
                    search = data["data"]["search"]
                    issue_count = search.get("issueCount", 0)
                    for node in search["nodes"]:
                        if not node:
                            continue
                        total += node.get("additions") or 0
                        pr_count += 1
                        chunk_pr_count += 1
                    _frac = f"{pr_count}/{total_prs}" if total_prs is not None else str(pr_count)
                    print(f"\r  [{_frac} PRs] Fetching total org LOC...\033[K", end="", file=sys.stderr, flush=True)
                    if not search["pageInfo"]["hasNextPage"]:
                        break
                    end_cursor = search["pageInfo"]["endCursor"]
                if issue_count > chunk_pr_count:
                    print(
                        f"\n  Warning: search cap hit for LOC chunk "
                        f"{cursor}..{chunk_end}: fetched {chunk_pr_count}/{issue_count} PRs"
                        f" — total LOC may be understated. Try a shorter --since window.",
                        file=sys.stderr,
                    )
            cursor = chunk_end + timedelta(days=1)
    except Exception as exc:
        print(f"\n  Warning: LOC fetch failed: {exc}", file=sys.stderr)
        return None
    return total


def get_revert_pr_count(org: str, since: date,
                        repos: Optional[List[str]] = None) -> Optional[int]:
    """Count merged PRs with 'revert' in the title merged since `since`."""
    today = date.today()
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    total = 0
    for qual in qualifiers:
        q = (f"{qual} is:pr is:merged revert in:title "
             f"merged:{since.isoformat()}..{today.isoformat()}")
        try:
            out = run_gh(["api", "-X", "GET", "search/issues",
                          "-f", f"q={q}", "--jq", ".total_count"])
            total += int(out.strip())
        except Exception:
            return None
    return total


def get_hotfix_pr_count(org: str, since: date,
                        repos: Optional[List[str]] = None) -> Optional[int]:
    """Count merged PRs matching any hotfix signal (branch, label, or title)."""
    today = date.today()
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    total = 0
    for qual in qualifiers:
        q = (f"{qual} is:pr is:merged "
             f"(hotfix in:title OR label:hotfix OR head:hotfix) "
             f"merged:{since.isoformat()}..{today.isoformat()}")
        try:
            out = run_gh(["api", "-X", "GET", "search/issues",
                          "-f", f"q={q}", "--jq", ".total_count"])
            total += int(out.strip())
        except Exception:
            return None
    return total


def _search_pr_count_range(org: str, from_date: date, to_date: date,
                            repos: Optional[List[str]] = None) -> Optional[int]:
    """Count merged PRs in [from_date, to_date]. Returns None if any query fails."""
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    total = 0
    for qual in qualifiers:
        q = (f"{qual} is:pr is:merged "
             f"merged:{from_date.isoformat()}..{to_date.isoformat()}")
        try:
            out = run_gh(["api", "-X", "GET", "search/issues",
                          "-f", f"q={q}", "--jq", ".total_count"])
            total += int(out.strip())
        except Exception:
            return None
    return total



def get_weekly_pr_counts(org: str, since: date,
                          repos: Optional[List[str]] = None) -> list:
    """Return [{week_start, total, qodo}, ...] from the Monday of since's week through today.

    Makes 1 search API call per week (total only). The caller is responsible for
    filling in the qodo counts from already-fetched PR data via _qodo_counts_by_week.
    """
    today = date.today()
    start = since - timedelta(days=since.weekday())  # rewind to Monday
    results = []
    cursor = start
    while cursor <= today:
        week_end = min(cursor + timedelta(days=6), today)
        total = _search_pr_count_range(org, cursor, week_end, repos)
        if total is None:
            print(f"  Warning: failed to fetch PR count for week {cursor}", file=sys.stderr)
        results.append({"week_start": cursor.isoformat(), "total": total, "qodo": 0})
        cursor += timedelta(days=7)
        time.sleep(2)  # pace to ~20 req/min, well under the Search API 30 req/min limit
    return results


def cmd_test_hotfix_signals(args):
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


def cmd_count(args):
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
    org_pr_count = get_org_pr_count(args.org, args.since, repos=args.repos)
    print(f" {org_pr_count}" if org_pr_count is not None else " (unavailable)", file=sys.stderr)

    print("  Fetching weekly PR counts...", end="", file=sys.stderr, flush=True)
    weekly_coverage = get_weekly_pr_counts(args.org, args.since, repos=args.repos)
    print(f" {len(weekly_coverage)} weeks", file=sys.stderr)

    print("  Fetching revert/hotfix counts...", end="", file=sys.stderr, flush=True)
    revert_count = get_revert_pr_count(args.org, args.since, repos=args.repos)
    hotfix_count = get_hotfix_pr_count(args.org, args.since, repos=args.repos)
    print(f" {revert_count} reverts, {hotfix_count} hotfixes", file=sys.stderr)

    all_pr_loc = get_all_pr_loc(args.org, args.since, repos=args.repos, total_prs=org_pr_count, page_size=args.loc_page_size)
    loc_str = f"{all_pr_loc:,}" if all_pr_loc is not None else "(unavailable)"
    print(f"\r  Fetching total org LOC... {loc_str}\033[K", file=sys.stderr)

    all_qodo_prs = list(search_merged_prs(args.org, args.since, repos=args.repos, total_prs=org_pr_count))
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
            pr_data_map = fetch_pr_data_batch(batch, raise_on_5xx=True)
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
                pr_data = fetch_pr_data(owner, repo, number, comments_limit=100)
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

    if args.inspect:
        cmd_inspect(args)
    elif args.test_hotfix_signals:
        cmd_test_hotfix_signals(args)
    else:
        cmd_count(args)


if __name__ == "__main__":
    main()