"""Проба точечного прогона (workflow#336): сумма в копейках в пределах."""


def clamp_kop(amount: int, low: int, high: int) -> int:
    return max(low, min(amount, high))
