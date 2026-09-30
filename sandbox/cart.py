"""Разбор позиций корзины из строки вида «3x100»."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    qty: int
    price_kop: int


def parse_item(spec: str) -> Item:
    """«3x100» → 3 штуки по 100 ₽ (цена хранится в копейках)."""
    qty_s, sep, price_s = spec.partition("x")
    if not sep:
        raise ValueError(f"ожидался формат КОЛxЦЕНА, получено {spec!r}")
    qty = int(qty_s)
    if qty <= 0:
        raise ValueError("количество должно быть положительным")
    price_kop = round(float(price_s) * 100)
    if price_kop < 0:
        raise ValueError("цена не может быть отрицательной")
    return Item(qty=qty, price_kop=price_kop)
