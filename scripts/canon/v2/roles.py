"""roles — модель и усилие ролей пути v2: здесь и только здесь (#333; спека #329, «Модели и
пределы»).

Без «по умолчанию»: роль, которой нет в таблице, — `KeyError`, а не запуск на чём придётся;
усилие вне `EFFORTS` — ошибка при загрузке модуля, до любого запуска. Роль, которой в
оркестраторе ещё нет (проверка постановки), возьмёт своё значение отсюда, когда появится. В
команду запуска уходят сейчас: сессия кода и проверка посылки (#349, сессия роли `parcel`) —
аргументами `claude` (`args`, через `session-run`; повтор после сбоя инструмента — на `OTHER`,
`v2/limits.py`), ревью ошибок и судья (`review --judge`, #345) — по признаку пути v2 в
окружении скрипта `review` (`V2_ENV`): `claude_run.role` берёт тогда модель и усилие роли
вызывающего скрипта из этой таблицы (судье `review` называет роль `judge` сам). Признак
выбирает только «таблица или нет» — ни модель, ни усилие окружением не задать. `/verify` идёт
внутри сессии маршрутом: скрипт `verify` в сессии пути v2 ставит себе этот признак сам (#351) и
берёт роль `verify`. Помощник
сессии в песочнице — встроенный агент `Explore` (#340): `docs-scout` личного слоя туда не
кладётся; его имя — `HELPER` задания сессии (`v2/task.py`), модель — его собственная.

Старый путь таблицу не применяет: у него модель не меняется.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

OPUS = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")  # `claude --effort`
ROLES = {
    "intake": (OPUS, "medium"),  # проверка постановки
    "session": (OPUS, "medium"),  # сессия кода
    "verify": (OPUS, "medium"),  # `/verify`
    "review": (OPUS, "xhigh"),  # ревью ошибок
    "judge": (OPUS, "xhigh"),  # судья
    "parcel": (OPUS, "xhigh"),  # проверка посылки
    "upkeep": (OPUS, "medium"),  # обслуживание инструмента проверки (#353, `v2/upkeep.py`)
}
OTHER = "claude-sonnet-5-5"  # повтор роли после сбоя инструмента — на другой модели (#349)
V2_ENV = "CANON_V2"  # «1» — скрипт с дочерним `claude -p` идёт ролью пути v2 (`claude_run.role`)

for _name, (_model, _effort) in ROLES.items():
    if _effort not in EFFORTS:
        raise ValueError(f"роль {_name}: усилие {_effort!r} — не из {', '.join(EFFORTS)}")


def args(role: str, other: bool = False) -> list[str]:
    """Аргументы `claude` роли: `--model` и `--effort`; `other` — повтор после сбоя инструмента
    на другой модели (`OTHER`) с тем же усилием (`v2/limits.py`, #349)."""
    model, effort = ROLES[role]
    return ["--model", OTHER if other else model, "--effort", effort]
