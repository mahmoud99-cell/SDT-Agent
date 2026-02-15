### Tests: Email Validation

**Function to Test:** `validate_email()` in `validation.py`

**Test Cases Needed:**

**Valid emails:**

- `"user@example.com"`
- `"first.last+alias@domain.co"`

**Invalid formats:**

- `"user@"`
- `"@example.com"`
- `"no_at_symbol.com"`

**Edge cases:**

- Empty string (`""`)
- None input
- Overlong addresses (>320 chars)

**Acceptance Criteria:**

- 100% function coverage
- Descriptive test names
- Fixtures for common patterns
