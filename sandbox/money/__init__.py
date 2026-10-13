"""Критичная зона «деньги»: расчёт суммы заказа и скидки."""

from sandbox.money.total import (
    LOYAL_DISCOUNT_PCT,
    effective_discount_pct,
    format_rub,
    order_total_kop,
)

__all__ = ["LOYAL_DISCOUNT_PCT", "effective_discount_pct", "format_rub", "order_total_kop"]
