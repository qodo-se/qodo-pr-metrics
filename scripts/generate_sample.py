#!/usr/bin/env python3
"""Generate examples/sample_report.html from synthetic anonymized data.

Run from the repo root:
    python3 scripts/generate_sample.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, datetime
from report import generate_html

SINCE = date(2025, 5, 14)
UNTIL = date(2026, 5, 14)
ORG = "acme-corp"

def _pr(repo, num, creator, url, lines, has_qodo,
        ar_s, ar_i, rr_s, rr_i,
        bugs_s, bugs_i, rule_s, rule_i, req_s, req_i):
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
    }


BASE = "https://github.com/acme-corp"

ROWS = [
    # repo-platform — high-activity repo, mixed implementation rates
    _pr("repo-platform", 441, "dev-alice", f"{BASE}/repo-platform/pull/441", 320, True,  6,5, 8,6, 4,3, 6,5, 4,3),
    _pr("repo-platform", 438, "dev-bob",   f"{BASE}/repo-platform/pull/438", 180, True,  4,4, 6,5, 3,3, 4,3, 3,3),
    _pr("repo-platform", 435, "dev-alice", f"{BASE}/repo-platform/pull/435", 95,  True,  2,2, 4,3, 2,2, 2,1, 2,2),
    _pr("repo-platform", 430, "dev-carol", f"{BASE}/repo-platform/pull/430", 210, True,  3,2, 5,4, 2,1, 3,3, 3,2),
    _pr("repo-platform", 427, "dev-bob",   f"{BASE}/repo-platform/pull/427", 140, True,  2,1, 3,2, 1,1, 2,1, 2,1),
    _pr("repo-platform", 420, "dev-dave",  f"{BASE}/repo-platform/pull/420", 55,  True,  1,1, 2,1, 1,1, 1,0, 1,1),
    _pr("repo-platform", 415, "dev-alice", f"{BASE}/repo-platform/pull/415", 88,  False, 0,0, 0,0, 0,0, 0,0, 0,0),
    _pr("repo-platform", 410, "dev-carol", f"{BASE}/repo-platform/pull/410", 33,  False, 0,0, 0,0, 0,0, 0,0, 0,0),

    # repo-api — backend API service
    _pr("repo-api", 892, "dev-eve",   f"{BASE}/repo-api/pull/892", 450, True,  8,7, 10,8, 5,4, 7,6, 6,5),
    _pr("repo-api", 887, "dev-frank", f"{BASE}/repo-api/pull/887", 280, True,  5,4, 7,6, 3,3, 5,4, 4,3),
    _pr("repo-api", 880, "dev-eve",   f"{BASE}/repo-api/pull/880", 190, True,  4,3, 6,5, 3,2, 4,3, 3,3),
    _pr("repo-api", 874, "dev-grace", f"{BASE}/repo-api/pull/874", 120, True,  3,3, 4,3, 2,2, 3,2, 2,2),
    _pr("repo-api", 868, "dev-frank", f"{BASE}/repo-api/pull/868", 75,  True,  2,1, 3,2, 1,1, 2,1, 2,1),
    _pr("repo-api", 860, "dev-grace", f"{BASE}/repo-api/pull/860", 40,  False, 0,0, 0,0, 0,0, 0,0, 0,0),

    # repo-web — frontend, lower suggestion density
    _pr("repo-web", 334, "dev-henry", f"{BASE}/repo-web/pull/334", 380, True,  3,2, 5,4, 2,1, 3,3, 3,2),
    _pr("repo-web", 329, "dev-alice", f"{BASE}/repo-web/pull/329", 220, True,  2,2, 4,3, 2,2, 2,1, 2,2),
    _pr("repo-web", 322, "dev-henry", f"{BASE}/repo-web/pull/322", 160, True,  2,1, 3,2, 1,1, 2,1, 2,1),
    _pr("repo-web", 315, "dev-carol", f"{BASE}/repo-web/pull/315", 95,  True,  1,1, 2,2, 1,1, 1,1, 1,1),
    _pr("repo-web", 308, "dev-bob",   f"{BASE}/repo-web/pull/308", 50,  False, 0,0, 0,0, 0,0, 0,0, 0,0),
    _pr("repo-web", 301, "dev-henry", f"{BASE}/repo-web/pull/301", 30,  False, 0,0, 0,0, 0,0, 0,0, 0,0),

    # repo-infra — ops/infra, fewer PRs but high suggestion counts
    _pr("repo-infra", 156, "dev-ivan",  f"{BASE}/repo-infra/pull/156", 600, True,  10,8, 12,9, 6,5, 8,7, 8,5),
    _pr("repo-infra", 151, "dev-judy",  f"{BASE}/repo-infra/pull/151", 340, True,   7,6,  9,7, 4,4, 6,5, 6,4),
    _pr("repo-infra", 147, "dev-ivan",  f"{BASE}/repo-infra/pull/147", 190, True,   5,4,  6,5, 3,3, 4,3, 4,3),
    _pr("repo-infra", 143, "dev-judy",  f"{BASE}/repo-infra/pull/143", 110, True,   3,2,  4,3, 2,2, 3,2, 2,1),
    _pr("repo-infra", 139, "dev-ivan",  f"{BASE}/repo-infra/pull/139", 70,  False,  0,0,  0,0, 0,0, 0,0, 0,0),

    # repo-mobile — new service, low volume
    _pr("repo-mobile", 78, "dev-kate",  f"{BASE}/repo-mobile/pull/78", 260, True,  4,3, 5,4, 3,2, 3,3, 3,2),
    _pr("repo-mobile", 74, "dev-frank", f"{BASE}/repo-mobile/pull/74", 180, True,  3,2, 4,3, 2,2, 2,1, 3,2),
    _pr("repo-mobile", 70, "dev-kate",  f"{BASE}/repo-mobile/pull/70", 120, True,  2,1, 3,2, 1,1, 2,1, 2,1),
    _pr("repo-mobile", 66, "dev-bob",   f"{BASE}/repo-mobile/pull/66", 55,  False, 0,0, 0,0, 0,0, 0,0, 0,0),
    _pr("repo-mobile", 62, "dev-kate",  f"{BASE}/repo-mobile/pull/62", 30,  False, 0,0, 0,0, 0,0, 0,0, 0,0),
]

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "sample_report.html")
    logo = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logo.svg")
    html = generate_html(ROWS, ORG, SINCE, UNTIL, logo_path=logo)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
        f.write("\n")
    print(f"Written: {out}")
