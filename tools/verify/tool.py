"""Инструмент проверки приложения для `/verify` пути v2 воркфлоу (#351).

    python3 tools/verify/tool.py [--dry-run] <команда> [аргументы]

Команды — функции карты `map.md` рядом (канон копирует оба файла из `main` по их путям):

- `doctor` — приложение запускается: `python -m sandbox --version` в проверяемой копии печатает
  «sandbox <версия>»;
- `functions` — карта функций: какие есть и как до каждой добраться;
- `total <позиции…> [флаги CLI]` — сумма заказа вызовом CLI с этими аргументами как есть.

Проверяемая копия — каталог из `CANON_VERIFY_APP` (его ставит `verify`; без неё — ошибка, а не
текущий каталог), Python приложения — только её `.venv/bin/python`: без окружения `doctor`
красный, другого интерпретатора инструмент не берёт. Вывод — одна строка JSON: `{"ok": true, …}`
или `{"ok": false, "error": …}`. Ответ приложения: `exit`, `lines` (stdout по `\\n`),
`lines_escaped` (те же строки с `\\uXXXX` — неразрывный пробел виден), `stderr` (все строки) и
`error` (последняя строка stderr) при ненулевом коде. Код выхода 0 — команда выполнена (ошибка
ввода приложения — тоже ответ); 1 — `doctor` красный; 2 — ошибка инструмента (нет копии или
окружения, приложение не запустилось, неизвестная команда). `--dry-run` в любом месте — пробный
режим: что было бы запущено, без запуска; разрушающих команд у этого приложения нет.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 60
DRY = "--dry-run"
NOT_TOTAL = {"--version", "--help", "-h"}  # ответ такой формы — не итог
ROW = re.compile(r"^\|\s*`?([^`|]+?)`?\s*\|\s*(.+?)\s*\|\s*`?([^`|]+?)`?\s*\|\s*$")
HEADER = ("Функция", "---")
ITEM = re.compile(r"^\s*[+-]?\d+\s*[xX]")  # позиция КОЛxЦЕНА, а не значение флага


def say(data: dict, code: int = 0) -> int:
    print(json.dumps(data, ensure_ascii=False))
    return code


def python(root: Path) -> Path | None:
    venv = root / ".venv/bin/python"
    return venv if venv.is_file() and os.access(venv, os.X_OK) else None


def run(root: Path, args: list[str], dry: bool) -> dict:
    interpreter = python(root)
    if interpreter is None:
        return {"ok": False, "error": f"в {root} нет окружения .venv: приложение не запустить"}
    argv = [str(interpreter), "-m", "sandbox", *args]
    if dry:
        return {"ok": True, "dry_run": True, "argv": argv, "cwd": str(root)}
    try:
        res = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "argv": argv, "error": f"приложение не запустилось: {exc}"}
    lines = res.stdout.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    stderr = res.stderr.split("\n")
    if stderr and stderr[-1] == "":
        stderr.pop()
    out = {
        "ok": True,
        "argv": argv,
        "exit": res.returncode,
        "lines": lines,
        "lines_escaped": [line.encode("unicode_escape").decode("ascii") for line in lines],
        "stderr": stderr,
    }
    if res.returncode != 0:  # причина — последняя строка: у traceback это сама ошибка
        out["error"] = stderr[-1] if stderr else f"код {res.returncode}, stderr пуст"
    return out


def functions() -> list[dict]:
    """Строки таблицы карты: функция, что делает, как вызвать."""
    rows = []
    for line in (HERE / "map.md").read_text(encoding="utf-8").splitlines():
        found = ROW.match(line)
        if found and not found[1].strip().startswith(HEADER):
            rows.append({"name": found[1].strip(), "what": found[2], "how": found[3].strip()})
    return rows


def main(argv: list[str]) -> int:
    dry = DRY in argv
    argv = [a for a in argv if a != DRY]
    if not argv:
        return say({"ok": False, "error": "нужна команда: doctor, functions, total"}, 2)
    command, rest = argv[0], argv[1:]
    if command == "functions":
        try:
            return say({"ok": True, "functions": functions()})
        except OSError as exc:
            return say({"ok": False, "error": f"карты функций нет: {exc}"}, 2)
    if not os.environ.get("CANON_VERIFY_APP"):
        return say({"ok": False, "error": "нет CANON_VERIFY_APP — какую копию проверять"}, 2)
    root = Path(os.environ["CANON_VERIFY_APP"]).resolve()
    if not (root / "sandbox/__main__.py").is_file():
        return say({"ok": False, "error": f"в {root} нет приложения (sandbox/__main__.py)"}, 2)
    if command == "doctor":
        out = run(root, ["--version"], dry)
        healthy = out.get("dry_run") or (
            out["ok"]
            and out.get("exit") == 0
            and out["lines"][:1] != []
            and out["lines"][0].startswith("sandbox ")
        )
        return say({**out, "ok": bool(healthy)}, 0 if healthy else 1)
    if command == "total":
        if not [a for a in rest if ITEM.match(a)] or NOT_TOTAL & set(rest):
            return say(
                {
                    "ok": False,
                    "error": "total: нужны позиции, например 3x100, без --help и --version",
                },
                2,
            )
        out = run(root, rest, dry)
        return say(out, 0 if out["ok"] else 2)
    return say({"ok": False, "error": f"неизвестная команда {command!r}"}, 2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
