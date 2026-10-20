"""ticket status — таблица тикетов проекта: шаг маршрута, кого ждём, PR, CI, расход, время на шаге.

    scripts/canon/ticket status

Из любого чекаута проекта. Данные — файлы состояния тикетов (`<общий каталог git>/canon-ticket/`)
и GitHub на момент запроса (`gh pr view`): состояние PR и итог CI — живые, не из файла; красный
сторож `review-guard` — не красный CI: «перезапуск сторожа», «сторож: нужен агент» (перезапуск
не поможет: исчерпаны или прогон не от сторожа канона) или «нет отметки». Слитый тикет
показывается один раз (`MERGED`), дальше скрыт без запросов к GitHub — в конце их число.
Таблица — в чат владельцу как есть; копии на GitHub нет (спека #111, этап B).
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import types
from pathlib import Path

RED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
GUARD = "review-guard"  # сторож отметки ревью: красный — не красный CI (#176)
REVIEW = "review"  # отметка ревью — статус на head
RERUN = "перезапуск сторожа"
STUCK = "сторож: нужен агент"  # перезапуск сторожа не поможет (#204, п. 5)
NEEDS_AGENT = ("красный", "нет отметки", STUCK)
HEADER = ("Тикет", "Шаг", "Ждём", "PR", "Состояние PR", "CI", "Расход", "На шаге")


def ci(rollup: list[dict]) -> str:
    """Итог проверок PR кратко: красный, идёт, перезапуск сторожа, нет отметки, зелёный, —
    (проверок нет).

    Красный сторож `review-guard` — не красный CI, как у `session-start` и `ticket wait` (#190,
    п. 11): при стоящей отметке ревью — сбой GitHub или отметка позже его ожидания, упавший
    прогон перезапускают (`review-guard-rerun`, `ticket wait`), чинить в ветке нечего; без
    отметки — оборвался финиш, её ставит агент (`ticket ship`)."""
    if not rollup:
        return "—"

    def state(c: dict) -> str:
        return (c.get("conclusion") or c.get("state") or "").upper()

    if any(state(c) in RED for c in rollup if c.get("name") != GUARD):
        return "красный"
    states = {state(c) for c in rollup}
    running = {(c.get("status") or "").upper() for c in rollup} - {"COMPLETED", ""}
    if running or states & PENDING or "" in states:
        return "идёт"
    if states & RED:  # красный остался только у сторожа
        marked = any(c.get("context") == REVIEW and state(c) == "SUCCESS" for c in rollup)
        return RERUN if marked else "нет отметки"
    return "зелёный"


_start: types.ModuleType | None = None


def load_session_start() -> types.ModuleType:
    """`session-start` рядом со скриптом — его проверка прогона сторожа; загружается раз."""
    global _start
    if _start is None:
        path = Path(__file__).resolve().parent / "session-start"
        module = types.ModuleType("canon_session_start_for_status")
        module.__file__ = str(path)
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
        _start = module
    return _start


def guard_stuck(rollup: list[dict]) -> bool:
    """Перезапуск красного сторожа не поможет (исчерпаны или прогон не от сторожа канона — джоб
    ветки) — та же проверка, что у `session-start` и `ticket wait` (`guard_run`): нужен агент, а
    не ожидание CI. Сбой `gh` — не «застрял»: статус только
    показывает, решение — у `ticket wait`."""
    start = load_session_start()
    guard = next((c for c in rollup if c.get("name") == GUARD and start.is_red(c)), None)
    if guard is None:
        return False
    try:
        return start.guard_run(guard)[0] == "stuck"
    except start.StartError:
        return False


def pull_request(core, state: dict) -> tuple[str, str, str]:
    """Ссылка, состояние и CI PR тикета; PR ищется по ветке, если маршрут его ещё не записал, —
    кроме PR прошлых маршрутов тикета (`past_prs`): ветка переоткрытого тикета с тем же именем
    нашла бы слитый PR, и тикет снова скрылся бы как слитый (#190, п. 5)."""
    url = state.get("pr")
    try:
        if not url and state.get("branch"):
            fields = ["--state", "all", "--json", "url"]
            past = set(state.get("past_prs") or [])
            found = core.gh_json("pr", "list", "--head", state["branch"], *fields) or []
            found = [pr for pr in found if pr.get("url") not in past]
            url = found[0]["url"] if found else None
        if not url:
            return "—", "—", "—"
        data = core.gh_json("pr", "view", url, "--json", "state,isDraft,statusCheckRollup")
    except core.Failure as exc:
        return url or "—", f"gh: {str(exc)[:60]}", "?"
    pr_state = data.get("state", "?") + (" (черновик)" if data.get("isDraft") else "")
    rollup = data.get("statusCheckRollup") or []
    checks = ci(rollup)
    if checks == RERUN and data.get("state") == "OPEN" and guard_stuck(rollup):
        checks = STUCK
    return url, pr_state, checks


def waiting(state: dict, pr_state: str, checks: str) -> str:
    if pr_state.startswith("MERGED") or pr_state.startswith("CLOSED"):
        return "—"
    if state.get("step") == "done" and pr_state.startswith("OPEN"):
        return "агент" if checks in NEEDS_AGENT else "CI"
    return state.get("waiting") or "агент"


def row(core, state: dict) -> tuple[str, ...]:
    """Строка таблицы тикета по его файлу состояния (столбцы `HEADER`); PR и CI — живые из
    GitHub. Ею же строит «статус» оркестратор (#222)."""
    url, pr_state, checks = pull_request(core, state)
    cost = sum(v for v in (state.get("cost") or {}).values() if isinstance(v, (int, float)))
    step = state.get("step")
    return (
        f"#{state.get('ticket')}",
        core.STEP_NAMES.get(step, step or "—"),
        waiting(state, pr_state, checks),
        url,
        pr_state,
        checks,
        f"{cost:.2f} USD",
        "—" if pr_state.startswith(("MERGED", "CLOSED")) else core.elapsed(state.get("since")),
    )


def table(core, header: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    """Таблица markdown — в чат владельцу как есть."""
    core.say("| " + " | ".join(header) + " |")
    core.say("|" + "---|" * len(header))
    for line in rows:
        core.say("| " + " | ".join(line) + " |")


def main(argv: list[str], core) -> int:
    if argv:
        print("ticket status: аргументов нет", flush=True)
        return 2
    states = core.all_states()
    if not states:
        core.say("ticket status: тикетов с маршрутом нет")
        return 0
    rows, hidden = [], 0
    for state in states:
        if state.get("merged"):  # слитый уже показан раз — дальше без запросов к GitHub
            hidden += 1
            continue
        rows.append(row(core, state))
        if rows[-1][4] == "MERGED" and str(state.get("ticket")).isdigit():
            record = core.State(int(state["ticket"]))
            record["merged"] = True
            record.save()
    table(core, HEADER, rows)
    if hidden:
        core.say(f"\nСлитых тикетов не показано: {hidden}.")
    return 0
