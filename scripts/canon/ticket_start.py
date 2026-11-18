"""ticket start — режим `/start`: старт сессии тикета и задание сделать его; вопрос — `ticket ask`.

    scripts/canon/ticket start <N>

Из любого чекаута проекта. Владелец запускает тикет одной командой и может отойти: вопросы
копятся в тикете (`ticket ask`), работа кончается `ticket ship`. Это единственный вход в тикет
(#452): прежний диалоговый вход набором скилла `/implement` упразднён, этот скилл Мэтта сессия
выполняет по заданию.

1. Подготовка — `session-start N` рядом со скриптом: уборка, PR автора первыми, тикет, worktree,
   метка `in-progress`. Тикет закрыт, занят или в нём текст посторонних (#196: тикет открыл не
   участник репозитория или есть комментарий не участника, и скрытый — #159) — остановка с
   причиной; сбой git или `gh` — код 1.
2. PR автора требует внимания (красный CI, нет отметки, конфликт) — задание довести его, тикет
   отложен; затем тот же вызов.
3. Иначе — задание: перейти в рабочую копию, выполнить `/implement N` Мэтта; согласование,
   которого просят скиллы, — решить самому; вне оркестратора — полный набор тестов в конце не
   гонять (он только в CI на PR и ночью; `ticket ship` гоняет допуск), сначала коммит, затем
   `code-review` Мэтта от основной ветки (`ticket_core.MANUAL_END`); вопрос владельцу — только
   `ticket ask N`; затем `ticket ship N`. Действия — словами, имя инструмента Claude — только
   примером в скобках (#453): задание выполнит любой агент. Маршрут тикета уже идёт (шаг `ship`
   записан) — задание продолжить его. PR маршрута слит (тикет
   переоткрыт) — маршрут с нуля: прошлый — в архив рядом с состоянием, задание — `/implement N`.
4. Вопрос `ticket ask` ждёт ответа, а сессии с ним нет (#457; живую сессию остановит на шаге 1
   отметка «тикет ведёт сессия» `session-start`, #458, — она проверяется раньше этой ветки, иначе
   ответ заберут две сессии; живую ждущую — с подсказкой продолжить её; `session-run` — ещё и его
   замок) — переход в рабочую копию плана и та же проверка ответа, что у
   `ticket ask`: ответ участника в тикете после вопроса (владельца, координатора по записанному,
   перенесённый координатором ответ владельца) — снимается метка `ready-for-human`; до маршрута —
   полное задание режима /start с ответом впереди, на шаге маршрута — следующий шаг; второго
   «продолжай» не нужно. Ответа нет — вопрос снова полным текстом, ответ из чата — в
   `.ticket/reply.md` рабочей копии, затем `ticket ask N`.

5. Маршрут остановлен (шаг `stopped`) — ответ владельца на отчёт об остановке ищется там, где он
   ответил (#459, слабое место 8): комментарии участника в тикете и в открытом PR ветки после
   своего отчёта (`ticket_core.after_report`, `report_answers`; ответ из чата, переданный
   сессией, — тоже). Есть — задание сделать по нему и продолжить `ticket ship N --again`; нет —
   «остановка»: ждём владельца, ответ из чата — в `.ticket/reply.md`. Черновой PR с отчётом —
   та же ветка: подготовка берёт её, финиш вернёт PR из черновика.

Внутри сессии тикета (`CANON_SESSION=ticket-N` на его ветке), которую запустил `session-run` после
своего старта (метка `CANON_SESSION_MARK` совпала с файлом замка; под оркестратором — всегда),
`session-start` не зовётся: старт уже сделан, замок — её собственный (#452), вход не нужен.

Под оркестратором (`CANON_ORCHESTRATOR=1`, #214) `session-start` PR автора не берёт; задание —
то же `/implement N` с самопроверкой `/code-review`, конец — «код готов» или вопрос; у сессии пути
v2 (`CANON_V2_TASK=1` от `session-run`, #340) — задание сессии v2 (`v2/task.py`) без `/implement`
и `/code-review`, конец — «код готов», вопрос или «стоп» с причиной; с копией
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
import types
from pathlib import Path

TASK = """Режим /start: владелец может быть не у экрана. Сделай тикет по скиллу
`/implement {n}` (Мэтта: тикет, TDD, коммит) — с такими поправками:
- согласовать, которое велят скиллы (/implement, /tdd, /grilling и другие: швы тестов, план,
  имена), — реши сам по тикету, его родительской спеке, ADR и профилю проекта;
{manual}- вопрос владельцу — только если без ответа тикет не сделать (требование неясно или
  противоречиво): вопрос на языке поведения продукта, варианты и рекомендацию запиши в файл
  `.ticket/ask.md` (Write), затем вызов `{ask}`: вопрос уйдёт
  комментарием в тикет, текст для чата даст его задание. Вопрос — только через `ticket ask`,
  не формой выбора (AskUserQuestion);
- так же и на шагах `ticket ship`: где задание велит спросить в чате, — `ticket ask`.{extra}
Работа закоммичена (сообщение одной строкой `-m`){checked} — конец работы над тикетом, его задания
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
# Сессия кода пути v2 (#340): задание `v2/task.py` вместо `/implement` и `/code-review`.
V2_TASK = """Режим /start пути v2: владелец может быть не у экрана, ревью ошибок, отметку и
ожидание мёржа ведёт оркестратор; сессия кончается на «код готов», вопросе или «стопе». Сделай
тикет по заданию сессии v2 ниже.{extra}

{rules}

Вызов маршрута ({long}; ревью ошибок идёт 5–8 минут):"""
# Копия прода под оркестратором (#307): живого прода и его API в песочнице нет.
PROD_COPY_TASK = """
- живого прода в песочнице нет: «на проде» в критериях тикета — обезличенная
  копия прода в песочнице, снимок от {copy}; нужны данные свежее снимка — вызов `{fresh}`
  ({long}): оркестратор снимет новую копию на Mac (не чаще раза в час) и ответит в том же
  вызове;"""


def enter(plan: dict) -> str:
    """Как войти в worktree плана `session-start`. Что делать, если вход не удался, — всегда: клик
    ждёт и worktree вне `.claude/worktrees/` (`needs_click`), а поведение входа зависит от версии
    Claude Code (#105, прогон #142). Следом — строка подготовки о хуке перед коммитом (`hook`
    плана, #497): её видит сессия в любой ветке задания старта."""
    wt = plan["worktree"]
    # Строка подготовки о хуке перед коммитом (#463) — как её печатает `session-start`, с
    # предупреждением о блоке fast-checks: без неё сессия не видит, что хук стоит (#497).
    hook = plan.get("hook", "").rstrip(".")
    hook = f"\n{hook[:1].upper()}{hook[1:]}." if hook else ""
    if plan.get("inside"):  # сессия `session-run` уже запущена в worktree тикета
        return f"Worktree — {wt} (ветка {plan['branch']}): сессия уже в нём, вход не нужен.{hook}"
    line = (
        f"Worktree — {wt} (ветка {plan['branch']}). Перейди в рабочую копию по пути "
        f"{wt} (Claude Code — EnterWorktree с path {wt})"
    )
    if plan.get("needs_click"):
        line += " — переход может ждать клика владельца (worktree вне .claude/worktrees/)"
    return (
        f"{line}; дальше вся работа и вызовы — из неё. Переход не удался — стоп: владельцу одной "
        "строкой «нужен клик входа в worktree тикета»; без входа маршрут не идёт — `ticket ship` "
        f"работает только из worktree тикета.{hook}"
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

    plan = own_session(core, n)
    if plan is None:
        plan = prepare(core, n)
        if isinstance(plan, int):
            return plan
    if st["question"] and plan["kind"] != "pr":
        return resume_question(core, n, plan)
    return route(core, n, st, plan)


def resume_question(core, n: int, plan: dict) -> int:
    """Вопрос `ticket ask` ждёт ответа, а сессии с ним уже нет (#457): новый `/start N` прошёл
    подготовку, переходит в рабочую копию плана и сам ищет ответ в тикете — той же проверкой,
    что `ticket ask`. Ответ есть — без второго «продолжай»: до маршрута — полное задание режима
    /start с ответом впереди (новая сессия прошлого задания не видела), на шаге маршрута —
    следующий шаг `ticket ask`; нет — вопрос снова полным текстом, ответ из чата — в
    `.ticket/reply.md` рабочей копии. Вопрос успели забрать, пока шла подготовка, — обычный
    маршрут: старт не отправит отложенный `.ticket/ask.md` и не попросит новый вопрос."""
    path = Path(__file__).resolve().parent / "ticket_ask.py"  # рядом, без `sys.path`
    module = types.ModuleType("canon_ticket_ask_for_start")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    ask = module.Ask(n, core, root=Path(plan["worktree"]))
    st = ask.state
    if not st["question"]:
        return route(core, n, st, plan)
    status, title, body, commands = ask.wait(None)
    st.save()
    if status == core.TURN and st.step is None:
        before = f"{module.ANSWER_HEAD}\n\n{core.quote(ask.answer)}\n\n"
        return route(core, n, st, plan, before=before)
    return core.emit(status, title, f"{enter(plan)}\n\n{body}", commands)


def own_session(core, n: int) -> dict | None:
    """План без `session-start`: режим `/start` внутри сессии тикета, которую запустил или
    возобновил `session-run` в worktree тикета (под оркестратором или из терминала владельца), —
    старт он уже сделал; второй запуск повторил бы уборку и `setup` проекта в worktree живой
    сессии (#214) и остановился бы на её собственном замке (#452). Вне оркестратора — только
    когда метка сессии в окружении (`CANON_SESSION_MARK`) совпала с меткой в файле замка: её
    пишет `session-run` после своего старта (не на `--resume`); иначе — `session-start` со всеми
    его проверками (тикет закрыт или занят, текст посторонних, сессия уже идёт)."""
    if os.environ.get("CANON_SESSION") != f"ticket-{n}":
        return None
    here = core.branch()
    if core.ticket_of(here) != n:
        return None
    if not core.orchestrated():
        mark = os.environ.get(core.SESSION_MARK)
        if not mark or mark != core.session_mark():
            return None
    worktree = core.toplevel()
    plan = {"kind": "ticket", "worktree": str(worktree), "branch": here, "inside": True}
    plan["hook"] = commit_hook(worktree)
    return plan


def commit_hook(worktree: Path) -> str:
    """Строка о хуке перед коммитом для сессии `session-run` (#497): старт его уже поставил и
    напечатал строку в терминал, а сессии её не видно — та же подготовка хука (`install`
    повторный — без вреда) даёт ту же строку для задания."""
    path = Path(__file__).resolve().parent / "hooks" / "commit_checks.py"  # рядом, без `sys.path`
    module = types.ModuleType("canon_commit_checks_for_ticket_start")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module.prepare(worktree)[1]


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


def report_answer(core, n: int, branch: str) -> tuple[str | None, str]:
    """Ответ владельца на отчёт об остановке в тикете и открытом PR ветки (#459, слабое место
    8) — цитатой для задания (пусто — ответа нет) и строка о черновом PR. Сбой `gh` — ответ
    None со строкой о сбое: старт не обрывается, ответ прочтёт сессия сама."""
    try:
        pr = core.open_pr(branch)
        answers = core.report_answers(core.after_report(n, branch, pr))
    except core.Failure as exc:
        read = f"`gh issue view {n} --comments` и комментарии PR ветки"
        return None, f"Ответ владельца не прочитан ({exc}) — прочитай сам: {read}.\n"
    draft = ""
    if pr and pr.get("isDraft"):
        draft = (
            f"PR ветки — {pr['url']}, черновик с отчётом: работа продолжается в его ветке "
            "(рабочая копия выше), финиш маршрута вернёт PR из черновика.\n"
        )
    return (core.quoted_answers(answers) if answers else ""), draft


def stopped(core, n: int, st, plan: dict) -> int:
    """Маршрут остановлен: следующий старт читает ответ владельца на отчёт там, где он ответил, —
    в тикете или в PR (#459); черновой PR с отчётом — та же ветка. Ответ есть — задание сделать
    по нему и продолжить (`--again`); нет — ждём владельца. Под оркестратором продолжает только
    `agent:go` владельца — ответ, если он есть, впереди задания."""
    where = (st["stop"] or {}).get("where", "?")
    answer, note = report_answer(core, n, plan["branch"])
    again = core.command("ship", n, "--again")
    head = f"{enter(plan)}\nМаршрут был остановлен, отчёт об остановке — {where}.\n{note}"
    said = f"Ответ владельца после отчёта (тикет и PR) —\n\n{answer}\n\n" if answer else ""
    if answer is None:  # не прочитан: «ответа нет» было бы неправдой — читает сессия
        body = (
            f"{head}Ответ есть — сделай по нему (правки — коммитами с сообщением одной строкой "
            f"`-m`); нет — ждём владельца, вызов не делать. Продолжить маршрут из worktree тикета, "
            f"задания — до {core.until()} ({core.LONG_CALL}):"
        )
        return core.emit(core.TURN, f"Тикет #{n}: продолжение после остановки", body, (again,))
    if core.orchestrated():
        read = (
            said + "По ответу и иди."
            if answer
            else "Ответа комментарием после отчёта нет — владелец поставил agent:go: иди по отчёту."
        )
        body = (
            f"{head}{read} Затем продолжить маршрут из worktree тикета, задания — до "
            f"{core.until()} ({core.LONG_CALL}):"
        )
        return core.emit(core.TURN, f"Тикет #{n}: продолжение после остановки", body, (again,))
    if answer:
        body = (
            f"{head}{said}Сделай по ответу (правки — коммитами с сообщением одной строкой `-m`; "
            f"новый вопрос владельцу — через `ticket ask {n}`), затем продолжить маршрут из "
            f"worktree тикета, задания — до {core.until()} ({core.LONG_CALL}):"
        )
        return core.emit(core.TURN, f"Тикет #{n}: ответ на отчёт об остановке", body, (again,))
    body = (
        f"{head}Ответа владельца после отчёта ни в тикете, ни в PR нет — ждём владельца: он "
        "отвечает комментарием в тикете или в PR (можно с телефона) или в чате. Ответ из чата — "
        "дословно в файл `.ticket/reply.md` (Write): маршрут передаст его в тикет (решение о "
        "/verify — только комментарием владельца). Ответил — "
        "продолжить маршрут из worktree тикета:"
    )
    return core.emit(core.STOPPED, f"Тикет #{n}: остановка", body, (again,))


def route(core, n: int, st, plan: dict, before: str = "") -> int:
    """`before` — начало задания перед ним (ответ на вопрос, `resume_question`)."""
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

    previous = before
    if url := merged_pr(core, st):
        st.retire(Path(plan["worktree"]))
        previous += f"PR прошлого маршрута тикета уже слит ({url}) — работа по тикету заново.\n\n"
    st["branch"] = plan["branch"]
    st.save()
    if st.step == "stopped":
        return stopped(core, n, st, plan)
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
    if core.v2_task():
        extra = ""
    if copy := core.prod_copy(st):
        fresh = core.command("fresh", n)
        extra += PROD_COPY_TASK.format(copy=copy, fresh=fresh, long=core.LONG_CALL)
    if core.v2_task():
        task = V2_TASK.format(extra=extra, rules=core.v2_rules(n), long=core.LONG_CALL)
    else:
        manual = "" if core.orchestrated() else f"{core.MANUAL_END.format(n=n)}\n"
        checked = "" if core.orchestrated() else " и самопроверка пройдена"
        task = TASK.format(
            n=n,
            ask=core.command("ask", n),
            long=core.LONG_CALL,
            extra=extra,
            until=core.until(),
            manual=manual,
            checked=checked,
        )
    body = f"{enter(plan)}\n\n{previous}{task}"
    return core.emit(core.TURN, f"Тикет #{n}: режим /start", body, (core.command("ship", n),))
