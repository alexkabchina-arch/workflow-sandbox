"""ticket start — режим `/start`: старт сессии тикета и задание сделать его без вопросов в чат.

    scripts/canon/ticket start <N>

Из любого чекаута проекта. Владелец запускает тикет одной командой и может отойти: вопросы
копятся в тикете (`ticket ask`), работа кончается тем же `ticket ship`, что и в режиме
`/implement N` с диалогом.

1. Подготовка — `session-start N` рядом со скриптом: уборка, PR автора первыми, тикет, worktree,
   метка `in-progress`. Тикет закрыт или занят — остановка с причиной; сбой git или `gh` — код 1.
2. PR автора требует внимания (красный CI, нет отметки, конфликт) — задание довести его, тикет
   отложен; затем тот же вызов.
3. Иначе — задание: войти в worktree, выполнить `/implement N` Мэтта; согласование, которого
   просят скиллы, — решить самому; вопрос владельцу — только `ticket ask N`; после коммита —
   `ticket ship N`. Маршрут тикета уже идёт (шаг `ship` записан) или вопрос ждёт ответа —
   задание продолжить его.

Как устроено — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json

TASK = """Режим /start: владелец не в чате и вопросов там не ждёт. Сделай тикет по скиллу
`/implement {n}` (Мэтта: тикет, TDD, коммит) — с такими поправками:
- согласовать, которое велят скиллы (/implement, /tdd, /grilling и другие: швы тестов, план,
  имена), — реши сам по тикету, его родительской спеке, ADR и профилю проекта;
- вопрос владельцу — только если без ответа тикет не сделать (требование неясно или
  противоречиво): вопрос на языке поведения продукта, варианты и рекомендацию — в
  `.ticket/ask.md` (инструмент Write), затем вызов `scripts/canon/ticket ask {n}`: вопрос уйдёт
  комментарием в тикет. В чат вопросов не задавать, `AskUserQuestion` не вызывать;
- так же и на шагах `ticket ship`: где задание велит спросить владельца в чате, — `ticket ask`.
Работа закоммичена (сообщение одной строкой `-m`) — конец работы над тикетом, его задания
выполнять до «готово» или «остановка». Вызов ({long}; ревью ошибок идёт 5–8 минут):"""


def enter(plan: dict) -> str:
    """Как войти в worktree плана `session-start`."""
    wt = plan["worktree"]
    line = f"Worktree — {wt} (ветка {plan['branch']}). Войди в него: EnterWorktree с path {wt}"
    if plan.get("needs_click"):
        line += (
            " — вход может ждать клика владельца (worktree вне .claude/worktrees/ или старт не "
            "из главного чекаута). Вход не удался — стоп: владельцу одной строкой «нужен клик "
            "входа в worktree тикета»; без входа маршрут не идёт — `ticket ship` работает только "
            "из worktree тикета"
        )
    return line + "; дальше вся работа и вызовы — из него."


def main(argv: list[str], core) -> int:
    parser = argparse.ArgumentParser(prog="ticket start", description=__doc__.split("\n\n")[0])
    parser.add_argument("ticket", type=int, help="номер тикета")
    n = parser.parse_args(argv).ticket
    st = core.State(n)
    if st["question"]:
        body = (
            f"Вопрос владельцу ждёт ответа: {st['question']['url']}. Владелец ответил и написал "
            "«продолжай» — ответ из чата дословно в `.ticket/reply.md`, затем вызов из worktree "
            "тикета:"
        )
        return core.emit(
            core.STOPPED, f"Тикет #{n}: ждём ответа", body, (f"scripts/canon/ticket ask {n}",)
        )

    res = core.canon("session-start", str(n), "--json")
    if res.returncode == 2:
        reason = res.stderr.strip() or res.stdout.strip()
        body = f"Старт не выполнен — {reason}\nПередай владельцу одной строкой: что мешает старту."
        return core.emit(core.STOPPED, f"Тикет #{n}: старт невозможен", body)
    if res.returncode != 0:
        raise core.Failure(f"session-start: {(res.stderr or res.stdout).strip()}")
    try:
        plan = json.loads(res.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise core.Failure(f"session-start: нет итога JSON: {res.stdout[-200:]}") from None

    if plan["kind"] == "pr":
        body = (
            f"Сначала PR автора: {plan['url']} — тикет #{n} отложен.\n{enter(plan)}\n"
            f"Задание: {plan['prompt']}\nPR доведён — снова старт тикета:"
        )
        return core.emit(
            core.TURN,
            f"PR автора первым: {plan['url']}",
            body,
            (f"scripts/canon/ticket start {n}",),
        )

    st["branch"] = plan["branch"]
    st.save()
    if st.step == "stopped":
        where = (st["stop"] or {}).get("where", "?")
        body = (
            f"{enter(plan)}\nМаршрут остановлен, отчёт об остановке — {where}. Владелец ответил "
            "«продолжай» — продолжить маршрут из worktree тикета:"
        )
        again = f"scripts/canon/ticket ship {n} --again"
        return core.emit(core.STOPPED, f"Тикет #{n}: остановка", body, (again,))
    if st.step is not None:
        name = core.STEP_NAMES.get(st.step, st.step)
        body = (
            f"{enter(plan)}\nМаршрут тикета уже идёт — шаг «{name}». Продолжай его без вопросов "
            f"в чат (вопрос владельцу — `ticket ask {n}`), задания выполнять до «готово» или "
            f"«остановка». Вызов ({core.LONG_CALL}):"
        )
        return core.emit(
            core.TURN, f"Тикет #{n}: продолжение", body, (f"scripts/canon/ticket ship {n}",)
        )
    st["since"], st["waiting"] = core.now(), core.AGENT
    st.save()
    body = f"{enter(plan)}\n\n{TASK.format(n=n, long=core.LONG_CALL)}"
    return core.emit(
        core.TURN, f"Тикет #{n}: режим /start", body, (f"scripts/canon/ticket ship {n}",)
    )
