"""Инструмент проверки приложения для `/verify` пути v2 воркфлоу (#351).

    python3 tools/verify/tool.py [--dry-run] <команда> [аргументы]

Команды — функции карты `map.md` рядом:

- `doctor` — приложение запускается: `python -m sandbox --version` в проверяемой копии;
- `functions` — карта функций: какие есть и как до каждой добраться;
- `total <позиции…> [флаги CLI]` — сумма заказа вызовом CLI с этими аргументами как есть.

Проверяемая копия — каталог из `CANON_VERIFY_APP` (его ставит `verify`), иначе текущий; Python
приложения — её `.venv/bin/python`, иначе тот, что запустил инструмент. Вывод — одна строка JSON:
`{"ok": true, …}` или `{"ok": false, "error": …}`. Код выхода 0 — команда инструмента
выполнена (ошибка ввода приложения — тоже ответ: `exit` и `error` в JSON); 1 — `doctor` красный;
2 — ошибка инструмента (нет копии, неизвестная команда). `--dry-run` — пробный режим: что было
бы запущено, без запуска; разрушающих команд у этого приложения нет (оно ничего не пишет).
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
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*`([^`]+)`\s*\|\s*$")


def say(data: dict, code: int = 0) -> int:
    print(json.dumps(data, ensure_ascii=False))
    return code


def app() -> Path:
    return Path(os.environ.get("CANON_VERIFY_APP") or os.getcwd()).resolve()


def python(root: Path) -> str:
    venv = root / ".venv/bin/python"
    return str(venv) if venv.exists() else sys.executable


def run(root: Path, args: list[str], dry: bool) -> dict:
    argv = [python(root), "-m", "sandbox", *args]
    if dry:
        return {"ok": True, "dry_run": True, "argv": argv, "cwd": str(root)}
    try:
        res = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "argv": argv, "error": f"приложение не запустилось: {exc}"}
    out = {"ok": True, "argv": argv, "exit": res.returncode, "lines": res.stdout.splitlines()}
    if res.returncode != 0:
        out["error"] = res.stderr.strip()
    return out


def functions() -> list[dict]:
    """Строки таблицы карты: функция, что делает, как вызвать."""
    rows = []
    for line in (HERE / "map.md").read_text(encoding="utf-8").splitlines():
        if found := ROW.match(line):
            rows.append({"name": found[1], "what": found[2], "how": found[3]})
    return rows


def main(argv: list[str]) -> int:
    dry = bool(argv) and argv[0] == "--dry-run"
    if dry:
        argv = argv[1:]
    if not argv:
        return say({"ok": False, "error": "нужна команда: doctor, functions, total"}, 2)
    command, rest = argv[0], argv[1:]
    root = app()
    if command == "functions":
        return say({"ok": True, "functions": functions()})
    if not (root / "sandbox/__main__.py").is_file():
        return say({"ok": False, "error": f"в {root} нет приложения (sandbox/__main__.py)"}, 2)
    if command == "doctor":
        out = run(root, ["--version"], dry)
        healthy = out.get("dry_run") or (out["ok"] and out.get("exit") == 0)
        return say({**out, "ok": bool(healthy)}, 0 if healthy else 1)
    if command == "total":
        if not rest:
            return say({"ok": False, "error": "total: нужны позиции, например 3x100"}, 2)
        return say(run(root, rest, dry))
    return say({"ok": False, "error": f"неизвестная команда {command!r}"}, 2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
