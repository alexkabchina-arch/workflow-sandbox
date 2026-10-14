import pytest

from sandbox.money.average import avg_unit_price_kop


def test_avg_of_even_split():
    # 300,00 ₽ на 3 штуки → 100,00 ₽
    assert avg_unit_price_kop(30000, 3) == 10000


def test_avg_rounds_to_nearest_kopeck():
    # 349,90 ₽ на 4 штуки = 87,475 → 87,48 (половина вверх)
    assert avg_unit_price_kop(34990, 4) == 8748


def test_avg_below_half_rounds_down():
    # 0,02 ₽ на 3 штуки = 0,667 коп. → 1; 0,01 ₽ на 3 = 0,333 → 0
    assert avg_unit_price_kop(2, 3) == 1
    assert avg_unit_price_kop(1, 3) == 0


def test_avg_exact_half_rounds_up():
    # Решение владельца 14.10: 0,03 ₽ на 2 штуки = 1,5 коп. → 2
    assert avg_unit_price_kop(3, 2) == 2


def test_avg_of_zero_total():
    # Скидка 100 %: итог 0 → средняя 0
    assert avg_unit_price_kop(0, 3) == 0


def test_refund_avg_mirrors_sale():
    # Возврат 0,03 ₽ на 2 штуки: −1,5 коп. → −2, как у продажи 1,5 → 2
    assert avg_unit_price_kop(-3, 2) == -2
    assert avg_unit_price_kop(-1, 3) == 0


def test_avg_exact_on_large_sum():
    # float на 10**17 теряет младшие цифры
    assert avg_unit_price_kop(10**17 + 2, 4) == 25 * 10**15 + 1


@pytest.mark.parametrize("qty", [0, -1])
def test_avg_needs_positive_qty(qty):
    with pytest.raises(ValueError):
        avg_unit_price_kop(100, qty)
