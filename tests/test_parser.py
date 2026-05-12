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
