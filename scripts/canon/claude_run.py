"""claude_run — общее для скриптов канона, запускающих дочерний `claude -p` (`review`, `verify`).

Дочерний процесс — без меток сессии тикета (иначе Stop-хук `stop-check` держал бы его), на
закреплённой модели, с аргументами списком (без оболочки) и пустым stdin. Результат
`--output-format json` сохраняется в `<отчёт.json>`, текст `result` — рядом в `<отчёт>.md`.
Запуск не состоялся (сбой `claude`, выход по бюджету, ошибка, пустой `modelUsage` или `result`,
не та модель) — `ChildError` с причиной.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

MODEL = "claude-opus-5-5"
SESSION_VARS = {"CANON_SESSION", "CANON_ENFORCE_STOP"}
DEFAULT_BASE = "origin/main"
DEFAULT_BUDGET_USD = "10"


class ChildError(Exception):
    """Дочерний `claude` не дал полного результата (код выхода 1 у вызывающего скрипта)."""


class UsageError(Exception):
    """Ошибка аргументов или git (код выхода 2 у вызывающего скрипта)."""


def report_path(value: str) -> Path:
    path = Path(value)
    if path.suffix != ".json":
        raise argparse.ArgumentTypeError(f"нужен путь .json (рядом пишется .md): {value}")
    return path


def budget_usd(value: str) -> str:
    """Предел проверяется до запуска: после оплаченного прогона падать на разборе поздно."""
    try:
        ok = float(value) > 0
    except ValueError:
        ok = False
    if not ok:
        raise argparse.ArgumentTypeError(f"нужно положительное число USD через точку: {value}")
    return value


def parser(prog: str, description: str) -> argparse.ArgumentParser:
    """Общие аргументы: `<отчёт.json> [--base] [--max-budget-usd]`."""
    result = argparse.ArgumentParser(prog=prog, description=description)
    result.add_argument("report", type=report_path, help="файл сырого результата (.json); рядом — .md")
    result.add_argument(
        "--base", default=DEFAULT_BASE, help=f"база диффа (по умолчанию {DEFAULT_BASE})"
    )
    result.add_argument(
        "--max-budget-usd",
        type=budget_usd,
        default=DEFAULT_BUDGET_USD,
        help=f"предел объёма в USD по прайсу API (по умолчанию {DEFAULT_BUDGET_USD})",
    )
    return result


def child_env() -> dict:
    """Окружение без меток сессии тикета: Stop-хук не должен держать дочерний процесс."""
    return {k: v for k, v in os.environ.items() if k not in SESSION_VARS}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_claude(args: list[str], report: Path, budget: str) -> dict:
    """Запускает `claude -p --model MODEL --max-budget-usd <budget> --output-format json <args>`.

    Сохраняет сырой результат в `report`, текст `result` — в `report.with_suffix(".md")`;
    возвращает разобранный JSON. Не состоялось — `ChildError`.
    """
    command = [
        "claude", "-p", "--model", MODEL, "--max-budget-usd", budget,
        "--output-format", "json", *args,
    ]
    # Итог прошлого прогона не должен пережить неудачный новый.
    report.with_suffix(".md").unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=child_env(),
        )
    except FileNotFoundError:
        raise ChildError("не найден `claude` на PATH") from None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout).strip()[-2000:]
        raise ChildError(f"claude вышел с кодом {proc.returncode} без JSON-результата: {tail}")
    if not isinstance(data, dict):
        raise ChildError(f"claude вернул не объект JSON (код {proc.returncode})")

    report.parent.mkdir(parents=True, exist_ok=True)
    write_json(report, data)
    cost = data.get("total_cost_usd", "?")
    subtype = data.get("subtype")
    if subtype == "error_max_budget_usd":
        raise ChildError(
            f"упёрлось в предел --max-budget-usd {budget} (потрачено {cost} USD, "
            "subtype error_max_budget_usd): результат неполный, итога нет. "
            f"Сырой результат — {report}. "
            "Объём слишком велик для предела: разбить ветку или поднять предел решением владельца."
        )
    if subtype != "success" or data.get("is_error"):
        raise ChildError(
            f"завершилось ошибкой (subtype {subtype}, is_error {data.get('is_error')}, "
            f"код {proc.returncode}); сырой результат — {report}"
        )
    text = data.get("result") or ""
    models = sorted(data.get("modelUsage") or {})
    if not models:
        # Сбой до модели (например, протухшая авторизация) приходит как subtype success
        # с пустым modelUsage и текстом ошибки в result.
        raise ChildError(
            f"не дошло до модели (modelUsage пуст): {text.strip()[:500] or 'без текста'}; "
            f"сырой результат — {report}"
        )
    if MODEL not in models:
        raise ChildError(
            f"шло не на {MODEL}: modelUsage — {', '.join(models)}; сырой результат — {report}"
        )
    if not text.strip():
        raise ChildError(f"пустой текст результата; сырой результат — {report}")
    report.with_suffix(".md").write_text(
        text if text.endswith("\n") else text + "\n", encoding="utf-8"
    )
    return data
