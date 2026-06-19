"""Provider-agnostic Qodo metrics: parsing, classification, timing, CSV,
anonymization, and checkpointing. No git-provider I/O lives here."""

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional


# All generated reports and resume checkpoints are written here (gitignored).
REPORTS_DIR = Path("reports")

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
IMPLEMENTED_MARKERS = ("~~", "<s>", "<del>", "<strike>", "☑")  # ☑

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
# Trailing Qodo label/badge blocks, e.g. "<code>🐞 Bug</code> <code>≡ Correctness</code>".
# Used to strip badges for the dedupe key without truncating the core title.
_TRAILING_BADGES = re.compile(r"(?:\s*<code>[^<]*</code>)+\s*$")

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

    # When Qodo re-reviews a PR after new commits, it edits the same comment and
    # appends a folded "Previous review results" block holding earlier review
    # snapshots (marked by "<!-- FOLDED_SECTION_START -->"). Counting every line
    # double-counts suggestions that recur across snapshots; counting only the
    # latest snapshot loses suggestions that were implemented in an *earlier*
    # cycle and then drop out of the current review (they survive only in the
    # folded history).
    #
    # So we take the deduplicated union of all snapshots: each distinct
    # suggestion (keyed by its cleaned title) is counted once, and is credited as
    # implemented if it was implemented in *any* snapshot. The first occurrence —
    # the current review, which precedes the folded blocks — wins for section and
    # category, so live state takes precedence over history.
    occurrences = []  # in document order: current review first, then folded blocks
    section = None    # "action_required" | "review_recommended" | None
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
            occurrences.append({
                "key": _dedupe_key(title),
                "title": title,
                "section": section,
                "cat": _classify_category(title),
                "sub_label": _classify_sublabel(title),
                "is_implemented": is_implemented,
                "is_dismissed": is_dismissed,
            })

    # Merge occurrences by key: the first occurrence (current review) fixes the
    # title/section/category *and* the dismissal state, so live state takes
    # precedence over folded history. is_implemented is OR-ed across all
    # snapshots so an implementation found only in an earlier cycle is credited.
    merged: dict = {}
    for occ in occurrences:
        existing = merged.get(occ["key"])
        if existing is None:
            merged[occ["key"]] = dict(occ)
        else:
            existing["is_implemented"] = existing["is_implemented"] or occ["is_implemented"]
            # is_dismissed is intentionally NOT OR-ed: the first occurrence's
            # (current-snapshot) dismissal state wins, matching section/category.

    for occ in merged.values():
        # Dismissal reflects the current snapshot; a dismissed suggestion is not
        # counted as implemented.
        is_dismissed = occ["is_dismissed"]
        is_implemented = occ["is_implemented"] and not is_dismissed
        section = occ["section"]
        cat = occ["cat"]
        sub_label = occ["sub_label"]

        # Global totals (cover PRs with no section headers)
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
                "title": _clean_title(occ["title"]),
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


def _dedupe_key(title: str) -> str:
    """Normalise a suggestion title into a stable key for deduplication.

    Unlike _clean_title (a display helper that truncates at the first non-ASCII
    character), this preserves Unicode in the core title and removes only the
    trailing Qodo label/badge blocks and implemented/dismissed markers. This way
    the same suggestion matches across snapshots whether or not it is struck
    through or dismissed, while two distinct titles that legitimately contain
    Unicode do not collapse to the same (or an empty) key.
    """
    text = _TRAILING_BADGES.sub("", title)       # drop trailing <code>…</code> badges
    text = _HTML_TAG.sub("", text)               # strip <s>/<del>/<strike> etc.
    text = text.replace("~~", "")                # markdown strikethrough delimiters
    text = text.replace(DISMISSED_MARKER, "")    # ✗ Dismissed badge
    text = text.replace("☑", "")            # ☑ implemented marker
    return re.sub(r"\s+", " ", text).strip().lower()


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
        "speed_to_fix_min": minutes_between(qodo_ts, first_ts),
    }


CSV_COLUMNS = [
    "Repo Name", "PR #", "PR URL", "PR Creation Date", "PR Merge Date",
    "Hours to Merge", "PR Creator", "Lines Added",
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


def hours_between(iso_start: str, iso_end: str) -> int:
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


def minutes_between(iso_start: str, iso_end: str) -> Optional[int]:
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
        qodo_min = minutes_between(pr_created, qodo_ts)
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
        human_min = minutes_between(pr_created, first["created_at"])
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


def build_csv_row(pr: dict, lines_added: int, stats: Optional["QodoStats"],
                  timing: Optional[dict] = None,
                  extras: Optional[dict] = None) -> dict:
    has_qodo = stats is not None
    total = stats.total_suggestions if has_qodo else 0
    implemented = stats.total_implemented if has_qodo else 0
    timing = timing or {}
    extras = extras or {}

    impl_rate = f"{100 * implemented / total:.1f}" if total > 0 else ""
    per_100 = (
        f"{100 * total / lines_added:.1f}" if lines_added > 0 and total > 0 else ""
    )

    qodo_min = timing.get("qodo_min")
    human_min = timing.get("human_min")

    return {
        "Repo Name":                            pr["repo"],
        "PR #":                                 pr["number"],
        "PR URL":                               pr.get("url", ""),
        "PR Creation Date":                     pr.get("created_at", ""),
        "PR Merge Date":                        pr.get("merged_at", ""),
        "Hours to Merge":                       hours_between(
                                                    pr.get("created_at", ""),
                                                    pr.get("merged_at", ""),
                                                ),
        "PR Creator":                           pr.get("creator", ""),
        "Lines Added":                          lines_added,
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


def checkpoint_path(org):
    # Sanitize org the same way _output_stem() does: org is part of the
    # provider-agnostic surface and future providers may use identifiers
    # containing path separators or '..', which would otherwise let the
    # checkpoint read/write outside the working directory.
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", org)
    return REPORTS_DIR / f"{safe_org}-checkpoint.json"


def load_checkpoint(org):
    p = checkpoint_path(org)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable checkpoint — start fresh rather than crash.
            return None
    return None


def save_checkpoint(org, state):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_path(org).write_text(json.dumps(state, indent=2))
