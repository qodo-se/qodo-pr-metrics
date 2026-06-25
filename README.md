# qodo-pr-metrics

Generates an HTML report measuring Qodo code-review impact across merged PRs in a GitHub org — covering suggestion volume, implementation rates, reviewer velocity, and developer adoption.

[View sample report](https://qodo-se.github.io/qodo-pr-metrics/examples/sample_report.html) · [View sample per-user report](https://qodo-se.github.io/qodo-pr-metrics/examples/sample_user_report.html)

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Providers](#providers)
  - [GitHub](#github)
  - [Bitbucket Data Center](#bitbucket-data-center)
- [Output](#output)
- [Engineering Audit](#engineering-audit-pre-install-diagnostic)

## What it does

For each merged PR in a configurable lookback window, the script:

1. Finds Qodo's review comment (identified by the "Code Review by Qodo" marker)
2. Counts suggestions made and how many were implemented (detected via strikethrough formatting)
3. Records timing, spotlight issues (Security/Correctness), and per-developer activity

It produces an org-wide HTML report, a per-developer impact report, and a raw CSV — see [Output files](#output-files) below.

## How it works

- Identifies Qodo comments by the stable "Code Review by Qodo" string, which is bot-account-name-independent
- Detects implemented suggestions by looking for strikethrough markers (`~~text~~`, `<s>`, `<del>`, `<strike>`, or the ☑ emoji) on suggestion titles

## Providers

### GitHub

#### Prerequisites

- Python 3.7+
- [`gh` CLI](https://cli.github.com/) installed and authenticated with access to the target org

```bash
gh auth status   # should show you logged in
```

- Authenticates through the `gh` CLI — no token management required
- Searches merged PRs in date-chunked windows (30-day chunks) to stay under GitHub's 1000-result search cap

The script uses the GitHub Search API and GraphQL API. The `gh` CLI handles authentication — no manual token management is required, but your token must have the right scopes:

| Scope | Required for |
| --- | --- |
| `repo` | Searching and reading private repos. Without this, only public repos are visible and private repos are silently omitted from results. |
| `read:org` | Org membership visibility. Optional — only needed if the `org:` search qualifier returns no results for your org. |

To check your current scopes:

```bash
gh auth status
```

To add a missing scope:

```bash
gh auth refresh -s repo
```

> **Note:** Results are always scoped to repos the authenticated token can access. If you suspect missing repos, compare the "Repos in results" list printed at the end of a run against your expected scope, or use `--repos` to declare the repos explicitly.

#### Usage

**Mac/Linux:**

```bash
# Full run, default 90-day lookback
python3 qodo_metrics.py --org acme-corp

# Custom date window
python3 qodo_metrics.py --org acme-corp --since 2025-05-12
python3 qodo_metrics.py --org acme-corp --days 90

# Scope to specific repos
python3 qodo_metrics.py --org acme-corp --repos frontend-app backend-api

# Anonymize developer and repo names for external sharing
python3 qodo_metrics.py --org acme-corp --anonymize

# Anonymize only developer names (keep repo names visible)
python3 qodo_metrics.py --org acme-corp --anonymize users

# Anonymize only repo names (keep developer names visible)
python3 qodo_metrics.py --org acme-corp --anonymize repos
```

**Windows:**

```bash
python qodo_metrics.py --org acme-corp
```

#### Options

| Flag | Description |
|---|---|
| `--org` | GitHub org login (required, e.g. `acme-corp`) |
| `--since` | Start date in `YYYY-MM-DD` format |
| `--days` | Lookback window in days (default: `90`; mutually exclusive with `--since`) |
| `--inspect` | Print the raw body of the first Qodo comment found and exit |
| `--verbose` | Print per-PR suggestion counts instead of just the final summary |
| `--resume` | Resume from a previous checkpoint (`reports/ORG-checkpoint.json`) |
| `--repos` | Space-delimited list of repo names to scope the run (e.g. `--repos frontend-app backend-api`); omit to scan the full org |
| `--anonymize [SCOPE]` | Replace identifying data with stable pseudonyms in all output files; output filenames get an `_anon` suffix. `SCOPE`: `users` (PR Creator / Final Approver only), `repos` (Repo Name / PR URL only), or omit `SCOPE` to anonymize both |
| `--loc-page-size N` | Starting page size for the org-wide LOC GraphQL query (default: `50`, range: `10`–`100`). Lower it (e.g. `25` or `10`) for very large orgs where GitHub returns persistent 5xx or stream-cancel errors on the LOC fetch — the script already shrinks adaptively on those errors, but starting smaller avoids the wasted retries. |
| `--pr-batch-size N` | Starting batch size for the per-PR GraphQL data lookup that pulls comments, reviews, commits, and CI status (default: `25`, range: `5`–`50`). Lower it (e.g. `10` or `5`) for very large orgs where GitHub returns persistent 5xx or stream-cancel errors during the main PR walk — the script already shrinks adaptively on those errors, but starting smaller avoids the wasted retries. |

### Bitbucket Data Center

Pass `--provider bitbucket-dc` to run against a Bitbucket Data Center (or Server) instance instead of GitHub. The same output files are produced; the same report sections are rendered.

#### Prerequisites

A personal access token with **read** access to the target project(s). Export it before running:

```bash
export BITBUCKET_TOKEN=<your-token>
```

No `gh` CLI is required for Bitbucket runs.

#### Usage

```bash
# Scan a single Bitbucket project, default 90-day lookback
python3 qodo_metrics.py --provider bitbucket-dc \
    --base-url https://bitbucket.example.com \
    --project COD

# Custom date window
python3 qodo_metrics.py --provider bitbucket-dc \
    --base-url https://bitbucket.example.com \
    --project COD --since 2025-05-01

# Scan every repo in the instance (requires broad token permissions)
python3 qodo_metrics.py --provider bitbucket-dc \
    --base-url https://bitbucket.example.com \
    --all-projects

# Only count LOC for Qodo-reviewed PRs (skip the whole-population LOC denominator)
python3 qodo_metrics.py --provider bitbucket-dc \
    --base-url https://bitbucket.example.com \
    --project COD --loc qodo-only

# Skip TLS verification for self-signed certificates
python3 qodo_metrics.py --provider bitbucket-dc \
    --base-url https://bitbucket.example.com \
    --project COD --insecure
```

#### Options

| Flag | Description |
|---|---|
| `--provider bitbucket-dc` | Select the Bitbucket Data Center collector (default: `github`) |
| `--base-url URL` | Bitbucket instance base URL, e.g. `https://bitbucket.example.com` (required) |
| `--project KEY` | Bitbucket project key to scan, e.g. `COD` (mutually exclusive with `--all-projects`) |
| `--all-projects` | Scan every repository in the instance (mutually exclusive with `--project`) |
| `--loc {all,qodo-only,off}` | Line-count collection mode (default: `all`). `all` fetches LOC for every merged PR; `qodo-only` skips the whole-population denominator; `off` disables LOC collection entirely |
| `--concurrency N` | Maximum concurrent per-PR HTTP fetches (default: `8`) |
| `--insecure` | Disable TLS certificate verification — use this for self-signed or internal CA certificates |

`BITBUCKET_TOKEN` environment variable must be set; the script exits with an error if it is absent.

#### Limitations

Bitbucket Data Center differs from GitHub in a few ways that affect what the report can show:

- **No PR labels.** Bitbucket DC does not support PR labels, so the *AI-authored-by-label* and *hotfix-by-label* signals are unavailable. Title, branch name, and PR body signals still work.
- **Coarser Time-to-First-Qodo-Comment.** Bitbucket's activity feed records when a comment was created but not its edit history, so this metric reflects the original post time rather than the time of any subsequent edits to the review body.
- **Per-section implemented breakdown unavailable.** Bitbucket's PR comment model does not expose the strikethrough edit history that signals an implemented finding. Implemented findings are moved to a "Resolved" section in the comment, but the action-level breakdown (which specific suggestion was resolved) is not recoverable — the resolved count is available, but the per-suggestion trail is not.
- **LOC requires Bitbucket DC 9.1+ for diff-stats.** On older instances the collector falls back to computing LOC from the raw diff endpoint, which is slower and counts changed lines rather than added lines for binary-patched files.

## Output

After a run completes, the script prints a terminal summary that includes:

- **Total LOC added (all PRs):** sum of lines added across every merged PR in the window
- **Qodo-reviewed LOC:** lines added in Qodo-reviewed PRs, with its share of the total

### Output files

The script generates three output files, all written into the `reports/` directory (created automatically and gitignored):

- `reports/{org}_{since_date}_{until_date}.csv` — raw per-PR data
- `reports/{org}_{since_date}_{until_date}.html` — org-wide visual summary report
- `reports/{org}_{since_date}_{until_date}_user.html` — per-developer impact report with an interactive date-range slider that recomputes the headline, at-a-glance panel, and per-developer table client-side

For example, running `python3 qodo_metrics.py --org acme-corp` creates `reports/acme-corp_2025-05-12_2026-05-12.csv`, `reports/acme-corp_2025-05-12_2026-05-12.html`, and `reports/acme-corp_2025-05-12_2026-05-12_user.html`.

> **Scope note:** In the per-developer report, "Total PRs" currently equals "Qodo-reviewed PRs" — the pipeline's search only returns PRs that carry a Qodo review comment. Reporting true totals (Qodo or not) requires the row producer to also fetch unreviewed PRs and mark them `Has Qodo Review: False`; the report already reads that field correctly. The per-developer report buckets PRs by **creation date** within the window, so a PR merged just inside the window but created before `since` is excluded from the per-developer view.

### CSV columns

Each row in the CSV contains 37 columns with per-PR data:

| Column | Description |
|---|---|
| Repo Name | Repository name within the org |
| PR # | Pull request number |
| PR URL | Link to the PR (GitHub PR URL, or Bitbucket PR self link) |
| PR Creation Date | ISO-8601 timestamp when the PR was opened |
| PR Merge Date | ISO-8601 timestamp when the PR was merged |
| Hours to Merge | Whole hours from creation to merge |
| PR Creator | Username of the PR author (GitHub login, or Bitbucket username) |
| Lines Added | Total lines added |
| Action Required Suggestions | Count of "Action Required" suggestions |
| Action Required Implemented | Count of implemented "Action Required" suggestions |
| Action Required Dismissed | Count of "Action Required" suggestions the developer explicitly dismissed (strikethrough + ✗ Dismissed badge). These are NOT included in `Action Required Implemented`. |
| Review Recommended Suggestions | Count of "Review Recommended" suggestions |
| Review Recommended Implemented | Count of implemented "Review Recommended" suggestions |
| Review Recommended Dismissed | Count of "Review Recommended" suggestions explicitly dismissed. |
| Bugs Suggested | Count of bug suggestions |
| Bugs Implemented | Count of implemented bug suggestions |
| Rule Violations Suggested | Count of rule-violation suggestions |
| Rule Violations Implemented | Count of implemented rule-violation suggestions |
| Requirement Gaps Suggested | Count of requirement-gap suggestions |
| Requirement Gaps Implemented | Count of implemented requirement-gap suggestions |
| Total Suggestions | Sum of all suggestion categories |
| Total Implemented | Sum of all implemented categories |
| Total Dismissed | Total suggestions dismissed across all sections. `Implementation Rate (%)` reflects only suggestions that were actually fixed, not dismissed ones. |
| Implementation Rate (%) | `Total Implemented / Total Suggestions × 100`, blank when 0 suggestions |
| Suggestions per 100 Lines | `Total Suggestions / Lines Added × 100`, blank when lines = 0 or suggestions = 0 |
| Time to First Qodo Comment (min) | Minutes from PR creation to Qodo's first comment; blank if no Qodo comment |
| Time to First Human Comment (min) | Minutes from PR creation to the first non-Qodo comment; blank if none |
| Has Human Comment | `True` if any non-Qodo comment exists on the PR |
| Spotlight Issues | JSON array of high-impact Action Required issues (Security or Correctness sub-label) that were implemented |
| Is AI Authored | `True` if the PR was detected as AI-assisted (by body patterns or labels) |
| AI Author Type | Which AI tool: `copilot`, `cursor`, `claude`, `ai`, or blank if not detected |
| Reviewer Count | Number of distinct reviewers who submitted a review |
| Had Request Changes | `True` if any reviewer submitted a "Request Changes" review |
| Final Approver | Username of the last reviewer to approve (GitHub login, or Bitbucket username); blank if none |
| CI Status | Status check rollup state at the last commit: `SUCCESS`, `FAILURE`, `PENDING`, or blank if unavailable |
| Commits After Qodo | Number of commits pushed after Qodo posted its first review |
| Speed to First Fix (min) | Minutes between Qodo's first review and the first commit that followed it; blank if no post-review commit |

### HTML report sections

The HTML report is organized into the following sections:

| Section | What it shows |
|---|---|
| Hero | Narrative headline summarizing Qodo's impact for the org and period |
| Executive Summary | At-a-glance stat cards: PRs reviewed by Qodo, total issues caught, issues resolved, overall implementation rate |
| Trend | Findings caught & fixed, week over week; shown when weekly coverage data is available |
| From merged PR to high-impact fix | Conversion funnel: the share of PRs surviving each step, down to PRs where Qodo measurably prevented something from reaching production |
| High-impact findings caught & fixed | Cards for Action Required findings flagged as Security or Correctness — the issues most likely to have caused an incident in production |
| What Qodo flagged & how it landed | Counts and implementation rates by severity (Action Required vs Review Recommended) and by category (Bugs, Rule Violations, Requirement Gaps) |
| First feedback on a PR | Density plot of time to first feedback, comparing Qodo's median vs the first human reviewer's |
| Developer adoption matrix | Every author plotted by action-required findings (log scale) vs implementation rate, split into four quadrants |
| Hours Saved | Estimated senior-engineer review hours offloaded, based on lines of code reviewed |

The Trend, Spotlight (High-impact findings), and Velocity (First feedback) sections are omitted from the report if no relevant data is present.

## Engineering Audit (pre-install diagnostic)

`engineering_audit.py` is a separate, **pre-install** report. Where the main report measures Qodo's impact *after* it's installed, the Engineering Audit establishes a **baseline before Qodo is in the picture** — it reads only the GitHub GraphQL API via the `gh` CLI and **does not touch any Qodo product data**. Run it before install to surface the pain a team should be solving, then re-run after install to quantify what changed.

It reads the last N days of merged PRs from a GitHub org (or a scoped list of repos) and renders a **single self-contained HTML file** (logo and mascot are inlined as data URIs — nothing is fetched at view time, and the report can be shared as one file). The report is rendered client-side: the Python emits an aggregates JSON blob and a scatter blob into the template, and the template's inline script draws every number, chart, and severity-banded sentence from those.

The report surfaces seven measurable patterns:

| Section | What it shows |
|---|---|
| 01 Review-bypass on large PRs | Share of merges that are big *and* fast with no human engagement, plus a lines-vs-hours scatter highlighting rubber-stamped PRs |
| 02 Review depth | Share of merged PRs that received no human engagement — neither a non-bot issue comment nor a human review (approve / request-changes / comment) — before merge |
| 03 Reviewer concentration | Bus factor — how few reviewers approve the bulk of all PRs |
| 04 Cycle-time tail | Distribution of time-to-merge, highlighting the long tail |
| 05 AI-authored share | Share of merges detected as AI-assisted |
| 06 Volume scaling | PR cadence week over week |
| 07 Cost of first-pass review | Interactive calculator sizing senior-engineer review hours in dollars / FTE, with adjustable assumptions and an outlier-trim toggle |

### Usage

```bash
# Full run, default 60-day lookback
python3 engineering_audit.py --org acme-corp

# Custom window
python3 engineering_audit.py --org acme-corp --since 2026-03-17
python3 engineering_audit.py --org acme-corp --days 60
python3 engineering_audit.py --org acme-corp --since 2026-03-17 --until 2026-05-21

# Scope to specific repos
python3 engineering_audit.py --org acme-corp --repos frontend-app api

# Write reports into a directory
python3 engineering_audit.py --org acme-corp --output-dir reports/

# Skip GitHub entirely and re-render from a previously saved audit JSON
# (fast path for iterating on the template — scatter chart will be blank)
# Re-rendered HTML defaults into reports/ next to the source JSON; override with --output
python3 engineering_audit.py --from-json reports/acme-corp_audit_2026-03-22_2026-05-21.json

# Build from a main-report CSV instead of fetching (only Qodo-reviewed PRs)
# qodo_metrics.py writes its CSV under reports/, so point --from-csv there
python3 engineering_audit.py --from-csv reports/acme-corp_2026-03-22_2026-05-21.csv
```

Prerequisites are the same as the main report: the [`gh` CLI](https://cli.github.com/) installed and authenticated (`gh auth status`), with `repo` scope if the org has private repos.

### Output files

A full run writes two files (`{org}_audit_{since}_{until}.*`):

- `reports/{org}_audit_{since}_{until}.html` — the self-contained report (default location; override with `--output-dir`)
- `reports/{org}_audit_{since}_{until}.json` — the computed aggregates, so you can re-render later with `--from-json` without re-fetching

> The `*_audit_*.json` data dumps are gitignored — they contain real org PR data and should not be committed. The HTML template (`engineering_audit_template.html`) **is** tracked.

### Options

| Flag | Description |
|---|---|
| `--org` | GitHub org login (required unless using `--from-csv` or `--from-json`) |
| `--since` | Start date in `YYYY-MM-DD` format (defaults to `--days` back from `--until`) |
| `--until` | End date in `YYYY-MM-DD` format (defaults to today) |
| `--days` | Lookback window in days when `--since` is omitted (default: `60`) |
| `--repos` | Space-delimited list of repo names to scope the run; omit to scan the full org |
| `--chunk-days` | Date-window size per GitHub search query (default: `30`). Lower it if a run warns that GitHub's 1000-result search cap was hit |
| `--output-dir` | Directory to write reports into (default: `reports/`) |
| `--template` | Path to the HTML template (default: `./engineering_audit_template.html`) |
| `--from-json` | Skip fetching and re-render the HTML from a previously saved audit JSON file |
| `--output` | Output HTML path when used with `--from-json` (default: `reports/<json-name>.html`) |
| `--from-csv` | Build the audit from a main-report CSV instead of fetching from GitHub (note: a CSV only contains Qodo-reviewed PRs) |
