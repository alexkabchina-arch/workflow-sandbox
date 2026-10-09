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
    # Знак отдельно: для -123456 divmod дал бы -1235 и 44 вместо -1234 и 56
    sign = "-" if kop < 0 else ""
    rub, rest = divmod(abs(kop), 100)
    # Тысячи и «₽» — через неразрывный пробел U+00A0, чтобы сумма не разрывалась переносом
    rub_str = f"{rub:,}".replace(",", "\u00a0")
    return f"{sign}{rub_str}.{rest:02d}\u00a0₽"
