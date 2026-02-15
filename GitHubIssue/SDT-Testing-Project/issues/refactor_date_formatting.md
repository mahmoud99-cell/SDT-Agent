### Refactor: Date Formatting

**Duplicate Locations:**

- `report_generator.py` (line 45)
- `data_export.py` (line 112)
- `utils.py` (line 203)

**Requested Changes:**

- New shared module `formatters.py`
- Standard function:

```python
def format_date(date: datetime, style: str = "ISO") -> str:
    """Styles: ISO, US, HUMAN"""
```

**Update all call sites**

**Acceptance Criteria:**

- No functionality change
- All imports updated
- Tests pass
