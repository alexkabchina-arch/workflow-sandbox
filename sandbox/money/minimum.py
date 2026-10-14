"""Минимальная сумма заказа: итог после скидки меньше минимума — отказ; ровно минимум проходит."""

from sandbox.money.total import format_rub

# Решение владельца в #113: 300 ₽, сравнение с итогом после скидки
MIN_ORDER_KOP = 30000


def check_min_order(total_kop: int) -> None:
    if total_kop < MIN_ORDER_KOP:
        raise ValueError(f"минимальная сумма заказа — {format_rub(MIN_ORDER_KOP)}")
