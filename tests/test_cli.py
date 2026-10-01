import pytest

from sandbox.__main__ import main


def test_valid_input_prints_total(capsys):
    assert main(["3x100", "1x49.90", "--discount", "10"]) == 0
    assert capsys.readouterr().out == "314.91 ₽\n"


@pytest.mark.parametrize(
    "argv, reason",
    [
        (["0x100"], "количество должно быть положительным"),
        (["3"], "ожидался формат КОЛxЦЕНА, получено '3'"),
        (["3x100", "--discount", "101"], "скидка должна быть от 0 до 100 %"),
        (["1x0.005"], "цена точнее копейки: '0.005'"),
        (["3x100", "--discount", "abc"], "argument --discount: invalid int value: 'abc'"),
        (["-1x100"], "the following arguments are required: items"),
        ([], "the following arguments are required: items"),
    ],
)
def test_bad_input_is_one_line_error_with_code_2(capsys, argv, reason):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"sandbox: error: {reason}\n"
