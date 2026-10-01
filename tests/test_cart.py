import pytest

from sandbox.cart import Item, parse_item


def test_parse_item():
    assert parse_item("3x100") == Item(qty=3, price_kop=10000)
    assert parse_item("1x49.90") == Item(qty=1, price_kop=4990)
    assert parse_item("1x0.29") == Item(qty=1, price_kop=29)
    assert parse_item("1x0.10") == Item(qty=1, price_kop=10)


def test_parse_item_uppercase_separator():
    assert parse_item("3X100") == Item(qty=3, price_kop=10000)


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
    ],
)
def test_parse_item_rejects(spec):
    with pytest.raises(ValueError):
        parse_item(spec)
