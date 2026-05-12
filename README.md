# qodo-pr-metrics

Counts Qodo code-review suggestions and their implementation rate across merged PRs in a GitHub org.

## What it does

For each merged PR in a configurable lookback window, the script:

1. Finds Qodo's review comment (identified by the "Code Review by Qodo" marker)
2. Counts the total number of suggestions Qodo made
3. Counts how many suggestions were implemented (detected via strikethrough formatting on the suggestion title)

It outputs a summary like:

```
Window:                      2025-05-12 → 2026-05-12
Merged PRs in window:        243
PRs with a Qodo review:      187
Total Qodo suggestions:      512
Implemented suggestions:     341
Implementation rate:         66.6%
```

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

## Usage

**Mac/Linux:**

```bash
# Full run, default 365-day lookback
python3 github.py --org acme-corp

# Custom date window
python3 github.py --org acme-corp --since 2025-05-12
python3 github.py --org acme-corp --days 90

# Per-PR detail (prints each PR's suggestion counts)
python3 github.py --org acme-corp --verbose

# Inspect mode — prints the first Qodo comment found (useful for verifying the parser)
python3 github.py --org acme-corp --inspect
```

**Windows:**

```bash
python github.py --org acme-corp
```

### Output files

The script automatically generates CSV and HTML report files (coming soon) in addition to printing the console summary. Files are named using the pattern: `{org}_{since_date}_{until_date}.csv`

For example, running `python3 github.py --org acme-corp` creates a file like `acme-corp_2025-05-12_2026-05-12.csv`.

Each row in the CSV contains 23 columns with per-PR data:

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
| Has Qodo Review | `True` if Qodo left a review comment, `False` otherwise |
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

### Options

| Flag | Description |
|---|---|
| `--org` | GitHub org login (required, e.g. `acme-corp`) |
| `--since` | Start date in `YYYY-MM-DD` format |
| `--days` | Lookback window in days (default: `365`; mutually exclusive with `--since`) |
| `--inspect` | Print the raw body of the first Qodo comment found and exit |
| `--verbose` | Print per-PR suggestion counts instead of just the final summary |
