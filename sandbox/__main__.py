"""CLI: python -m sandbox 3x100 1x49.90 --discount 10"""

import argparse

from sandbox.cart import parse_item
from sandbox.money import format_rub, order_total_kop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbox", description="Сумма заказа")
    parser.add_argument("items", nargs="+", help="позиции вида КОЛxЦЕНА (x или X), например 3x100")
    parser.add_argument("--discount", type=int, default=0, help="скидка, %%")
    args = parser.parse_args(argv)
    try:
        items = [parse_item(spec) for spec in args.items]
        total = order_total_kop(items, args.discount)
    except ValueError as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    print(format_rub(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
