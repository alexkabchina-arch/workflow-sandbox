"""journal — журнал событий пути v2: строка на каждый запуск роли (#334; спека #329).

Журнал — `events.jsonl` в каталоге состояния оркестратора (`.git/canon-orchestrator/` главного
чекаута): вне песочницы (она видит только свой клон и канон чтением) и вне ветки. Пишет его
оркестратор, когда запуск роли кончился, — только у тикета пути v2; у старого пути записи нет.
Отчётов по журналу пока нет: данные копятся для пределов ролей и сравнения попыток.

Событие — объект JSON в строке:

- `at` — когда записано; `ticket` — номер; `role` — роль из `v2/roles.py`; `model`, `effort` —
  её модель и усилие из той же таблицы;
- `started`, `ended` — часы оркестратора; `seconds` — длительность: `duration_ms` итога
  `claude`, а без него — по часам оркестратора; не измерена (осиротевшая сессия) — `null`;
- `tokens` — `input`, `output`, `cache_read`, `cache_write`: сумма по моделям `modelUsage`
  итога (с субагентами), без токенов в ней — `usage`; итога нет — `null` (неизвестно, а не ноль);
- `usd`, `estimated` — расход: факт итога или оценка худшим случаем (`--max-budget-usd`), когда
  итога нет;
- у возобновлённой сессии `modelUsage` и `total_cost_usd` итога `claude -p --resume` накоплены
  с начала сессии (прогон #334): событие пишет только часть запуска — минус итог прошлого
  запуска той же сессии (`part`);
- `outcome` — `ok`, `error`, `killed` (запуск завершён оркестратором), `timeout`; `code` —
  `subtype` итога `claude` (`success`, `error_max_budget_usd`…), `no_result`, у ревью ещё
  `no_report` (отчёт не прочитан) и `skipped` (ревью пропущено: PR только из документов);
  `exit` — код выхода скрипта роли, где он известен (ревью), иначе `null`: сессия идёт своей
  группой процессов, её код такт не видит;
- у сессии ещё `run` — метка запуска, и `resumed`.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
from pathlib import Path

EVENTS = "events.jsonl"
# Поле события — поле `modelUsage` и поле `usage` итога `claude`.
TOKENS = {
    "input": ("inputTokens", "input_tokens"),
    "output": ("outputTokens", "output_tokens"),
    "cache_read": ("cacheReadInputTokens", "cache_read_input_tokens"),
    "cache_write": ("cacheCreationInputTokens", "cache_creation_input_tokens"),
}


def number(source: dict, key: str) -> int:
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def models(result: dict | None) -> list[dict]:
    """Модели `modelUsage` итога, если в нём есть токены, иначе пусто."""
    found = result.get("modelUsage") if isinstance(result, dict) else None
    found = [m for m in found.values() if isinstance(m, dict)] if isinstance(found, dict) else []
    return found if any(field in m for m in found for field, _ in TOKENS.values()) else []


def tokens(result: dict | None) -> dict | None:
    """Токены итога `claude`: сумма по моделям `modelUsage`, если в ней есть токены, иначе
    `usage`; нет ни того, ни другого — `None`."""
    if not isinstance(result, dict):
        return None
    if found := models(result):
        return {k: sum(number(m, field) for m in found) for k, (field, _) in TOKENS.items()}
    usage = result.get("usage")
    if isinstance(usage, dict) and any(field in usage for _, field in TOKENS.values()):
        return {k: number(usage, field) for k, (_, field) in TOKENS.items()}
    return None


def seconds(result: dict | None, measured: float | None) -> float | None:
    """Длительность запуска: `duration_ms` итога, без него — измеренная оркестратором; не
    измерена — `None`."""
    ms = result.get("duration_ms") if isinstance(result, dict) else None
    if isinstance(ms, (int, float)) and not isinstance(ms, bool):
        return round(ms / 1000, 3)
    return None if measured is None else round(max(measured, 0), 3)


def usd(result: dict | None) -> float | None:
    cost = result.get("total_cost_usd") if isinstance(result, dict) else None
    return cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


def part(result: dict | None, previous: dict | None) -> tuple[float | None, dict | None]:
    """Расход и токены запуска по итогу `result`; `previous` — итог прошлого запуска той же
    сессии: накопленное до него вычитается. Итог не накоплен (расход меньше прошлого) — как
    есть; токены из `usage` (только этот запуск) не вычитаются."""
    cost, used = usd(result), tokens(result)
    before = usd(previous)
    if cost is None or before is None or cost < before:
        return cost, used
    if models(result) and models(previous):
        earlier = tokens(previous)
        used = {k: max(used[k] - earlier[k], 0) for k in used}
    return round(cost - before, 6), used


def event(roles, n: int, role: str, result: dict | None, measured: float | None, **fields) -> dict:
    """Событие запуска роли `role` тикета `n`: модель и усилие — из таблицы `roles`, токены и
    длительность — из итога `result` (без него — `measured`); остальное — `fields` (`tokens` в
    них — вместо токенов итога)."""
    model, effort = roles.ROLES[role]
    head = {"ticket": n, "role": role, "model": model, "effort": effort}
    used = fields.pop("tokens") if "tokens" in fields else tokens(result)
    return {**head, **fields, "seconds": seconds(result, measured), "tokens": used}


def write(directory: Path, record: dict) -> None:
    """Событие — строкой в конец журнала в `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / EVENTS, "a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
