#!/usr/bin/env python3
"""Generate examples/sample_report.html from synthetic anonymized data.

Run from the repo root:
    python3 scripts/generate_sample.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, datetime
from report import generate_html

SINCE = date(2025, 3, 20)
UNTIL = date(2025, 5, 19)
ORG = "acme-corp"

import json as _json

def _pr(repo, num, creator, url, lines, has_qodo,
        ar_s, ar_i, rr_s, rr_i,
        bugs_s, bugs_i, rule_s, rule_i, req_s, req_i,
        qodo_min=None, human_min=None, has_human=True, spotlight=None,
        is_ai_authored=False, ai_author_type="", reviewer_count=1,
        had_request_changes=False, final_approver="", ci_status="SUCCESS",
        commits_after_qodo="", speed_to_fix_min=""):
    total_s = ar_s + rr_s
    total_i = ar_i + rr_i
    rate = f"{100 * total_i / total_s:.1f}" if total_s else ""
    return {
        "Repo Name": repo,
        "PR #": num,
        "PR URL": url,
        "PR Creation Date": "2025-11-01T10:00:00Z",
        "PR Merge Date": "2025-11-01T14:00:00Z",
        "Hours to Merge": 4,
        "PR Creator": creator,
        "Lines Changed": lines,
        "Has Qodo Review": has_qodo,
        "Action Required Suggestions": ar_s,
        "Action Required Implemented": ar_i,
        "Review Recommended Suggestions": rr_s,
        "Review Recommended Implemented": rr_i,
        "Bugs Suggested": bugs_s,
        "Bugs Implemented": bugs_i,
        "Rule Violations Suggested": rule_s,
        "Rule Violations Implemented": rule_i,
        "Requirement Gaps Suggested": req_s,
        "Requirement Gaps Implemented": req_i,
        "Total Suggestions": total_s,
        "Total Implemented": total_i,
        "Implementation Rate (%)": rate,
        "Suggestions per 100 Lines": round(100 * total_s / lines, 1) if lines else 0,
        "Time to First Qodo Comment (min)": qodo_min if qodo_min is not None else "",
        "Time to First Human Comment (min)": human_min if human_min is not None else "",
        "Has Human Comment": has_human,
        "Spotlight Issues": _json.dumps(spotlight or []),
        "Is AI Authored": is_ai_authored,
        "AI Author Type": ai_author_type,
        "Reviewer Count": reviewer_count,
        "Had Request Changes": had_request_changes,
        "Final Approver": final_approver,
        "CI Status": ci_status,
        "Commits After Qodo": commits_after_qodo,
        "Speed to First Fix (min)": speed_to_fix_min,
    }


BASE = "https://github.com/acme-corp"

_SPOTLIGHT_HARDCODED_KEY  = [{"title": "Hardcoded API key in config loader",                    "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_SQL_INJECTION  = [{"title": "SQL injection via unescaped user input",                 "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_WRONG_VARIANT  = [{"title": "Wrong auth variant applied in token refresh",            "category": "requirement_gap", "sub_label": "Correctness"}]
_SPOTLIGHT_JWT_LEAK       = [{"title": "JWT secret logged to stdout in debug handler",           "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_OPEN_REDIRECT  = [{"title": "Open redirect via unvalidated return_url parameter",     "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_CSRF           = [{"title": "Missing CSRF token on state-changing endpoint",          "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_MISSING_AUTHZ  = [{"title": "Missing authorization check on admin endpoint",          "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_RACE_CONDITION = [{"title": "Race condition in session cleanup causes data loss",     "category": "bug",             "sub_label": "Correctness"}]
_SPOTLIGHT_NULL_DEREF     = [{"title": "Null pointer dereference on incomplete user profile",    "category": "bug",             "sub_label": "Correctness"}]
_SPOTLIGHT_PAGINATION     = [{"title": "Pagination skips records when page size equals dataset", "category": "requirement_gap", "sub_label": "Correctness"}]
_SPOTLIGHT_OFF_BY_ONE     = [{"title": "Off-by-one drops last record in batch processor",       "category": "bug",             "sub_label": "Correctness"}]
_SPOTLIGHT_TIMEZONE       = [{"title": "Timezone conversion error produces incorrect billing timestamps", "category": "requirement_gap", "sub_label": "Correctness"}]
_SPOTLIGHT_SSRF           = [{"title": "SSRF via unvalidated webhook URL in integration handler","category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_XXE            = [{"title": "XXE injection in XML config parser",                    "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_PRIV_ESC       = [{"title": "Privilege escalation via missing role check on /admin", "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_DESER          = [{"title": "Unsafe deserialization allows RCE via crafted payload", "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_PATH_TRAV      = [{"title": "Path traversal in file-download endpoint",              "category": "bug",             "sub_label": "Security"}]
_SPOTLIGHT_BROKEN_LOCK    = [{"title": "Broken pessimistic lock allows concurrent writes to order record", "category": "bug", "sub_label": "Correctness"}]
_SPOTLIGHT_SILENT_FAIL    = [{"title": "Payment refund silently succeeds when gateway returns 500", "category": "requirement_gap", "sub_label": "Correctness"}]
_SPOTLIGHT_N_PLUS_1       = [{"title": "N+1 query in user-listing endpoint causes timeout under load", "category": "requirement_gap", "sub_label": "Correctness"}]
_SPOTLIGHT_DIV_ZERO       = [{"title": "Division by zero crash when monthly_active_users is 0","category": "bug",             "sub_label": "Correctness"}]
_SPOTLIGHT_STALE_CACHE    = [{"title": "Stale cache serves deleted user profile for up to 24h","category": "requirement_gap", "sub_label": "Correctness"}]
_SPOTLIGHT_PARTIAL_WRITE  = [{"title": "Partial write on network failure leaves DB in inconsistent state", "category": "bug", "sub_label": "Correctness"}]
_SPOTLIGHT_REPLAY         = [{"title": "Replay attack possible — webhook signature does not check timestamp", "category": "bug", "sub_label": "Security"}]
_SPOTLIGHT_MISSING_INDEX  = [{"title": "Missing index on events.user_id causes full-table scan in hot path", "category": "requirement_gap", "sub_label": "Correctness"}]

ROWS = [
    # ── repo-platform (high-volume, mixed rates) ─────────────────────────────
    # alice target: 120 sugg, 48% rate → POWER
    _pr("repo-platform", 512, "dev-alice", f"{BASE}/repo-platform/pull/512", 1230, True,  8,4, 10,5, 5,3, 8,4, 5,3,  qodo_min=6,  human_min=320, spotlight=_SPOTLIGHT_HARDCODED_KEY,  is_ai_authored=True,  ai_author_type="copilot", reviewer_count=2, speed_to_fix_min=14, commits_after_qodo=3),
    _pr("repo-platform", 508, "dev-alice", f"{BASE}/repo-platform/pull/508", 870, True,  6,3, 8,4,  4,2, 6,3, 4,2,  qodo_min=8,  human_min=480, speed_to_fix_min=22, commits_after_qodo=2, reviewer_count=2),
    _pr("repo-platform", 503, "dev-alice", f"{BASE}/repo-platform/pull/503", 660, True,  5,3, 7,3,  3,2, 5,3, 3,1,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=18, commits_after_qodo=2),
    _pr("repo-platform", 499, "dev-alice", f"{BASE}/repo-platform/pull/499", 555, True,  4,2, 6,3,  3,1, 4,2, 3,2,  qodo_min=7,  human_min=290, spotlight=_SPOTLIGHT_JWT_LEAK, speed_to_fix_min=31, commits_after_qodo=1),
    _pr("repo-platform", 495, "dev-alice", f"{BASE}/repo-platform/pull/495", 420, True,  3,2, 5,2,  2,1, 3,1, 2,1,  qodo_min=9,  human_min=360, speed_to_fix_min=45, commits_after_qodo=2),
    _pr("repo-platform", 490, "dev-alice", f"{BASE}/repo-platform/pull/490", 285,  True,  2,1, 3,2,  1,1, 2,1, 1,1,  qodo_min=5,  human_min=210, speed_to_fix_min=12, commits_after_qodo=1, final_approver="dev-frank"),
    _pr("repo-platform", 485, "dev-alice", f"{BASE}/repo-platform/pull/485", 180,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # frank target: 85 sugg, 18% rate → COACH
    _pr("repo-platform", 511, "dev-frank", f"{BASE}/repo-platform/pull/511", 1140, True,  9,1, 12,2, 4,1, 6,1, 4,0,  qodo_min=10, human_min=520, spotlight=_SPOTLIGHT_CSRF, reviewer_count=2, speed_to_fix_min=120, commits_after_qodo=1, had_request_changes=True),
    _pr("repo-platform", 506, "dev-frank", f"{BASE}/repo-platform/pull/506", 780, True,  6,1, 8,1,  3,0, 5,1, 3,0,  qodo_min=8,  human_min=None, has_human=False, speed_to_fix_min=95, commits_after_qodo=1),
    _pr("repo-platform", 500, "dev-frank", f"{BASE}/repo-platform/pull/500", 570, True,  4,1, 5,1,  2,0, 3,0, 2,0,  qodo_min=7,  human_min=440, speed_to_fix_min=80, commits_after_qodo=2),
    _pr("repo-platform", 493, "dev-frank", f"{BASE}/repo-platform/pull/493", 390, True,  3,0, 4,1,  2,0, 2,0, 1,0,  qodo_min=11, human_min=None, has_human=False, speed_to_fix_min=150, commits_after_qodo=1),
    _pr("repo-platform", 488, "dev-frank", f"{BASE}/repo-platform/pull/488", 210,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # rosa target: 35 sugg, 11% rate → ABSENT
    _pr("repo-platform", 509, "dev-rosa",  f"{BASE}/repo-platform/pull/509", 450, True,  3,0, 5,0,  2,0, 3,0, 2,0,  qodo_min=9,  human_min=310, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-platform", 504, "dev-rosa",  f"{BASE}/repo-platform/pull/504", 330, True,  2,0, 4,1,  2,0, 2,0, 2,0,  qodo_min=7,  human_min=240, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-platform", 497, "dev-rosa",  f"{BASE}/repo-platform/pull/497", 195,  True,  2,0, 4,0,  1,0, 2,0, 1,0,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-platform", 491, "dev-rosa",  f"{BASE}/repo-platform/pull/491", 120,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # mia target: 28 sugg, 50% rate → CURIOUS
    _pr("repo-platform", 507, "dev-mia",   f"{BASE}/repo-platform/pull/507", 540, True,  3,2, 4,2,  2,1, 2,1, 2,1,  qodo_min=6,  human_min=190, speed_to_fix_min=10, commits_after_qodo=2),
    _pr("repo-platform", 501, "dev-mia",   f"{BASE}/repo-platform/pull/501", 360, True,  2,1, 3,1,  1,1, 2,1, 1,1,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=8, commits_after_qodo=1),
    _pr("repo-platform", 494, "dev-mia",   f"{BASE}/repo-platform/pull/494", 165,  True,  1,1, 2,1,  1,1, 1,1, 1,1,  qodo_min=4,  human_min=140, speed_to_fix_min=5, commits_after_qodo=1, final_approver="dev-alice"),
    _pr("repo-platform", 486, "dev-mia",   f"{BASE}/repo-platform/pull/486", 90,  False, 0,0, 0,0,  0,0, 0,0, 0,0),

    # ── repo-api (backend API, high suggestion density) ─────────────────────
    # bob target: 95 sugg, 44% rate → POWER
    _pr("repo-api", 1102, "dev-bob",   f"{BASE}/repo-api/pull/1102", 1560, True,  9,4, 11,5, 6,3, 8,4, 5,2,  qodo_min=5,  human_min=410, spotlight=_SPOTLIGHT_SQL_INJECTION, is_ai_authored=True, ai_author_type="copilot", reviewer_count=2, speed_to_fix_min=18, commits_after_qodo=4),
    _pr("repo-api", 1098, "dev-bob",   f"{BASE}/repo-api/pull/1098", 1050, True,  7,3, 9,4,  4,2, 6,3, 4,2,  qodo_min=7,  human_min=560, spotlight=_SPOTLIGHT_MISSING_AUTHZ, speed_to_fix_min=25, commits_after_qodo=3, reviewer_count=2),
    _pr("repo-api", 1093, "dev-bob",   f"{BASE}/repo-api/pull/1093", 720, True,  5,2, 7,3,  3,1, 4,2, 3,1,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=40, commits_after_qodo=2),
    _pr("repo-api", 1087, "dev-bob",   f"{BASE}/repo-api/pull/1087", 495, True,  4,2, 5,2,  2,1, 3,1, 2,1,  qodo_min=9,  human_min=330, speed_to_fix_min=35, commits_after_qodo=2),
    _pr("repo-api", 1081, "dev-bob",   f"{BASE}/repo-api/pull/1081", 270,  True,  2,1, 3,1,  1,1, 2,1, 1,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=20, commits_after_qodo=1),
    _pr("repo-api", 1075, "dev-bob",   f"{BASE}/repo-api/pull/1075", 150,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # grace target: 78 sugg, 20% rate → COACH
    _pr("repo-api", 1101, "dev-grace", f"{BASE}/repo-api/pull/1101", 1380, True,  9,2, 11,2, 5,1, 6,1, 4,0,  qodo_min=8,  human_min=490, spotlight=_SPOTLIGHT_SSRF, reviewer_count=3, speed_to_fix_min=200, commits_after_qodo=2, had_request_changes=True),
    _pr("repo-api", 1096, "dev-grace", f"{BASE}/repo-api/pull/1096", 900, True,  6,1, 7,1,  3,0, 5,1, 3,0,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=160, commits_after_qodo=1),
    _pr("repo-api", 1090, "dev-grace", f"{BASE}/repo-api/pull/1090", 600, True,  5,1, 6,1,  3,0, 4,0, 2,0,  qodo_min=9,  human_min=380, speed_to_fix_min=180, commits_after_qodo=1),
    _pr("repo-api", 1084, "dev-grace", f"{BASE}/repo-api/pull/1084", 345, True,  3,0, 4,1,  2,0, 2,0, 2,0,  qodo_min=11, human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-api", 1078, "dev-grace", f"{BASE}/repo-api/pull/1078", 165,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # sam target: 28 sugg, 18% rate → ABSENT
    _pr("repo-api", 1099, "dev-sam",   f"{BASE}/repo-api/pull/1099", 540, True,  3,0, 4,1,  2,0, 2,0, 2,0,  qodo_min=7,  human_min=270, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-api", 1092, "dev-sam",   f"{BASE}/repo-api/pull/1092", 360, True,  2,0, 3,0,  1,0, 2,0, 1,0,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-api", 1085, "dev-sam",   f"{BASE}/repo-api/pull/1085", 180,  True,  2,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=6,  human_min=210, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-api", 1079, "dev-sam",   f"{BASE}/repo-api/pull/1079", 105,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # nate target: 22 sugg, 45% rate → CURIOUS
    _pr("repo-api", 1097, "dev-nate",  f"{BASE}/repo-api/pull/1097", 465, True,  2,1, 3,2,  2,1, 2,1, 1,1,  qodo_min=5,  human_min=190, speed_to_fix_min=9, commits_after_qodo=1),
    _pr("repo-api", 1089, "dev-nate",  f"{BASE}/repo-api/pull/1089", 270,  True,  2,1, 2,1,  1,1, 1,0, 1,1,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=11, commits_after_qodo=1),
    _pr("repo-api", 1082, "dev-nate",  f"{BASE}/repo-api/pull/1082", 135,  True,  1,0, 2,1,  1,0, 1,1, 1,0,  qodo_min=6,  human_min=155, speed_to_fix_min=7, commits_after_qodo=1, final_approver="dev-bob"),
    _pr("repo-api", 1076, "dev-nate",  f"{BASE}/repo-api/pull/1076", 60,  False, 0,0, 0,0,  0,0, 0,0, 0,0),

    # ── repo-web (frontend, moderate volume) ─────────────────────────────────
    # carol target: 72 sugg, 40% rate → POWER
    _pr("repo-web", 445, "dev-carol",  f"{BASE}/repo-web/pull/445", 1440, True,  8,3, 11,4, 4,2, 6,3, 4,2,  qodo_min=11, human_min=430, spotlight=_SPOTLIGHT_OPEN_REDIRECT, is_ai_authored=True, ai_author_type="cursor", reviewer_count=3, speed_to_fix_min=50, commits_after_qodo=5, final_approver="dev-bob"),
    _pr("repo-web", 440, "dev-carol",  f"{BASE}/repo-web/pull/440", 930, True,  5,2, 7,3,  3,1, 5,2, 3,1,  qodo_min=8,  human_min=310, speed_to_fix_min=38, commits_after_qodo=3, reviewer_count=2),
    _pr("repo-web", 435, "dev-carol",  f"{BASE}/repo-web/pull/435", 570, True,  4,2, 5,2,  2,1, 3,1, 2,1,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=29, commits_after_qodo=2),
    _pr("repo-web", 429, "dev-carol",  f"{BASE}/repo-web/pull/429", 330, True,  3,1, 4,2,  2,1, 2,1, 1,0,  qodo_min=9,  human_min=270, speed_to_fix_min=42, commits_after_qodo=1),
    _pr("repo-web", 423, "dev-carol",  f"{BASE}/repo-web/pull/423", 180,  True,  1,0, 2,1,  1,0, 1,0, 1,1,  qodo_min=7,  human_min=180, speed_to_fix_min=15, commits_after_qodo=1),
    _pr("repo-web", 417, "dev-carol",  f"{BASE}/repo-web/pull/417", 105,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # henry target: 70 sugg, 15% rate → COACH
    _pr("repo-web", 444, "dev-henry",  f"{BASE}/repo-web/pull/444", 1260, True,  10,1, 12,1, 4,0, 5,1, 4,0,  qodo_min=12, human_min=450, spotlight=_SPOTLIGHT_PAGINATION, reviewer_count=2, speed_to_fix_min=240, commits_after_qodo=1, had_request_changes=True),
    _pr("repo-web", 438, "dev-henry",  f"{BASE}/repo-web/pull/438", 840, True,  5,1, 7,1,  3,0, 4,0, 3,0,  qodo_min=9,  human_min=None, has_human=False, speed_to_fix_min=180, commits_after_qodo=1),
    _pr("repo-web", 432, "dev-henry",  f"{BASE}/repo-web/pull/432", 540, True,  4,0, 5,1,  2,0, 3,0, 2,0,  qodo_min=8,  human_min=390, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-web", 425, "dev-henry",  f"{BASE}/repo-web/pull/425", 315, True,  3,0, 4,1,  2,0, 2,0, 1,0,  qodo_min=10, human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=1),
    _pr("repo-web", 419, "dev-henry",  f"{BASE}/repo-web/pull/419", 180,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # tara target: 22 sugg, 20% rate → ABSENT
    _pr("repo-web", 442, "dev-tara",   f"{BASE}/repo-web/pull/442", 420, True,  2,0, 3,1,  2,0, 2,0, 1,0,  qodo_min=8,  human_min=260, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-web", 434, "dev-tara",   f"{BASE}/repo-web/pull/434", 270,  True,  2,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-web", 427, "dev-tara",   f"{BASE}/repo-web/pull/427", 150,  True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=5,  human_min=180, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-web", 420, "dev-tara",   f"{BASE}/repo-web/pull/420", 75,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # olivia target: 18 sugg, 39% rate → CURIOUS
    _pr("repo-web", 441, "dev-olivia", f"{BASE}/repo-web/pull/441", 510, True,  2,1, 3,1,  1,1, 2,1, 1,0,  qodo_min=7,  human_min=200, speed_to_fix_min=12, commits_after_qodo=1),
    _pr("repo-web", 433, "dev-olivia", f"{BASE}/repo-web/pull/433", 300, True,  2,1, 2,1,  1,1, 1,0, 1,0,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=8, commits_after_qodo=1),
    _pr("repo-web", 426, "dev-olivia", f"{BASE}/repo-web/pull/426", 150,  True,  1,1, 1,0,  1,0, 1,0, 1,1,  qodo_min=4,  human_min=150, speed_to_fix_min=6, commits_after_qodo=1, final_approver="dev-carol"),
    _pr("repo-web", 418, "dev-olivia", f"{BASE}/repo-web/pull/418", 75,  False, 0,0, 0,0,  0,0, 0,0, 0,0),

    # ── repo-infra (ops/infra, large PRs, high suggestion density) ──────────
    # diana target: 65 sugg, 38% rate → POWER
    _pr("repo-infra", 201, "dev-diana", f"{BASE}/repo-infra/pull/201", 2340, True,  9,3, 11,5, 5,2, 8,3, 5,2,  qodo_min=4,  human_min=640, spotlight=_SPOTLIGHT_RACE_CONDITION, is_ai_authored=True, ai_author_type="copilot", reviewer_count=2, speed_to_fix_min=35, commits_after_qodo=6, final_approver="dev-evan"),
    _pr("repo-infra", 197, "dev-diana", f"{BASE}/repo-infra/pull/197", 1560, True,  7,3, 8,3,  4,2, 5,2, 4,1,  qodo_min=7,  human_min=510, spotlight=_SPOTLIGHT_NULL_DEREF, speed_to_fix_min=28, commits_after_qodo=4, reviewer_count=2),
    _pr("repo-infra", 193, "dev-diana", f"{BASE}/repo-infra/pull/193", 900, True,  5,2, 6,2,  3,1, 4,2, 2,1,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=45, commits_after_qodo=2),
    _pr("repo-infra", 189, "dev-diana", f"{BASE}/repo-infra/pull/189", 480, True,  3,1, 4,2,  2,1, 3,1, 1,0,  qodo_min=8,  human_min=380, speed_to_fix_min=22, commits_after_qodo=1),
    _pr("repo-infra", 185, "dev-diana", f"{BASE}/repo-infra/pull/185", 240,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # irene target: 62 sugg, 22% rate → COACH
    _pr("repo-infra", 200, "dev-irene", f"{BASE}/repo-infra/pull/200", 2040, True,  10,2, 11,2, 5,1, 7,1, 4,0,  qodo_min=6,  human_min=620, spotlight=_SPOTLIGHT_WRONG_VARIANT, reviewer_count=3, speed_to_fix_min=300, commits_after_qodo=2, had_request_changes=True),
    _pr("repo-infra", 196, "dev-irene", f"{BASE}/repo-infra/pull/196", 1320, True,  6,1, 7,1,  3,0, 5,1, 3,0,  qodo_min=8,  human_min=None, has_human=False, speed_to_fix_min=240, commits_after_qodo=1),
    _pr("repo-infra", 192, "dev-irene", f"{BASE}/repo-infra/pull/192", 750, True,  4,1, 5,1,  2,0, 3,0, 2,1,  qodo_min=7,  human_min=450, speed_to_fix_min=180, commits_after_qodo=1),
    _pr("repo-infra", 188, "dev-irene", f"{BASE}/repo-infra/pull/188", 390, True,  3,0, 4,1,  2,0, 2,0, 1,0,  qodo_min=10, human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-infra", 184, "dev-irene", f"{BASE}/repo-infra/pull/184", 180,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # uma target: 18 sugg, 22% rate → ABSENT
    _pr("repo-infra", 199, "dev-uma",   f"{BASE}/repo-infra/pull/199", 600, True,  2,0, 3,1,  2,0, 2,0, 1,0,  qodo_min=9,  human_min=340, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-infra", 194, "dev-uma",   f"{BASE}/repo-infra/pull/194", 390, True,  2,0, 2,0,  1,0, 2,0, 1,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-infra", 190, "dev-uma",   f"{BASE}/repo-infra/pull/190", 210,  True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=6,  human_min=240, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-infra", 186, "dev-uma",   f"{BASE}/repo-infra/pull/186", 105,  False, 0,0, 0,0,  0,0, 0,0, 0,0),

    # ── repo-data (data-pipeline, new in window) ─────────────────────────────
    # evan target: 55 sugg, 35% rate → POWER
    _pr("repo-data", 88,  "dev-evan",  f"{BASE}/repo-data/pull/88",  1650, True,  10,3, 14,3, 4,2, 6,2, 4,2,  qodo_min=5,  human_min=380, spotlight=_SPOTLIGHT_N_PLUS_1, is_ai_authored=True, ai_author_type="cursor", reviewer_count=2, speed_to_fix_min=20, commits_after_qodo=4),
    _pr("repo-data", 84,  "dev-evan",  f"{BASE}/repo-data/pull/84",  1080, True,  6,2, 6,2,  3,1, 4,2, 3,1,  qodo_min=8,  human_min=420, speed_to_fix_min=28, commits_after_qodo=3, reviewer_count=2),
    _pr("repo-data", 80,  "dev-evan",  f"{BASE}/repo-data/pull/80",  600, True,  4,1, 5,2,  2,1, 3,1, 2,1,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=35, commits_after_qodo=2),
    _pr("repo-data", 76,  "dev-evan",  f"{BASE}/repo-data/pull/76",  300, True,  2,1, 3,1,  1,0, 2,1, 1,0,  qodo_min=9,  human_min=290, speed_to_fix_min=18, commits_after_qodo=1),
    _pr("repo-data", 72,  "dev-evan",  f"{BASE}/repo-data/pull/72",  165,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # jack target: 57 sugg, 17% rate → COACH
    _pr("repo-data", 87,  "dev-jack",  f"{BASE}/repo-data/pull/87",  1470, True,  9,1, 11,1, 4,0, 5,1, 4,0,  qodo_min=9,  human_min=510, spotlight=_SPOTLIGHT_STALE_CACHE, reviewer_count=2, speed_to_fix_min=200, commits_after_qodo=1, had_request_changes=True),
    _pr("repo-data", 83,  "dev-jack",  f"{BASE}/repo-data/pull/83",  930, True,  6,1, 8,1,  3,0, 4,0, 3,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=160, commits_after_qodo=1),
    _pr("repo-data", 79,  "dev-jack",  f"{BASE}/repo-data/pull/79",  540, True,  4,0, 5,1,  2,0, 3,0, 2,0,  qodo_min=8,  human_min=370, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-data", 75,  "dev-jack",  f"{BASE}/repo-data/pull/75",  285,  True,  3,0, 4,0,  1,0, 2,0, 1,0,  qodo_min=10, human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-data", 71,  "dev-jack",  f"{BASE}/repo-data/pull/71",  135,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # victor target: 14 sugg, 14% rate → ABSENT
    _pr("repo-data", 86,  "dev-victor",f"{BASE}/repo-data/pull/86",  480, True,  2,0, 3,0,  1,0, 2,0, 1,0,  qodo_min=8,  human_min=300, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-data", 81,  "dev-victor",f"{BASE}/repo-data/pull/81",  285,  True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-data", 77,  "dev-victor",f"{BASE}/repo-data/pull/77",  150,  True,  1,0, 1,0,  1,0, 1,0, 1,0,  qodo_min=5,  human_min=220, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-data", 73,  "dev-victor",f"{BASE}/repo-data/pull/73",  75,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # peter target: 15 sugg, 33% rate → CURIOUS
    _pr("repo-data", 85,  "dev-peter", f"{BASE}/repo-data/pull/85",  420, True,  2,1, 2,1,  1,1, 1,0, 1,0,  qodo_min=6,  human_min=180, speed_to_fix_min=10, commits_after_qodo=1),
    _pr("repo-data", 82,  "dev-peter", f"{BASE}/repo-data/pull/82",  240,  True,  1,0, 2,1,  1,0, 1,1, 1,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=7, commits_after_qodo=1),
    _pr("repo-data", 78,  "dev-peter", f"{BASE}/repo-data/pull/78",  120,  True,  1,0, 1,0,  1,0, 1,0, 1,1,  qodo_min=5,  human_min=150, speed_to_fix_min=5, commits_after_qodo=1, final_approver="dev-evan"),
    _pr("repo-data", 74,  "dev-peter", f"{BASE}/repo-data/pull/74",  60,  False, 0,0, 0,0,  0,0, 0,0, 0,0),

    # ── repo-mobile (new service, lower volume) ──────────────────────────────
    # kate target: 52 sugg, 25% rate → COACH
    _pr("repo-mobile", 112, "dev-kate",  f"{BASE}/repo-mobile/pull/112", 1320, True,  11,2, 15,2, 4,1, 5,1, 4,1,  qodo_min=6,  human_min=400, spotlight=_SPOTLIGHT_OFF_BY_ONE, reviewer_count=2, speed_to_fix_min=110, commits_after_qodo=2, had_request_changes=True),
    _pr("repo-mobile", 108, "dev-kate",  f"{BASE}/repo-mobile/pull/108", 870, True,  5,1, 7,1,  3,0, 4,1, 3,1,  qodo_min=8,  human_min=None, has_human=False, speed_to_fix_min=90, commits_after_qodo=1),
    _pr("repo-mobile", 104, "dev-kate",  f"{BASE}/repo-mobile/pull/104", 510, True,  3,1, 4,1,  2,0, 3,0, 2,0,  qodo_min=7,  human_min=330, spotlight=_SPOTLIGHT_TIMEZONE, speed_to_fix_min=0, commits_after_qodo=1),
    _pr("repo-mobile", 100, "dev-kate",  f"{BASE}/repo-mobile/pull/100", 300, True,  2,0, 3,1,  1,0, 2,0, 2,0,  qodo_min=9,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-mobile", 96,  "dev-kate",  f"{BASE}/repo-mobile/pull/96",  150,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # wendy target: 12 sugg, 25% rate → ABSENT
    _pr("repo-mobile", 110, "dev-wendy", f"{BASE}/repo-mobile/pull/110", 390, True,  1,0, 2,1,  1,0, 1,0, 1,0,  qodo_min=7,  human_min=240, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-mobile", 105, "dev-wendy", f"{BASE}/repo-mobile/pull/105", 240,  True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-mobile", 101, "dev-wendy", f"{BASE}/repo-mobile/pull/101", 135,  True,  1,1, 1,0,  1,0, 1,1, 1,0,  qodo_min=6,  human_min=190, speed_to_fix_min=8, commits_after_qodo=1),
    _pr("repo-mobile", 97,  "dev-wendy", f"{BASE}/repo-mobile/pull/97",  75,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # quinn target: 12 sugg, 42% rate → CURIOUS
    _pr("repo-mobile", 111, "dev-quinn", f"{BASE}/repo-mobile/pull/111", 360, True,  1,1, 2,1,  1,0, 1,1, 1,1,  qodo_min=5,  human_min=170, speed_to_fix_min=8, commits_after_qodo=1),
    _pr("repo-mobile", 106, "dev-quinn", f"{BASE}/repo-mobile/pull/106", 210,  True,  1,0, 2,1,  1,1, 1,1, 1,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=6, commits_after_qodo=1),
    _pr("repo-mobile", 102, "dev-quinn", f"{BASE}/repo-mobile/pull/102", 105,  True,  1,1, 1,0,  1,1, 1,0, 1,0,  qodo_min=5,  human_min=140, speed_to_fix_min=4, commits_after_qodo=1, final_approver="dev-kate"),
    _pr("repo-mobile", 98,  "dev-quinn", f"{BASE}/repo-mobile/pull/98",  60,  False, 0,0, 0,0,  0,0, 0,0, 0,0),

    # ── repo-auth (security-critical, bug spotlight focus) ───────────────────
    # leo target: 50 sugg, 24% rate → COACH
    _pr("repo-auth", 78,  "dev-leo",   f"{BASE}/repo-auth/pull/78",  1500, True,  11,2, 12,2, 4,1, 5,1, 4,0,  qodo_min=5,  human_min=560, spotlight=_SPOTLIGHT_DESER,    reviewer_count=3, speed_to_fix_min=180, commits_after_qodo=2, had_request_changes=True),
    _pr("repo-auth", 74,  "dev-leo",   f"{BASE}/repo-auth/pull/74",  990, True,  5,1, 6,1,  3,0, 4,1, 3,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=120, commits_after_qodo=1),
    _pr("repo-auth", 70,  "dev-leo",   f"{BASE}/repo-auth/pull/70",  570, True,  4,0, 5,1,  2,0, 3,0, 2,0,  qodo_min=8,  human_min=420, spotlight=_SPOTLIGHT_PRIV_ESC, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-auth", 66,  "dev-leo",   f"{BASE}/repo-auth/pull/66",  300, True,  3,1, 4,1,  1,0, 2,0, 1,0,  qodo_min=11, human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=1),
    _pr("repo-auth", 62,  "dev-leo",   f"{BASE}/repo-auth/pull/62",  150,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # xavier target: 9 sugg, 0% rate → ABSENT
    _pr("repo-auth", 76,  "dev-xavier",f"{BASE}/repo-auth/pull/76",  360, True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=8,  human_min=280, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-auth", 72,  "dev-xavier",f"{BASE}/repo-auth/pull/72",  225,  True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-auth", 68,  "dev-xavier",f"{BASE}/repo-auth/pull/68",  120,  True,  1,0, 1,0,  1,0, 1,0, 1,0,  qodo_min=5,  human_min=200, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-auth", 64,  "dev-xavier",f"{BASE}/repo-auth/pull/64",  60,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # yara target: 8 sugg, 0% rate → ABSENT
    _pr("repo-auth", 75,  "dev-yara",  f"{BASE}/repo-auth/pull/75",  300, True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=9,  human_min=260, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-auth", 71,  "dev-yara",  f"{BASE}/repo-auth/pull/71",  195,  True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-auth", 67,  "dev-yara",  f"{BASE}/repo-auth/pull/67",  105,  True,  1,0, 1,0,  0,0, 1,0, 1,0,  qodo_min=6,  human_min=190, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-auth", 63,  "dev-yara",  f"{BASE}/repo-auth/pull/63",  54,  False, 0,0, 0,0,  0,0, 0,0, 0,0),

    # ── repo-search (new team, mixed adoption) ───────────────────────────────
    # zack target: 6 sugg, 17% rate → ABSENT
    _pr("repo-search", 45, "dev-zack",  f"{BASE}/repo-search/pull/45", 270,  True,  1,0, 2,0,  1,0, 1,0, 1,0,  qodo_min=7,  human_min=220, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 41, "dev-zack",  f"{BASE}/repo-search/pull/41", 165,  True,  1,0, 1,0,  1,0, 1,0, 1,1,  qodo_min=5,  human_min=None, has_human=False, speed_to_fix_min=15, commits_after_qodo=1),
    _pr("repo-search", 37, "dev-zack",  f"{BASE}/repo-search/pull/37", 90,  True,  1,0, 1,0,  1,0, 1,0, 1,0,  qodo_min=6,  human_min=180, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 33, "dev-zack",  f"{BASE}/repo-search/pull/33", 45,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # alex target: 5 sugg, 0% rate → ABSENT
    _pr("repo-search", 44, "dev-alex",  f"{BASE}/repo-search/pull/44", 240,  True,  1,0, 1,0,  1,0, 1,0, 1,0,  qodo_min=8,  human_min=240, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 40, "dev-alex",  f"{BASE}/repo-search/pull/40", 150,  True,  1,0, 1,0,  1,0, 1,0, 1,0,  qodo_min=6,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 36, "dev-alex",  f"{BASE}/repo-search/pull/36", 75,  True,  1,0, 1,0,  1,0, 1,0, 1,0,  qodo_min=5,  human_min=190, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 32, "dev-alex",  f"{BASE}/repo-search/pull/32", 36,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
    # brett target: 3 sugg, 0% rate → ABSENT
    _pr("repo-search", 43, "dev-brett", f"{BASE}/repo-search/pull/43", 180,  True,  1,0, 1,0,  1,0, 1,0, 1,0,  qodo_min=9,  human_min=200, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 39, "dev-brett", f"{BASE}/repo-search/pull/39", 120,  True,  0,0, 1,0,  0,0, 1,0, 1,0,  qodo_min=7,  human_min=None, has_human=False, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 35, "dev-brett", f"{BASE}/repo-search/pull/35", 66,  True,  0,0, 0,0,  0,0, 0,0, 0,0,  qodo_min=5,  human_min=170, speed_to_fix_min=0, commits_after_qodo=0),
    _pr("repo-search", 31, "dev-brett", f"{BASE}/repo-search/pull/31", 30,  False, 0,0, 0,0,  0,0, 0,0, 0,0),
]

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "sample_report.html")
    logo = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logo.svg")
    html = generate_html(ROWS, ORG, SINCE, UNTIL, logo_path=logo,
                         org_pr_count=620, org_author_count=52,
                         revert_count=8, hotfix_count=3)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
        f.write("\n")
    print(f"Written: {out}")
