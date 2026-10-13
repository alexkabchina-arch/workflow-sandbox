"""CLI: python -m sandbox 3x100 1x49.90 --discount 10 [--kop] [--count]"""

import argparse
from typing import NoReturn

from sandbox import __version__
from sandbox.cart import parse_item
from sandbox.money import format_rub, order_total_kop


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
    parser.add_argument("--kop", action="store_true", help="итог целым числом копеек, без «₽»")
    parser.add_argument("--count", action="store_true", help="второй строкой — сумма количеств")
    parser.add_argument("--version", action=VersionAction, help="показать версию и выйти")
    args = parser.parse_args(argv)
    try:
        items = [parse_item(spec) for spec in args.items]
        total = order_total_kop(items, args.discount)
    except ValueError as exc:
        parser.error(str(exc))
    print(total if args.kop else format_rub(total))
    if args.count:
        print(f"позиций: {sum(item.qty for item in items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
