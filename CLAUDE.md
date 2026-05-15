# qodo-pr-metrics

## Rules

### Sample report must stay in sync
Any change to HTML report code (`report.py`) must also update `examples/sample_report.html` by running:
```
python3 scripts/generate_sample.py
```
Commit the updated sample in the same commit as the code change.

### README must stay in sync

Any code change that affects documented behavior, usage, features, or configuration must also update `README.md` to match. Commit the updated README in the same commit as the code change.
