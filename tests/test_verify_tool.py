"""Инструмент проверки `tools/verify/tool.py`: ответы JSON, пробный режим, карта функций."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools/verify/tool.py"


@pytest.fixture
def app(tmp_path):
    """Проверяемая копия: приложение и окружение `.venv` с интерпретатором тестов."""
    (tmp_path / "sandbox").symlink_to(ROOT / "sandbox")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(sys.executable)
    return tmp_path


def tool(*args: str, app: Path | None = None) -> tuple[int, dict]:
    env = {"PATH": ""}
    if app is not None:
        env["CANON_VERIFY_APP"] = str(app)
    res = subprocess.run(
        [sys.executable, str(TOOL), *args], capture_output=True, text=True, env=env, check=False
    )
    return res.returncode, json.loads(res.stdout)


def test_total_answers_lines_of_app(app):
    code, out = tool("total", "3x100", "1x49.90", "--discount", "10", app=app)
    assert code == 0
    assert out["exit"] == 0
    assert out["lines"] == ["314.91 ₽"]
    assert out["lines_escaped"] == ["314.91\\xa0\\u20bd"]  # неразрывный пробел виден


def test_app_input_error_is_answer_with_whole_stderr(app):
    code, out = tool("total", "1x1", app=app)
    assert code == 0
    assert out["exit"] == 2
    assert out["stderr"] == ["sandbox: error: минимальная сумма заказа — 300.00\u00a0₽"]
    assert out["error"] == out["stderr"][-1]


def test_dry_run_anywhere_does_not_run(app):
    for args in (("--dry-run", "total", "3x100"), ("total", "3x100", "--dry-run")):
        code, out = tool(*args, app=app)
        assert code == 0
        assert out["dry_run"] is True
        assert out["argv"][-3:] == ["-m", "sandbox", "3x100"]


def test_doctor_red_without_venv_app_or_variable(app, tmp_path_factory):
    assert tool("doctor", app=app)[0] == 0
    bare = tmp_path_factory.mktemp("bare")
    (bare / "sandbox").symlink_to(ROOT / "sandbox")
    code, out = tool("doctor", app=bare)  # без окружения — красный, своего Python не берёт
    assert code == 1
    assert "нет окружения .venv" in out["error"]
    assert tool("doctor", app=tmp_path_factory.mktemp("empty"))[0] == 2
    assert tool("doctor")[0] == 2  # без CANON_VERIFY_APP


def test_total_launch_failure_and_not_total_are_tool_errors(app, tmp_path_factory):
    bare = tmp_path_factory.mktemp("bare")
    (bare / "sandbox").symlink_to(ROOT / "sandbox")
    assert tool("total", "3x100", app=bare)[0] == 2
    for args in (("total",), ("total", "--discount", "10"), ("total", "3x100", "--version")):
        assert tool(*args, app=app)[0] == 2
    assert tool("nope", app=app)[0] == 2


def test_every_map_example_reaches_its_function(app):
    code, out = tool("functions")
    assert code == 0
    rows = out["functions"]
    table = [
        line
        for line in (ROOT / "tools/verify/map.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and not line.startswith(("| Функция", "|---"))
    ]
    assert len(rows) == len(table)  # ни одна строка карты не выпала
    for row in rows:
        args = shlex.split(row["how"])[1:]
        code, answer = tool(*args, app=app)
        assert code == 0, row
        expected = 2 if row["name"] == "минимум заказа" else 0
        assert answer.get("exit", 0) == expected, (row, answer)
