import pytest

from sandbox.money.round_check import ROUND_BONUS_KOP, is_round_rub


def test_round_bonus_is_one_rub():
    # Условие #143
    assert ROUND_BONUS_KOP == 100


@pytest.mark.parametrize("total_kop", [30000, -200, 0, 100])
def test_whole_rubles_are_round(total_kop):
    assert is_round_rub(total_kop) is True


@pytest.mark.parametrize("total_kop", [30001, 1, 99, -1, -250])
def test_kopecks_are_not_round(total_kop):
    assert is_round_rub(total_kop) is False
