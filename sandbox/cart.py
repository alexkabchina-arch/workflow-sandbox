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
    # Разделитель — первый «x» или «X». Не через lower(): он меняет длину строки («İ» → 2 символа),
    # а части нужны дословно — для сообщений об ошибке.
    qty_s, sep, price_s = spec.replace("X", "x", 1).partition("x")
    qty_raw, price_raw = qty_s.strip(), price_s.strip()
    if not sep:
        raise ValueError(f"ожидался формат КОЛxЦЕНА, получено {spec!r}")
    try:
        qty = int(qty_s)
    except ValueError:
        raise ValueError(f"количество должно быть целым числом, получено {qty_raw!r}") from None
    if qty <= 0:
        raise ValueError("количество должно быть положительным")
    # Нечисло и слишком длинное число — разные ошибки: сообщение не должно винить длину «abc».
    # Decimal() — точная конверсия; без ловушки в глобальном контексте нечисло даёт NaN.
    try:
        price = Decimal(price_s)
    except InvalidOperation:
        price = None
    if price is None or not price.is_finite():
        raise ValueError(f"цена должна быть числом, получено {price_raw!r}")
    try:
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
