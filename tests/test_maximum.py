import pytest

from sandbox.money.maximum import max_kop


def test_max_of_several_amounts():
    assert max_kop(100, 250, -5) == 250


def test_max_of_single_amount():
    assert max_kop(-3) == -3


def test_max_exact_on_large_sum():
    # Целые без float: на 10**17 float не различил бы суммы
    assert max_kop(10**17, 10**17 + 1) == 10**17 + 1


def test_max_of_no_amounts_is_error():
    with pytest.raises(ValueError, match="нужна хотя бы одна сумма"):
        max_kop()
