"""Деление суммы на части в копейках: сумма частей равна исходной, разница частей — до копейки."""


def split_kop(total_kop: int, parts: int) -> list[int]:
    if parts < 1:
        raise ValueError("число частей должно быть положительным")
    # Знак отдельно, как в avg_unit_price_kop: деление возврата зеркально делению продажи
    sign = -1 if total_kop < 0 else 1
    base, rest = divmod(abs(total_kop), parts)
    # Решение владельца в #133: копейки остатка — по одной первым частям
    return [sign * (base + 1 if i < rest else base) for i in range(parts)]
