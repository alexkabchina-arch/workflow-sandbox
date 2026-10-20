import pytest

from sandbox.money.installments import installments_kop, installments_table


def test_installments_remainder_to_last_payment():
    # 1 000,00 ₽ на 3 месяца: копейка остатка — в последний платёж
    assert installments_kop(100000, 3) == [33333, 33333, 33334]


def test_installments_with_first_payment_and_fee():
    # Комиссия 5 % = 50,00 ₽, взнос 10 % от суммы без комиссии = 100,00 ₽, остаток 950,00 ₽ на 4
    schedule = installments_kop(100000, 4, first_pct=10, fee_pct=5)
    assert schedule == [10000, 23750, 23750, 23750, 23750]
    assert sum(schedule) == 105000


def test_installments_remainder_with_first_payment():
    # 1 000,01 ₽, взнос 10 % = 100,00 ₽ (10 000,1 коп.), остаток 900,01 ₽ на 3: копейка — последнему
    assert installments_kop(100001, 3, first_pct=10) == [10000, 30000, 30000, 30001]


def test_installments_whole_remainder_to_last_payment():
    # Остаток деления целиком в последний платёж, а не по копейке нескольким
    assert installments_kop(100, 6) == [16, 16, 16, 16, 16, 20]


def test_installments_fee_rounds_half_up():
    # 5 % от 0,10 ₽ = 0,5 коп. → 1 коп.; 11 коп. на 2 платежа
    assert installments_kop(10, 2, fee_pct=5) == [5, 6]


def test_installments_first_payment_rounds_half_up():
    # 5 % от 0,10 ₽ = 0,5 коп. → взнос 1 коп.; остаток 9 коп. на 2 платежа
    assert installments_kop(10, 2, first_pct=5) == [1, 4, 5]


def test_installments_first_payment_ignores_fee():
    # Взнос — процент от суммы без комиссии: 50 % от 1 000,00 ₽ = 500,00 ₽, не 750,00 ₽
    assert installments_kop(100000, 2, first_pct=50, fee_pct=50) == [50000, 50000, 50000]


def test_installments_no_first_payment_element_when_zero_pct():
    # first_pct = 0 — взноса в списке нет, только платежи
    assert len(installments_kop(100000, 5)) == 5
    assert len(installments_kop(100000, 5, first_pct=1)) == 6


def test_installments_of_zero_total():
    assert installments_kop(0, 3) == [0, 0, 0]
    assert installments_kop(0, 2, first_pct=10, fee_pct=5) == [0, 0, 0]


def test_installments_are_int():
    assert all(type(p) is int for p in installments_kop(100001, 7, first_pct=13, fee_pct=7))


def test_installments_exact_on_large_sum():
    # float на 10**17 теряет младшие цифры
    assert installments_kop(10**17 + 2, 3) == [
        33333333333333334,
        33333333333333334,
        33333333333333334,
    ]
    assert installments_kop(10**17 + 3, 2) == [5 * 10**16 + 1, 5 * 10**16 + 2]


@pytest.mark.parametrize("total", [0, 1, 99, 100000, 100001, 10**6 + 7])
@pytest.mark.parametrize("months", [2, 3, 7, 24])
@pytest.mark.parametrize("first_pct", [0, 1, 10, 50])
@pytest.mark.parametrize("fee_pct", [0, 1, 5, 50])
def test_installments_invariants(total, months, first_pct, fee_pct):
    # Сумма списка — сумма плюс комиссия; платежи равны, последний больше остальных
    # не более чем на months − 1 коп.
    schedule = installments_kop(total, months, first_pct=first_pct, fee_pct=fee_pct)
    fee = (total * fee_pct + 50) // 100
    assert sum(schedule) == total + fee
    payments = schedule[1:] if first_pct > 0 else schedule
    assert len(payments) == months
    assert len(set(payments[:-1])) == 1
    assert 0 <= payments[-1] - payments[0] < months


@pytest.mark.parametrize("months", [2, 24])
def test_installments_months_bounds_allowed(months):
    assert len(installments_kop(100000, months)) == months


@pytest.mark.parametrize("months", [1, 25, 0, -1])
def test_installments_months_out_of_bounds(months):
    with pytest.raises(ValueError):
        installments_kop(100000, months)


@pytest.mark.parametrize("pct", [0, 50])
def test_installments_pct_bounds_allowed(pct):
    installments_kop(100000, 2, first_pct=pct)
    installments_kop(100000, 2, fee_pct=pct)


@pytest.mark.parametrize("pct", [-1, 51, 100])
def test_installments_first_pct_out_of_bounds(pct):
    with pytest.raises(ValueError):
        installments_kop(100000, 2, first_pct=pct)


@pytest.mark.parametrize("pct", [-1, 51, 100])
def test_installments_fee_pct_out_of_bounds(pct):
    with pytest.raises(ValueError):
        installments_kop(100000, 2, fee_pct=pct)


def test_installments_negative_total():
    with pytest.raises(ValueError):
        installments_kop(-1, 2)


def test_installments_table_without_first_payment():
    assert installments_table(100000, 3) == (
        "1. 333.33\u00a0₽\n2. 333.33\u00a0₽\n3. 333.34\u00a0₽\nитого: 1\u00a0000.00\u00a0₽"
    )


def test_installments_table_with_first_payment_and_fee():
    assert installments_table(100000, 4, first_pct=10, fee_pct=5) == "\n".join(
        [
            "взнос: 100.00\u00a0₽",
            "1. 237.50\u00a0₽",
            "2. 237.50\u00a0₽",
            "3. 237.50\u00a0₽",
            "4. 237.50\u00a0₽",
            "итого: 1\u00a0050.00\u00a0₽",
        ]
    )


def test_installments_table_of_zero_total():
    assert installments_table(0, 2) == "1. 0.00\u00a0₽\n2. 0.00\u00a0₽\nитого: 0.00\u00a0₽"


def test_installments_table_validates_like_schedule():
    with pytest.raises(ValueError):
        installments_table(100000, 1)
