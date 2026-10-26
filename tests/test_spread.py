import os

import pytest

from sandbox.money.spread import range_kop


def test_range_of_several_amounts():
    # Наибольшая 250, наименьшая −5: разница 255
    assert range_kop(100, 250, -5) == 255


def test_range_of_single_amount_is_zero():
    assert range_kop(7) == 0


def test_range_is_signed_not_by_abs():
    # Возврат −500 и продажа 100: разброс 600, не 400
    assert range_kop(-500, 100) == 600


def test_range_exact_on_large_sum():
    # Целые без float: на 10**17 float не различил бы суммы
    assert range_kop(10**17, 10**17 + 1) == 1


def test_range_of_no_amounts_is_error():
    with pytest.raises(ValueError, match="нужна хотя бы одна сумма"):
        range_kop()


def test_ci_probe():
    # Условие #155: намеренно падает только в CI — проба причин красного CI
    assert os.environ.get("GITHUB_ACTIONS") != "true"
