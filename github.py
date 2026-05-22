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


# Stable marker in Qodo Merge's review comment, independent of bot account name.
QODO_MARKER = re.compile(r"Code Review by Qodo", re.IGNORECASE)

_TRANSIENT_HTTP = re.compile(r"HTTP 5\d\d")

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

# Indicates the user explicitly dismissed a suggestion (✗ Dismissed badge).
# A dismissed item has strikethrough but must NOT count as implemented.
DISMISSED_MARKER = "✗ Dismissed"   # ✗ = U+2717

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

_AI_BODY_PATTERNS = [
    # GitHub Copilot — covers github-copilot[bot], copilot-swe-agent[bot], and plain Copilot
    (re.compile(r"co-authored-by:[^\n]*copilot", re.IGNORECASE), "copilot"),
    # Cursor
    (re.compile(r"co-authored-by:[^\n]*cursor", re.IGNORECASE), "cursor"),
    (re.compile(r"generated\s+with\s+cursor", re.IGNORECASE), "cursor"),
    # Claude / Claude Code
    (re.compile(r"co-authored-by:[^\n]*claude", re.IGNORECASE), "claude"),
    (re.compile(r"claude\.com/claude-code", re.IGNORECASE), "claude"),
    # Codex / OpenAI (Codex, ChatGPT, GPT — all use @openai.com email)
    (re.compile(r"co-authored-by:[^\n]*(codex|@openai\.com|chatgpt)", re.IGNORECASE), "codex"),
    # Kiro (Amazon)
    (re.compile(r"co-authored-by:[^\n]*\bkiro\b", re.IGNORECASE), "kiro"),
    (re.compile(r"@kiro-agent\b", re.IGNORECASE), "kiro"),
    # Gemini Code Assist
    (re.compile(r"co-authored-by:[^\n]*gemini", re.IGNORECASE), "gemini"),
    # Windsurf / Codeium (Windsurf uses cascade@windsurf.ai or noreply@codeium.com)
    (re.compile(r"co-authored-by:[^\n]*(windsurf|codeium)", re.IGNORECASE), "windsurf"),
    # Devin
    (re.compile(r"co-authored-by:[^\n]*devin-ai-integration", re.IGNORECASE), "devin"),
    (re.compile(r"app\.devin\.ai/sessions/", re.IGNORECASE), "devin"),
    # Aider (email is always aider@aider.chat)
    (re.compile(r"co-authored-by:[^\n]*\baider\b", re.IGNORECASE), "aider"),
    # Amazon Q Developer (prose attribution only — no git trailer in the wild)
    (re.compile(r"co-authored\s+by\s+amazon\s+q\b", re.IGNORECASE), "amazon-q"),
]
_AI_LABEL_NAMES = {
    "copilot": "copilot",
    "ai-generated": "ai",
    "cursor": "cursor",
    "claude": "claude",
    "codex": "codex",
    "kiro": "kiro",
    "gemini": "gemini",
    "windsurf": "windsurf",
    "codeium": "windsurf",  # Codeium label → windsurf (same product family)
    "devin": "devin",
    "aider": "aider",
    "amazon-q": "amazon-q",
}

_GQL_SEARCH_QUERY = (
    "query($q:String!,$cursor:String){"
    "search(query:$q,type:ISSUE,first:100,after:$cursor){"
    "pageInfo{hasNextPage endCursor}"
    "nodes{...on PullRequest{"
    "number id "
    "repository{nameWithOwner} "
    "url "
    "author{login} "
    "createdAt mergedAt"
    "}}}}"
)

_GQL_LOC_SEARCH_QUERY = (
    "query($q:String!,$cursor:String){"
    "search(query:$q,type:ISSUE,first:100,after:$cursor){"
    "pageInfo{hasNextPage endCursor}"
    "nodes{...on PullRequest{"
    "additions"
    "}}}}"
)


@dataclass
class QodoStats:
    action_required_total: int = 0
    action_required_implemented: int = 0
    action_required_dismissed: int = 0
    review_recommended_total: int = 0
    review_recommended_implemented: int = 0
    review_recommended_dismissed: int = 0
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
    total_dismissed: int = 0
    security_suggested: int = 0
    security_implemented: int = 0
    correctness_suggested: int = 0
    correctness_implemented: int = 0
    spotlight_issues: list = field(default_factory=list)


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


def run_gh(args, paginate=False):
    """Run `gh` and return stdout. Retries on rate limits and transient HTTP 5xx errors."""
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


def search_merged_prs(org, since, chunk_days=30, repos=None):
    """Yield a dict of PR metadata for every merged PR in the window.

    Each yielded dict contains: owner, repo, number, node_id, url, creator,
    created_at, merged_at.

    Uses GraphQL search (core API, 5000 req/hr) instead of REST search/issues
    (Search API, 30 req/min) to avoid rate limiting on large orgs.

    Chunked by date range to stay under GitHub's 1000-result search query cap.
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


def fetch_pr_data_batch(prs: list, batch_size: int = 50) -> dict:
    """Fetch PR data for multiple PRs in batched GraphQL calls.

    prs: list of dicts with a "node_id" key (from search_merged_prs).
    Returns: dict mapping node_id -> {"comments": [...], "additions": int, "deletions": int}.
    """
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
            "comments(first:20){nodes{body createdAt "
            "userContentEdits(last:2){nodes{editedAt}} "
            "author{login __typename}}}"
            "}}"
            "}"
        )
        out = run_gh(["api", "graphql", "-f", f"query={query}"])
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
    seen_spotlight: set = set()

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
            is_dismissed = DISMISSED_MARKER in title
            is_implemented = (
                any(marker in title for marker in IMPLEMENTED_MARKERS)
                and not is_dismissed
            )
            cat = _classify_category(title)

            # Always increment global totals (covers PRs with no section headers)
            stats.total_suggestions += 1
            if is_implemented:
                stats.total_implemented += 1
            if is_dismissed:
                stats.total_dismissed += 1

            # Section counts
            if section == "action_required":
                stats.action_required_total += 1
                if is_implemented:
                    stats.action_required_implemented += 1
                if is_dismissed:
                    stats.action_required_dismissed += 1
            elif section == "review_recommended":
                stats.review_recommended_total += 1
                if is_implemented:
                    stats.review_recommended_implemented += 1
                if is_dismissed:
                    stats.review_recommended_dismissed += 1

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
                key = (_clean_title(title), sub_label)
                if key not in seen_spotlight:
                    seen_spotlight.add(key)
                    stats.spotlight_issues.append({
                        "title": key[0],
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


def detect_ai_authored(body: str, labels: list) -> tuple:
    """Return (is_ai_authored: bool, ai_type: str) for a PR.

    Checks body text for co-author signatures and labels for AI tags.
    Returns the first match found; ai_type is '' when not AI-authored.
    """
    text = body or ""
    for pattern, ai_type in _AI_BODY_PATTERNS:
        if pattern.search(text):
            return True, ai_type
    for label in labels:
        lower = label.lower()
        for name, ai_type in _AI_LABEL_NAMES.items():
            if name in lower:
                return True, ai_type
    return False, ""


def parse_reviews(reviews: list) -> dict:
    """Extract reviewer count, request-changes flag, and final approver.

    reviewer_count: unique authors across all reviews.
    had_request_changes: True if any review has state CHANGES_REQUESTED.
    approver: login of the last reviewer to submit an APPROVED state.
    """
    reviewers: set = set()
    had_changes = False
    approved_reviews = []
    for r in sorted(reviews, key=lambda r: r.get("submittedAt") or ""):
        login = (r.get("author") or {}).get("login", "")
        if login:
            reviewers.add(login)
        if r.get("state") == "CHANGES_REQUESTED":
            had_changes = True
        if r.get("state") == "APPROVED" and login:
            approved_reviews.append((r.get("submittedAt") or "", login))
    approver = approved_reviews[-1][1] if approved_reviews else ""
    return {
        "reviewer_count": len(reviewers),
        "had_request_changes": had_changes,
        "approver": approver,
    }


def compute_speed_to_fix(qodo_ts: Optional[str], commits: list) -> dict:
    """Return commits pushed after qodo_ts and time to the first such commit.

    ISO string comparison works because GitHub timestamps are all UTC Z-suffixed.
    speed_to_fix_min is None when there are no commits after the Qodo review.
    """
    if not qodo_ts or not commits:
        return {"commits_after_qodo": 0, "speed_to_fix_min": None}
    after = sorted(
        [c for c in commits if ((c.get("commit") or {}).get("committedDate") or "") > qodo_ts],
        key=lambda c: (c.get("commit") or {}).get("committedDate") or "",
    )
    if not after:
        return {"commits_after_qodo": 0, "speed_to_fix_min": None}
    first_ts = after[0]["commit"]["committedDate"]
    return {
        "commits_after_qodo": len(after),
        "speed_to_fix_min": _minutes_between(qodo_ts, first_ts),
    }


CSV_COLUMNS = [
    "Repo Name", "PR #", "PR URL", "PR Creation Date", "PR Merge Date",
    "Hours to Merge", "PR Creator", "Lines Changed",
    "Action Required Suggestions", "Action Required Implemented", "Action Required Dismissed",
    "Review Recommended Suggestions", "Review Recommended Implemented", "Review Recommended Dismissed",
    "Bugs Suggested", "Bugs Implemented",
    "Rule Violations Suggested", "Rule Violations Implemented",
    "Requirement Gaps Suggested", "Requirement Gaps Implemented",
    "Total Suggestions", "Total Implemented", "Total Dismissed",
    "Implementation Rate (%)", "Suggestions per 100 Lines",
    "Time to First Qodo Comment (min)", "Time to First Human Comment (min)",
    "Has Human Comment", "Spotlight Issues",
    "Is AI Authored", "AI Author Type",
    "Reviewer Count", "Had Request Changes", "Final Approver",
    "CI Status",
    "Commits After Qodo", "Speed to First Fix (min)",
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
    if qodo_comment:
        # GitHub logs creation as an edit; last:2 gives [first_real_edit, creation].
        # When 2 edits exist, edit[0] is when the placeholder was replaced with real content.
        edits = qodo_comment.get("user_content_edits", [])
        qodo_ts = edits[0]["edited_at"] if len(edits) >= 2 else qodo_comment["created_at"]
        qodo_min = _minutes_between(pr_created, qodo_ts)
    else:
        qodo_min = None
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


def _output_stem(org: str, since: date, until: date,
                 repos: Optional[List[str]] = None,
                 anonymize=None) -> str:
    """Return the base filename (no extension) for output files."""
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", org)
    suffix = "_anon" if anonymize else ""
    if repos:
        n = len(repos)
        repo_segment = f"{n}-repo" if n == 1 else f"{n}-repos"
        return f"{safe_org}_{repo_segment}_{since.isoformat()}_{until.isoformat()}{suffix}"
    return f"{safe_org}_{since.isoformat()}_{until.isoformat()}{suffix}"


def _build_anon_maps(rows):
    """Build deterministic user and repo pseudonym mappings from row data.

    Returns tuple (user_map, repo_map) where each is a dict mapping
    original names to pseudonyms (User 1, User 2, ..., Repo 1, Repo 2, ...).

    Blank approvers are excluded. Rows missing Final Approver key don't crash.
    """
    users = sorted(
        (
            {r.get("PR Creator", "") for r in rows} |
            {r.get("Final Approver", "") for r in rows}
        ) - {""}
    )
    repos = sorted({r.get("Repo Name", "") for r in rows} - {""})
    user_map = {name: f"User {i + 1}" for i, name in enumerate(users)}
    repo_map = {name: f"Repo {i + 1}" for i, name in enumerate(repos)}
    return user_map, repo_map


def _apply_anonymization(rows, user_map, repo_map, scope="all"):
    """Apply pseudonym substitutions in-place to row data.

    scope: "all" → users and repos; "users" → user columns only; "repos" → repo columns only.
    """
    for row in rows:
        if scope in ("all", "users"):
            row["PR Creator"] = user_map.get(row.get("PR Creator", ""), row.get("PR Creator", ""))
            approver = row.get("Final Approver", "")
            row["Final Approver"] = user_map.get(approver, approver)
        if scope in ("all", "repos"):
            row["Repo Name"] = repo_map.get(row.get("Repo Name", ""), row.get("Repo Name", ""))
            row["PR URL"] = f"#PR-{row.get('PR #', '')}"


def build_csv_row(pr: dict, lines_changed: int, stats: Optional["QodoStats"],
                  timing: Optional[dict] = None,
                  extras: Optional[dict] = None) -> dict:
    has_qodo = stats is not None
    total = stats.total_suggestions if has_qodo else 0
    implemented = stats.total_implemented if has_qodo else 0
    timing = timing or {}
    extras = extras or {}

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
        "Action Required Suggestions":          stats.action_required_total if has_qodo else 0,
        "Action Required Implemented":          stats.action_required_implemented if has_qodo else 0,
        "Action Required Dismissed":            stats.action_required_dismissed if has_qodo else 0,
        "Review Recommended Suggestions":       stats.review_recommended_total if has_qodo else 0,
        "Review Recommended Implemented":       stats.review_recommended_implemented if has_qodo else 0,
        "Review Recommended Dismissed":         stats.review_recommended_dismissed if has_qodo else 0,
        "Bugs Suggested":                       stats.bugs_suggested if has_qodo else 0,
        "Bugs Implemented":                     stats.bugs_implemented if has_qodo else 0,
        "Rule Violations Suggested":            stats.rule_violations_suggested if has_qodo else 0,
        "Rule Violations Implemented":          stats.rule_violations_implemented if has_qodo else 0,
        "Requirement Gaps Suggested":           stats.requirement_gaps_suggested if has_qodo else 0,
        "Requirement Gaps Implemented":         stats.requirement_gaps_implemented if has_qodo else 0,
        "Total Suggestions":                    total,
        "Total Implemented":                    implemented,
        "Total Dismissed":                      stats.total_dismissed if has_qodo else 0,
        "Implementation Rate (%)":              impl_rate,
        "Suggestions per 100 Lines":            per_100,
        "Time to First Qodo Comment (min)":     qodo_min if qodo_min is not None else "",
        "Time to First Human Comment (min)":    human_min if human_min is not None else "",
        "Has Human Comment":                    timing.get("has_human", False),
        "Spotlight Issues":                     json.dumps(stats.spotlight_issues if has_qodo else []),
        "Is AI Authored":                       extras.get("is_ai_authored", False),
        "AI Author Type":                       extras.get("ai_author_type", ""),
        "Reviewer Count":                       extras.get("reviewer_count", 0),
        "Had Request Changes":                  extras.get("had_request_changes", False),
        "Final Approver":                       extras.get("approver", ""),
        "CI Status":                            extras.get("ci_status") or "",
        "Commits After Qodo":                   extras.get("commits_after_qodo", 0),
        "Speed to First Fix (min)":             extras.get("speed_to_fix_min")
                                                if extras.get("speed_to_fix_min") is not None else "",
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


def get_org_author_count(org: str, since: date, repos: Optional[List[str]] = None, chunk_days: int = 30) -> Optional[int]:
    """Return count of unique PR authors who merged a PR in the window (Qodo or not).

    Uses date chunking to stay under GitHub Search API's 1000-result cap,
    matching the approach used by search_merged_prs.
    """
    today = date.today()
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


def get_all_pr_loc(org: str, since: date, repos: Optional[List[str]] = None, chunk_days: int = 30) -> Optional[int]:
    """Return total additions across all merged PRs in the window."""
    today = date.today()
    qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
    cursor = since
    total = 0
    try:
        while cursor <= today:
            chunk_end = min(cursor + timedelta(days=chunk_days), today)
            for qual in qualifiers:
                q = (
                    f"{qual} is:pr is:merged "
                    f"merged:{cursor.isoformat()}..{chunk_end.isoformat()}"
                )
                end_cursor = None
                while True:
                    gh_args = [
                        "api", "graphql",
                        "-f", f"query={_GQL_LOC_SEARCH_QUERY}",
                        "-f", f"q={q}",
                    ]
                    if end_cursor:
                        gh_args += ["-f", f"cursor={end_cursor}"]
                    else:
                        gh_args += ["-F", "cursor=null"]
                    out = run_gh(gh_args)
                    data = json.loads(out)
                    search = data["data"]["search"]
                    for node in search["nodes"]:
                        if not node:
                            continue
                        total += node.get("additions") or 0
                    if not search["pageInfo"]["hasNextPage"]:
                        break
                    end_cursor = search["pageInfo"]["endCursor"]
            cursor = chunk_end + timedelta(days=1)
    except Exception:
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


def _qodo_counts_by_week(prs):
    """Count PRs per calendar week. Returns {monday_iso_str: count}.

    Each PR is bucketed to the Monday of its merged_at week.
    PRs with a missing or empty merged_at are silently skipped.
    """
    counts = {}
    for pr in prs:
        merged_at = pr.get("merged_at") or ""
        if not merged_at:
            continue
        d = date.fromisoformat(merged_at[:10])
        monday = d - timedelta(days=d.weekday())
        key = monday.isoformat()
        counts[key] = counts.get(key, 0) + 1
    return counts


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

    print("  Fetching total org LOC...", end="", file=sys.stderr, flush=True)
    all_pr_loc = get_all_pr_loc(args.org, args.since, repos=args.repos)
    print(f" {all_pr_loc:,}" if all_pr_loc is not None else " (unavailable)", file=sys.stderr)

    all_qodo_prs = list(search_merged_prs(args.org, args.since, repos=args.repos))
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

    for i in range(0, len(pending), 25):
        batch = pending[i:i + 25]
        pr_data_map = fetch_pr_data_batch(batch)
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
            lines_changed = pr_data["additions"] + pr_data["deletions"]
            qodo = find_qodo_comment(comments)
            timing = compute_timing(pr, comments)

            if not qodo:
                # Qodo comment not in first 20 — re-fetch with higher limit before giving up.
                pr_data = fetch_pr_data(owner, repo, number, comments_limit=100)
                graphql_nodes += 100
                comments = pr_data["comments"]
                lines_changed = pr_data["additions"] + pr_data["deletions"]
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

            rows.append(build_csv_row(pr, lines_changed, stats, timing, extras=extras))

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
    args = p.parse_args()

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