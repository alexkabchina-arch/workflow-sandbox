from decimal import localcontext

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
    ],
)
def test_parse_item_price_errors(spec, message):
    with pytest.raises(ValueError, match=message):
        parse_item(spec)


def test_parse_item_price_ignores_global_decimal_context():
    with localcontext() as ctx:
        ctx.prec = 3
        assert parse_item("1x12345.67") == Item(qty=1, price_kop=1234567)
