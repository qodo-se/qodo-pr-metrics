# HTML Summary Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a branded, print-ready HTML summary report (plus always-on CSV) that is auto-generated on every run of `github.py`, targeting an internal Qodo evangelist persona.

**Architecture:** A new stdlib-only `report.py` module exposes `aggregate(rows)` and `generate_html(...)`. `github.py` accumulates all PR rows during its run, writes a CSV incrementally (crash-safe), then calls `report.generate_html` to write the HTML at the end. Both files are auto-named `{org}_{since}_{until}.csv/.html` — no flags required.

**Tech Stack:** Python 3.9+, stdlib only (`csv`, `base64`, `pathlib`, `collections`, `dataclasses`, `datetime`). HTML/CSS via f-strings. Tests via `pytest`.

---

## File Structure

| File | Action | Responsibility |
| --- | --- | --- |
| `github.py` | Modify | Fix `build_csv_row`, remove `--csv`/`--html` flags, add `_output_stem`, wire `report` module |
| `report.py` | Create | `aggregate(rows) -> ReportData`, `generate_html(...) -> str`, all HTML/CSS rendering |
| `tests/test_csv_row.py` | Already has failing tests | Tests for `build_csv_row` with `stats=None` — fix them in Task 1 |
| `tests/test_report.py` | Create | Tests for `aggregate()` and a smoke test for `generate_html()` |

---

## Task 1: Fix `build_csv_row` — accept `stats=None`, add `Has Qodo Review` column

Two tests in `tests/test_csv_row.py` are already written and failing. This task makes them pass.

**Files:**
- Modify: `github.py` — `CSV_COLUMNS`, `build_csv_row`

- [ ] **Step 1: Confirm the tests are failing**

```bash
python3 -m pytest tests/test_csv_row.py::test_build_csv_row_no_qodo tests/test_csv_row.py::test_build_csv_row_with_qodo -v
```

Expected: 2 FAILED

- [ ] **Step 2: Add `Optional` import and `Has Qodo Review` to `CSV_COLUMNS`**

In `github.py`, the `import` block currently has no `typing` import. Add it, and insert `"Has Qodo Review"` into `CSV_COLUMNS` after `"Lines Changed"`:

```python
# Add to imports at top of github.py
from typing import Optional
```

```python
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
```

- [ ] **Step 3: Update `build_csv_row` signature and body**

Replace the existing `build_csv_row` function entirely:

```python
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
```

- [ ] **Step 4: Run the previously failing tests**

```bash
python3 -m pytest tests/test_csv_row.py -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add github.py
git commit -m "fix: build_csv_row accepts stats=None, adds Has Qodo Review column"
```

---

## Task 2: Update `cmd_count` to include all PRs in output and always fetch lines

Currently `cmd_count` skips non-Qodo PRs from output and only fetches `lines_changed` when `--csv` is active. This task makes every merged PR appear as a row (with `Has Qodo Review=False` for non-Qodo PRs), and always fetches line counts for Qodo PRs.

**Files:**
- Modify: `github.py` — `cmd_count`

- [ ] **Step 1: Declare `rows` list at the start of `cmd_count`**

Find this block near the top of `cmd_count` (after the `--resume` block):

```python
    csv_file = None
    csv_writer = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="", encoding="utf-8")
        csv_writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        csv_writer.writeheader()
```

Replace it with:

```python
    rows: list[dict] = []
```

(The permanent CSV file setup moves to Task 3. For now this just removes the old block and declares `rows`.)

- [ ] **Step 2: Update the non-Qodo branch to append a row instead of skipping**

Find:

```python
            if not qodo:
                if args.verbose:
                    print(f"{owner}/{repo}#{number}: (no Qodo comment, skipped)")
                processed.add((owner, repo, str(number)))
                save_checkpoint(args.org, {
```

Replace with:

```python
            if not qodo:
                if args.verbose:
                    print(f"{owner}/{repo}#{number}: (no Qodo comment)")
                rows.append(build_csv_row(pr, lines_changed=0, stats=None))
                processed.add((owner, repo, str(number)))
                save_checkpoint(args.org, {
```

- [ ] **Step 3: Update the Qodo branch to always fetch lines and append to rows**

Find:

```python
            lines_changed = fetch_pr_lines(owner, repo, number) if args.csv else 0
            if csv_writer:
                csv_writer.writerow(build_csv_row(pr, lines_changed, stats))
```

Replace with:

```python
            lines_changed = fetch_pr_lines(owner, repo, number)
            rows.append(build_csv_row(pr, lines_changed, stats))
```

- [ ] **Step 4: Remove the `finally` block that closed the old csv_file**

Find and delete:

```python
    finally:
        if csv_file:
            csv_file.close()
```

- [ ] **Step 5: Run all tests to confirm nothing is broken**

```bash
python3 -m pytest tests/ -v
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add github.py
git commit -m "refactor: accumulate all PR rows in memory, always fetch lines for Qodo PRs"
```

---

## Task 3: Remove `--csv`/`--html` flags, add `_output_stem`, write both output files

**Files:**
- Modify: `github.py` — `_output_stem`, `cmd_count`, `main`

- [ ] **Step 1: Add `_output_stem` helper after `_hours_between`**

```python
def _output_stem(org: str, since: "date", until: "date") -> str:
    """Return the base filename (no extension) for output files."""
    return f"{org}_{since.isoformat()}_{until.isoformat()}"
```

- [ ] **Step 2: Remove `--csv` and `--html` arguments from the argparse block in `main`**

Find and delete these two lines in `main`:

```python
    p.add_argument("--csv", metavar="FILE",
                   help="Write per-PR CSV report to FILE (e.g. report.csv)")
```

- [ ] **Step 3: Add CSV writing to `cmd_count` after rows are accumulated**

Find the `if not args.verbose:` block that prints a newline at the end of the loop:

```python
    if not args.verbose:
        print(file=sys.stderr)  # end the rolling status line
```

After that block (before the `if cp_path.exists(): cp_path.unlink()` line), insert:

```python
    stem = _output_stem(args.org, args.since, date.today())
    csv_path = Path(f"{stem}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add github.py
git commit -m "feat: replace --csv/--html flags with auto-named output files"
```

---

## Task 4: Create `report.py` with `ReportData` dataclass and `aggregate()` + tests

**Files:**
- Create: `report.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_report.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from report import aggregate, ReportData


def _row(repo="backend", creator="alice", has_qodo=True,
         suggestions=4, implemented=2,
         ar_sug=2, ar_imp=1, rr_sug=2, rr_imp=1,
         bugs_sug=1, bugs_imp=1, rule_sug=2, rule_imp=1,
         req_sug=1, req_imp=0):
    return {
        "Repo Name": repo, "PR #": 1,
        "PR URL": "https://github.com/acme/backend/pull/1",
        "PR Creator": creator,
        "Has Qodo Review": has_qodo,
        "Total Suggestions": suggestions,
        "Total Implemented": implemented,
        "Action Required Suggestions": ar_sug,
        "Action Required Implemented": ar_imp,
        "Review Recommended Suggestions": rr_sug,
        "Review Recommended Implemented": rr_imp,
        "Bugs Suggested": bugs_sug,
        "Bugs Implemented": bugs_imp,
        "Rule Violations Suggested": rule_sug,
        "Rule Violations Implemented": rule_imp,
        "Requirement Gaps Suggested": req_sug,
        "Requirement Gaps Implemented": req_imp,
        "Implementation Rate (%)": f"{100 * implemented / suggestions:.1f}" if suggestions else "",
    }


def test_aggregate_empty():
    agg = aggregate([])
    assert agg.total_prs == 0
    assert agg.prs_with_qodo == 0
    assert agg.qodo_coverage_pct == 0.0
    assert agg.total_suggestions == 0
    assert agg.by_repo == []
    assert agg.by_developer == []
    assert agg.top_prs == []


def test_aggregate_basic_counts():
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        _row(repo="api", creator="bob", suggestions=2, implemented=2),
        _row(repo="web", creator="alice", suggestions=1, implemented=0),
    ]
    agg = aggregate(rows)
    assert agg.total_prs == 3
    assert agg.prs_with_qodo == 3
    assert agg.total_suggestions == 8
    assert agg.total_implemented == 5
    assert agg.overall_impl_rate_pct == 62.5


def test_aggregate_coverage_excludes_non_qodo():
    rows = [
        _row(has_qodo=True),
        _row(has_qodo=False, suggestions=0, implemented=0),
        _row(has_qodo=False, suggestions=0, implemented=0),
    ]
    agg = aggregate(rows)
    assert agg.total_prs == 3
    assert agg.prs_with_qodo == 1
    assert agg.qodo_coverage_pct == round(100 / 3, 1)


def test_aggregate_non_qodo_excluded_from_repo_and_dev_stats():
    rows = [
        _row(repo="api", creator="alice", has_qodo=True, suggestions=4, implemented=2),
        _row(repo="api", creator="alice", has_qodo=False, suggestions=0, implemented=0),
    ]
    agg = aggregate(rows)
    assert len(agg.by_repo) == 1
    assert agg.by_repo[0]["prs"] == 1
    assert len(agg.by_developer) == 1
    assert agg.by_developer[0]["prs"] == 1


def test_aggregate_by_repo_sorted_by_prs_desc():
    rows = [
        _row(repo="api"),
        _row(repo="web"),
        _row(repo="api"),
    ]
    agg = aggregate(rows)
    assert agg.by_repo[0]["repo"] == "api"
    assert agg.by_repo[0]["prs"] == 2


def test_aggregate_by_developer_capped_at_10():
    rows = [_row(creator=f"dev{i}") for i in range(15)]
    agg = aggregate(rows)
    assert len(agg.by_developer) == 10


def test_aggregate_top_prs_returns_top_5_by_suggestions():
    rows = [_row(suggestions=i, implemented=0) for i in range(10, 0, -1)]
    agg = aggregate(rows)
    assert len(agg.top_prs) == 5
    assert agg.top_prs[0]["Total Suggestions"] == 10


def test_aggregate_severity_totals():
    rows = [
        _row(ar_sug=3, ar_imp=2, rr_sug=1, rr_imp=1),
        _row(ar_sug=1, ar_imp=0, rr_sug=2, rr_imp=1),
    ]
    agg = aggregate(rows)
    assert agg.action_required_suggested == 4
    assert agg.action_required_implemented == 2
    assert agg.review_recommended_suggested == 3
    assert agg.review_recommended_implemented == 2


def test_aggregate_category_totals():
    rows = [
        _row(bugs_sug=2, bugs_imp=1, rule_sug=1, rule_imp=0, req_sug=1, req_imp=1),
        _row(bugs_sug=1, bugs_imp=1, rule_sug=3, rule_imp=2, req_sug=0, req_imp=0),
    ]
    agg = aggregate(rows)
    assert agg.bugs_suggested == 3
    assert agg.bugs_implemented == 2
    assert agg.rule_violations_suggested == 4
    assert agg.rule_violations_implemented == 2
    assert agg.requirement_gaps_suggested == 1
    assert agg.requirement_gaps_implemented == 1
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
python3 -m pytest tests/test_report.py -v
```

Expected: all FAILED with `ModuleNotFoundError: No module named 'report'`

- [ ] **Step 3: Create `report.py` with `ReportData` and `aggregate`**

Create `report.py`:

```python
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional
import base64


def _rate(implemented: int, total: int) -> float:
    return round(100 * implemented / total, 1) if total > 0 else 0.0


@dataclass
class ReportData:
    total_prs: int
    prs_with_qodo: int
    qodo_coverage_pct: float
    total_suggestions: int
    total_implemented: int
    overall_impl_rate_pct: float
    action_required_suggested: int
    action_required_implemented: int
    action_required_rate_pct: float
    review_recommended_suggested: int
    review_recommended_implemented: int
    review_recommended_rate_pct: float
    bugs_suggested: int
    bugs_implemented: int
    bugs_rate_pct: float
    rule_violations_suggested: int
    rule_violations_implemented: int
    rule_violations_rate_pct: float
    requirement_gaps_suggested: int
    requirement_gaps_implemented: int
    requirement_gaps_rate_pct: float
    by_repo: list
    by_developer: list
    top_prs: list


def aggregate(rows: list) -> ReportData:
    total_prs = len(rows)
    prs_with_qodo = sum(1 for r in rows if r.get("Has Qodo Review"))

    total_sug = sum(r.get("Total Suggestions", 0) for r in rows)
    total_imp = sum(r.get("Total Implemented", 0) for r in rows)

    ar_sug = sum(r.get("Action Required Suggestions", 0) for r in rows)
    ar_imp = sum(r.get("Action Required Implemented", 0) for r in rows)
    rr_sug = sum(r.get("Review Recommended Suggestions", 0) for r in rows)
    rr_imp = sum(r.get("Review Recommended Implemented", 0) for r in rows)
    bugs_sug = sum(r.get("Bugs Suggested", 0) for r in rows)
    bugs_imp = sum(r.get("Bugs Implemented", 0) for r in rows)
    rule_sug = sum(r.get("Rule Violations Suggested", 0) for r in rows)
    rule_imp = sum(r.get("Rule Violations Implemented", 0) for r in rows)
    req_sug = sum(r.get("Requirement Gaps Suggested", 0) for r in rows)
    req_imp = sum(r.get("Requirement Gaps Implemented", 0) for r in rows)

    repo_acc: dict = defaultdict(lambda: {"prs": 0, "suggestions": 0, "implemented": 0})
    dev_acc: dict = defaultdict(lambda: {"prs": 0, "suggestions": 0, "implemented": 0})
    for r in rows:
        if not r.get("Has Qodo Review"):
            continue
        repo_acc[r["Repo Name"]]["prs"] += 1
        repo_acc[r["Repo Name"]]["suggestions"] += r.get("Total Suggestions", 0)
        repo_acc[r["Repo Name"]]["implemented"] += r.get("Total Implemented", 0)
        dev_acc[r["PR Creator"]]["prs"] += 1
        dev_acc[r["PR Creator"]]["suggestions"] += r.get("Total Suggestions", 0)
        dev_acc[r["PR Creator"]]["implemented"] += r.get("Total Implemented", 0)

    by_repo = sorted(
        [{"repo": k, **v} for k, v in repo_acc.items()],
        key=lambda x: x["prs"], reverse=True,
    )
    by_developer = sorted(
        [{"developer": k, **v} for k, v in dev_acc.items()],
        key=lambda x: x["prs"], reverse=True,
    )[:10]
    top_prs = sorted(
        [r for r in rows if r.get("Has Qodo Review")],
        key=lambda r: r.get("Total Suggestions", 0), reverse=True,
    )[:5]

    return ReportData(
        total_prs=total_prs,
        prs_with_qodo=prs_with_qodo,
        qodo_coverage_pct=_rate(prs_with_qodo, total_prs),
        total_suggestions=total_sug,
        total_implemented=total_imp,
        overall_impl_rate_pct=_rate(total_imp, total_sug),
        action_required_suggested=ar_sug,
        action_required_implemented=ar_imp,
        action_required_rate_pct=_rate(ar_imp, ar_sug),
        review_recommended_suggested=rr_sug,
        review_recommended_implemented=rr_imp,
        review_recommended_rate_pct=_rate(rr_imp, rr_sug),
        bugs_suggested=bugs_sug,
        bugs_implemented=bugs_imp,
        bugs_rate_pct=_rate(bugs_imp, bugs_sug),
        rule_violations_suggested=rule_sug,
        rule_violations_implemented=rule_imp,
        rule_violations_rate_pct=_rate(rule_imp, rule_sug),
        requirement_gaps_suggested=req_sug,
        requirement_gaps_implemented=req_imp,
        requirement_gaps_rate_pct=_rate(req_imp, req_sug),
        by_repo=by_repo,
        by_developer=by_developer,
        top_prs=top_prs,
    )
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_report.py -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add report.py tests/test_report.py
git commit -m "feat: add report.py with ReportData dataclass and aggregate()"
```

---

## Task 5: Implement `generate_html()` in `report.py`

This task adds the full HTML rendering to `report.py`. It is one large addition to the file — all helpers and the public `generate_html` function. A smoke test is included to verify the output is structurally correct.

**Files:**
- Modify: `report.py` — add CSS constant, helper functions, `generate_html`
- Modify: `tests/test_report.py` — add smoke test

- [ ] **Step 1: Add smoke test to `tests/test_report.py`**

Append to `tests/test_report.py`:

```python
from datetime import date


def test_generate_html_smoke():
    from report import generate_html
    rows = [
        _row(repo="api", creator="alice", suggestions=5, implemented=3),
        _row(repo="web", creator="bob", has_qodo=False, suggestions=0, implemented=0),
    ]
    html = generate_html(rows, "acme-corp", date(2025, 1, 1), date(2026, 1, 1), logo_path=None)
    assert "<!DOCTYPE html>" in html
    assert "acme-corp" in html
    assert "Qodo Code Review" in html
    assert "Executive Summary" in html
    assert "Adoption" in html
    assert "Impact by Severity" in html
    assert "Impact by Category" in html
    assert "Top 5 PRs" in html
    # stat values present
    assert ">1<" in html   # prs_with_qodo
    assert ">5<" in html   # total_suggestions
    assert ">3<" in html   # total_implemented
```

- [ ] **Step 2: Run the smoke test to confirm it fails**

```bash
python3 -m pytest tests/test_report.py::test_generate_html_smoke -v
```

Expected: FAILED with `ImportError` (generate_html not defined yet)

- [ ] **Step 3: Append the CSS constant and all rendering helpers + `generate_html` to `report.py`**

Append everything below to the bottom of `report.py` (after the `aggregate` function):

```python
_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  background: #f4f4f4;
  color: #1c1c1c;
  font-size: 14px;
  line-height: 1.5;
}
.page { max-width: 900px; margin: 0 auto; padding: 32px 24px; }
.report-header {
  display: flex; align-items: center; gap: 20px;
  background: #ffffff; border-radius: 8px;
  padding: 24px 28px; margin-bottom: 24px;
  border-bottom: 3px solid #634fd1;
}
.report-header img { height: 48px; flex-shrink: 0; }
.report-header h1 { font-size: 22px; font-weight: 700; color: #634fd1; }
.subtitle { color: #6e6e6e; font-size: 13px; margin-top: 4px; }
section {
  background: #ffffff; border-radius: 8px;
  padding: 24px 28px; margin-bottom: 24px;
}
section h2 {
  font-size: 16px; font-weight: 600; color: #634fd1;
  margin-bottom: 18px; border-bottom: 1px solid #dfdfdf; padding-bottom: 10px;
}
.subsection { font-size: 13px; font-weight: 600; color: #3d3d3d; margin-bottom: 10px; }
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 14px;
}
.stat-card {
  background: #dddcff; border-radius: 6px;
  padding: 18px 16px; text-align: center;
}
.stat-value { font-size: 28px; font-weight: 700; color: #634fd1; line-height: 1; }
.stat-label {
  font-size: 11px; color: #3d3d3d; margin-top: 6px;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.impact-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
}
.impact-card {
  border-radius: 6px; padding: 16px;
  border-left: 4px solid #634fd1; background: #f4f4f4;
}
.impact-card.red  { border-left-color: #e55c83; }
.impact-card.orange { border-left-color: #cca05a; }
.impact-card.purple { border-left-color: #634fd1; }
.impact-card h3 { font-size: 13px; font-weight: 600; color: #3d3d3d; margin-bottom: 10px; }
.impact-row {
  display: flex; justify-content: space-between;
  font-size: 13px; margin-bottom: 4px;
}
.impact-row .val { font-weight: 600; }
.rate-pill {
  display: inline-block; padding: 2px 8px; border-radius: 12px;
  font-size: 12px; font-weight: 600;
  background: #c7f5ea; color: #206b58; margin-top: 8px;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 8px 12px;
  background: #f4f4f4; font-weight: 600; color: #3d3d3d;
  border-bottom: 2px solid #dfdfdf;
}
tbody tr:nth-child(even) { background: #f4f4f4; }
tbody td { padding: 8px 12px; border-bottom: 1px solid #dfdfdf; }
tbody td a { color: #634fd1; text-decoration: none; }
tbody td a:hover { text-decoration: underline; }
@media print {
  body { background: #ffffff; font-size: 11px; }
  .page { max-width: 100%; padding: 0; }
  section { break-inside: avoid; border: 1px solid #dfdfdf; margin-bottom: 16px; }
  .report-header { border: 1px solid #dfdfdf; margin-bottom: 16px; }
  .two-col { grid-template-columns: 1fr 1fr; }
}
"""


def _embed_logo(logo_path: Optional[str]) -> str:
    if not logo_path:
        return ""
    p = Path(logo_path)
    if not p.exists():
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b64}" alt="Qodo" height="48">'


def _rate_str(implemented: int, total: int) -> str:
    return f"{100 * implemented / total:.1f}%" if total > 0 else "—"


def _stat_card(label: str, value: str) -> str:
    return (
        f'<div class="stat-card">'
        f'<div class="stat-value">{value}</div>'
        f'<div class="stat-label">{label}</div>'
        f'</div>'
    )


def _impact_card(title: str, color: str, suggested: int, implemented: int) -> str:
    rate = _rate_str(implemented, suggested)
    return (
        f'<div class="impact-card {color}">'
        f'<h3>{title}</h3>'
        f'<div class="impact-row"><span>Suggested</span><span class="val">{suggested}</span></div>'
        f'<div class="impact-row"><span>Implemented</span><span class="val">{implemented}</span></div>'
        f'<div><span class="rate-pill">{rate} implemented</span></div>'
        f'</div>'
    )


def _section_exec_summary(agg: ReportData) -> str:
    cards = "".join([
        _stat_card("PRs Reviewed by Qodo", str(agg.prs_with_qodo)),
        _stat_card("Qodo Coverage", f"{agg.qodo_coverage_pct:.1f}%"),
        _stat_card("Total Issues Caught", str(agg.total_suggestions)),
        _stat_card("Issues Implemented", str(agg.total_implemented)),
        _stat_card("Implementation Rate", f"{agg.overall_impl_rate_pct:.1f}%"),
    ])
    return f'<section><h2>Executive Summary</h2><div class="stat-grid">{cards}</div></section>'


def _table(headers: list, rows_html: str) -> str:
    ths = "".join(f"<th>{h}</th>" for h in headers)
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{rows_html}</tbody></table>"


def _section_adoption(agg: ReportData) -> str:
    repo_rows = "".join(
        f"<tr><td>{r['repo']}</td><td>{r['prs']}</td><td>{r['suggestions']}</td>"
        f"<td>{_rate_str(r['implemented'], r['suggestions'])}</td></tr>"
        for r in agg.by_repo
    )
    dev_rows = "".join(
        f"<tr><td>{r['developer']}</td><td>{r['prs']}</td><td>{r['suggestions']}</td>"
        f"<td>{_rate_str(r['implemented'], r['suggestions'])}</td></tr>"
        for r in agg.by_developer
    )
    repo_table = _table(["Repository", "PRs", "Issues", "Impl. Rate"], repo_rows)
    dev_table = _table(["Developer", "PRs", "Issues", "Impl. Rate"], dev_rows)
    return (
        f'<section><h2>Adoption</h2>'
        f'<div class="two-col">'
        f'<div><p class="subsection">By Repository</p>{repo_table}</div>'
        f'<div><p class="subsection">By Developer (top 10)</p>{dev_table}</div>'
        f'</div></section>'
    )


def _section_severity(agg: ReportData) -> str:
    cards = (
        _impact_card("Action Required", "red",
                     agg.action_required_suggested, agg.action_required_implemented) +
        _impact_card("Review Recommended", "orange",
                     agg.review_recommended_suggested, agg.review_recommended_implemented)
    )
    return f'<section><h2>Impact by Severity</h2><div class="impact-grid">{cards}</div></section>'


def _section_categories(agg: ReportData) -> str:
    cards = (
        _impact_card("Bugs", "red", agg.bugs_suggested, agg.bugs_implemented) +
        _impact_card("Rule Violations", "orange",
                     agg.rule_violations_suggested, agg.rule_violations_implemented) +
        _impact_card("Requirement Gaps", "purple",
                     agg.requirement_gaps_suggested, agg.requirement_gaps_implemented)
    )
    return f'<section><h2>Impact by Category</h2><div class="impact-grid">{cards}</div></section>'


def _section_top_prs(agg: ReportData) -> str:
    pr_rows = ""
    for r in agg.top_prs:
        url = r.get("PR URL", "")
        pr_ref = f'<a href="{url}">#{r["PR #"]}</a>' if url else f'#{r["PR #"]}'
        pr_rows += (
            f'<tr><td>{r["Repo Name"]}</td><td>{pr_ref}</td>'
            f'<td>{r["PR Creator"]}</td>'
            f'<td>{r["Total Suggestions"]}</td><td>{r["Total Implemented"]}</td>'
            f'<td>{r.get("Implementation Rate (%)", "—")}</td></tr>'
        )
    table = _table(["Repo", "PR", "Creator", "Issues", "Implemented", "Rate"], pr_rows)
    return f'<section><h2>Top 5 PRs by Issues Found</h2>{table}</section>'


def generate_html(
    rows: list,
    org: str,
    since: "date",
    until: "date",
    logo_path: Optional[str] = "logo.png",
) -> str:
    agg = aggregate(rows)
    logo_tag = _embed_logo(logo_path)
    since_fmt = since.strftime("%b %d, %Y")
    until_fmt = until.strftime("%b %d, %Y")
    generated = date.today().strftime("%b %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Qodo Impact Report &mdash; {org}</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="page">
  <header class="report-header">
    {logo_tag}
    <div>
      <h1>Qodo Code Review &mdash; Impact Report</h1>
      <p class="subtitle">{org} &middot; {since_fmt} &ndash; {until_fmt} &middot; Generated {generated}</p>
    </div>
  </header>
  {_section_exec_summary(agg)}
  {_section_adoption(agg)}
  {_section_severity(agg)}
  {_section_categories(agg)}
  {_section_top_prs(agg)}
</div>
</body>
</html>"""
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add report.py tests/test_report.py
git commit -m "feat: implement generate_html() with full branded HTML report"
```

---

## Task 6: Wire `report.py` into `github.py` and write HTML output

**Files:**
- Modify: `github.py` — import `report`, call `generate_html`, print output paths

- [ ] **Step 1: Add `import report` to the top of `github.py`**

Add after the existing stdlib imports (near `from pathlib import Path`):

```python
import report
```

- [ ] **Step 2: Add HTML generation after CSV writing in `cmd_count`**

Find the block you added in Task 3 that writes the CSV:

```python
    stem = _output_stem(args.org, args.since, date.today())
    csv_path = Path(f"{stem}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
```

Replace it with:

```python
    stem = _output_stem(args.org, args.since, date.today())

    csv_path = Path(f"{stem}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    html_path = Path(f"{stem}.html")
    html_path.write_text(
        report.generate_html(rows, args.org, args.since, date.today(), "logo.png"),
        encoding="utf-8",
    )

    print(f"\nReports written:")
    print(f"  CSV:  {csv_path}")
    print(f"  HTML: {html_path}")
```

- [ ] **Step 3: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all PASSED

- [ ] **Step 4: Verify output with a dry-run against the existing CSV**

The project has `report3.csv`. Run a quick manual check to confirm `generate_html` produces valid output from real data:

```bash
python3 - <<'EOF'
import csv, report
from datetime import date

with open("report3.csv") as f:
    rows = list(csv.DictReader(f))

# DictReader returns strings; coerce numeric fields
for r in rows:
    for col in ["Total Suggestions", "Total Implemented",
                "Action Required Suggestions", "Action Required Implemented",
                "Review Recommended Suggestions", "Review Recommended Implemented",
                "Bugs Suggested", "Bugs Implemented",
                "Rule Violations Suggested", "Rule Violations Implemented",
                "Requirement Gaps Suggested", "Requirement Gaps Implemented"]:
        r[col] = int(r[col]) if r.get(col) else 0
    r["Has Qodo Review"] = r.get("Has Qodo Review", "True") not in ("False", "", "0")

html = report.generate_html(rows, "qodo-se", date(2026, 1, 1), date(2026, 5, 12), "logo.png")
open("preview.html", "w").write(html)
print(f"Written {len(html)} bytes to preview.html")
EOF
```

Expected: prints `Written NNNNN bytes to preview.html`. Open `preview.html` in a browser to visually verify the report looks correct.

- [ ] **Step 5: Delete the preview file**

```bash
rm preview.html
```

- [ ] **Step 6: Commit**

```bash
git add github.py
git commit -m "feat: wire report.py into github.py, auto-write HTML on every run"
```
