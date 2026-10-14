"""CLI суммы заказа: python -m sandbox 3x100 1x49.90 --discount 10; флаги — в --help."""

import argparse
from typing import NoReturn

from sandbox import __version__
from sandbox.cart import parse_item, total_qty
from sandbox.money import LOYAL_DISCOUNT_PCT, avg_unit_price_kop, format_rub, order_total_kop


class OneLineErrorParser(argparse.ArgumentParser):
    """Любая ошибка ввода — одна строка «sandbox: error: <причина>» и код 2, без usage."""

    def error(self, message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: error: {message}\n")


class VersionAction(argparse.Action):
    """«sandbox <версия>» в stdout, код 0; встроенный action="version" переносит её по COLUMNS."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(f"{parser.prog} {__version__}")
        parser.exit()


def main(argv: list[str] | None = None) -> int:
    parser = OneLineErrorParser(prog="sandbox", description="Сумма заказа")
    parser.add_argument("items", nargs="+", help="позиции вида КОЛxЦЕНА (x или X), например 3x100")
    parser.add_argument("--discount", type=int, default=0, help="скидка, %%")
    parser.add_argument(
        "--loyal",
        action="store_true",
        help=f"постоянный клиент: скидка {LOYAL_DISCOUNT_PCT} %%, с --discount — бо́льшая из двух",
    )
    # Оба флага меняют вид итога: вместе — ошибка, а не молчаливый выбор одного
    unit = parser.add_mutually_exclusive_group()
    unit.add_argument("--kop", action="store_true", help="итог целым числом копеек, без «₽»")
    unit.add_argument(
        "--code",
        action="store_true",
        help="код валюты «RUB» через обычный пробел вместо «₽»; с --kop несовместим",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="второй строкой «позиций: K», K — сумма количеств (штук)",
    )
    parser.add_argument(
        "--avg",
        action="store_true",
        help="второй строкой «средняя цена: X» — итог со скидкой на число штук, в формате итога",
    )
    parser.add_argument("--version", action=VersionAction, help="показать версию и выйти")
    args = parser.parse_args(argv)
    try:
        items = [parse_item(spec) for spec in args.items]
        total = order_total_kop(items, args.discount, loyal=args.loyal)
    except ValueError as exc:
        parser.error(str(exc))

    def format_amount(kop: int) -> str:
        """Сумма в виде итога: --kop, --code или «₽»."""
        if args.kop:
            return str(kop)
        if args.code:
            # Меняется только хвост: U+00A0 и «₽»; разделитель тысяч остаётся неразрывным
            return format_rub(kop).removesuffix("\u00a0₽") + " RUB"
        return format_rub(kop)

    # Вывод собирается целиком до печати: int→str длиннее sys.int_info.str_digits_check_threshold
    # цифр — ValueError, и без этого stdout остался бы с частью вывода и traceback.
    try:
        qty = total_qty(items)
        lines = [format_amount(total)]
        if args.avg:
            lines.append(f"средняя цена: {format_amount(avg_unit_price_kop(total, qty))}")
        if args.count:
            lines.append(f"позиций: {qty}")
    except ValueError:
        parser.error("число слишком большое для вывода")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
