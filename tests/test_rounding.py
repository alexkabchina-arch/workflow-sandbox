from sandbox.money.rounding import round_rub_kop


def test_half_rounds_up():
    # 1,50 ₽ → 2,00 ₽
    assert round_rub_kop(150) == 200


def test_below_half_rounds_down():
    # 1,49 ₽ → 1,00 ₽
    assert round_rub_kop(149) == 100


def test_refund_half_mirrors_sale():
    # Возврат −1,50 ₽ → −2,00 ₽, а не −1,00 ₽
    assert round_rub_kop(-150) == -200


def test_refund_below_half_mirrors_sale():
    assert round_rub_kop(-149) == -100


def test_zero_stays_zero():
    assert round_rub_kop(0) == 0


def test_whole_rubles_unchanged():
    assert round_rub_kop(300) == 300


def test_exact_on_large_sum():
    # float тут теряет копейки
    assert round_rub_kop(10**17 + 50) == 10**17 + 100
