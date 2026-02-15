### Bug Fix: Discount Calculation

**File:** `price_utils.py`

**Error Conditions:**

1. Crashes when price is 0 (`DivisionByZeroError`)
2. Incorrect results with negative discounts

**Expected Behavior:**

- Return 0 if price is 0 or negative
- Treat negative discounts as 0%
- Round final price to 2 decimal places

**Current Code:**

```python
def apply_discount(price: float, discount_percent: float) -> float:
    return price * (1 - discount_percent / 100)
```

**Acceptance Criteria:**

- Handles zero/negative prices
- Validates discount range (0-100)
- Includes unit tests
