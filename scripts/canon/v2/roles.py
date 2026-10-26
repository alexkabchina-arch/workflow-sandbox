"""roles — модель и усилие ролей пути v2: здесь и только здесь (#333; спека #329, «Модели и
пределы»).

Без «по умолчанию»: роль, которой нет в таблице, — `KeyError`, а не запуск на чём придётся;
усилие вне `EFFORTS` — ошибка при загрузке модуля, до любого запуска. Роли, которых в
оркестраторе ещё нет (проверка постановки, судья, проверка посылки), берут своё значение отсюда,
когда появятся. В команду запуска уходят сейчас: сессия кода — аргументами `claude` (`args`,
через `session-run`), ревью ошибок — по признаку пути v2 в окружении скрипта `review` (`V2_ENV`):
`claude_run.role` берёт тогда модель и усилие роли вызывающего скрипта из этой таблицы. Признак
выбирает только «таблица или нет» — ни модель, ни усилие окружением не задать. `/verify` идёт
внутри сессии маршрутом — его значение подключит шаг v2, который заменит проверку. Помощник
сессии — агент `docs-scout` личного слоя; как дать его сессиям в песочнице — отдельный тикет.

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
}
HELPER = "docs-scout"  # помощник сессии
V2_ENV = "CANON_V2"  # «1» — скрипт с дочерним `claude -p` идёт ролью пути v2 (`claude_run.role`)

for _name, (_model, _effort) in ROLES.items():
    if _effort not in EFFORTS:
        raise ValueError(f"роль {_name}: усилие {_effort!r} — не из {', '.join(EFFORTS)}")


def args(role: str) -> list[str]:
    """Аргументы `claude` роли: `--model` и `--effort`."""
    model, effort = ROLES[role]
    return ["--model", model, "--effort", effort]
