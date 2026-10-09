from decimal import InvalidOperation, localcontext

import pytest

from sandbox.cart import Item, parse_item


def test_parse_item():
    assert parse_item("3x100") == Item(qty=3, price_kop=10000)
    assert parse_item("1x49.90") == Item(qty=1, price_kop=4990)
    assert parse_item("1x0.29") == Item(qty=1, price_kop=29)
    assert parse_item("1x0.10") == Item(qty=1, price_kop=10)


def test_parse_item_uppercase_separator():
    assert parse_item("3X100") == Item(qty=3, price_kop=10000)


def test_parse_item_allows_spaces_around_separator():
    assert parse_item(" 3 x 100 ") == Item(qty=3, price_kop=10000)
    assert parse_item("1 X 49.90") == Item(qty=1, price_kop=4990)
    assert parse_item("\t3\tx\t100\n") == Item(qty=3, price_kop=10000)


@pytest.mark.parametrize(
    "spec",
    [
        "3",
        "0x100",
        "-1x100",
        "1x-5",
        "0X100",
        "1X-5",
        "X100",
        "1x-0.004",
        "1x0.005",
        "1x0.285",
        "1xabc",
        "1xnan",
        "1xinf",
        "3 0x1",
        "3x1 00",
    ],
)
def test_parse_item_rejects(spec):
    with pytest.raises(ValueError):
        parse_item(spec)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("1x-0.004", "точнее копейки"),
        ("1x-0.01", "отрицательной"),
        ("1x0.285", "точнее копейки"),
        ("1x1.0000000000000000000000000001", "точнее копейки"),
        ("1x1e-1000030", "точнее копейки"),
        ("1x123456789012345678901234567.89", "до 15 цифр"),
        ("1x1e999999", "до 15 цифр"),
        ("1xNaN", "'NaN'"),
        (" 3 x 0.005 ", "точнее копейки: '0.005'"),
        ("3x1 00", "^цена должна быть числом, получено '1 00'$"),
        ("1xabc", "^цена должна быть числом, получено 'abc'$"),
        ("1xinf", "^цена должна быть числом, получено 'inf'$"),
        ("1x", "^цена должна быть числом, получено ''$"),
        ("1xİ5", "^цена должна быть числом, получено 'İ5'$"),
        ("1XabX", "^цена должна быть числом, получено 'abX'$"),
    ],
)
def test_parse_item_price_errors(spec, message):
    with pytest.raises(ValueError, match=message):
        parse_item(spec)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("3 0x1", "^количество должно быть целым числом, получено '3 0'$"),
        ("abcx100", "^количество должно быть целым числом, получено 'abc'$"),
        ("1.5x100", "^количество должно быть целым числом, получено '1.5'$"),
        (" x100", "^количество должно быть целым числом, получено ''$"),
        ("İ1x5", "^количество должно быть целым числом, получено 'İ1'$"),
    ],
)
def test_parse_item_qty_errors(spec, message):
    with pytest.raises(ValueError, match=message):
        parse_item(spec)


def test_parse_item_price_not_a_number_ignores_global_decimal_context():
    with localcontext() as ctx:
        ctx.traps[InvalidOperation] = False
        with pytest.raises(ValueError, match="^цена должна быть числом, получено 'abc'$"):
            parse_item("1xabc")


def test_parse_item_price_ignores_global_decimal_context():
    with localcontext() as ctx:
        ctx.prec = 3
        assert parse_item("1x12345.67") == Item(qty=1, price_kop=1234567)
