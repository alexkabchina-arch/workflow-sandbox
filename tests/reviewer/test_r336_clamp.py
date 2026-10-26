"""Проба точечного прогона (workflow#336): тест ревьюера к `clamp_kop`."""

from sandbox.money.clamp import clamp_kop


def test_clamp_inside_keeps_amount():
    assert clamp_kop(50, 0, 100) == 50
