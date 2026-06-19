# Reports Directory — Design

**Date:** 2026-06-19
**Status:** Approved (design)

## Goal

Route every report the tool generates into a single `reports/` directory at the
repo root, and gitignore that directory. Today the generated artifacts (CSV,
HTML, user-impact HTML, engineering-audit HTML/JSON, and resume checkpoints)
are written to the current working directory, cluttering the repo root.

## Decisions

- **Directory:** `reports/`, hardcoded (no new CLI flag on `qodo_metrics.py`).
- **Existing loose files:** move the already-generated report artifacts from
  root into `reports/`.
- **Checkpoints:** also go into `reports/`.

## Changes

### 1. Single source of truth — `core.py`

Add a module-level constant:

```python
REPORTS_DIR = Path("reports")
```

All output paths and the checkpoint logic reference this constant, so the
location is defined in exactly one place.

### 2. `qodo_metrics.py`

- Replace `base = Path.cwd()` with `base = REPORTS_DIR`.
- Call `base.mkdir(parents=True, exist_ok=True)` before writing.
- The three outputs (`{stem}.csv`, `{stem}.html`, `{stem}_user.html`) then land
  in `reports/`.
- No messaging change needed: the "Reports written:" block already prints
  `csv_path` / `html_path` / `user_html_path`, which now carry the `reports/`
  prefix.
- `logo.svg` is embedded as inline base64 at generation time and is read
  relative to cwd (not the output dir), so moving the HTML into a subdirectory
  does not break the logo.

### 3. Checkpoints — `core.py`

- `checkpoint_path(org)` returns `REPORTS_DIR / f"{safe_org}-checkpoint.json"`
  (org is still sanitized with the existing regex, preserving the
  "stay inside the working dir" safety property).
- `save_checkpoint(org, state)` calls `REPORTS_DIR.mkdir(parents=True,
  exist_ok=True)` before writing, because a checkpoint may be written before
  any report exists.

### 4. `engineering_audit.py`

- Default `out_dir` changes from `Path(".")` to `REPORTS_DIR` when
  `--output-dir` is not provided. Concretely:
  `out_dir = Path(args.output_dir) if args.output_dir else REPORTS_DIR`.
- The `--output-dir` flag stays for overrides.
- The existing `out_dir.mkdir(parents=True, exist_ok=True)` line is unchanged.
- Import `REPORTS_DIR` from `core`.

### 5. `.gitignore`

- Add a `reports/` line.
- Keep the existing `*.csv`, `*.html`, and `*_audit_*.json` globs as a safety
  net for any stray root artifacts. The `examples/` and
  `engineering_audit_template.html` un-ignore exceptions are unaffected.

### 6. Move existing loose files (one-time)

Move the already-generated, untracked report artifacts from root into
`reports/`:

- `codium-ai_*.csv`, `codium-ai_*.html` (including `_user` and `_audit_`)
- `codium-ai_audit_*.json`
- `qodo-ai_*.csv`, `qodo-ai_*.html` (including `_anon` and `_audit_`)
- `qodo-ai_audit_*.json`

**Leave in place** (not reports / tracked / dev artifacts):

- `engineering_audit_template.html` — the template (git-tracked, un-ignored).
- `logo.svg` — asset read relative to cwd.
- `report.csv`, `report2.csv`, `report3.csv`, `preview.html`,
  `engineering_audit_report.html` — dev/preview fixtures. Confirm each before
  leaving; do not move.

### 7. Docs

- Update `README.md` wherever it documents output location to state that
  reports are written to `reports/` (CLAUDE.md requires README sync with
  documented behavior).

## Testing

- The existing checkpoint test monkeypatches `core.checkpoint_path`, so it is
  unaffected by the default change.
- Add one small assertion that `checkpoint_path(org)` resolves under
  `reports/` by default.
- Run the full suite to confirm no regressions.

## Out of scope

- Adding a `--output-dir` flag to `qodo_metrics.py` (hardcoded by decision).
- Changes to the `examples/` sample-report generation flow.
- Moving or renaming the dev fixtures listed above.
