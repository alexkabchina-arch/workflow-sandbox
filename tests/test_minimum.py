import pytest

from sandbox.money.minimum import MIN_ORDER_KOP, check_min_order


def test_min_order_is_300_rub():
    # Решение владельца в #113: 300 ₽ после скидки
    assert MIN_ORDER_KOP == 30000


@pytest.mark.parametrize("total_kop", [30000, 30001, 10**17])
def test_order_at_or_above_minimum_passes(total_kop):
    check_min_order(total_kop)


@pytest.mark.parametrize("total_kop", [29999, 1, 0])
def test_order_below_minimum_is_error(total_kop):
    with pytest.raises(ValueError, match="минимальная сумма заказа — 300\\.00\u00a0₽"):
        check_min_order(total_kop)
