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

**Пределы на роль** (#356) — таблица `ROLE_LIMITS`: время (секунды), ходы и USD одного запуска
роли; числа — 90-й процентиль попытки 2, где её метрики есть (`SOURCES`), у прочих ролей и
величин — аварийные потолки `EMERGENCY` (2 ч и 30 USD на запуск; ходов — без предела). Числа —
настройка: правка таблицы, кода они не меняют. Упор роли в свой предел — не повтор, а стоп и
вопрос владельцу (`ROLE_CODE`): `agent:go` — превышение этого запуска принято, а следующий
запуск роли — один раз с потолком запуска (`raised`); `agent:retry` — тикет с нуля. Упор в
остаток предела тикета (60 USD) — остановка, как у старого пути.

**Условие 8 допуска** (`over`): деньги и время в пределах — расход тикета не больше предела
тикета, каждый запуск роли попытки по журналу (`v2/journal.py`), начатый с пределом роли
(`limited`), — не больше пределов своей роли, `/verify` — по расходу маршрута попытки;
превышение, принятое владельцем (`agent:go` после стопа по пределу роли или после отказа
условия 8), — не отказ.

Удалить — по списку раздела «Пределы кругов, проверка посылки и сбой роли» и «Пределы на роль и
метрики тикета» в `docs/orchestrator.md` канона.
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


# --- пределы на роль (#356) ---

EMERGENCY = {"seconds": 2 * 3600, "turns": None, "usd": 30.0}  # аварийные потолки запуска
# 90-й процентиль попытки 2 (ozon #1139–#1169, старый путь; `docs/control-runs/2026-10-30-role-
# limits.md`): сессия — по запуску, ревью и `/verify` — по тикету (их запуски не разделены).
# Нет числа — аварийный потолок этой величины.
ROLE_LIMITS = {
    "session": {"seconds": 1230, "turns": 45, "usd": 8.5},
    "review": {"usd": 4.2},
    "verify": {"usd": 6.0},
}
SOURCES = {
    "session": "90-й процентиль попытки 2 по 56 запускам",
    "review": "90-й процентиль попытки 2 по 17 тикетам",
    "verify": "90-й процентиль попытки 2 по 17 тикетам",
}
EMERGENCY_SOURCE = "аварийный потолок: метрик попытки 2 по роли нет"
ROLE_CODE = "предел-роли"
QUANTITIES = {"seconds": "время", "turns": "ходы", "usd": "USD"}
ROLE_GO = (
    "снова запуск этой роли (сессия — новая, на той же ветке с отчётом передачи) с аварийным "
    "потолком вместо предела роли — один раз; превышение этого запуска принято"
)
# Предел времени — по часам такта: в них ещё `sbx exec`, worktree и хуки, которых нет в
# `duration_ms` итога, из которого выведено число.
CLOCK_SLACK = 120
ROLE_ADVICE = (
    "запуск роли упёрся в свой предел — 90-й процентиль прошлых тикетов: такая работа дороже или "
    "дольше 9 из 10. Посмотрите отчёт передачи и дифф в PR: работа идёт и осталось немного — "
    "`agent:go`; тикет велик или сессия ходит по кругу — разрежьте тикет и `agent:retry`. "
    "**Риск:** `agent:go` тратит ещё до предела запуска; `agent:retry` теряет сделанное в ветке."
)
OVER_CODE = "предел-превышен"


def role_limit(role: str) -> dict:
    """Пределы роли: число таблицы, иначе аварийный потолок; `source` — откуда числа."""
    own = ROLE_LIMITS.get(role) or {}
    found = {k: own.get(k, v) for k, v in EMERGENCY.items()}
    return {**found, "source": SOURCES.get(role) or EMERGENCY_SOURCE}


def role_budget(role: str, run_limit: float, left: float, t: dict | None = None) -> tuple[str, str]:
    """`--max-budget-usd` запуска роли и что его дало: `role` — предел роли (или потолок
    запуска), `raised` — потолок запуска после `agent:go` на стопе по пределу этой роли,
    `ticket` — остаток предела тикета."""
    raised = role in (((t or {}).get("limits") or {}).get("raised") or [])  # только чтение
    cap = run_limit if raised else min(role_limit(role)["usd"], run_limit)
    if left < cap:
        return f"{left:.2f}", "ticket"
    return f"{cap:.2f}", "raised" if raised else "role"


def spend_raise(t: dict, role: str, key: list) -> None:
    """Запуск с поднятым пределом начат: подъём израсходован, его превышение принято."""
    raised = (t.get("limits") or {}).get("raised") or []
    if role in raised:
        raised.remove(role)
        accept(t, [key])


def turns_args(role: str) -> list[str]:
    """`--max-turns` роли, если у неё есть предел ходов."""
    turns = role_limit(role)["turns"]
    return ["--max-turns", str(turns)] if turns else []


def role_reason(role: str, what: str, why: str) -> str:
    """Причина стопа по пределу роли: код, величина, предел и откуда он."""
    limit = role_limit(role)
    return (
        f"предел роли, код `{ROLE_CODE}`: запуск роли «{role}» упёрся в предел — {what} "
        f"({limit['source']}): {why}"
    )


def role_hit(t: dict, reason: str, key: list) -> None:
    """Стоп по пределу роли: `agent:go` после него примет превышение запуска `key` и поднимет
    предел следующего запуска этой роли до потолка запуска."""
    state(t)["hit"] = {"kind": "role", "reason": reason, "keys": [key]}


def run_key(event: dict) -> list:
    """Запуск роли в журнале: роль и начало."""
    return [event.get("role"), event.get("started")]


def overruns(events: list[dict]) -> list[tuple[list, str]]:
    """Запуски журнала сверх пределов своей роли: `(ключ, что превышено)`. Только запуски,
    начатые с пределом роли (`limited`): запуск до #356 шёл по старым правилам. Оценка расхода
    (`estimated`) — это бюджет запуска, а не трата: не превышение."""
    found = []
    for e in events:
        if not e.get("limited"):
            continue
        limit = role_limit(e.get("role") or "")
        said = []
        for key, name in QUANTITIES.items():
            value = e.get(key)
            if key == "usd" and e.get("estimated"):
                continue
            if isinstance(value, (int, float)) and limit[key] is not None and value > limit[key]:
                said.append(f"{name} {value:g} > {limit[key]:g}")
        if said:
            found.append((run_key(e), f"«{e.get('role')}» {', '.join(said)}"))
    return found


def over(
    t: dict, events: list[dict], spent: float, ticket_limit: float, route: dict | None = None
) -> tuple[str, list]:
    """Условие 8: превышения, не принятые владельцем, — `(почему, ключи)`; нет — `("", [])`.
    `route` — расход дочерних прогонов маршрута попытки: `/verify` идёт внутри сессии, в журнал
    не пишется — его предел (число на тикет) сверяется с этим расходом."""
    accepted = state(t).get("accepted") or []
    said, keys = [], []
    if spent > ticket_limit and not state(t).get("ticket_ok"):
        said.append(f"расход тикета {spent:.2f} USD > предела тикета {ticket_limit:g} USD")
        keys.append(["ticket", None])
    verify, limit = (route or {}).get("verify") or 0, role_limit("verify")["usd"]
    if verify > limit and ["verify", None] not in accepted:
        said.append(f"«verify» USD {verify:g} > {limit:g} на попытку")
        keys.append(["verify", None])
    for key, what in overruns(events):
        if key not in accepted:
            said.append(what)
            keys.append(key)
    return "; ".join(said), keys


def accept(t: dict, keys: list) -> None:
    """Превышения `keys` приняты владельцем (`agent:go`): условие 8 их больше не считает."""
    accepted = state(t).setdefault("accepted", [])
    for key in keys:
        if key == ["ticket", None]:
            state(t)["ticket_ok"] = True
        elif key not in accepted:
            accepted.append(key)


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
    if kind == "role":
        return ROLE_GO, ROLE_ADVICE
    return GO.format(name=NAMES[kind]), CONFLICT_ADVICE if kind == "conflicts" else ADVICE


def grant(t: dict) -> str | None:
    """`agent:go` на остановке: сбои роли подряд — с нуля (после остановки по ним — снова
    повторы); по пределу — ещё один круг этого вида; по пределу роли — превышение запуска
    принято (#356). Итог — вид или None."""
    state(t).pop("failures", None)
    found = state(t).pop("hit", None)
    if not found:
        return None
    if found["kind"] == "role":
        accept(t, found.get("keys") or [])
        raised = state(t).setdefault("raised", [])
        for role, _ in found.get("keys") or []:
            if role not in raised:
                raised.append(role)
        return "role"
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
    state(t)["retried"] = state(t).get("retried", 0) + 1  # всего повторов — в метрики (#356)


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
