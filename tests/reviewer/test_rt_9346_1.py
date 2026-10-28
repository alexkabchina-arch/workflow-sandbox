from sandbox.money.discount import discount_kop


def test_half_kopeck_rounds_up():
    assert discount_kop(105, 10) == 11
