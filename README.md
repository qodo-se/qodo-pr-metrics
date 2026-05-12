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

### Options

| Flag | Description |
|---|---|
| `--org` | GitHub org login (required, e.g. `acme-corp`) |
| `--since` | Start date in `YYYY-MM-DD` format |
| `--days` | Lookback window in days (default: `365`; mutually exclusive with `--since`) |
| `--inspect` | Print the raw body of the first Qodo comment found and exit |
| `--verbose` | Print per-PR suggestion counts instead of just the final summary |
