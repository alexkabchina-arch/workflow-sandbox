"""Процент от суммы в копейках: до копейки, половина вверх, возврат зеркален продаже."""


def percent_kop(total_kop: int, pct: int) -> int:
    if not 0 <= pct <= 100:
        raise ValueError("процент должен быть от 0 до 100 %")
    # Знак отдельно, как в avg_unit_price_kop: процент от возврата зеркален проценту от продажи
    sign = -1 if total_kop < 0 else 1
    # Половина вверх в целых: floor(x * p / 100 + 1/2) = (x * p + 50) // 100, без float
    return sign * ((abs(total_kop) * pct + 50) // 100)
