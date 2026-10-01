"""claude_run — общее для скриптов канона, запускающих дочерний `claude -p` (`review`, `verify`).

Дочерний процесс — без меток сессии тикета (иначе Stop-хук `stop-check` держал бы его), на
закреплённой модели, с аргументами списком (без оболочки) и пустым stdin. Результат
`--output-format json` сохраняется в `<отчёт.json>`, текст `result` — рядом в `<отчёт>.md`;
со схемой (`--json-schema`) ответ — в поле `structured_output`, а `.md` пишет вызывающий из его
полей. Запуск не состоялся (сбой `claude`, выход по бюджету, ошибка, пустой `modelUsage`, не та
модель; без схемы — пустой `result`, со схемой — нет `structured_output`) — `ChildError` с
причиной, а непустой `result` такого прогона — в `<отчёт>.raw.md` для разбора.

`<отчёт>.md` бывает только у состоявшегося прогона. Перед новым запуском он и его `.json`
переносятся в `<отчёт>.prev.md` и `.prev.json`: под основным именем не остаётся старый итог, а
оплаченный отчёт не теряется, если новый прогон упадёт. Следы упавшего прогона (`.json`,
`.raw.md`) новый запуск удаляет: его стоимость остаётся в транскрипте дочернего `claude`.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
from pathlib import Path

MODEL = "claude-opus-5-5"
SESSION_VARS = {"CANON_SESSION", "CANON_ENFORCE_STOP"}
DEFAULT_BASE = "origin/main"
# Ревью Opus xhigh в разведке workflow#2 стоили 0,82–3,67 USD (в среднем 1,89); уточнить на пилоте.
DEFAULT_BUDGET_USD = "10"
# Как пишет предел человек: цифры и точка; без `inf`, экспоненты и `_`, которые съел бы float().
BUDGET_FORMAT = re.compile(r"[0-9]+(?:\.[0-9]+)?")


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
    ok = BUDGET_FORMAT.fullmatch(value) is not None and 0 < float(value) < math.inf
    if not ok:
        raise argparse.ArgumentTypeError(f"нужно положительное число USD через точку: {value}")
    return value


def parser(prog: str, description: str) -> argparse.ArgumentParser:
    """Общие аргументы: `<отчёт.json> [--base] [--max-budget-usd]`."""
    result = argparse.ArgumentParser(prog=prog, description=description)
    result.add_argument(
        "report", type=report_path, help="файл сырого результата (.json); рядом — .md"
    )
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


def write_text(path: Path, text: str) -> None:
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def sibling(report: Path, suffix: str) -> Path:
    """`<отчёт><suffix>`: `.prev.md`, `.raw.md` и т. п. рядом с `<отчёт>.json`."""
    return report.with_name(report.stem + suffix)


def set_aside(report: Path) -> None:
    """Готовый отчёт прошлого прогона — в `.prev`, следы упавшего — удалить."""
    findings = report.with_suffix(".md")
    if findings.exists():
        findings.replace(sibling(report, ".prev.md"))
        if report.exists():
            report.replace(sibling(report, ".prev.json"))
    report.unlink(missing_ok=True)
    sibling(report, ".raw.md").unlink(missing_ok=True)


def budget_verdict(cost, budget: str, what: str) -> dict:
    """Перерасход по итоговой стоимости: предупреждение и признак ready-for-human (вдвое и больше).

    `what` — начало предупреждения: «ревью стоило», «проверка стоила».
    """
    limit = float(budget)
    known = isinstance(cost, (int, float))
    over = known and cost > limit
    return {
        "cost_usd": cost if known else None,
        "budget_usd": limit,
        "over_budget": over,
        "ready_for_human": known and cost >= 2 * limit,
        "warning": f"{what} {cost:.2f} USD при пределе {budget} USD" if over else None,
    }


def run_claude(args: list[str], report: Path, budget: str, schema: dict | None = None) -> dict:
    """Запускает `claude -p --model MODEL --max-budget-usd <budget> --output-format json <args>`.

    Сохраняет сырой результат в `report`; возвращает разобранный JSON. Без схемы текст `result`
    пишется в `report.with_suffix(".md")`. Со схемой — `--json-schema`, ответ в
    `structured_output` (объект), `.md` пишет вызывающий. Не состоялось — `ChildError`, непустой
    `result` — в `<отчёт>.raw.md`.
    """
    schema_args = (
        [] if schema is None else ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    )
    command = [
        "claude",
        "-p",
        "--model",
        MODEL,
        "--max-budget-usd",
        budget,
        "--output-format",
        "json",
        *schema_args,
        *args,
    ]
    set_aside(report)
    try:
        proc = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(),
        )
    except FileNotFoundError:
        raise ChildError("не найден `claude` на PATH") from None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout).strip()[-2000:]
        raise ChildError(
            f"claude вышел с кодом {proc.returncode} без JSON-результата: {tail}"
        ) from None
    if not isinstance(data, dict):
        raise ChildError(f"claude вернул не объект JSON (код {proc.returncode})")

    report.parent.mkdir(parents=True, exist_ok=True)
    write_json(report, data)
    try:
        return check_result(data, report, budget, schema, proc.returncode)
    except ChildError as exc:
        text = data.get("result")
        if isinstance(text, str) and text.strip():
            write_text(sibling(report, ".raw.md"), text)
            raise ChildError(f"{exc}; сырой ответ — {sibling(report, '.raw.md')}") from None
        raise


def check_result(data: dict, report: Path, budget: str, schema: dict | None, code: int) -> dict:
    """Прогон состоялся — `data` (без схемы `.md` записан); иначе `ChildError`."""
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
            f"код {code}); сырой результат — {report}"
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
    if schema is not None:
        if not isinstance(data.get("structured_output"), dict):
            raise ChildError(
                "нет ответа по схеме --json-schema (structured_output) — итог не распознан; "
                f"сырой результат — {report}"
            )
        return data
    if not text.strip():
        raise ChildError(f"пустой текст результата; сырой результат — {report}")
    write_text(report.with_suffix(".md"), text)
    return data
