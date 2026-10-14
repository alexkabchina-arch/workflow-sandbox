"""Средняя цена единицы: итог заказа / число единиц, в копейках, округление половина вверх."""


def avg_unit_price_kop(total_kop: int, qty: int) -> int:
    if qty <= 0:
        raise ValueError("число единиц должно быть положительным")
    # Половина вверх в целых: floor(x / q + 1/2) = (2x + q) // 2q, без float
    return (2 * total_kop + qty) // (2 * qty)
