import pytest

from sandbox.money.split import split_kop


def test_split_even():
    # 300,00 ₽ на 3 части → по 100,00 ₽
    assert split_kop(30000, 3) == [10000, 10000, 10000]


def test_split_remainder_one_kopeck_to_first_parts():
    # Решение владельца в #133 (вариант 3): по копейке остатка первым частям
    assert split_kop(100, 3) == [34, 33, 33]
    assert split_kop(101, 3) == [34, 34, 33]


def test_split_parts_differ_by_at_most_one_kopeck():
    # 10 000,07 ₽ на 9 частей: ничего не теряется, раздел ровный
    shares = split_kop(10**6 + 7, 9)
    assert sum(shares) == 10**6 + 7
    assert max(shares) - min(shares) <= 1


@pytest.mark.parametrize("total", [*range(-25, 26), 10**6 + 7, -(10**6 + 7)])
@pytest.mark.parametrize("parts", range(1, 8))
def test_split_invariants(total, parts):
    # Сумма сохраняется; части по модулю не возрастают и отличаются не больше копейки;
    # у возврата все части не больше нуля, у продажи — не меньше
    shares = split_kop(total, parts)
    sizes = [abs(s) for s in shares]
    assert len(shares) == parts
    assert sum(shares) == total
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] - sizes[-1] <= 1
    assert all(s <= 0 for s in shares) if total < 0 else all(s >= 0 for s in shares)


def test_split_into_one_part():
    # Одна часть — вся сумма
    assert split_kop(34990, 1) == [34990]


def test_split_less_than_parts():
    # 0,02 ₽ на 5 частей: по копейке двум первым, остальным 0
    assert split_kop(2, 5) == [1, 1, 0, 0, 0]
    assert split_kop(0, 3) == [0, 0, 0]


def test_refund_split_mirrors_sale():
    # Возврат делится зеркально продаже: лишняя копейка — тоже первым частям
    assert split_kop(-100, 3) == [-34, -33, -33]
    assert split_kop(-101, 3) == [-34, -34, -33]
    assert split_kop(-300, 3) == [-100, -100, -100]
    assert split_kop(-2, 5) == [-1, -1, 0, 0, 0]


def test_split_exact_on_large_sum():
    # float на 10**17 теряет младшие цифры
    assert split_kop(10**17 + 2, 4) == [25 * 10**15 + 1, 25 * 10**15 + 1, 25 * 10**15, 25 * 10**15]


@pytest.mark.parametrize("parts", [0, -1])
def test_split_needs_positive_parts(parts):
    with pytest.raises(ValueError):
        split_kop(100, parts)
