from sandbox.money.discount import discount_kop


def test_discount_of_round_sum():
    assert discount_kop(1000, 10) == 100
