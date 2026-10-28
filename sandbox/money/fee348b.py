"""Прогон #348: тикет B зависит от rounding."""

from sandbox.money import rounding


def fee_kop(total_kop: int) -> int:
    return rounding.round_rub_kop(total_kop) // 100
