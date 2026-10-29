"""limits — пределы кругов тикета пути v2, проверка посылки и сбой роли (#349; спека #329,
«Возвраты, сбои, команды, пределы»; решение `docs/decisions/2026-10-23-v2-loop-limits.md`).

**Пределы по виду круга** — таблица `LIMITS`: числа, которые держит оркестратор, здесь и только
здесь (пересматриваются по метрикам пробы правкой таблицы):

- `checks` — красные проверки в песочнице и в CI, общий счётчик возвратов (`returns`
  оркестратора; возвраты исправлений после ревью, `v2/fixes.py`, идут в тот же счёт, но
  проверки посылки не дают);
- `conflicts` — конфликт догона, `clean` — догон без конфликта подряд (дальше остальные слияния
  держатся, `v2/catchup.py`);
- `short` — короткое ревью (`v2/fixes.py`; `agent:go` после превышения принимает версию).

Числа, которые держит не таблица (`FIXED`, для сверки со спекой): FAIL `/verify` — 2, свой
предел маршрута сессии (`ticket_ship.LIMIT`); здесь — что после его «стопа» «FAIL N-й раз»;
спор — 1 на находку: решение судьи окончательное (`v2/fixes.py`).

**Третий круг починки** (`checks`, `verify`) — проверка посылки: свежая сессия роли `parcel`
(`v2/roles.py`, Opus xhigh) с заданием `PARCEL_TASK` — «две починки на посылке X не помогли —
сначала проверь X». Возвратом она не считается, у вида она одна на тикет; не помогло —
остановка с рекомендацией простыми словами (`ADVICE`).

**`agent:go` после остановки по пределу** даёт ещё один круг этого вида (`grant`: `extra`), после
остановки по сбою роли — снова повторы (`failures` с нуля);
`agent:retry` — тикет с нуля, счётчики и запас — тоже (`fresh` оркестратора).

**Сбой роли** (сессии кода и проверки посылки) — по виду (`failure`), возвратом не считается:
упёрлась в предел — повтор с меньшим объёмом; обрыв сети — повтор как есть; сбой инструмента —
повтор на другой модели (`roles.OTHER`); неизвестное — один повтор; после `RETRIES` повторов —
остановка с кодом вида (`FAILURE_CODES`). Удачный запуск («код готов», вопрос, «стоп») счёт
сбоев обнуляет.

Состояние — `t["limits"]`: `extra` (круги сверх предела от `agent:go`), `parcel` (виды, где
проверка посылки была), `hit` (остановка по пределу: вид и причина — для `agent:go` и совета
отчёта), `failures` (повторы по сбою роли подряд: виды), `task` (задание оркестратора до первого
повтора), `next` (назначенный запуск: роль, модель, что засчитать, когда он начат).

Удалить — по списку раздела «Пределы кругов, проверка посылки и сбой роли» в
`docs/orchestrator.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import re

LIMITS = {
    "checks": 2,  # красные проверки в песочнице и в CI — общий счётчик
    "conflicts": 2,  # конфликт догона
    "clean": 3,  # догон без конфликта подряд
    "short": 1,  # короткое ревью
}
FIXED = {"verify": 2, "dispute": 1}  # держат маршрут и судья — не эта таблица
PARCEL_KINDS = ("checks", "verify")  # виды починки: третий круг — проверка посылки
NAMES = {
    "checks": "красные проверки (в песочнице и CI)",
    "verify": "FAIL `/verify`",
    "conflicts": "конфликт догона",
}
CODES = {"checks": "предел-проверок", "verify": "предел-verify", "conflicts": "конфликт-догона"}
VERIFY_STOP = re.compile(r"^/verify: FAIL \d+-й раз$")  # остановка маршрута по своему пределу
PARCEL_TASK = """Оркестратор: третий круг починки тикета #{n} — проверка посылки. Две починки
не помогли: {what}. Обе стояли на одной посылке X — на том, что они считали причиной и верным
(диагноз, допущение о коде, тесте, среде или критерии). Сначала проверь X фактом, а не
рассуждением: запусти, прочитай, сравни; не повторяй третий раз ту же починку. Посылка неверна —
чини от верной; верна — ищи, чего обе починки не видели. Что было X и что показала проверка —
в отчёт передачи. Починить нельзя — «стоп» маршрута с причиной простыми словами.

{task}"""
ADVICE = (
    "починки и третий круг — проверка посылки на сильной модели — не помогли: по коду дальше "
    "без вас не продвинуться. Посмотрите, верна ли постановка тикета: критерий, который нельзя "
    "выполнить, или неверное допущение в нём. Поправили тикет — `agent:retry`; уверены, что "
    "ещё одна попытка поможет, — `agent:go`. **Риск:** `agent:go` даёт один круг этого вида и, "
    "скорее всего, остановится снова; `agent:retry` теряет сделанное в ветке."
)
CONFLICT_ADVICE = (
    "ветка раз за разом конфликтует с новой основой, и сессия сводила конфликт: соседние тикеты "
    "правят те же места. Дождитесь слияния соседей — `agent:go` (ещё один догон сессией); тикет "
    "надо разрезать иначе — правка тикета и `agent:retry`. **Риск:** `agent:go`, пока соседи "
    "идут, скорее всего, встретит конфликт снова."
)
GO = "ещё один круг этого вида ({name}) сверх предела; после него — снова остановка"

# Сбой роли: вид → действие повтора.
RETRIES = 2  # повторов по сбою роли подряд — затем остановка с кодом
FAILURE_CODES = {
    "limit": "сбой-роли-предел",
    "network": "сбой-роли-сеть",
    "tool": "сбой-роли-инструмент",
    "unknown": "сбой-роли-неизвестно",
}
# Только ошибки API и соединения с ним: фраза агента «Command timed out» или «network access
# denied» — постоянная причина, а не обрыв.
NETWORK = re.compile(
    r"API Error: (Connection (error|refused)|Request timed out|5\d\d)|overloaded_error|ECONNRESET"
    r"|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|socket hang up|fetch failed",
    re.I,
)
TOOL = re.compile(
    r"tool_use|tool_result|invalid_request_error|API Error: 400|tool (call|use) (failed|error)",
    re.I,
)
LIMITED = re.compile(r"prompt is too long|context (window|length)|max_tokens|too many tokens", re.I)
MAX_TURNS = "error_max_turns"
RETRY_TASK = {
    "limit": (
        "Оркестратор: прошлый запуск тикета #{n} упёрся в предел ({why}) — повтор с меньшим "
        "объёмом: делай по одному шагу и коммить сделанное сразу (сообщение одной строкой `-m`), "
        "не читай файлы целиком без нужды, длинный вывод — в файл и хвостом."
    ),
    "network": (
        "Оркестратор: прошлый запуск тикета #{n} оборвался по сети ({why}) — повтор как есть."
    ),
    "tool": (
        "Оркестратор: прошлый запуск тикета #{n} кончился сбоем инструмента ({why}) — повтор на "
        "другой модели."
    ),
    "unknown": (
        "Оркестратор: прошлый запуск тикета #{n} кончился без результата ({why}) — один повтор."
    ),
}


def state(t: dict) -> dict:
    return t.setdefault("limits", {})


def allowed(t: dict, kind: str) -> int:
    """Кругов вида на тикет: предел из таблицы и запас от `agent:go`."""
    return LIMITS[kind] + (state(t).get("extra") or {}).get(kind, 0)


def parcel_due(t: dict, kind: str) -> bool:
    """Третий круг вида — проверка посылки: её у вида ещё не было."""
    return kind in PARCEL_KINDS and kind not in (state(t).get("parcel") or [])


def parcel_done(t: dict, kind: str) -> None:
    state(t).setdefault("parcel", []).append(kind)


def parcel_task(n: int, what: str, task: str) -> str:
    return PARCEL_TASK.format(n=n, what=what, task=task)


def reason(t: dict, kind: str, why: str) -> str:
    """Причина остановки по пределу вида починки: код, что было и что круги кончились."""
    extra = (state(t).get("extra") or {}).get(kind, 0)
    fixes = (LIMITS.get(kind) or FIXED[kind]) + extra
    tried = f"починок: {fixes}" + (f" (после `agent:go`: {extra})" if extra else "")
    tried += " и проверка посылки"
    return f"предел кругов, код `{CODES[kind]}`: {why} — {NAMES[kind]}: {tried} не помогли"


def hit(t: dict, kind: str, why: str) -> None:
    """Остановка по пределу вида `kind` с причиной `why` — для `agent:go` и совета отчёта."""
    state(t)["hit"] = {"kind": kind, "reason": why}


def advice(t: dict, why: str) -> tuple[str, str] | None:
    """`agent:go` и «Рекомендация и риск» отчёта об остановке по пределу (та же причина)."""
    found = state(t).get("hit") or {}
    if found.get("reason") != why:
        return None
    kind = found["kind"]
    return GO.format(name=NAMES[kind]), CONFLICT_ADVICE if kind == "conflicts" else ADVICE


def grant(t: dict) -> str | None:
    """`agent:go` на остановке: сбои роли подряд — с нуля (после остановки по ним — снова
    повторы); по пределу — ещё один круг этого вида. Итог — вид или None."""
    state(t).pop("failures", None)
    found = state(t).pop("hit", None)
    if not found:
        return None
    extra = state(t).setdefault("extra", {})
    extra[found["kind"]] = extra.get(found["kind"], 0) + 1
    return found["kind"]


def verify_over(stop: dict | None) -> bool:
    """Маршрут остановился по своему пределу FAIL `/verify`."""
    stop = stop if isinstance(stop, dict) else {}
    return stop.get("step") == "verify" and bool(VERIFY_STOP.match(str(stop.get("reason") or "")))


# --- сбой роли ---


def failure(subtype: str | None, result: str) -> str:
    """Вид сбоя роли по итогу запуска: `limit` — упёрлась в предел (ходов, контекста; бюджет
    запуска оркестратор называет сам), `network` — обрыв соединения или перегрузка API, `tool` —
    сбой инструмента или запроса к API, `unknown` — прочее."""
    if subtype == MAX_TURNS or LIMITED.search(result or ""):
        return "limit"
    if TOOL.search(result or ""):
        return "tool"
    if NETWORK.search(result or ""):
        return "network"
    return "unknown"


def retry(t: dict, kind: str) -> bool:
    """Повтор по сбою роли вида `kind` — можно: повторов подряд меньше `RETRIES`, а неизвестное —
    не больше одного. Повтор засчитывает вызывающий, когда запуск начат (`retried`)."""
    done = state(t).get("failures") or []
    return len(done) < RETRIES and not (kind == "unknown" and "unknown" in done)


def retried(t: dict, kind: str) -> None:
    state(t).setdefault("failures", []).append(kind)


def succeeded(t: dict) -> None:
    """Запуск кончился делом («код готов», вопрос, «стоп») — сбои подряд с нуля, задание до
    повторов забыто. Старый путь состояния не получает."""
    if isinstance(t.get("limits"), dict):
        t["limits"].pop("failures", None)
        t["limits"].pop("task", None)


def relaunch(t: dict) -> dict:
    """Роль и модель запуска, назначенного после кончившегося (`next`): аргументы `launch`."""
    how = (t.get("limits") or {}).get("next") or {}
    return {"role": how.get("role") or "session", "other": bool(how.get("other"))}


def started(t: dict) -> None:
    """Назначенный запуск начат: засчитать, что он такое, — проверку посылки вида или повтор по
    сбою роли вида."""
    how = (t.get("limits") or {}).pop("next", None) or {}
    what, kind = (how.get("count") or [None, None])[:2]
    if what == "parcel":
        parcel_done(t, kind)
    elif what == "failure":
        retried(t, kind)
    elif what == "return":  # круг `agent:go` после предела проверок — возврат
        t["returns"] = t.get("returns", 0) + 1


def forget(t: dict) -> None:
    """Назначенный запуск не состоится (worktree нет — новая сессия без задания): не засчитывать."""
    (t.get("limits") or {}).pop("next", None)


def retry_task(n: int, kind: str, why: str, task: str) -> str:
    return f"{RETRY_TASK[kind].format(n=n, why=why)} {task}"


def failure_reason(t: dict, kind: str, why: str) -> str:
    done = state(t).get("failures") or []
    tried = f"повторов после сбоя: {len(done)} ({', '.join(done)})"
    return f"сбой роли, код `{FAILURE_CODES[kind]}`: {why} — {tried}"
