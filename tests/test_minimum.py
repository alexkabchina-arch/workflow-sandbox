import pytest

from sandbox.money.minimum import MIN_ORDER_KOP, check_min_order, min_kop


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


def test_min_of_several_amounts():
    assert min_kop(100, 250, -5) == -5


def test_min_is_signed_not_by_abs():
    # Возврат −500 меньше продажи 100: минимум со знаком, не по модулю
    assert min_kop(-500, 100) == -500
    assert min_kop(-5, -3) == -5


def test_min_of_single_amount():
    assert min_kop(7) == 7


def test_min_exact_on_large_sum():
    # Целые без float: на 10**17 float не различил бы суммы
    # Ответ 10**17 + 1 во float непредставим: float-реализация дала бы 10**17
    assert min_kop(10**17 + 1, 10**17 + 2) == 10**17 + 1


def test_min_of_no_amounts_is_error():
    with pytest.raises(ValueError, match="нужна хотя бы одна сумма"):
        min_kop()
