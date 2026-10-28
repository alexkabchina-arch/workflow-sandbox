"""Скидка в копейках (прогон workflow#346)."""


def discount_kop(total_kop: int, pct: int) -> int:
    """Скидка `pct` % от суммы в копейках; половина копейки — вверх."""
    if total_kop < 0 or not 0 <= pct <= 100:
        raise ValueError("сумма и процент — неотрицательные, процент не больше 100")
    return (total_kop * pct * 2 + 100) // 200
