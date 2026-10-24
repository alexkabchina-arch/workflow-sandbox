"""Минимум в копейках.

Минимальная сумма заказа: итог после скидки меньше минимума — отказ; ровно минимум проходит.
Наименьшая сумма: минимум из сумм в копейках; без сумм — ошибка.
"""

from sandbox.money.total import format_rub

# Решение владельца в #113: 300 ₽, сравнение с итогом после скидки
MIN_ORDER_KOP = 30000


def check_min_order(total_kop: int) -> None:
    if total_kop < MIN_ORDER_KOP:
        raise ValueError(f"минимальная сумма заказа — {format_rub(MIN_ORDER_KOP)}")


def min_kop(*amounts_kop: int) -> int:
    # Условие #152: без сумм — ValueError; своё сообщение вместо английского из min()
    if not amounts_kop:
        raise ValueError("нужна хотя бы одна сумма")
    return min(amounts_kop)
