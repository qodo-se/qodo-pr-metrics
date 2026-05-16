# qodo-pr-metrics

Generates an HTML report measuring Qodo code-review impact across merged PRs in a GitHub org — covering suggestion volume, implementation rates, reviewer velocity, and developer adoption.

[View sample report](https://qodo-se.github.io/qodo-pr-metrics/examples/sample_report.html)

## What it does

For each merged PR in a configurable lookback window, the script:

1. Finds Qodo's review comment (identified by the "Code Review by Qodo" marker)
2. Counts suggestions made and how many were implemented (detected via strikethrough formatting)
3. Records timing, spotlight issues (Security/Correctness), and per-developer activity

It produces an HTML report and a raw CSV — see [Output files](#output-files) below.

## How it works

- Authenticates through the `gh` CLI — no token management required
- Searches merged PRs in date-chunked windows (30-day chunks) to stay under GitHub's 1000-result search cap
- Identifies Qodo comments by the stable "Code Review by Qodo" string, which is bot-account-name-independent
- Detects implemented suggestions by looking for strikethrough markers (`~~text~~`, `<s>`, `<del>`, `<strike>`, or the ☑ emoji) on suggestion titles

## Prerequisites

- Python 3.7+
- [`gh` CLI](https://cli.github.com/) installed and authenticated with access to the target org

```bash
gh auth status   # should show you logged in
```

### GitHub token permissions

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

## Usage

**Mac/Linux:**

```bash
# Full run, default 365-day lookback
python3 github.py --org acme-corp

# Custom date window
python3 github.py --org acme-corp --since 2025-05-12
python3 github.py --org acme-corp --days 90

# Scope to specific repos
python3 github.py --org acme-corp --repos frontend-app backend-api
```

**Windows:**

```bash
python github.py --org acme-corp
```

### Output files

The script generates two output files:

- `{org}_{since_date}_{until_date}.csv` — raw per-PR data
- `{org}_{since_date}_{until_date}.html` — visual summary report

For example, running `python3 github.py --org acme-corp` creates `acme-corp_2025-05-12_2026-05-12.csv` and `acme-corp_2025-05-12_2026-05-12.html`.

Each row in the CSV contains 35 columns with per-PR data:

| Column | Description |
|---|---|
| Repo Name | Repository name within the org |
| PR # | Pull request number |
| PR URL | Link to the PR on GitHub |
| PR Creation Date | ISO-8601 timestamp when the PR was opened |
| PR Merge Date | ISO-8601 timestamp when the PR was merged |
| Hours to Merge | Whole hours from creation to merge |
| PR Creator | GitHub login of the PR author |
| Lines Changed | Total lines added + deleted |
| Has Qodo Review | `True` if Qodo posted a review on this PR |
| Action Required Suggestions | Count of "Action Required" suggestions |
| Action Required Implemented | Count of implemented "Action Required" suggestions |
| Review Recommended Suggestions | Count of "Review Recommended" suggestions |
| Review Recommended Implemented | Count of implemented "Review Recommended" suggestions |
| Bugs Suggested | Count of bug suggestions |
| Bugs Implemented | Count of implemented bug suggestions |
| Rule Violations Suggested | Count of rule-violation suggestions |
| Rule Violations Implemented | Count of implemented rule-violation suggestions |
| Requirement Gaps Suggested | Count of requirement-gap suggestions |
| Requirement Gaps Implemented | Count of implemented requirement-gap suggestions |
| Total Suggestions | Sum of all suggestion categories |
| Total Implemented | Sum of all implemented categories |
| Implementation Rate (%) | `Total Implemented / Total Suggestions × 100`, blank when 0 suggestions |
| Suggestions per 100 Lines | `Total Suggestions / Lines Changed × 100`, blank when lines = 0 or suggestions = 0 |
| Time to First Qodo Comment (min) | Minutes from PR creation to Qodo's first comment; blank if no Qodo comment |
| Time to First Human Comment (min) | Minutes from PR creation to the first non-Qodo comment; blank if none |
| Has Human Comment | `True` if any non-Qodo comment exists on the PR |
| Spotlight Issues | JSON array of high-impact Action Required issues (Security or Correctness sub-label) that were implemented |
| Is AI Authored | `True` if the PR was detected as AI-assisted (by body patterns or labels) |
| AI Author Type | Which AI tool: `copilot`, `cursor`, `claude`, `ai`, or blank if not detected |
| Reviewer Count | Number of distinct reviewers who submitted a review |
| Had Request Changes | `True` if any reviewer submitted a "Request Changes" review |
| Final Approver | GitHub login of the last reviewer to approve; blank if none |
| CI Status | Status check rollup state at the last commit: `SUCCESS`, `FAILURE`, `PENDING`, or blank if unavailable |
| Commits After Qodo | Number of commits pushed after Qodo posted its first review |
| Speed to First Fix (min) | Minutes between Qodo's first review and the first commit that followed it; blank if no post-review commit |

### HTML report sections

The HTML report is organized into the following sections:

| Section | What it shows |
|---|---|
| Executive Summary | At-a-glance stat cards: PRs reviewed by Qodo, total issues caught, issues resolved, overall implementation rate |
| Velocity — Time to First Feedback | Median time for Qodo vs the first human reviewer to leave a comment; speed multiplier; % of PRs where Qodo was the sole reviewer before merge |
| High-Impact Issues Caught & Resolved | Cards for every Action Required issue flagged as Security or Correctness that was implemented before merge |
| Adoption | Developer breadth stats (how many developers had PRs reviewed, how many implemented suggestions); per-repository and per-developer (top 10) breakdown tables |
| Impact by Severity | Action Required vs Review Recommended suggestion counts and implementation rates |
| Impact by Category | Bugs, Rule Violations, and Requirement Gaps counts and implementation rates |
| Speed to First Fix | Median minutes from Qodo's first review to the developer's first follow-up commit; shown when data is available |
| AI-Authored PRs | Count and implementation rate for PRs detected as AI-assisted |
| Quality Signals | Revert PR count and hotfix PR count for the period; hotfixes are detected by PR title containing "hotfix", PR label "hotfix", or branch name starting with "hotfix"; shown when data is available |
| Top 5 PRs by Issues Found | The PRs with the most Qodo suggestions |
| Top 5 PRs by Implemented Suggestions | The PRs with the most implemented suggestions |

The Velocity, High-Impact, Speed to First Fix, and Quality Signals sections are omitted from the report if no relevant data is present.

### Options

| Flag | Description |
|---|---|
| `--org` | GitHub org login (required, e.g. `acme-corp`) |
| `--since` | Start date in `YYYY-MM-DD` format |
| `--days` | Lookback window in days (default: `365`; mutually exclusive with `--since`) |
| `--inspect` | Print the raw body of the first Qodo comment found and exit |
| `--verbose` | Print per-PR suggestion counts instead of just the final summary |
| `--resume` | Resume from a previous checkpoint (`ORG-checkpoint.json`) |
| `--repos` | Space-delimited list of repo names to scope the run (e.g. `--repos frontend-app backend-api`); omit to scan the full org |
| `--test-hotfix-signals` | Smoke-test hotfix detection signals against the org and exit. Prints counts for each signal (branch, label, title) and the combined OR query, and confirms that OR deduplication is working correctly. |
