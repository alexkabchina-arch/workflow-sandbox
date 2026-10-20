import pytest

from sandbox.money.percent import percent_kop


def test_percent_of_round_sum():
    # 15 % от 100,00 ₽ → 15,00 ₽
    assert percent_kop(10000, 15) == 1500


def test_percent_exact_half_rounds_up():
    # 50 % от 3,33 ₽ = 166,5 коп. → 167 (половина вверх)
    assert percent_kop(333, 50) == 167


def test_percent_below_half_rounds_down():
    # 10 % от 0,04 ₽ = 0,4 коп. → 0; 10 % от 0,06 ₽ = 0,6 коп. → 1
    assert percent_kop(4, 10) == 0
    assert percent_kop(6, 10) == 1


def test_refund_percent_mirrors_sale():
    # Возврат: −166,5 коп. → −167, как у продажи 166,5 → 167
    assert percent_kop(-333, 50) == -167
    assert percent_kop(-4, 10) == 0


def test_percent_bounds_are_allowed():
    assert percent_kop(333, 0) == 0
    assert percent_kop(333, 100) == 333


def test_percent_of_zero_total():
    assert percent_kop(0, 15) == 0


def test_percent_exact_on_large_sum():
    # float на 10**17 теряет младшие цифры
    assert percent_kop(10**17 + 1, 50) == 5 * 10**16 + 1


@pytest.mark.parametrize("pct", [-1, 101])
def test_percent_out_of_range(pct):
    with pytest.raises(ValueError):
        percent_kop(100, pct)
