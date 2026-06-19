# Reports Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every generated report (CSV, HTML, user-impact HTML, engineering-audit HTML/JSON) and resume checkpoints into a single gitignored `reports/` directory.

**Architecture:** Define one constant `REPORTS_DIR = Path("reports")` in `core.py`. Both entry points (`qodo_metrics.py`, `engineering_audit.py`) and the checkpoint helpers reference it. Directories are created on demand with `mkdir(parents=True, exist_ok=True)`. Existing loose report artifacts are moved into `reports/` one time.

**Tech Stack:** Python 3, `pathlib`, `pytest`.

## Global Constraints

- `REPORTS_DIR = Path("reports")` — single source of truth, defined in `core.py`, imported elsewhere. No other module hardcodes the string `"reports"`.
- `qodo_metrics.py` gets **no** new CLI flag — the directory is hardcoded.
- `engineering_audit.py` keeps its existing `--output-dir` flag; only its default changes.
- `logo.svg` stays at repo root and is read relative to cwd; do not move it.
- Per CLAUDE.md: any change to documented behavior must update `README.md` in the same change.
- `engineering_audit_template.html` is git-tracked and un-ignored — never move it.

---

### Task 1: Add `REPORTS_DIR` and route checkpoints (core.py)

**Files:**
- Modify: `core.py` (top-level constants region near line 13; `checkpoint_path` / `save_checkpoint` near lines 584-606)
- Test: `tests/test_github_repo_filter.py` (append a new test after the existing checkpoint tests, ~line 220)

**Interfaces:**
- Produces: `core.REPORTS_DIR` (a `Path`, value `Path("reports")`); `checkpoint_path(org)` now returns `REPORTS_DIR / f"{safe_org}-checkpoint.json"`; `save_checkpoint(org, state)` creates `REPORTS_DIR` before writing.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_github_repo_filter.py`:

```python
def test_checkpoint_path_defaults_under_reports_dir():
    from core import checkpoint_path, REPORTS_DIR
    p = checkpoint_path("acme")
    assert p == REPORTS_DIR / "acme-checkpoint.json"
    assert p.parent.name == "reports"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_github_repo_filter.py::test_checkpoint_path_defaults_under_reports_dir -v`
Expected: FAIL with `ImportError: cannot import name 'REPORTS_DIR'` (or `AssertionError`).

- [ ] **Step 3: Add the constant**

In `core.py`, after the imports and before `QODO_MARKER` (around line 12), add:

```python
# All generated reports and resume checkpoints are written here (gitignored).
REPORTS_DIR = Path("reports")
```

- [ ] **Step 4: Route checkpoints through `REPORTS_DIR`**

In `core.py`, change `checkpoint_path` to return inside `REPORTS_DIR`:

```python
def checkpoint_path(org):
    # Sanitize org the same way _output_stem() does: org is part of the
    # provider-agnostic surface and future providers may use identifiers
    # containing path separators or '..', which would otherwise let the
    # checkpoint read/write outside the working directory.
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", org)
    return REPORTS_DIR / f"{safe_org}-checkpoint.json"
```

And make `save_checkpoint` create the directory first:

```python
def save_checkpoint(org, state):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_path(org).write_text(json.dumps(state, indent=2))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_github_repo_filter.py -v`
Expected: PASS (new test plus the two existing checkpoint tests, which monkeypatch `core.checkpoint_path` and are unaffected).

- [ ] **Step 6: Commit**

```bash
git add core.py tests/test_github_repo_filter.py
git commit -m "feat: add REPORTS_DIR constant and route checkpoints into reports/"
```

---

### Task 2: Write qodo_metrics.py reports into `reports/`

**Files:**
- Modify: `qodo_metrics.py` (import block lines 40-50; output block lines 311-313)

**Interfaces:**
- Consumes: `core.REPORTS_DIR` from Task 1.
- Produces: `{stem}.csv`, `{stem}.html`, `{stem}_user.html` written under `reports/`.

- [ ] **Step 1: Import the constant**

In `qodo_metrics.py`, add `REPORTS_DIR` to the existing `from core import (...)` block (lines 40-50). Insert it on the line with `checkpoint_path, load_checkpoint, save_checkpoint,`:

```python
    checkpoint_path, load_checkpoint, save_checkpoint,
    REPORTS_DIR,
```

- [ ] **Step 2: Point `base` at `REPORTS_DIR` and create it**

In `qodo_metrics.py`, replace the line at 313:

```python
    base = Path.cwd()
```

with:

```python
    base = REPORTS_DIR
    base.mkdir(parents=True, exist_ok=True)
```

(The `Path` import at line 35 stays — it is still used elsewhere.)

- [ ] **Step 3: Verify the import resolves and the module loads**

Run: `python3 -c "import qodo_metrics; from core import REPORTS_DIR; print(REPORTS_DIR)"`
Expected: prints `reports` with no ImportError.

- [ ] **Step 4: Smoke-test that outputs land in reports/**

Run:
```bash
python3 - <<'PY'
import datetime, qodo_metrics, core
from pathlib import Path
# Confirm the entry module references the shared constant, not cwd.
import inspect, re
src = inspect.getsource(qodo_metrics)
assert "base = REPORTS_DIR" in src, "base must use REPORTS_DIR"
assert "Path.cwd()" not in src, "cwd output path must be gone"
print("OK")
PY
```
Expected: prints `OK`.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add qodo_metrics.py
git commit -m "feat: write qodo_metrics reports into reports/ directory"
```

---

### Task 3: Default engineering_audit.py output to `reports/`

**Files:**
- Modify: `engineering_audit.py` (output-dir resolution near line 582)

**Interfaces:**
- Consumes: `core.REPORTS_DIR` from Task 1 (module already does `import core`).
- Produces: audit `{stem}.html` / `{stem}.json` default into `reports/`; `--output-dir` override still honored.

- [ ] **Step 1: Change the default**

In `engineering_audit.py`, replace:

```python
    out_dir = Path(args.output_dir or ".")
```

with:

```python
    out_dir = Path(args.output_dir) if args.output_dir else core.REPORTS_DIR
```

(The existing `out_dir.mkdir(parents=True, exist_ok=True)` on the next line is unchanged.)

- [ ] **Step 2: Verify default and override both resolve**

Run:
```bash
python3 - <<'PY'
import inspect, engineering_audit, core
src = inspect.getsource(engineering_audit)
assert 'core.REPORTS_DIR' in src, "default must use core.REPORTS_DIR"
assert 'Path(args.output_dir or ".")' not in src, "old cwd default must be gone"
print("OK", core.REPORTS_DIR)
PY
```
Expected: prints `OK reports`.

- [ ] **Step 3: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add engineering_audit.py
git commit -m "feat: default engineering_audit output to reports/ directory"
```

---

### Task 4: Gitignore the reports directory

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the `reports/` line**

In `.gitignore`, add the following line directly under the `.DS_Store` line at the top (keep all existing globs and exceptions as-is):

```
reports/
```

- [ ] **Step 2: Verify reports/ is ignored**

Run:
```bash
mkdir -p reports && touch reports/_probe.csv reports/_probe.json
git check-ignore reports/_probe.csv reports/_probe.json
rm reports/_probe.csv reports/_probe.json
```
Expected: both probe paths are printed (meaning ignored).

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore reports/ directory"
```

---

### Task 5: Move existing loose report artifacts into reports/

**Files:**
- No source changes. One-time relocation of untracked artifacts at repo root.

**Interfaces:**
- Consumes: nothing. Pure file move.

- [ ] **Step 1: Confirm what will move (dry run)**

Run:
```bash
ls -1 codium-ai_*.csv codium-ai_*.html codium-ai_audit_*.json \
      qodo-ai_*.csv qodo-ai_*.html qodo-ai_audit_*.json 2>/dev/null
```
Expected: lists only the dated `codium-ai_*` / `qodo-ai_*` report outputs. It must NOT list `engineering_audit_template.html`, `logo.svg`, `report.csv`, `report2.csv`, `report3.csv`, `preview.html`, or `engineering_audit_report.html`.

- [ ] **Step 2: Verify none of those are git-tracked**

Run: `git ls-files -- codium-ai_* qodo-ai_*`
Expected: no output (all untracked / gitignored).

- [ ] **Step 3: Move them**

Run:
```bash
mkdir -p reports
mv codium-ai_*.csv codium-ai_*.html codium-ai_audit_*.json \
   qodo-ai_*.csv qodo-ai_*.html qodo-ai_audit_*.json reports/ 2>/dev/null
ls -1 reports/ | head
```
Expected: the moved files now appear under `reports/`; repo root no longer holds them.

- [ ] **Step 4: Confirm git status is clean of the move**

Run: `git status --short`
Expected: no new tracked/untracked report files at root (the moved files are inside the now-ignored `reports/`). The only changes pending are from earlier tasks if not yet committed.

(No commit — these are gitignored artifacts; nothing to stage.)

---

### Task 6: Update README to document the reports/ location

**Files:**
- Modify: `README.md` (Output files section ~lines 92-100; engineering audit Output files ~line 228; options table ~line 243)

**Interfaces:**
- Consumes: behavior from Tasks 2-3.

- [ ] **Step 1: Update the qodo_metrics Output files section**

In `README.md`, in the "Output files" section (around lines 94-100), update the file list and example to show the `reports/` prefix. Replace:

```
The script generates three output files:

- `{org}_{since_date}_{until_date}.csv` — raw per-PR data
- `{org}_{since_date}_{until_date}.html` — org-wide visual summary report
- `{org}_{since_date}_{until_date}_user.html` — per-developer impact report with an interactive date-range slider that recomputes the headline, at-a-glance panel, and per-developer table client-side

For example, running `python3 qodo_metrics.py --org acme-corp` creates `acme-corp_2025-05-12_2026-05-12.csv`, `acme-corp_2025-05-12_2026-05-12.html`, and `acme-corp_2025-05-12_2026-05-12_user.html`.
```

with:

```
The script generates three output files, all written into the `reports/` directory (created automatically and gitignored):

- `reports/{org}_{since_date}_{until_date}.csv` — raw per-PR data
- `reports/{org}_{since_date}_{until_date}.html` — org-wide visual summary report
- `reports/{org}_{since_date}_{until_date}_user.html` — per-developer impact report with an interactive date-range slider that recomputes the headline, at-a-glance panel, and per-developer table client-side

For example, running `python3 qodo_metrics.py --org acme-corp` creates `reports/acme-corp_2025-05-12_2026-05-12.csv`, `reports/acme-corp_2025-05-12_2026-05-12.html`, and `reports/acme-corp_2025-05-12_2026-05-12_user.html`.
```

- [ ] **Step 2: Update the engineering audit Output files section**

In `README.md`, in the engineering audit "Output files" section (around line 228), update the paths to show the `reports/` default. Replace:

```
- `{org}_audit_{since}_{until}.html` — the self-contained report
```

with:

```
- `reports/{org}_audit_{since}_{until}.html` — the self-contained report (default location; override with `--output-dir`)
```

If the adjacent `.json` bullet also names a bare `{org}_audit_*.json` path, give it the same `reports/` prefix for consistency.

- [ ] **Step 3: Update the `--output-dir` option description**

In `README.md`, in the engineering audit options table (around line 243), replace:

```
| `--output-dir` | Directory to write reports into (default: current directory) |
```

with:

```
| `--output-dir` | Directory to write reports into (default: `reports/`) |
```

- [ ] **Step 4: Verify no stale "current directory" output wording remains**

Run: `grep -n "current directory" README.md`
Expected: no line referring to report output going to the current directory (the `--output-dir` default line is updated).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document reports/ output directory in README"
```

---

## Self-Review

**Spec coverage:**
- Single source of truth `REPORTS_DIR` → Task 1. ✓
- qodo_metrics into `reports/` → Task 2. ✓
- Checkpoints into `reports/` → Task 1. ✓
- engineering_audit default → Task 3. ✓
- `.gitignore` add `reports/` → Task 4. ✓
- Move existing loose files (leave template/logo/fixtures) → Task 5. ✓
- README sync → Task 6. ✓
- Testing note (checkpoint default assertion) → Task 1 Step 1. ✓

**Placeholder scan:** No TBD/TODO; every code step shows exact code and commands.

**Type consistency:** `REPORTS_DIR` is a `Path` defined once in `core.py`; `qodo_metrics.py` imports it via `from core import`, `engineering_audit.py` uses `core.REPORTS_DIR` (it does `import core`). `checkpoint_path` return type unchanged (`Path`). Consistent across tasks.
