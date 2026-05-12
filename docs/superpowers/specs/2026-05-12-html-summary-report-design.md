# Design: HTML Summary Report

**Date:** 2026-05-12
**Status:** Approved

## Overview

Add a branded, print-ready HTML summary report generated automatically alongside the existing CSV on every run of `github.py`. The report targets an internal evangelist persona — someone inside a Qodo customer organization who wants to make the case for Qodo's value to colleagues and leadership.

---

## Architecture

### New file: `report.py`

A stdlib-only module responsible for all HTML generation. Exposes one public function:

```python
def generate_html(rows: list[dict], org: str, since: date, until: date, logo_path: Optional[str]) -> str
```

- `rows` — the same list of per-PR dicts already accumulated during `cmd_count` (identical structure to CSV rows)
- Returns a complete, self-contained HTML string (all CSS in a `<style>` block, logo embedded as base64 data URI)
- No third-party imports — stdlib only (`base64`, `datetime`, `pathlib`, string formatting)

### Changes to `github.py`

- **Remove** `--csv` and `--html` CLI flags
- **Add** `import report` at the top
- In `cmd_count`:
  - Accumulate all PR rows in a `rows: list[dict]` regardless (previously only done when `--csv` was set)
  - After the run, write both output files using the naming convention below
  - Pass `logo_path="logo.png"` (relative to CWD) to `generate_html`; if the file doesn't exist, the header renders without a logo

### Output file naming

Both files are written to the current working directory:

```text
{org}_{since}_{until}.csv
{org}_{since}_{until}.html
```

Example: `codium-ai_2025-05-12_2026-05-12.csv` / `codium-ai_2025-05-12_2026-05-12.html`

`since` and `until` are `YYYY-MM-DD` formatted dates matching the run window.

---

## Report Sections

Sections are ordered to tell an adoption + impact story.

### 1. Header

- Qodo logo (`logo.png`, embedded as base64 data URI; omitted gracefully if file not found)
- Report title: "Qodo Code Review — Impact Report"
- Subtitle: org name, date range (`May 12 2025 – May 12 2026`), generated date

### 2. Executive Summary

Five stat cards in a 2×3 grid (last card centered if odd):

| Card | Value |
| --- | --- |
| PRs Reviewed by Qodo | count of PRs with a Qodo comment |
| Qodo Coverage | `prs_with_qodo / total_prs` as a percentage |
| Total Issues Caught | sum of `Total Suggestions` across all rows |
| Issues Resolved | sum of `Total Implemented` |
| Overall Implementation Rate | `implemented / suggestions` as a percentage |

Cards use the purple-50 emphasis background (`#dddcff`) with the purple-500 (`#634fd1`) accent.

### 3. Adoption

Two tables side by side (stacked on narrow/print):

**By Repository** — columns: Repo, PRs Reviewed, Total Suggestions, Implementation Rate (%). Sorted by PRs Reviewed descending.

**By Developer** — columns: Developer, PRs Reviewed, Total Suggestions, Implementation Rate (%). Sorted by PRs Reviewed descending. Top 10 only (avoids overwhelming tables on large orgs).

### 4. Impact by Severity

Two cards side by side:

- **Action Required** — Suggested / Resolved / Rate
- **Review Recommended** — Suggested / Resolved / Rate

Action Required uses red-400 (`#e55c83`) accent; Review Recommended uses orange-400 (`#cca05a`) accent.

### 5. Impact by Category

Three cards in a row:

- **Bugs** — Suggested / Resolved / Rate — red accent
- **Rule Violations** — Suggested / Resolved / Rate — orange accent
- **Requirement Gaps** — Suggested / Resolved / Rate — purple accent

### 6. Top PRs

Table of the top 5 PRs by `Total Suggestions`, columns: Repo, PR #, Creator, Suggestions, Implemented, Rate (%), link to PR URL. PR # is a hyperlink to the PR URL.

---

## Visual Styling

### Colors (from Qodo design system)

| Role | Value |
| --- | --- |
| Page background | `#f4f4f4` (neutral-100) |
| Card/panel background | `#ffffff` |
| Emphasis card background | `#dddcff` (purple-50) |
| Primary accent | `#634fd1` (purple-500) |
| Positive / implemented | `#4ec2a4` (green-400) |
| Negative / action-required | `#e55c83` (red-400) |
| Warning / review-recommended | `#cca05a` (orange-400) |
| Body text | `#1c1c1c` (neutral-700) |
| Muted text | `#6e6e6e` (neutral-400) |
| Table border | `#dfdfdf` (neutral-200) |

### Typography

System font stack: `-apple-system, "Segoe UI", Helvetica, Arial, sans-serif`. No external font requests.

### Layout

- Single-column, `max-width: 900px`, centered
- Stat cards: CSS grid, `repeat(auto-fill, minmax(160px, 1fr))`
- All CSS in a single `<style>` block in `<head>` — zero external stylesheets or scripts
- `@media print` rules: hide browser chrome artifacts, ensure section page breaks fall cleanly, expand any collapsed tables

### Logo

`logo.png` is read from the working directory at report generation time, base64-encoded, and inlined as an `<img src="data:image/png;base64,...">` tag. If the file is missing, the `<img>` tag is omitted; the rest of the header renders normally. Logo height: `48px`.

---

## Constraints

- Zero third-party Python dependencies — stdlib only
- No external HTTP requests at render time (no CDN fonts, scripts, or images)
- Compatible with Python 3.9+ (matches existing codebase)
- HTML file must be fully self-contained — open in any browser and print to PDF with one click
