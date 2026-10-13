import importlib
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

import sandbox
from sandbox.__main__ import main


def test_valid_input_prints_total(capsys):
    assert main(["3x100", "1x49.90", "--discount", "10"]) == 0
    assert capsys.readouterr().out == "314.91\u00a0₽\n"


def test_kop_flag_prints_integer_kopecks(capsys):
    assert main(["3x100", "1x49.90", "--discount", "10", "--kop"]) == 0
    assert capsys.readouterr().out == "31491\n"


def test_kop_flag_has_no_thousands_separator(capsys):
    # С --kop нет ни «₽», ни U+00A0 между тысячами, которые format_rub ставит от 1000 ₽
    assert main(["1x1234.56", "--kop"]) == 0
    assert capsys.readouterr().out == "123456\n"


@pytest.mark.parametrize(
    ("argv", "out"),
    [
        (["3x100", "1x49.90"], "349.90\u00a0₽\n"),
        (["3x100", "1x49.90", "--count"], "349.90\u00a0₽\nпозиций: 4\n"),
        (["3x100", "1x49.90", "--discount", "10", "--count"], "314.91\u00a0₽\nпозиций: 4\n"),
        (["3x100", "1x49.90", "--kop", "--count"], "34990\nпозиций: 4\n"),
    ],
)
def test_count_flag_adds_total_quantity_line(capsys, argv, out):
    assert main(argv) == 0
    assert capsys.readouterr().out == out


@pytest.mark.parametrize(
    ("argv", "out"),
    [
        (["3x100", "--code"], "300.00 RUB\n"),
        (["3x100", "1x49.90", "--discount", "10", "--code"], "314.91 RUB\n"),
        # Разделитель тысяч остаётся неразрывным, обычный пробел — только перед кодом
        (["1x1234.56", "--code"], "1\u00a0234.56 RUB\n"),
        (["3x100", "1x49.90", "--code", "--count"], "349.90 RUB\nпозиций: 4\n"),
    ],
)
def test_code_flag_prints_currency_code_instead_of_sign(capsys, argv, out):
    assert main(argv) == 0
    assert capsys.readouterr().out == out


@pytest.mark.parametrize(
    ("argv", "out"),
    [
        # 5 % от 349,90 = 17,495 → скидка 17,49 (округление скидки вниз до копейки), итог 332,41
        (["3x100", "1x49.90", "--loyal"], "332.41\u00a0₽\n"),
        (["3x100", "1x49.90", "--loyal", "--discount", "3"], "332.41\u00a0₽\n"),
        (["3x100", "1x49.90", "--loyal", "--discount", "10"], "314.91\u00a0₽\n"),
        (["3x100", "1x49.90", "--loyal", "--kop"], "33241\n"),
    ],
)
def test_loyal_flag_applies_larger_discount(capsys, argv, out):
    assert main(argv) == 0
    assert capsys.readouterr().out == out


def test_help_names_loyal_discount(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")  # справка флага — одной строкой
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert (
        "постоянный клиент: скидка 5 %, с --discount — бо́льшая из двух" in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "argv, reason",
    [
        (["0x100"], "количество должно быть положительным"),
        (["3"], "ожидался формат КОЛxЦЕНА, получено '3'"),
        (["3x100", "--discount", "101"], "скидка должна быть от 0 до 100 %"),
        (["3x100", "--discount", "101", "--kop"], "скидка должна быть от 0 до 100 %"),
        (["3x100", "--discount", "-1", "--loyal"], "скидка должна быть от 0 до 100 %"),
        (["3x100", "1xabc", "--count"], "цена должна быть числом, получено 'abc'"),
        # parse_item пропускает количество в 4300 цифр, но int→str длиннее 4300 цифр — отказ
        (["9" * 4300 + "x1", "--kop"], "число слишком большое для вывода"),
        (["9" * 4300 + "x0", "9" * 4300 + "x0", "--count"], "число слишком большое для вывода"),
        (["1x0.005"], "цена точнее копейки: '0.005'"),
        (["3x100", "--code", "--kop"], "argument --kop: not allowed with argument --code"),
        (["3x100", "--kop", "--code"], "argument --code: not allowed with argument --kop"),
        (["3 0x1"], "количество должно быть целым числом, получено '3 0'"),
        (["1xabc"], "цена должна быть числом, получено 'abc'"),
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


def test_version_prints_pyproject_version_and_exits_0(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "10")  # строка не переносится по ширине терминала
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"sandbox {pyproject['project']['version']}\n"
    assert captured.err == ""


def test_without_package_metadata_cli_still_works(capsys, monkeypatch):
    def not_installed(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", not_installed)
    try:
        importlib.reload(sandbox)
        assert sandbox.__version__ == "unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(sandbox)
    assert main(["3x100"]) == 0
    assert capsys.readouterr().out == "300.00\u00a0₽\n"
