"""Средняя цена единицы: итог заказа / число единиц, в копейках, округление половина вверх."""


def avg_unit_price_kop(total_kop: int, qty: int) -> int:
    if qty <= 0:
        raise ValueError("число единиц должно быть положительным")
    # Знак отдельно, как в vat_included_kop: ничья у отрицательного итога уходит от нуля
    sign = -1 if total_kop < 0 else 1
    # Половина вверх в целых: floor(x / q + 1/2) = (2x + q) // 2q, без float
    return sign * ((2 * abs(total_kop) + qty) // (2 * qty))
