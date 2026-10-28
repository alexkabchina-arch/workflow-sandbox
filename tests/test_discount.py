from sandbox.money.discount import discount_kop


def test_round_sum():
    assert discount_kop(10000, 15) == 1500
