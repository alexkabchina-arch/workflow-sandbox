"""Скидка в копейках: процент от суммы, половина копейки — вверх."""


def discount_kop(total_kop: int, pct: int) -> int:
    if not 0 <= pct <= 100:
        raise ValueError("процент должен быть от 0 до 100 %")
    return total_kop * pct // 100
