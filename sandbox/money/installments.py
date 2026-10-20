"""График рассрочки в копейках: первый взнос, комиссия и равные платежи, остаток — в последний.
Таблица графика — строками с суммами в рублях."""

from sandbox.money.percent import percent_kop
from sandbox.money.total import format_rub

# Границы из условия #144
MONTHS_MIN, MONTHS_MAX = 2, 24
FIRST_PCT_MAX = 50
FEE_PCT_MAX = 50


def installments_kop(
    total_kop: int, months: int, first_pct: int = 0, fee_pct: int = 0
) -> list[int]:
    if total_kop < 0:
        raise ValueError("сумма не может быть отрицательной")
    if not MONTHS_MIN <= months <= MONTHS_MAX:
        raise ValueError(f"срок рассрочки должен быть от {MONTHS_MIN} до {MONTHS_MAX} месяцев")
    if not 0 <= first_pct <= FIRST_PCT_MAX:
        raise ValueError(f"первый взнос должен быть от 0 до {FIRST_PCT_MAX} %")
    if not 0 <= fee_pct <= FEE_PCT_MAX:
        raise ValueError(f"комиссия должна быть от 0 до {FEE_PCT_MAX} %")
    fee = percent_kop(total_kop, fee_pct)
    # Условие #144: взнос — процент от суммы без комиссии
    first = percent_kop(total_kop, first_pct)
    base, rest = divmod(total_kop + fee - first, months)
    # Условие #144: копейки остатка деления — целиком в последний платёж
    payments = [base] * (months - 1) + [base + rest]
    return [first, *payments] if first_pct > 0 else payments


def installments_table(total_kop: int, months: int, first_pct: int = 0, fee_pct: int = 0) -> str:
    # Условие #144: «взнос: X ₽» перед платежами «N. X ₽», в конце «итого: X ₽»
    schedule = installments_kop(total_kop, months, first_pct, fee_pct)
    lines = []
    payments = schedule
    if first_pct > 0:
        lines.append(f"взнос: {format_rub(schedule[0])}")
        payments = schedule[1:]
    lines += [f"{n}. {format_rub(p)}" for n, p in enumerate(payments, 1)]
    lines.append(f"итого: {format_rub(sum(schedule))}")
    return "\n".join(lines)
