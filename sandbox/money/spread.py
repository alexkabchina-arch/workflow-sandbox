"""Разброс сумм: разница между наибольшей и наименьшей из сумм в копейках; без сумм — ошибка."""


def range_kop(*amounts_kop: int) -> int:
    # Условие #155: без сумм — ValueError; своё сообщение вместо английского из max()/min()
    if not amounts_kop:
        raise ValueError("нужна хотя бы одна сумма")
    return max(amounts_kop) - min(amounts_kop)
