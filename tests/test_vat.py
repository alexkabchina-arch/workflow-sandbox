import pytest

from sandbox.money.vat import vat_included_kop


def test_vat_included_at_default_rate():
    # 120,00 ₽ с НДС 20 % → НДС 20,00 ₽
    assert vat_included_kop(12000) == 2000


def test_vat_rounds_half_up_to_kopeck():
    # 1,00 ₽ * 20 / 120 = 16,67 коп. → 17
    assert vat_included_kop(100) == 17


def test_vat_exact_half_rounds_up():
    # 0,03 ₽ * 20 / 120 = 0,5 коп. → 1
    assert vat_included_kop(3) == 1


def test_zero_rate_gives_no_vat():
    assert vat_included_kop(1000, 0) == 0


@pytest.mark.parametrize("rate", [-1, 101])
def test_rate_out_of_range(rate):
    with pytest.raises(ValueError):
        vat_included_kop(1000, rate)
