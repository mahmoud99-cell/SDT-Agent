# test_price_utils.py
from price_utils import apply_discount

def test_apply_discount():
    price = 100
    discount = 20  # Now it's in percentage
    expected = 80  # 100 - 20% of 100
    result = apply_discount(price, discount)
    assert result == expected

