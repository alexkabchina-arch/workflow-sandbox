"""ticket status — таблица тикетов проекта: шаг маршрута, кого ждём, PR, CI, расход, время на шаге.

    scripts/canon/ticket status

Из любого чекаута проекта. Данные — файлы состояния тикетов (`<общий каталог git>/canon-ticket/`)
и GitHub на момент запроса (`gh pr view`): состояние PR и итог CI — живые, не из файла. Слитый
тикет показывается один раз (`MERGED`), дальше скрыт без запросов к GitHub — в конце их число.
Таблица — в чат владельцу как есть; копии на GitHub нет (спека #111, этап B).
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

RED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
HEADER = ("Тикет", "Шаг", "Ждём", "PR", "Состояние PR", "CI", "Расход", "На шаге")


def ci(rollup: list[dict]) -> str:
    """Итог проверок PR одним словом: красный, идёт, зелёный, — (проверок нет)."""
    if not rollup:
        return "—"
    states = {(c.get("conclusion") or c.get("state") or "").upper() for c in rollup}
    if states & RED:
        return "красный"
    running = {(c.get("status") or "").upper() for c in rollup} - {"COMPLETED", ""}
    if running or states & PENDING or "" in states:
        return "идёт"
    return "зелёный"


def pull_request(core, state: dict) -> tuple[str, str, str]:
    """Ссылка, состояние и CI PR тикета; PR ищется по ветке, если маршрут его ещё не записал."""
    url = state.get("pr")
    try:
        if not url and state.get("branch"):
            fields = ["--state", "all", "--json", "url"]
            found = core.gh_json("pr", "list", "--head", state["branch"], *fields)
            url = found[0]["url"] if found else None
        if not url:
            return "—", "—", "—"
        data = core.gh_json("pr", "view", url, "--json", "state,isDraft,statusCheckRollup")
    except core.Failure as exc:
        return url or "—", f"gh: {str(exc)[:60]}", "?"
    pr_state = data.get("state", "?") + (" (черновик)" if data.get("isDraft") else "")
    return url, pr_state, ci(data.get("statusCheckRollup") or [])


def waiting(state: dict, pr_state: str, checks: str) -> str:
    if pr_state.startswith("MERGED") or pr_state.startswith("CLOSED"):
        return "—"
    if state.get("step") == "done" and pr_state.startswith("OPEN"):
        return "агент" if checks == "красный" else "CI"
    return state.get("waiting") or "агент"


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
        url, pr_state, checks = pull_request(core, state)
        if pr_state == "MERGED" and str(state.get("ticket")).isdigit():
            record = core.State(int(state["ticket"]))
            record["merged"] = True
            record.save()
        cost = sum(v for v in (state.get("cost") or {}).values() if isinstance(v, (int, float)))
        step = state.get("step")
        rows.append(
            (
                f"#{state.get('ticket')}",
                core.STEP_NAMES.get(step, step or "—"),
                waiting(state, pr_state, checks),
                url,
                pr_state,
                checks,
                f"{cost:.2f} USD",
                core.elapsed(state.get("since")),
            )
        )
    core.say("| " + " | ".join(HEADER) + " |")
    core.say("|" + "---|" * len(HEADER))
    for row in rows:
        core.say("| " + " | ".join(row) + " |")
    if hidden:
        core.say(f"\nСлитых тикетов не показано: {hidden}.")
    return 0
