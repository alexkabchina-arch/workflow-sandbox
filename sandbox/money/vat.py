"""НДС, включённый в сумму: total * rate / (100 + rate), в копейках."""


def vat_included_kop(total_kop: int, rate_pct: int = 20) -> int:
    if not 0 <= rate_pct <= 100:
        raise ValueError("ставка НДС должна быть от 0 до 100 %")
    # Половина вверх в целых: floor(x / d + 1/2) = (2x + d) // 2d, без float
    divisor = 100 + rate_pct
    return (2 * total_kop * rate_pct + divisor) // (2 * divisor)
