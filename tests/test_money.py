import pytest

from sandbox.__main__ import main
from sandbox.cart import Item
from sandbox.money import (
    LOYAL_DISCOUNT_PCT,
    effective_discount_pct,
    format_rub,
    order_total_kop,
)


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
        (0, "0.00\u00a0₽"),
        (5, "0.05\u00a0₽"),
        (99, "0.99\u00a0₽"),
        (31491, "314.91\u00a0₽"),
        (99999, "999.99\u00a0₽"),
        # Тысячи рублей разделяются неразрывным пробелом U+00A0
        (100000, "1\u00a0000.00\u00a0₽"),
        (123456, "1\u00a0234.56\u00a0₽"),
        (100000000, "1\u00a0000\u00a0000.00\u00a0₽"),
        # Отрицательные: знак отдельно, рубли и копейки — от модуля
        (-1, "-0.01\u00a0₽"),
        (-5, "-0.05\u00a0₽"),
        (-99, "-0.99\u00a0₽"),
        (-100, "-1.00\u00a0₽"),
        (-101, "-1.01\u00a0₽"),
        (-123456, "-1\u00a0234.56\u00a0₽"),
        (-100000000, "-1\u00a0000\u00a0000.00\u00a0₽"),
    ],
)
def test_format_rub(kop, expected):
    assert format_rub(kop) == expected


def test_cli(capsys):
    assert main(["3x100", "1x49.90", "--discount", "10"]) == 0
    assert capsys.readouterr().out == "314.91\u00a0₽\n"


def test_cli_thousands_separator(capsys):
    assert main(["1000x1000"]) == 0
    assert capsys.readouterr().out == "1\u00a0000\u00a0000.00\u00a0₽\n"


@pytest.mark.parametrize(
    ("discount_pct", "loyal", "expected"),
    [
        (0, False, 0),
        (10, False, 10),
        (0, True, 5),
        (3, True, 5),  # бо́льшая из двух, а не сумма 8 %
        (10, True, 10),
        (100, True, 100),
    ],
)
def test_effective_discount_is_larger_of_two(discount_pct, loyal, expected):
    assert effective_discount_pct(discount_pct, loyal=loyal) == expected


def test_loyal_discount_is_5_pct():
    # Размер — решение владельца в #89; help флага берёт его из константы
    assert LOYAL_DISCOUNT_PCT == 5


@pytest.mark.parametrize("pct", [-1, 101])
def test_effective_discount_rejects_bad_discount_even_for_loyal(pct):
    # max(-1, 5) спрятал бы ошибку ввода
    with pytest.raises(ValueError, match="скидка должна быть от 0 до 100 %"):
        effective_discount_pct(pct, loyal=True)
