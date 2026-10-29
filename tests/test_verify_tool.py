"""Инструмент проверки `tools/verify/tool.py`: ответы JSON, пробный режим, карта функций."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools/verify/tool.py"


def tool(*args: str, app: Path = ROOT) -> tuple[int, dict]:
    res = subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env={"CANON_VERIFY_APP": str(app), "PATH": ""},
        check=False,
    )
    return res.returncode, json.loads(res.stdout)


def test_total_answers_lines_of_app():
    code, out = tool("total", "3x100", "1x49.90", "--discount", "10")
    assert code == 0
    assert out["exit"] == 0
    assert out["lines"] == ["314.91 ₽"]


def test_app_input_error_is_answer_not_failure():
    code, out = tool("total", "1x1")
    assert code == 0
    assert out["exit"] == 2
    assert "минимальная сумма заказа" in out["error"]


def test_dry_run_does_not_run():
    code, out = tool("--dry-run", "total", "3x100")
    assert code == 0
    assert out["dry_run"] is True
    assert out["argv"][-3:] == ["-m", "sandbox", "3x100"]


def test_doctor_and_missing_app(tmp_path):
    assert tool("doctor")[0] == 0
    code, out = tool("doctor", app=tmp_path)
    assert code == 2
    assert out["ok"] is False


def test_functions_from_map():
    code, out = tool("functions")
    assert code == 0
    names = [f["name"] for f in out["functions"]]
    assert "сумма" in names and "минимум заказа" in names
