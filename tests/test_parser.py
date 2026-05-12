import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from github import parse_qodo_comment

SAMPLE_WITH_SECTIONS = """
## Code Review by Qodo

🐛 Bugs (2) | 📋 Rule violations (1) | 🔗 Requirement gaps (0)

### Action Required

<details><summary>1. ~~Fix null pointer in handler~~ ☑ 🐛 Bug ≡ Correctness</summary>details</details>
<details><summary>2. Wrong regex pattern 📋 Rule violation ≡ Correctness</summary>details</details>

### Review Recommended

<details><summary>3. Consider extracting helper 🐛 Bug ≡ Correctness</summary>details</details>
<details><summary>4. ~~Rename variable for clarity~~ ☑ 🐛 Bug ≡ Correctness</summary>details</details>
"""

def test_section_totals():
    stats = parse_qodo_comment(SAMPLE_WITH_SECTIONS)
    assert stats.action_required_total == 2
    assert stats.review_recommended_total == 2
    assert stats.total_suggestions == 4

def test_section_implemented():
    stats = parse_qodo_comment(SAMPLE_WITH_SECTIONS)
    assert stats.action_required_implemented == 1
    assert stats.review_recommended_implemented == 1
    assert stats.total_implemented == 2

def test_no_qodo_comment_returns_zeroes():
    stats = parse_qodo_comment("")
    assert stats.total_suggestions == 0
    assert stats.total_implemented == 0


SAMPLE_CATEGORIES = """
## Code Review by Qodo

### Action Required

<details><summary>1. Missing null check 🐛 Bug ≡ Correctness</summary>d</details>
<details><summary>2. ~~Naming convention~~ ☑ 📋 Rule violation ≡ Style</summary>d</details>
<details><summary>3. Spec mismatch 🔗 Requirement gap ≡ Completeness</summary>d</details>

### Review Recommended

<details><summary>4. ~~Performance fix~~ ☑ 🐛 Bug ≡ Performance</summary>d</details>
"""

def test_category_counts():
    stats = parse_qodo_comment(SAMPLE_CATEGORIES)
    assert stats.bugs_suggested == 2          # items 1 and 4
    assert stats.rule_violations_suggested == 1
    assert stats.requirement_gaps_suggested == 1

def test_category_implemented():
    stats = parse_qodo_comment(SAMPLE_CATEGORIES)
    assert stats.bugs_implemented == 1        # only item 4
    assert stats.rule_violations_implemented == 1
    assert stats.requirement_gaps_implemented == 0

def test_no_sections_still_counts_totals():
    """Items before any section header still count toward global totals."""
    body = """
## Code Review by Qodo

<details><summary>1. ~~Some issue~~ ☑ 🐛 Bug ≡ Correctness</summary>d</details>
<details><summary>2. Other issue 🐛 Bug ≡ Correctness</summary>d</details>
"""
    stats = parse_qodo_comment(body)
    # Category counts always fire
    assert stats.bugs_suggested == 2
    assert stats.bugs_implemented == 1
    # Section buckets are empty — no section headings present
    assert stats.action_required_total == 0
    assert stats.review_recommended_total == 0
    # Global totals count ALL items regardless of section
    assert stats.total_suggestions == 2
    assert stats.total_implemented == 1
