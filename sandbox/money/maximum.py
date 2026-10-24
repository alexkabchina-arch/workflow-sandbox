"""Наибольшая сумма: максимум из сумм в копейках; без сумм — ошибка."""


def max_kop(*amounts_kop: int) -> int:
    # Условие #150: без сумм — ValueError, а не 0
    if not amounts_kop:
        raise ValueError("нужна хотя бы одна сумма")
    return max(amounts_kop)
