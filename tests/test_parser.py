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


def test_two_suggestions_on_one_line():
    """Each match on a single line must be counted independently."""
    body = "<summary>1. Fix null check 🐛 Bug ≡ Correctness</summary> <summary>2. ~~Rename var~~ ☑ 📋 Rule violation ≡ Style</summary>"
    stats = parse_qodo_comment(body)
    assert stats.total_suggestions == 2
    assert stats.total_implemented == 1
    assert stats.bugs_suggested == 1
    assert stats.rule_violations_suggested == 1
    assert stats.rule_violations_implemented == 1


# Uses real-world Qodo HTML format (code tags with emoji)
_SECURITY_BODY = """
<h3>Code Review by Qodo</h3>
<img src="https://www.qodo.ai/wp-content/uploads/2026/01/action-required.png" height="20" alt="Action required">
<details>
<summary>  1.  Hardcoded OpenAI API key <code>🐞 Bug</code> <code>🛡 Security</code></summary>
</details>
<details>
<summary>  2.  ~~Logs API key to console~~ ☑ <code>🐞 Bug</code> <code>🛡 Security</code></summary>
</details>
"""

_CORRECTNESS_BODY = """
<h3>Code Review by Qodo</h3>
<img src="https://www.qodo.ai/wp-content/uploads/2026/01/action-required.png" height="20" alt="Action required">
<details>
<summary>  1.  ~~Wrong variant appended in stream~~ ☑ <code>📎 Requirement gap</code> <code>≡ Correctness</code></summary>
</details>
<details>
<summary>  2.  Old prompt name remains <code>📎 Requirement gap</code> <code>≡ Correctness</code></summary>
</details>
"""

def test_security_label_detected():
    stats = parse_qodo_comment(_SECURITY_BODY)
    assert stats.security_suggested == 2

def test_security_implemented_counted():
    stats = parse_qodo_comment(_SECURITY_BODY)
    assert stats.security_implemented == 1

def test_correctness_label_detected():
    stats = parse_qodo_comment(_CORRECTNESS_BODY)
    assert stats.correctness_suggested == 2

def test_correctness_implemented_counted():
    stats = parse_qodo_comment(_CORRECTNESS_BODY)
    assert stats.correctness_implemented == 1


_SPOTLIGHT_BODY = """
<h3>Code Review by Qodo</h3>
<img src="https://www.qodo.ai/wp-content/uploads/2026/01/action-required.png" height="20" alt="Action required">
<details>
<summary>  1.  ~~Hardcoded API key~~ ☑ <code>🐞 Bug</code> <code>🛡 Security</code></summary>
</details>
<details>
<summary>  2.  Missing null check <code>🐞 Bug</code> <code>🛡 Security</code></summary>
</details>
<img src="https://www.qodo.ai/wp-content/uploads/2026/01/review-recommended.png" height="20" alt="Remediation recommended">
<details>
<summary>  3.  ~~Style cleanup~~ ☑ <code>🐞 Bug</code> <code>🛡 Security</code></summary>
</details>
"""

def test_spotlight_only_action_required_implemented():
    # item 1: action required + security + implemented → spotlight
    # item 2: action required + security + NOT implemented → not spotlight
    # item 3: review recommended + security + implemented → not spotlight (wrong section)
    stats = parse_qodo_comment(_SPOTLIGHT_BODY)
    assert len(stats.spotlight_issues) == 1

def test_spotlight_entry_shape():
    stats = parse_qodo_comment(_SPOTLIGHT_BODY)
    issue = stats.spotlight_issues[0]
    assert issue["title"] == "Hardcoded API key"   # HTML tags stripped
    assert issue["category"] == "bug"
    assert issue["sub_label"] == "Security"

def test_spotlight_empty_when_no_match():
    stats = parse_qodo_comment(_CORRECTNESS_BODY)
    # Correctness items present but none implemented → no spotlight
    assert stats.spotlight_issues == []
