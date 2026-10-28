"""Тест ревьюера к #9344: половина копейки скидки округляется вверх, а не отбрасывается."""

from sandbox.money.discount import discount_kop


def test_half_kopeck_rounds_up():
    # 10 % от 105 коп. = 10,5 коп. → по тикету 11 коп.
    assert discount_kop(105, 10) == 11
