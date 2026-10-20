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


def test_half_on_even_ruble_rounds_up():
    # 2,50 ₽ → 3,00 ₽: банковское округление дало бы 2,00 ₽
    assert round_rub_kop(250) == 300
    assert round_rub_kop(-250) == -300


def test_below_one_ruble():
    # 0,49 ₽ → 0, 0,50 ₽ → 1,00 ₽, возврат зеркально
    assert round_rub_kop(49) == 0
    assert round_rub_kop(50) == 100
    assert round_rub_kop(-49) == 0
    assert round_rub_kop(-50) == -100


def test_exact_on_large_sum():
    # 10**18 коп. — за 2**53 · 100: float тут теряет половину рубля
    assert round_rub_kop(10**18 + 50) == 10**18 + 100
    assert round_rub_kop(-(10**18 + 50)) == -(10**18 + 100)
