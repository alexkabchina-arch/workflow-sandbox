"""Разбор позиций корзины из строки вида «3x100»."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Item:
    qty: int
    price_kop: int


def parse_item(spec: str) -> Item:
    """«3x100» или «3X100» → 3 штуки по 100 ₽ (цена хранится в копейках)."""
    qty_s, sep, price_s = spec.lower().partition("x")
    if not sep:
        raise ValueError(f"ожидался формат КОЛxЦЕНА, получено {spec!r}")
    qty = int(qty_s)
    if qty <= 0:
        raise ValueError("количество должно быть положительным")
    try:
        price = Decimal(price_s) * 100
    except InvalidOperation:
        raise ValueError(f"цена должна быть числом, получено {price_s!r}") from None
    if not price.is_finite():
        raise ValueError(f"цена должна быть числом, получено {price_s!r}")
    if price < 0:
        raise ValueError("цена не может быть отрицательной")
    if price != price.to_integral_value():
        raise ValueError(f"цена точнее копейки: {price_s!r}")
    return Item(qty=qty, price_kop=int(price))
