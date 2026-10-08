import pytest

from sandbox.__main__ import main
from sandbox.cart import Item
from sandbox.money import format_rub, order_total_kop


def test_total_without_discount():
    assert order_total_kop([Item(3, 10000), Item(1, 4990)]) == 34990


def test_discount_rounds_in_favor_of_buyer():
    # 10 % от 349,90 = 34,99 → итог 314,91
    assert order_total_kop([Item(3, 10000), Item(1, 4990)], 10) == 31491


@pytest.mark.parametrize("pct", [-1, 101])
def test_discount_out_of_range(pct):
    with pytest.raises(ValueError):
        order_total_kop([Item(1, 100)], pct)


@pytest.mark.parametrize(
    ("kop", "expected"),
    [
        (0, "0.00 ₽"),
        (5, "0.05 ₽"),
        (99, "0.99 ₽"),
        (31491, "314.91 ₽"),
        (99999, "999.99 ₽"),
        # Тысячи рублей разделяются неразрывным пробелом U+00A0
        (100000, "1\u00a0000.00 ₽"),
        (123456, "1\u00a0234.56 ₽"),
        (100000000, "1\u00a0000\u00a0000.00 ₽"),
        # Отрицательные: знак отдельно, рубли и копейки — от модуля
        (-5, "-0.05 ₽"),
        (-100, "-1.00 ₽"),
        (-123456, "-1\u00a0234.56 ₽"),
    ],
)
def test_format_rub(kop, expected):
    assert format_rub(kop) == expected


def test_cli(capsys):
    assert main(["3x100", "1x49.90", "--discount", "10"]) == 0
    assert capsys.readouterr().out.strip() == "314.91 ₽"


def test_cli_thousands_separator(capsys):
    assert main(["1000x1000"]) == 0
    assert capsys.readouterr().out == "1\u00a0000\u00a0000.00 ₽\n"
