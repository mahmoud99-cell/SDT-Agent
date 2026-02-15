def apply_discount(price: float, discount_percent: float) -> float:
    if price <= 0:
        return 0.0

    discount = max(0, discount_percent)
    final_price = price * (1 - discount / 100)
    return round(final_price, 2)
