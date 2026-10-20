"""Округление суммы в копейках до целого рубля, половина вверх."""


def round_rub_kop(total_kop: int) -> int:
    # Знак отдельно, как в vat_included_kop: иначе ничья у возврата уйдёт к нулю (-1,50 → -1)
    sign = -1 if total_kop < 0 else 1
    # Половина вверх в целых: floor(x / 100 + 1/2) = (x + 50) // 100, без float
    return sign * ((abs(total_kop) + 50) // 100 * 100)
