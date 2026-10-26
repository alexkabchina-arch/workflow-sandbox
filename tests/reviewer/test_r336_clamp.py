"""Проба точечного прогона (workflow#336): тесты ревьюера к `clamp_kop`."""

from sandbox.money.clamp import clamp_kop


def test_clamp_inside_keeps_amount():
    # На ветке проходит; на основе модуля нет — ошибка импорта.
    assert clamp_kop(50, 0, 100) == 50


def test_clamp_inverted_bounds_is_refused():
    # Намеренная находка: при low > high функция молча отдаёт low — проверка падает.
    assert clamp_kop(50, 100, 0) == 50
