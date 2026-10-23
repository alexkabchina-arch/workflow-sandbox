"""ticket start — режим `/start`: старт сессии тикета и задание сделать его; вопрос — `ticket ask`.

    scripts/canon/ticket start <N>

Из любого чекаута проекта. Владелец запускает тикет одной командой и может отойти: вопросы
копятся в тикете (`ticket ask`), работа кончается тем же `ticket ship`, что и в режиме
`/implement N` с диалогом.

1. Подготовка — `session-start N` рядом со скриптом: уборка, PR автора первыми, тикет, worktree,
   метка `in-progress`. Тикет закрыт, занят или в нём текст посторонних (#196: тикет открыл не
   участник репозитория или есть комментарий не участника, и скрытый — #159) — остановка с
   причиной; сбой git или `gh` — код 1.
2. PR автора требует внимания (красный CI, нет отметки, конфликт) — задание довести его, тикет
   отложен; затем тот же вызов.
3. Иначе — задание: войти в worktree, выполнить `/implement N` Мэтта; согласование, которого
   просят скиллы, — решить самому; вопрос владельцу — только `ticket ask N`; после коммита —
   `ticket ship N`. Маршрут тикета уже идёт (шаг `ship` записан) или вопрос ждёт ответа —
   задание продолжить его. PR маршрута слит (тикет переоткрыт) — маршрут с нуля: прошлый — в
   архив рядом с состоянием, задание — `/implement N`.

Под оркестратором (`CANON_ORCHESTRATOR=1`, #214) `session-start` PR автора не берёт; задание —
то же `/implement N` с самопроверкой `/code-review`, конец — «код готов» или вопрос; с копией
прода в песочнице (`CANON_PROD_COPY`, #307) — что «на проде» значит она, время её снимка и запрос
свежей (`ticket fresh N`). Вопрос ждёт — задание забрать ответ (`ticket ask N`): сессию возобновил
оркестратор после ответа. Маршрут остановлен — задание `ticket ship N --again`: оркестратор
возобновляет его по `agent:go`.
Дефект слитого PR, найденный сессией, — новый тикет с меткой `escaped-defect` и ссылкой на PR
(#219): их считает сводка PRD.

Как устроено — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

TASK = """Режим /start: владелец может быть не у экрана. Сделай тикет по скиллу
`/implement {n}` (Мэтта: тикет, TDD, коммит) — с такими поправками:
- согласовать, которое велят скиллы (/implement, /tdd, /grilling и другие: швы тестов, план,
  имена), — реши сам по тикету, его родительской спеке, ADR и профилю проекта;
- вопрос владельцу — только если без ответа тикет не сделать (требование неясно или
  противоречиво): вопрос на языке поведения продукта, варианты и рекомендацию — в
  `.ticket/ask.md` (инструмент Write), затем вызов `{ask}`: вопрос уйдёт
  комментарием в тикет, текст для чата даст его задание. Вопрос — только через `ticket ask`,
  `AskUserQuestion` не вызывать;
- так же и на шагах `ticket ship`: где задание велит спросить в чате, — `ticket ask`.{extra}
Работа закоммичена (сообщение одной строкой `-m`) — конец работы над тикетом, его задания
выполнять до {until}. Вызов ({long}; ревью ошибок идёт 5–8 минут):"""

# Под оркестратором (#214): чата нет, ревью ошибок и отметку ведёт он — самопроверка исполнителя
# остаётся в сессии.
ORCHESTRATED_TASK = """
- сессия под оркестратором: самопроверку `/code-review` в конце `/implement` не пропускать — Spec
  и Standards чинить до коммита; ревью ошибок, отметку и ожидание мёржа ведёт оркестратор, сессия
  кончается на «код готов» или на вопросе;{ask}
- нашла дефект, который внёс уже слитый PR (не этого тикета), — заведи новый тикет
  (`gh issue create`) с меткой `escaped-defect` и ссылкой на PR, который его внёс, — в брифе
  строка «Источник: #<тикет PR>, слит ДД.ММ» и ссылка на PR: их считает сводка PRD
  оркестратора; в теле — обе строки `**Владения:**` и `**Флаги:**` по образцу
  `.claude/canon/ticket-ownership.md` (без них тикет пойдёт один); свой тикет дальше — как
  обычно;"""
# Копия прода под оркестратором (#307): живого прода и его API в песочнице нет.
PROD_COPY_TASK = """
- живого прода в песочнице нет: «на проде» в критериях тикета — обезличенная
  копия прода в песочнице, снимок от {copy}; нужны данные свежее снимка — вызов `{fresh}`
  ({long}): оркестратор снимет новую копию на Mac (не чаще раза в час) и ответит в том же
  вызове;"""


def enter(plan: dict) -> str:
    """Как войти в worktree плана `session-start`. Что делать, если вход не удался, — всегда: клик
    ждёт и worktree вне `.claude/worktrees/` (`needs_click`), а поведение входа зависит от версии
    Claude Code (#105, прогон #142)."""
    wt = plan["worktree"]
    line = f"Worktree — {wt} (ветка {plan['branch']}). Войди в него: EnterWorktree с path {wt}"
    if plan.get("needs_click"):
        line += " — вход может ждать клика владельца (worktree вне .claude/worktrees/)"
    return (
        f"{line}; дальше вся работа и вызовы — из него. Вход не удался — стоп: владельцу одной "
        "строкой «нужен клик входа в worktree тикета»; без входа маршрут не идёт — `ticket ship` "
        "работает только из worktree тикета."
    )


def merged_pr(core, st) -> str | None:
    """PR маршрута, если он слит: маршрут кончился, его работа в `main` — старт переоткрытого
    тикета начинает новый (#190, п. 5), а не продолжает «готово» или остановку прошлого. Сбой
    `gh` старт не обрывает: маршрут продолжается, а слитый PR заметит `ticket ship`."""
    url = st["pr"]
    if st.step is None or not url:
        return None
    if st["merged"] or st["merged_at"]:  # отметки `ticket status` и `ticket wait`
        return url
    try:
        data = core.gh_json("pr", "view", url, "--json", "state") or {}
    except core.Failure as exc:
        core.say(f"ticket: слит ли PR {url}, не проверено — {exc}")
        return None
    return url if data.get("state") == "MERGED" else None


def main(argv: list[str], core) -> int:
    parser = argparse.ArgumentParser(prog="ticket start", description=__doc__.split("\n\n")[0])
    parser.add_argument("ticket", type=int, help="номер тикета")
    n = parser.parse_args(argv).ticket
    st = core.State(n)
    if core.orchestrated() and isinstance(stale := st["fresh"], dict):
        # Запуск начинается со старта: запрос свежей копии прошлого запуска (его убили посреди
        # ожидания) никто не ждёт — ожидание прежнее (#307).
        st["fresh"], st["waiting"] = None, stale.get("waiting") or core.AGENT
        st.save()
    if st["question"] and core.orchestrated():
        # Оркестратор возобновляет сессию после ответа владельца в тикете — его и забрать.
        body = (
            f"Вопрос владельцу: {st['question']['url']}. Сессию возобновил оркестратор — ответ "
            "владельца в тикете забирает вызов из worktree тикета:"
        )
        return core.emit(core.TURN, f"Тикет #{n}: ответ владельца", body, (core.command("ask", n),))
    if st["question"]:
        body = (
            f"{core.pending(st['question'])}Владелец ответил и написал «продолжай» — ответ из "
            "чата дословно в `.ticket/reply.md`, затем вызов из worktree тикета:"
        )
        return core.emit(core.STOPPED, f"Тикет #{n}: ждём ответа", body, (core.command("ask", n),))

    plan = own_session(core, n)
    if plan is None:
        plan = prepare(core, n)
        if isinstance(plan, int):
            return plan
    return route(core, n, st, plan)


def own_session(core, n: int) -> dict | None:
    """План без `session-start`: режим `/start` внутри сессии, которую оркестратор запустил или
    возобновил `session-run` в worktree тикета, — старт он уже сделал; второй запуск повторил бы
    уборку и `setup` проекта в worktree живой сессии (#214)."""
    if not core.orchestrated() or os.environ.get("CANON_SESSION") != f"ticket-{n}":
        return None
    here = core.branch()
    if core.ticket_of(here) != n:
        return None
    return {"kind": "ticket", "worktree": str(core.toplevel()), "branch": here}


def prepare(core, n: int):
    """`session-start N`: план или код выхода (остановка уже напечатана)."""
    res = core.canon("session-start", str(n), "--json")
    if res.returncode == 2:
        reason = res.stderr.strip() or res.stdout.strip()
        body = f"Старт не выполнен — {reason}\nПередай владельцу одной строкой: что мешает старту."
        return core.emit(core.STOPPED, f"Тикет #{n}: старт невозможен", body)
    if res.returncode != 0:
        raise core.Failure(f"session-start: {(res.stderr or res.stdout).strip()}")
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise core.Failure(f"session-start: нет итога JSON: {res.stdout[-200:]}") from None


def route(core, n: int, st, plan: dict) -> int:

    if plan["kind"] == "pr":
        body = (
            f"Сначала PR автора: {plan['url']} — тикет #{n} отложен.\n{enter(plan)}\n"
            f"Задание: {plan['prompt']}\nPR доведён — снова старт тикета:"
        )
        return core.emit(
            core.TURN,
            f"PR автора первым: {plan['url']}",
            body,
            (core.command("start", n),),
        )

    previous = ""
    if url := merged_pr(core, st):
        st.retire(Path(plan["worktree"]))
        previous = f"PR прошлого маршрута тикета уже слит ({url}) — работа по тикету заново.\n\n"
    st["branch"] = plan["branch"]
    st.save()
    if st.step == "stopped" and core.orchestrated():
        # Оркестратор возобновляет остановленный тикет только по `agent:go` владельца.
        where = (st["stop"] or {}).get("where", "?")
        # Ответ на отчёт маршрут сам не читает (#289): отчёт в PR — и ответ под ним.
        read = f"`gh issue view {n} --comments`"
        if "/pull/" in where:
            read += f" и `gh pr view {where} --comments`"
        body = (
            f"{enter(plan)}\nМаршрут был остановлен, отчёт об остановке — {where}; владелец "
            f"поставил agent:go. Сначала прочитай комментарии владельца после отчёта об "
            f"остановке ({read}): в них его ответ — по нему и иди. Затем продолжить маршрут из "
            f"worktree тикета, задания — до {core.until()} ({core.LONG_CALL}):"
        )
        again = core.command("ship", n, "--again")
        return core.emit(core.TURN, f"Тикет #{n}: продолжение после остановки", body, (again,))
    if st.step == "stopped":
        where = (st["stop"] or {}).get("where", "?")
        body = (
            f"{enter(plan)}\nМаршрут остановлен, отчёт об остановке — {where}. Владелец ответил "
            "«продолжай» — продолжить маршрут из worktree тикета:"
        )
        again = core.command("ship", n, "--again")
        return core.emit(core.STOPPED, f"Тикет #{n}: остановка", body, (again,))
    if st.step is not None:
        name = core.STEP_NAMES.get(st.step, st.step)
        body = (
            f"{enter(plan)}\nМаршрут тикета уже идёт — шаг «{name}». Продолжай его; вопрос "
            f"владельцу — только через `ticket ask {n}`, задания выполнять до {core.until()}. "
            f"Вызов ({core.LONG_CALL}):"
        )
        return core.emit(core.TURN, f"Тикет #{n}: продолжение", body, (core.command("ship", n),))
    st["since"], st["waiting"] = core.now(), core.AGENT
    if core.orchestrated() and st["copy"] is None and (copy := os.environ.get(core.PROD_COPY)):
        st["copy"] = copy  # базу сессии клонируют в начале работы — с этой копии (#307)
    st.save()
    extra = ORCHESTRATED_TASK.format(ask=core.ORCHESTRATED_ASK) if core.orchestrated() else ""
    if copy := core.prod_copy(st):
        fresh = core.command("fresh", n)
        extra += PROD_COPY_TASK.format(copy=copy, fresh=fresh, long=core.LONG_CALL)
    task = TASK.format(
        n=n, ask=core.command("ask", n), long=core.LONG_CALL, extra=extra, until=core.until()
    )
    body = f"{enter(plan)}\n\n{previous}{task}"
    return core.emit(core.TURN, f"Тикет #{n}: режим /start", body, (core.command("ship", n),))
