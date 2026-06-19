"""GitHub collector: all `gh`-CLI / GraphQL I/O behind the Collector contract."""

import json
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from typing import List, Optional

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


class GitHubCollector:

    def search_merged_prs(self, org, since, until=None, chunk_days=None,
                          repos=None, total_prs=None, qodo_only=True):
        """Yield PR metadata dicts for merged PRs in the window.

        until:     end of window (defaults to today).
        qodo_only: when True, restrict to PRs containing a Qodo review comment.
        """
        qualifiers = [f"repo:{org}/{r}" for r in repos] if repos else [f"org:{org}"]
        end = until if until is not None else date.today()
        if chunk_days is None:
            chunk_days = _safe_chunk_days(total_prs, since)
        total_days = max(1, (end - since).days)
        total_chunks = ((total_days + chunk_days - 1) // chunk_days) * len(qualifiers)
        cursor = since
        seen = set()
        call_num = 0
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=chunk_days), end)
            for qual in qualifiers:
                call_num += 1
                qual_label = qual.split(":", 1)[1]
                chunk_prefix = f"[{call_num}/{total_chunks}] " if total_chunks > 1 else ""
                print(
                    f"  {chunk_prefix}Searching {cursor} .. {chunk_end}"
                    f" ({qual_label}) ...",
                    end="", file=sys.stderr, flush=True,
                )
                qodo_filter = '"Code Review by Qodo" in:comments ' if qodo_only else ""
                q = (
                    f"{qual} is:pr is:merged "
                    f"{qodo_filter}"
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
                        f"  Warning: search cap hit for PR chunk "
                        f"{cursor}..{chunk_end}: fetched {chunk_count}/{issue_count} PRs"
                        f" — some PRs may be missing. Try a shorter window.",
                        file=sys.stderr,
                    )
            cursor = chunk_end + timedelta(days=1)

    def fetch_pr_data(self, owner: str, repo: str, number: int, comments_limit: int = 20) -> dict:
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

    def fetch_pr_data_batch(self, prs: list, batch_size: int = 50, raise_on_5xx: bool = False,
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

    def get_org_author_count(self, org: str, since: date, repos: Optional[List[str]] = None, chunk_days: Optional[int] = None, total_prs: Optional[int] = None) -> Optional[int]:
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

    def get_org_repo_count(self, org: str) -> Optional[int]:
        """Return total repository count for the org (public + private)."""
        try:
            out = run_gh(["api", f"orgs/{org}", "--jq", ".public_repos + .total_private_repos"])
            return int(out.strip())
        except Exception:
            return None

    def get_org_pr_count(self, org: str, since: date, repos: Optional[List[str]] = None) -> Optional[int]:
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

    def get_qodo_pr_count(self, org: str, since: date, repos: Optional[List[str]] = None) -> Optional[int]:
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

    def get_all_pr_loc(self, org: str, since: date, repos: Optional[List[str]] = None, chunk_days: Optional[int] = None, total_prs: Optional[int] = None, page_size: Optional[int] = None) -> Optional[int]:
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

    def get_revert_pr_count(self, org: str, since: date,
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

    def get_hotfix_pr_count(self, org: str, since: date,
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

    def get_weekly_pr_counts(self, org: str, since: date,
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
