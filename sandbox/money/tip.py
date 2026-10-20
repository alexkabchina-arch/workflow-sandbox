"""Варианты чаевых: 5, 10 и 15 % от суммы в копейках, до копейки, половина — вверх."""

from sandbox.money.percent import percent_kop

# Варианты из условия #136
TIP_OPTIONS_PCT = (5, 10, 15)


def tip_options_kop(total_kop: int) -> list[int]:
    # Условие #136: сумма меньше 0 — ошибка ввода
    if total_kop < 0:
        raise ValueError("сумма не может быть отрицательной")
    return [percent_kop(total_kop, pct) for pct in TIP_OPTIONS_PCT]
