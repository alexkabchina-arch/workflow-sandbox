"""Сумма заказа в копейках. Скидка — процент от суммы, округление вниз в пользу покупателя."""

from collections.abc import Iterable

from sandbox.cart import Item


def order_total_kop(items: Iterable[Item], discount_pct: int = 0) -> int:
    if not 0 <= discount_pct <= 100:
        raise ValueError("скидка должна быть от 0 до 100 %")
    subtotal = sum(item.qty * item.price_kop for item in items)
    discount = subtotal * discount_pct // 100
    return subtotal - discount


def format_rub(kop: int) -> str:
    rub, rest = divmod(kop, 100)
    # Тысячи — через неразрывный пробел U+00A0, чтобы число не разрывалось переносом
    rub_str = f"{rub:,}".replace(",", "\u00a0")
    return f"{rub_str}.{rest:02d} ₽"
