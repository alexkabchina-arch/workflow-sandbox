import pytest

from sandbox.money.tip import tip_options_kop


def test_tip_options_of_round_sum():
    # 5, 10 и 15 % от 100,00 ₽
    assert tip_options_kop(10000) == [500, 1000, 1500]


def test_tip_options_round_to_nearest():
    # 16,65 → 17, 33,3 → 33, 49,95 → 50
    assert tip_options_kop(333) == [17, 33, 50]


def test_tip_options_exact_half_rounds_up():
    # 5 % от 0,10 ₽ = 0,5 коп. → 1; 15 % = 1,5 коп. → 2
    assert tip_options_kop(10) == [1, 1, 2]


def test_tip_options_of_zero_total():
    assert tip_options_kop(0) == [0, 0, 0]


def test_tip_options_are_int():
    assert all(type(tip) is int for tip in tip_options_kop(333))


def test_tip_options_exact_on_large_sum():
    # float на 10**17 теряет младшие цифры
    assert tip_options_kop(10**17 + 10) == [5 * 10**15 + 1, 10**16 + 1, 15 * 10**15 + 2]


def test_tip_options_negative_total():
    with pytest.raises(ValueError):
        tip_options_kop(-1)
