"""roles — модель и усилие ролей: здесь и только здесь, одна таблица для пути `/start` и пути v2
(#333, #469; спека #329, «Модели и пределы»; решение 30.10 `2026-10-30-models-per-role.md`).

Путь `/start`: исполнитель — роль `session` (координатор запускает сессию тикета с ней явно и
сверяет по данным приложения; сессия, открытая руками, — на ней же, `.claude/canon/process.md`;
в Codex исполнитель — субагент с заданием `$start N`: модель ставит владелец, сверки нет, таблица
его не задаёт, в живом проекте его нет, пока нет защиты Codex), ревью ошибок — `review`,
`/verify` — `verify` (`claude_run.role` по имени скрипта, без признака пути), агент `reviewer`
личного слоя — `reviewer` (его frontmatter сверяет тест). Самопроверка `/code-review` наследует
модель исполнителя, координатор — на усмотрение владельца: их в таблице нет.

Без «по умолчанию»: роль, которой нет в таблице, — `KeyError`, а не запуск на чём придётся;
усилие вне `EFFORTS` — ошибка при загрузке модуля, до любого запуска. В команду запуска уходят
сейчас: сессия кода пути v2 и проверка посылки (#349, сессия роли `parcel`) — аргументами
`claude` (`args`, через `session-run`; повтор после сбоя инструмента — на `OTHER`,
`v2/limits.py`); скрипты с дочерним `claude -p` (проверка постановки `v2/intake.py`, ревью
ошибок и судья `review --judge`, #345, `/verify`, обслуживание) — через `claude_run.role`: модель
и усилие роли вызывающего скрипта из этой таблицы на любом пути (судье `review` называет роль
`judge` сам) — ни модель, ни усилие окружением не задать. Признак `V2_ENV` выбирает только схему
и вход `review` пути v2 (`--ticket`, `--judge`); `verify` узнаёт путь v2 по `CANON_V2_TASK`.
Помощник
сессии в песочнице — встроенный агент `Explore` (#340): `docs-scout` личного слоя туда не
кладётся; его имя — `HELPER` задания сессии (`v2/task.py`), модель — его собственная.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

OPUS = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")  # `claude --effort`
ROLES = {
    "intake": (OPUS, "medium"),  # проверка постановки
    "session": (OPUS, "medium"),  # сессия кода — исполнитель, на `/start` и в v2
    "verify": (OPUS, "medium"),  # `/verify`
    "review": (OPUS, "xhigh"),  # ревью ошибок
    "judge": (OPUS, "xhigh"),  # судья
    "parcel": (OPUS, "xhigh"),  # проверка посылки
    "upkeep": (OPUS, "medium"),  # обслуживание инструмента проверки (#353, `v2/upkeep.py`)
    "reviewer": (OPUS, "high"),  # агент `reviewer` личного слоя (`owner/agents/reviewer.md`)
}
OTHER = "claude-sonnet-5-5"  # повтор роли после сбоя инструмента — на другой модели (#349)
V2_ENV = "CANON_V2"  # «1» — `review` идёт путём v2: схема v2, `--ticket` и `--judge`

for _name, (_model, _effort) in ROLES.items():
    if _effort not in EFFORTS:
        raise ValueError(f"роль {_name}: усилие {_effort!r} — не из {', '.join(EFFORTS)}")


def args(role: str, other: bool = False) -> list[str]:
    """Аргументы `claude` роли: `--model` и `--effort`; `other` — повтор после сбоя инструмента
    на другой модели (`OTHER`) с тем же усилием (`v2/limits.py`, #349)."""
    model, effort = ROLES[role]
    return ["--model", OTHER if other else model, "--effort", effort]
