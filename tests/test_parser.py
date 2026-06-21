import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core import parse_qodo_comment

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
<summary>  1.  Wrong variant appended in stream <code>📎 Requirement gap</code> <code>≡ Correctness</code></summary>
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
    assert stats.correctness_implemented == 0


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


# ---------------------------------------------------------------------------
# Dismissed detection
# ---------------------------------------------------------------------------

_DISMISSED_BODY = """
<h3>Code Review by Qodo</h3>
<img src="https://www.qodo.ai/wp-content/uploads/2026/01/action-required.png" height="20" alt="Action required">
<details>
<summary>  1.  Fix null pointer <code>🐞 Bug</code> <code>≡ Correctness</code></summary>
</details>
<details>
<summary>  2.  <s>AR-only CSV None crash</s> <code>✗ Dismissed</code> <code>🐞 Bug</code> <code>☼ Reliability</code></summary>
</details>
<details>
<summary>  3.  <s>AR-only tooltip mismatch</s> <code>✓ Resolved</code> <code>🐞 Bug</code> <code>≡ Correctness</code></summary>
</details>
<img src="https://www.qodo.ai/wp-content/uploads/2026/01/review-recommended.png" height="20" alt="Remediation recommended">
<details>
<summary>  4.  <s>Anonymize scope unvalidated</s> <code>✗ Dismissed</code> <code>🐞 Bug</code> <code>☼ Reliability</code></summary>
</details>
"""

def test_dismissed_not_counted_as_implemented():
    stats = parse_qodo_comment(_DISMISSED_BODY)
    # items 2 and 4 are dismissed — must NOT count as implemented
    assert stats.total_implemented == 1   # only item 3
    assert stats.total_dismissed == 2     # items 2 and 4

def test_dismissed_total_suggestions_unchanged():
    stats = parse_qodo_comment(_DISMISSED_BODY)
    assert stats.total_suggestions == 4   # all 4 items counted

def test_dismissed_section_buckets():
    stats = parse_qodo_comment(_DISMISSED_BODY)
    assert stats.action_required_dismissed == 1    # item 2
    assert stats.review_recommended_dismissed == 1 # item 4

def test_dismissed_does_not_appear_in_implemented_buckets():
    stats = parse_qodo_comment(_DISMISSED_BODY)
    assert stats.action_required_implemented == 1  # only item 3
    assert stats.review_recommended_implemented == 0


# When Qodo re-reviews a PR after new commits, it keeps a single comment but
# appends the prior review under a folded "Previous review results" block,
# marked by <!-- FOLDED_SECTION_START -->. Those historical items overlap with
# the current review and must NOT be counted (mirrors real PRs #3100 / #3156).
_FOLDED_PREVIOUS_BODY = """
## Code Review by Qodo

<code>🐞 Bugs (1)</code> <code>📘 Rule violations (1)</code>

### Action Required

<details><summary>  1.  Headless shell selected first <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>
<details><summary>  2.  docs not updated <code>📘 Rule violation</code> <code>✧ Quality</code></summary>d</details>

<!-- FOLDED_SECTION_START -->
<img src="https://www.qodo.ai/wp-content/uploads/2025/11/light-grey-line.svg" alt="Grey Divider">

### Previous review results

<details><summary>Results up to commit af92cdb</summary>

<br><code>🐞 Bugs (1)</code> <code>📘 Rule violations (1)</code>

<img src="https://www.qodo.ai/wp-content/uploads/2026/01/action-required.png" alt="Action required">

<details><summary>  1.  Headless shell selected first <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>
<details><summary>  2.  docs not updated <code>📘 Rule violation</code> <code>✧ Quality</code></summary>d</details>
</details>
"""

def test_folded_previous_review_not_double_counted():
    """Suggestions repeated in the folded block are counted once, not twice."""
    stats = parse_qodo_comment(_FOLDED_PREVIOUS_BODY)
    assert stats.total_suggestions == 2          # deduped union, not 4
    assert stats.bugs_suggested == 1             # not 2
    assert stats.rule_violations_suggested == 1  # not 2
    assert stats.action_required_total == 2


# Edge case: a suggestion implemented in an EARLIER review cycle drops out of the
# current review (it's been fixed) but survives in the folded history with the ☑
# marker. The deduplicated union must still count it — and credit it as
# implemented — so implementation rate isn't undercounted (mirrors real PR #1791).
_FOLDED_IMPLEMENTED_BODY = """
## Code Review by Qodo

<code>🐞 Bugs (1)</code>

### Action Required

<details><summary>  1.  SP token error uses None <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>

<!-- FOLDED_SECTION_START -->
### Previous review results

<details><summary>Results up to commit a349002</summary>

<img src="https://www.qodo.ai/wp-content/uploads/2026/01/action-required.png" alt="Action required">

<details><summary>  1.  <s>Wrong credential picked</s> ☑ <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>
<details><summary>  2.  SP token error uses None <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>
</details>
"""

def test_folded_implemented_only_in_history_is_counted():
    stats = parse_qodo_comment(_FOLDED_IMPLEMENTED_BODY)
    # Two distinct suggestions total: the still-open one (current) and the
    # fixed one that survives only in the folded history.
    assert stats.total_suggestions == 2
    assert stats.total_implemented == 1          # "Wrong credential picked" ☑
    assert stats.bugs_suggested == 2
    assert stats.bugs_implemented == 1


def test_folded_implemented_in_history_not_lost_when_open_in_current():
    """If a suggestion is open in the current review but ☑ in history, count it implemented."""
    body = """
## Code Review by Qodo

### Action Required

<details><summary>  1.  Token cache missing <code>🐞 Bug</code> <code>☼ Reliability</code></summary>d</details>

<!-- FOLDED_SECTION_START -->
### Previous review results

<details><summary>Results up to commit abc1234</summary>
<details><summary>  1.  <s>Token cache missing</s> ☑ <code>🐞 Bug</code> <code>☼ Reliability</code></summary>d</details>
</details>
"""
    stats = parse_qodo_comment(body)
    assert stats.total_suggestions == 1          # same suggestion, counted once
    assert stats.total_implemented == 1          # implemented status OR-ed from history


# Regression: dismissal state must reflect the *current* snapshot. A suggestion
# that is open in the current review but marked ✗ Dismissed only in folded
# history must NOT be counted as dismissed — live state wins, matching how
# section/category use first-occurrence precedence.
def test_folded_dismissed_in_history_does_not_override_current():
    body = """
## Code Review by Qodo

### Action Required

<details><summary>  1.  Risky cast <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>

<!-- FOLDED_SECTION_START -->
### Previous review results

<details><summary>Results up to commit abc1234</summary>
<details><summary>  1.  <s>Risky cast</s> ✗ Dismissed <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>
</details>
"""
    stats = parse_qodo_comment(body)
    assert stats.total_suggestions == 1          # same suggestion, counted once
    assert stats.total_dismissed == 0            # current snapshot is open, not dismissed
    assert stats.total_implemented == 0          # open now, never implemented


# Regression: the dedupe key must not truncate non-ASCII content in the core
# title. Two distinct titles that both begin with a non-ASCII character used to
# collapse to the same (empty) key under the old _clean_title-based key.
def test_unicode_titles_do_not_collapse():
    body = """
## Code Review by Qodo

### Action Required

<details><summary>  1.  Δ threshold too low <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>
<details><summary>  2.  λ handler leaks <code>🐞 Bug</code> <code>≡ Correctness</code></summary>d</details>
"""
    stats = parse_qodo_comment(body)
    assert stats.total_suggestions == 2          # two distinct titles, not collapsed to 1
    assert stats.bugs_suggested == 2


SAMPLE_BITBUCKET = """\
### Code Review by Qodo

`🐞 Bugs (2)`  `📘 Rule violations (0)`  `📎 Requirement gaps (0)`

---

### Action Required

#### 1. Wrong word counting `🐞 Bug` `✓ Correctness`

### Review Recommended

#### 2. pad_left can crash `🐞 Bug` `⛯ Reliability`

### Resolved

#### ~~3. Misleading truncate comment~~ `🐞 Bug` `✧ Quality`

*Reviewed by* **[Qodo](https://www.qodo.ai)**
"""

def test_bitbucket_summary_totals():
    stats = parse_qodo_comment(SAMPLE_BITBUCKET)
    assert stats.total_suggestions == 3
    assert stats.total_implemented == 1   # the one under ### Resolved (~~struck~~)

def test_bitbucket_summary_sections():
    stats = parse_qodo_comment(SAMPLE_BITBUCKET)
    assert stats.action_required_total == 1
    assert stats.review_recommended_total == 1

def test_bitbucket_summary_categories():
    stats = parse_qodo_comment(SAMPLE_BITBUCKET)
    assert stats.bugs_suggested == 3
    assert stats.bugs_implemented == 1
