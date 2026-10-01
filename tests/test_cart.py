import pytest

from sandbox.cart import Item, parse_item


def test_parse_item():
    assert parse_item("3x100") == Item(qty=3, price_kop=10000)
    assert parse_item("1x49.90") == Item(qty=1, price_kop=4990)


def test_parse_item_uppercase_separator():
    assert parse_item("3X100") == Item(qty=3, price_kop=10000)


@pytest.mark.parametrize("spec", ["3", "0x100", "-1x100", "1x-5", "0X100", "1X-5", "X100"])
def test_parse_item_rejects(spec):
    with pytest.raises(ValueError):
        parse_item(spec)


def test_parse_item_rejects_tiny_negative_price():
    # #15: round(float) превращает -0.004 в 0, и отрицательная цена проходит
    with pytest.raises(ValueError):
        parse_item("1x-0.004")
