"""Разбор позиций корзины из строки вида «3x100»."""

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, InvalidOperation

# Цена — в рублях, не точнее копейки и не длиннее MAX_PRICE_DIGITS цифр. Свой контекст, а не
# глобальный getcontext(): округление и переполнение здесь — ошибка ввода, а не тихая правка суммы.
MAX_PRICE_DIGITS = 15
KOPECK = Decimal("0.01")
PRICE_CONTEXT = Context(prec=MAX_PRICE_DIGITS, traps=[Inexact, InvalidOperation])


@dataclass(frozen=True)
class Item:
    qty: int
    price_kop: int


def parse_item(spec: str) -> Item:
    """«3x100» или «3X100» → 3 штуки по 100 ₽ (цена хранится в копейках).

    Пробельные символы (как у str.strip(): пробел, таб, перевод строки) вокруг «x» и по краям
    допустимы (« 3 x 100 »), внутри числа — ValueError («3 0x1»): так ведут себя int() и Decimal().

    Цена неотрицательна и не точнее копейки: «1x0.005» и «1x-0.01» — ValueError, без округления.
    """
    qty_s, sep, price_s = spec.lower().partition("x")
    if not sep:
        raise ValueError(f"ожидался формат КОЛxЦЕНА, получено {spec!r}")
    qty = int(qty_s)
    if qty <= 0:
        raise ValueError("количество должно быть положительным")
    price_raw = spec[len(qty_s) + len(sep) :].strip()
    try:
        price = Decimal(price_s)
        if not price.is_finite():
            raise InvalidOperation
        price = price.quantize(KOPECK, context=PRICE_CONTEXT)
    except Inexact:
        raise ValueError(f"цена точнее копейки: {price_raw!r}") from None
    except InvalidOperation:
        raise ValueError(
            f"цена должна быть числом до {MAX_PRICE_DIGITS} цифр, получено {price_raw!r}"
        ) from None
    if price < 0:
        raise ValueError("цена не может быть отрицательной")
    return Item(qty=qty, price_kop=int(price.scaleb(2, context=PRICE_CONTEXT)))
