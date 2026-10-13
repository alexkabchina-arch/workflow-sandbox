"""ticket ship — конец работы над тикетом: от проверки ветки до PR с отметкой ревью и авто-мёржем.

    scripts/canon/ticket ship <N> [--again]

Из worktree тикета (ветка `ticket-N` или `ticket-N-…`), когда работа закоммичена. Шаги по
порядку; механику делает скрипт, на шаге с суждением печатает задание и выходит («ход агента»):

| Шаг | Скрипт | Агент (файл в `.ticket/`) |
|---|---|---|
| ревью ошибок | `review`, отчёт рядом с состоянием; «только документы» — без разбора | — |
| разбор | находки и правило разбора | решения, исправления (`findings.md`) |
| проверка разбора | все разобраны; тикеты техдолга и канона — сам | ответ владельца (`answer.md`) |
| тесты и линтеры | блок `checks` профиля через `run-log` | починка (`checks.md`) |
| `/verify` | критерии из тикета, `verify`, итог | FAIL — починить |
| догон базы | `git merge origin/main` | конфликт — разрешить (`sync.md`) |
| тело PR | факты: Closes, тесты, ревью, `/verify`, догон | «Что сделано» (`summary.md`) |
| финиш | `ship-finish`: push, PR, авто-мёрж, отметка ревью | привязка PR, Auto-fix, `ticket wait` |
| остановка | отчёт из данных, `ready-for-human` | вопрос и варианты (`stop.md`) |

Пределы — два круга исправлений каждого вида (красные проверки, FAIL `/verify`, догон), третий —
остановка. Новые коммиты после финиша (красный CI, конфликт) — тот же вызов: проверки, догон и
отметка на новый head без второго ревью. `--again` — продолжить после остановки (владелец
ответил «продолжай»). Как устроено и почему — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import types
from pathlib import Path

LIMIT = 2
CANON_REPO = "alexkabchina-arch/workflow"
LABEL = "ready-for-human"
PROFILE = Path("docs/agents/profile.md")
TRIAGE = Path("docs/agents/triage-labels.md")
KINDS = ("исправлено", "отклонено", "техдолг", "не записано", "канон", "вопрос")
NEEDS_TEXT = {"отклонено", "техдолг", "канон", "вопрос"}
DASH = r"\s*(?:[—–:-]+\s*)?"
DECISION = re.compile(rf"^(\d+)[.)]\s*({'|'.join(KINDS)}){DASH}(.*)$", re.I)
WEAKENING = re.compile(rf"^ослабление{DASH}(.+)$", re.I)
VERIFY = re.compile(rf"^/?verify{DASH}(не нужен|нужен){DASH}(.*)$", re.I)
ITEM = re.compile(r"^(\d+)\. (.*)$")

RULE = """Правило разбора — по каждой находке одно решение (сверить находку с кодом):
- исправлено — дефект вносит ветка: в изменённых строках или ветка делает его достижимым (новый
  вызов, новые входные данные). Чинить в этой ветке, отдельным коммитом; порога нет.
- отклонено — находка неверна: почему, одной строкой.
- вопрос — находка показывает, что вред, который чинит тикет, остался на другом пути (даже в
  старом коде). Это не техдолг: вопрос владельцу до финиша — доделать в этой ветке или отдельным
  тикетом.
- техдолг — посторонний дефект вне диффа, был до ветки, со сценарием вреда (неверный результат,
  потеря данных, красный шаг процесса, неверная команда агенту), прослеженным чтением или
  запуском. В ветке не чинить; тикет заведёт скрипт — сценарий и как прослежен.
- канон — такой же дефект в вендоренном файле канона (перечислен в `.canon.lock`): в проекте не
  правится, тикет в трекер канона заведёт скрипт.
- не записано — всё прочее: стиль, упрощения, дубли, «если когда-нибудь», непрослеженный
  сценарий. Никуда не пишется, в тело PR — только число.
Инструкция агенту (CLAUDE.md, скиллы, профиль, подсказки скриптов) — код: устаревшая, по которой
агент выполнит неверную команду, — дефект. Правку сверх тикета молча не делать: вопрос
владельцу или отступление в «Что сделано»."""

FORMAT = """Ответ — файл `.ticket/findings.md` (инструмент Write): строка на находку, строка verify:
    1. исправлено — что сделано
    2. отклонено — почему
    3. техдолг — сценарий вреда; как прослежен
    4. не записано
    verify — нужен: что проверить   (или: verify — не нужен: почему)
`/verify` нужен, когда ветка меняет наблюдаемое поведение: интерфейс, ответ API, записываемые
данные; иначе — не нужен с причиной («правка только документации»). Веб-интерфейс проверяется
только с рецептом из раздела профиля «Рецепт запуска для `/verify`» (файл
`.claude/skills/run-<проект>/SKILL.md` в git): ветка меняет веб-интерфейс, а рецепта нет —
вопрос в `.ticket/stop.md`, рецепт заводит владелец. Исправления — коммитами (сообщение одной
строкой `-m`)."""

WEAKENING_TASK = """Ревью нашло ослабление защиты (проверок, прав агента, правил или обезличивания):
{text}
Решение — строкой `ослабление — …` в том же файле: «запрошено тикетом: <цитата из тикета>»,
«убрано коммитом <sha>: …» (коммит после ревью) или «ослабления нет: <почему>». Убрать нельзя, не
сломав тикет, — вопрос владельцу в `.ticket/stop.md`."""

QUESTION_TASK = """Находка показывает, что вред, который чинит тикет, остался на другом пути. Это не
техдолг, а вопрос владельцу до финиша: после финиша PR сольётся без ответа.

{questions}

Спроси владельца в чате на языке поведения продукта: что увидит пользователь или что станет с
данными при каждом варианте, без имён функций и файлов. Варианты — доделать в этой ветке или
отдельным тикетом; рекомендация — твоя. Дождись ответа. Собеседника нет (дочерний `claude -p`) —
вопрос и варианты в `.ticket/stop.md`.
После ответа: ответ владельца дословно — в `.ticket/answer.md`; решение по находке в
`.ticket/findings.md` — `исправлено` (доделано в ветке, коммит) или `техдолг` (отдельный тикет
заведёт скрипт)."""

CHECKS_TASK = """Реши, чья вина, и почини — без вопроса владельцу:
- ветка: падает изменённый код или неизменённый, который его вызывает, — починить, коммит;
- окружение: тот же тест красный на origin/main, сбой соединения, порта, контейнера, базы, чужой
  файл вне ветки — в ветке не чинить. Неотслеживаемый файл не из ветки перенести в scratchpad
  сессии (`mv`, не `rm` — его блокирует классификатор), локальный кэш очистить, окружение своей
  сессии поправить; общее окружение и чужие сессии не трогать. Причину и что сделано — строкой в
  `.ticket/checks.md` (уйдёт в тело PR);
- линтер или формат красный только на файлах вне диффа (обновился инструмент) — не чинить:
  вопрос и варианты в `.ticket/stop.md`.
Попытка {attempt} из {limit}; дальше — отчёт об остановке. Полный вывод — в логе из строки
`run-log: завершено …`; длинный лог читать через docs-scout, не целиком."""

CONFLICT_TASK = """Ветка догоняет origin/main мёржем (не rebase: отчёт ревью должен остаться
предком HEAD) — конфликт в файлах: {files}.
Разреши, сохранив смысл обеих правок — и ветки, и main. Каждый участок читать с обеих сторон,
что и зачем меняла сторона — по её коммитам (`git log -p --merge -- <файл>`). Сторону целиком
брать, только если она уже содержит правку другой. Мелкие добавления в общий документ (строка
глоссария, строка карты README) — сохраняются обе.
{vendored}
Смысловой конфликт — две ветки по-разному меняют одну логику (правило, порог, поведение), и
сохранить обе нельзя: сам не выбирай — `git merge --abort`, вопрос владельцу (что меняла каждая
сторона, что увидит пользователь при каждом варианте, рекомендация) — в `.ticket/stop.md`.
Разрешил — `git add` этих файлов, `git commit --no-edit`; файлы и как сведены обе правки —
строкой в `.ticket/sync.md` (уйдёт в тело PR). Затем вызов: тесты пойдут на слитом коде."""

VENDORED = """Вендоренный канон (файлы из `.canon.lock` и сам `.canon.lock`) — сторона main
(`git checkout --theirs -- <файл>`): обычная ветка их не правит. Lock-файл зависимостей — сторона
main, затем пересборка командой проекта, которая его пишет."""

VENDORED_INSTALL = """Ветка ставит или обновляет канон (в её диффе `.canon.lock`): вендоренные
файлы и `.canon.lock` руками не сводить и сторону main не брать — это откатило бы обновление.
Взять сторону ветки (`git checkout --ours -- <файл>`), довести слияние коммитом и заново выполнить
`canon-sync` той же версии из клона канона на этом worktree — он пересоберёт `.claude/settings.json`
и сверит суммы; затем коммит. Клона канона нет — вопрос в `.ticket/stop.md`."""

SUMMARY_TASK = """Напиши «Что сделано» в `.ticket/summary.md`: 2–5 строк для владельца-неинженера —
что меняется в поведении продукта и зачем; отступления от тикета (правки сверх него) — здесь же.
Ветка меняет проверки (тесты, CI, ревью, хуки), права агента, правила (CLAUDE.md, скиллы,
профиль) или обезличивание — первая строка «Меняются правила процесса: <что и зачем>».
Closes, тесты, ревью, `/verify` и догон базы скрипт допишет сам."""

TRACE_TASK = """Разобрать каждый пункт: рецепт `.claude/skills/run-*` — откатить (отслеживаемое —
`git checkout -- <путь>`, новое — удалить): итог проверки не засчитан, /verify пройдёт заново;
`.claude/skills/verify/` — удалить; тестовые файлы — откатить; коммиты /verify поверх ветки —
разобрать так же, лишнее откатить коммитом; «ветка сменилась» или «история переписана» — ничего
не откатывать, вопрос в `.ticket/stop.md`."""

BIND = """Привязка PR к сессии: в приложении Claude (инструменты ccd_pr; отложенные — загрузить
поиском инструментов) — get_status; этот PR не привязан — bind_pr со ссылкой выше, без вопроса
владельцу. Ошибка bind_pr — не стоп: в итоге «PR не привязан». В терминале — пропуск.
Затем Auto-fix (решение владельца 13.10, прогон B0): set_monitor с url PR, auto_fix и
address_comments true — без вопроса; событие CI разбудит сессию на красном CI или конфликте. PR не
привязан или в терминале — пропуск: красный CI и конфликт найдёт ожидание мёржа."""

WAIT = """Затем ожидание мёржа и уборка — сессия ждёт сама, до 20 минут ({long}); итог ожидания
передать владельцу вместе с итогом выше:"""


def load_review() -> types.ModuleType:
    """`review` рядом со скриптом — его `is_docs`: «только документы» тем же правилом."""
    path = Path(__file__).resolve().parent / "review"
    module = types.ModuleType("canon_review_for_ticket")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def parse_decisions(text: str, count: int, weakening_needed: bool):
    """Решения по находкам из `findings.md`: ({номер: (вид, текст)}, verify, ослабление, ошибки)."""
    decisions: dict[int, tuple[str, str]] = {}
    verify = weakening = None
    errors: list[str] = []
    last = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := DECISION.match(line):
            number, kind = int(m.group(1)), m.group(2).lower()
            if not 1 <= number <= count:
                errors.append(f"находка {number}: такой нет (находок {count})")
            elif number in decisions:
                errors.append(f"находка {number}: два решения")
            decisions[number] = (kind, m.group(3).strip())
            last = number
        elif m := VERIFY.match(line):
            verify = {"needed": m.group(1).lower() == "нужен", "why": m.group(2).strip()}
            last = None
        elif m := WEAKENING.match(line):
            weakening = m.group(1).strip()
            last = None
        elif last is not None:  # продолжение текста решения
            kind, said = decisions[last]
            decisions[last] = (kind, f"{said} {line}".strip())
    for number in range(1, count + 1):
        if number not in decisions:
            errors.append(f"находка {number}: нет решения")
        elif decisions[number][0] in NEEDS_TEXT and not decisions[number][1]:
            errors.append(f"находка {number}: «{decisions[number][0]}» — без пояснения")
    if verify is None:
        errors.append("нет строки «verify — нужен: …» или «verify — не нужен: почему»")
    elif not verify["needed"] and not verify["why"]:
        errors.append("verify — не нужен: без причины")
    if weakening_needed and not weakening:
        errors.append("нет строки «ослабление — …» с решением по ослаблению защиты")
    return decisions, verify, weakening, errors


def review_items(md: str) -> dict[int, str]:
    """Находки из `review.md` по номерам (с их строкой «Сценарий»)."""
    items: dict[int, str] = {}
    number = None
    for line in md.splitlines():
        if m := ITEM.match(line):
            number = int(m.group(1))
            items[number] = m.group(2)
        elif number is not None and line.startswith("   ") and line.strip():
            items[number] += "\n   " + line.strip()
        elif not line.strip() and number is not None:
            continue
        else:
            number = None
    return items


def triage_label(root: Path) -> str:
    """Метка роли `needs-triage` в трекере проекта (`docs/agents/triage-labels.md`)."""
    try:
        text = (root / TRIAGE).read_text(encoding="utf-8")
    except OSError:
        return "needs-triage"
    for line in text.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == "needs-triage" and cells[1]:
            return cells[1]
    return "needs-triage"


def rollup_cost(path: Path) -> float:
    try:
        cost = json.loads(path.read_text(encoding="utf-8")).get("total_cost_usd")
    except (OSError, ValueError, AttributeError):
        return 0.0
    return float(cost) if isinstance(cost, (int, float)) else 0.0


class Route:
    def __init__(self, number: int, core):
        self.n, self.core = number, core
        self.cmd = f"scripts/canon/ticket ship {number}"
        self.state = core.State(number)

    # --- вывод ---

    def turn(self, title: str, body: str, *, long: str = ""):
        hint = f"Затем вызов ({self.core.LONG_CALL}"
        hint += f"; {long})" if long else ")"
        return (self.core.TURN, title, f"{body}\n\n{hint}:", (self.cmd,))

    # --- вход ---

    def run(self, again: bool) -> int:
        core, st = self.core, self.state
        here = core.branch()
        if core.ticket_of(here) != self.n:
            return self.elsewhere(here)
        self.root = core.toplevel()
        core.answers(self.root)
        st["branch"] = here
        if st.step is None:
            st.go("review")
        if again and st.step == "stopped":
            st["attempts"], st["wait"] = {}, None  # владелец ответил — круги и ожидания заново
            st.go((st["stop"] or {}).get("step") or "review")
            st["stop"] = None
        for _ in range(30):
            out = self.advance()
            st.save()
            if out is not None:
                return core.emit(*out)
        raise core.Failure("маршрут не сошёлся за 30 переходов")

    def elsewhere(self, here: str | None) -> int:
        path = self.core.worktree_of(self.n)
        where = self.core.toplevel()
        if path is None:
            body = (
                f"Скрипт запущен из {where} на ветке {here or '(detached)'}, а worktree тикета "
                f"#{self.n} нет: сначала старт сессии тикета (`session-start {self.n}`)."
            )
            return self.core.emit(self.core.TURN, "Нет worktree тикета", body)
        body = (
            f"Скрипт запущен из {where} на ветке {here or '(detached)'}: здесь правила и код "
            f"могут отставать от ветки тикета. Маршрут тикета #{self.n} идёт из его worktree — "
            f"войди в него (`EnterWorktree` с path {path}; в терминале — открой сессию там) и "
            "вызови оттуда:"
        )
        return self.core.emit(self.core.TURN, "Запуск не из worktree тикета", body, (self.cmd,))

    def advance(self):
        """Один переход: None — шаг пройден, дальше; кортеж — вывод и выход."""
        core, st = self.core, self.state
        if question := st["question"]:  # вопрос `ticket ask` ждёт ответа — маршрут стоит
            body = (
                f"Вопрос владельцу ждёт ответа: {question['url']}. Маршрут не идёт до ответа. "
                "Владелец ответил и написал «продолжай» — ответ из чата дословно в "
                "`.ticket/reply.md`, затем вызов:"
            )
            st["waiting"] = core.OWNER
            ask = f"scripts/canon/ticket ask {self.n}"
            return (core.STOPPED, f"Тикет #{self.n}: ждём ответа", body, (ask,))
        if st.step == "stopped":
            where = (st["stop"] or {}).get("where", "?")
            body = (
                f"Отчёт об остановке — {where}. Ждём владельца. Владелец ответил «продолжай» — "
                "продолжить маршрут:"
            )
            return (core.STOPPED, f"Тикет #{self.n}: остановка", body, (f"{self.cmd} --again",))
        if st.step != "stop" and core.read_answer(self.root, "stop.md"):
            name = self.core.STEP_NAMES.get(st.step, st.step)
            return self.stop(f"сессия остановила маршрут на шаге «{name}»")
        if st.step == "done":
            if st["finished_head"] == core.head():
                return self.result()
            st["attempts"] = {}  # новый круг: красный CI или конфликт после финиша
            if self.pr_merged():
                self.retire_review()  # отчёт покрывает код слитого PR — новая работа без ревью
                st.go("review")
                return None
            st.go("checks")  # новые коммиты после финиша: проверки и отметка на новый head
            return None
        if st.step not in ("stop", "sync") and self.dirty():
            return self.turn(
                "Незакоммиченные правки",
                f"В worktree незакоммиченные правки отслеживаемых файлов:\n{self.dirty()}\n"
                "Закоммить (сообщение одной строкой `-m`) или откати.",
            )
        return getattr(self, f"step_{st.step}")()

    def dirty(self) -> str:
        return self.core.git("status", "--porcelain", "--untracked-files=no")

    # --- ревью ошибок ---

    def reviewed(self) -> dict | None:
        """`canon_review` отчёта, если он годен для этой ветки (иначе ревью заново)."""
        core = self.core
        try:
            data = json.loads((self.state.dir / "review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        verdict = data.get("canon_review") if isinstance(data, dict) else None
        if not isinstance(verdict, dict) or not all(
            isinstance(verdict.get(k), str) for k in ("base_sha", "head")
        ):
            return None
        fresh = core.git_ok("merge-base", "--is-ancestor", verdict["base_sha"], "origin/main")
        mine = core.git_ok("merge-base", "--is-ancestor", verdict["head"], "HEAD")
        if verdict.get("skipped") is True and fresh and mine:
            # Пропуск годен, пока в ветке только документы (`ship-finish` проверит так же).
            files = core.git("diff", "--name-only", "--no-renames", "origin/main...HEAD")
            mine = all(load_review().is_docs(f) for f in files.splitlines())
        return verdict if fresh and mine else None

    def pr_merged(self) -> bool:
        if not self.state["pr"]:
            return False
        data = self.core.gh_json("pr", "view", self.state["pr"], "--json", "state") or {}
        return data.get("state") == "MERGED"

    def retire_review(self) -> None:
        """Отчёт ревью — в сторону: следующий шаг `review` запустит ревью заново."""
        report = self.state.dir / "review.json"
        if report.exists():
            report.replace(self.state.dir / f"review-{self.core.now()}.json")

    def step_review(self):
        core, st = self.core, self.state
        verdict = self.reviewed()
        if verdict is None:
            core.profile_checks(self.root / PROFILE)  # профиль без проверок — до оплаты ревью
            st.dir.mkdir(parents=True, exist_ok=True)
            for old in ("findings.md", "answer.md"):  # ответы прошлого ревью к новому не относятся
                (self.root / core.ANSWERS / old).unlink(missing_ok=True)
            st["asked"] = st["decisions"] = None
            core.say("ticket: ревью ошибок — обычно 5–8 минут, ждите")
            res = core.canon("review", str(st.dir / "review.json"))
            core.say(res.stdout.rstrip())
            if res.returncode != 0:
                why = f"ревью ошибок не состоялось (код {res.returncode}): {tail(res.stderr, 5)}"
                return self.stop(why)
            verdict = self.reviewed()
            if verdict is None:
                return self.stop("ревью ошибок не оставило годного отчёта")
            lines = res.stdout.splitlines()
            st["review_warnings"] = [x for x in lines if x.startswith("review: предупреждение")]
            spent = st["cost"].get("review") or 0  # прошлые прогоны оплачены — не теряются
            st["cost"]["review"] = spent + (verdict.get("cost_usd") or 0)
        st["review"] = {
            "head": verdict["head"],
            "skipped": verdict.get("skipped") is True,
            "findings": verdict.get("findings") or 0,
            "weakening": verdict.get("weakening") or "",
        }
        if st["review"]["skipped"]:
            st["verify"] = {"needed": False, "why": "только документы"}
            st.go("checks")
            return None
        st.go("findings")
        return self.findings_task()

    # --- разбор ---

    def findings_task(self, errors: list[str] | None = None):
        review = self.state["review"]
        md = (self.state.dir / "review.md").read_text(encoding="utf-8").strip()
        cost = self.state["cost"].get("review") or 0
        parts = []
        if errors:
            parts.append("Ответ не принят:\n" + "\n".join(f"- {e}" for e in errors))
        parts.append(f"Ревью ошибок ({cost:.2f} USD), находки — {self.state.dir / 'review.md'}:")
        parts.append(md)
        if review["weakening"]:
            parts.append(WEAKENING_TASK.format(text=review["weakening"]))
        parts += [RULE, FORMAT]
        return self.turn("Разбор находок ревью", "\n\n".join(parts), long="тесты — до 45 минут")

    def step_findings(self):
        core, st = self.core, self.state
        text = core.read_answer(self.root, "findings.md")
        if not text:
            return self.findings_task()
        review = st["review"]
        decisions, verify, weakening, errors = parse_decisions(
            text, review["findings"], bool(review["weakening"])
        )
        if errors:
            return self.findings_task(errors)
        questions = {n: said for n, (kind, said) in decisions.items() if kind == "вопрос"}
        if questions:
            asked = st["asked"] or {}
            asked.update({str(n): said for n, said in questions.items()})
            st["asked"] = asked
            st.go("findings", core.OWNER)
            listed = "\n".join(f"- находка {n}: {said}" for n, said in sorted(questions.items()))
            return self.turn("Вопрос владельцу до финиша", QUESTION_TASK.format(questions=listed))
        if st["asked"] and not core.read_answer(self.root, "answer.md"):
            st.go("findings", core.OWNER)
            listed = "\n".join(f"- находка {n}: {said}" for n, said in st["asked"].items())
            return self.turn(
                "Ответ владельца не записан",
                f"Был вопрос владельцу:\n{listed}\nЕго ответ дословно — в `.ticket/answer.md`.",
            )
        st["decisions"] = {str(n): list(d) for n, d in sorted(decisions.items())}
        st["verify"], st["weakening"] = verify, weakening
        try:
            self.file_issues(decisions)
        except core.Failure as exc:
            return self.stop(f"тикет по находкам не заведён: {exc}")
        st.go("checks")
        return None

    def file_issues(self, decisions: dict[int, tuple[str, str]]) -> None:
        items = review_items((self.state.dir / "review.md").read_text(encoding="utf-8"))
        debt = [n for n, (kind, _) in sorted(decisions.items()) if kind == "техдолг"]
        canon = [n for n, (kind, _) in sorted(decisions.items()) if kind == "канон"]
        if debt:
            label = triage_label(self.root)
            self.issue("techdebt", debt, decisions, items, f"Техдолг после #{self.n}", label)
        if canon:
            repo = self.core.gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
            title = f"Находки ревью {repo}#{self.n}"
            self.issue("canon_issue", canon, decisions, items, title, "needs-triage", CANON_REPO)

    def issue(self, key, numbers, decisions, items, title, label, repo=None) -> None:
        """Тикет по находкам: новый или комментарий в уже заведённый этой веткой."""
        core, st = self.core, self.state
        record = st[key] or {"url": None, "numbers": []}
        new = [n for n in numbers if n not in record["numbers"]]
        if not new:
            return
        lines = [
            f"Находки ревью ошибок ветки `{st['branch']}` (тикет #{self.n}) со сценарием вреда — "
            "в ветке не исправлялись:"
            if record["url"] is None
            else f"{title} — ещё:",
            "",
        ]
        for n in new:
            lines.append(f"{n}. {items.get(n, '(текст находки — в отчёте ревью)')}")
            lines.append(f"   Решение сессии: {decisions[n][1]}")
        body = "\n".join(lines)
        if record["url"]:
            core.gh("issue", "comment", record["url"], "--body", body)
        else:
            area = re.search(r"`([^`:]+)", items.get(new[0], "")) or None
            full = f"{title}: {area.group(1)}" if area else title
            args = ["issue", "create", "--title", full, "--label", label, "--body", body]
            record["url"] = core.gh(*args, *(["--repo", repo] if repo else [])).splitlines()[-1]
        record["numbers"] = record["numbers"] + new
        st[key] = record
        st.save()

    # --- тесты и линтеры ---

    def step_checks(self):
        core, st = self.core, self.state
        if self.reviewed() is None:  # код после пропуска «только документы», ветка переписана
            st.go("review")
            return None
        current = core.head()
        green = st["checks"]
        if green and green["head"] == current:
            st.go("verify")
            return None
        docs_since = bool(green) and self.docs_only_since(green["head"])
        results = []
        for name, argv in core.profile_checks(self.root / PROFILE):
            if docs_since and name == "tests":
                note = f"не повторялся: после `{green['head'][:7]}` — только документы"
                results.append({"name": name, "code": 0, "note": note})
                continue
            core.say(f"ticket: {name} …")
            if Path(argv[0]).name != "run-log":
                argv = [sys.executable, str(core.HERE / "run-log"), "-l", name, "--", *argv]
            res = core.run(*argv, cwd=self.root)
            log = re.search(r"лог: (\S+)", res.stdout)
            results.append(
                {
                    "name": name,
                    "code": res.returncode,
                    "log": log.group(1) if log else None,
                    "tail": tail(res.stdout + res.stderr, 30),
                }
            )
        st["checks_run"] = {"head": current, "results": [self.brief(r) for r in results]}
        red = [r for r in results if r["code"] != 0]
        if not red:
            st["checks"] = st["checks_run"]
            st["attempts"].pop("checks", None)
            st.go("verify")
            return None
        attempt = st.attempt("checks")
        names = ", ".join(f"`{r['name']}` — код {r['code']}" for r in red)
        if attempt > LIMIT:
            return self.stop(f"тесты и линтеры красные {attempt}-й раз: {names}")
        st.go("checks")
        shown = "\n\n".join(f"`{r['name']}` — код {r['code']}:\n{r['tail']}" for r in red)
        body = f"{shown}\n\n{CHECKS_TASK.format(attempt=attempt, limit=LIMIT)}"
        return self.turn("Красные тесты или линтеры", body, long="тесты — до 45 минут")

    @staticmethod
    def brief(result: dict) -> dict:
        return {k: v for k, v in result.items() if k != "tail"}

    def recipe_touched(self, before: str) -> bool:
        """`/verify` менял рецепт `.claude/skills/run-*` — в дереве или своими коммитами."""
        core = self.core
        moved = (
            core.git("diff", "--name-only", before, "HEAD")
            if core.git_ok("cat-file", "-e", f"{before}^{{commit}}")
            else ""
        )
        tree = core.git("status", "--porcelain", "--untracked-files=all")
        paths = moved.splitlines() + [line[3:] for line in tree.splitlines()]
        return any(".claude/skills/run-" in p for p in paths)

    def docs_only_since(self, sha: str | None) -> bool:
        if not sha:
            return False
        if not self.core.git_ok("cat-file", "-e", f"{sha}^{{commit}}"):
            return False
        files = self.core.git("diff", "--name-only", "--no-renames", sha, "HEAD").splitlines()
        is_docs = load_review().is_docs
        return all(is_docs(f) for f in files)

    # --- /verify ---

    def step_verify(self):
        core, st = self.core, self.state
        decision = st["verify"] or {}
        passed = decision.get("verdict") in ("PASS", "пропущено")
        if not decision.get("needed") or (passed and self.docs_only_since(decision["head"])):
            st.go("sync")
            return None
        issue = core.gh_json("issue", "view", str(self.n), "--json", "title,body") or {}
        criteria = st.dir / "verify-criteria.txt"
        text = f"Тикет #{self.n}: {issue.get('title', '')}\n\n{issue.get('body', '')}".rstrip()
        if decision.get("why"):
            text += f"\n\nЧто проверить (решение сессии): {decision['why']}"
        criteria.write_text(text + "\n", encoding="utf-8")
        report = st.dir / "verify.json"
        before = core.head()
        stamp = report.stat().st_mtime_ns if report.exists() else None
        core.say("ticket: /verify — до 7 минут, ждите")
        res = core.canon("verify", str(report), "--criteria-file", str(criteria))
        core.say(res.stdout.rstrip())
        if report.exists() and report.stat().st_mtime_ns != stamp:  # прогон дошёл до `claude`
            st["cost"]["verify"] = (st["cost"].get("verify") or 0) + rollup_cost(report)
        lines = (res.stdout + res.stderr).splitlines()
        mark = "verify: ready-for-human: "
        human = [x.split(mark, 1)[1] for x in lines if mark in x]
        if human:
            st["ready_for_human"] = human[0]
        if res.returncode != 0:
            return self.stop(f"/verify не состоялся (код {res.returncode}): {tail(res.stderr, 5)}")
        left = [x for x in lines if x.startswith("verify: /verify оставил в рабочем дереве")]
        if "verify: пропущено" in res.stdout:
            verdict = "пропущено"
        else:
            m = re.search(r"— (PASS|FAIL) \(", res.stdout)
            verdict = m.group(1) if m else None
        if verdict is None:
            return self.stop("итог /verify не распознан: " + tail(res.stdout, 3))
        if left and self.recipe_touched(before):
            verdict = None  # рецепт менял сам /verify (файлом или коммитом) — итог не засчитан
        decision.update(verdict=verdict, head=before)
        st["verify"] = decision
        if verdict == "PASS":
            st["attempts"].pop("verify", None)
        if left:
            if st.attempt("verify_trace") > LIMIT:
                return self.stop(f"/verify оставляет след в worktree: {left[0]}")
            failed = ""
            if verdict == "FAIL":
                st.go("checks")  # после уборки и починки — тесты и /verify заново
                report_md = (st.dir / "verify.md").read_text(encoding="utf-8").strip()
                failed = f"/verify: FAIL — почини и это (коммит):\n{report_md}\n\n"
            return self.turn(
                "След /verify в worktree",
                f"{failed}{left[0]}\n{TRACE_TASK}",
            )
        if verdict != "FAIL":
            st.go("sync")
            return None
        attempt = st.attempt("verify")
        if attempt > LIMIT:
            return self.stop(f"/verify: FAIL {attempt}-й раз")
        st.go("checks")  # после починки — тесты и /verify заново
        report_md = (st.dir / "verify.md").read_text(encoding="utf-8").strip()
        return self.turn(
            "/verify: FAIL",
            f"{report_md}\n\nПочини в ветке (коммит, сообщение одной строкой `-m`); тесты и "
            f"/verify пройдут заново. Попытка {attempt} из {LIMIT}; критерий не подтверждается "
            "и починить нельзя — вопрос в `.ticket/stop.md`, не просить одобрить непроверенное.",
            long="тесты и /verify — до 45 минут",
        )

    # --- догон базы ---

    def merging(self) -> bool:
        return self.core.git_ok("rev-parse", "-q", "--verify", "MERGE_HEAD")

    def step_sync(self):
        core, st = self.core, self.state
        if self.merging():
            unresolved = core.git("diff", "--name-only", "--diff-filter=U").splitlines()
            if unresolved:
                return self.conflict_task(unresolved)
            return self.turn(
                "Слияние не доведено",
                "Конфликты разрешены, а коммита слияния нет: `git commit --no-edit`.",
            )
        if self.dirty():
            return self.turn("Незакоммиченные правки", f"{self.dirty()}\nЗакоммить или откати.")
        fetch = core.run("git", "fetch", "-q", "origin")
        if fetch.returncode != 0:
            return self.stop(f"git fetch origin: {tail(fetch.stderr, 3)}")
        if core.git_ok("merge-base", "--is-ancestor", "origin/main", "HEAD"):
            if (st["checks"] or {}).get("head") != core.head():
                st.go("checks")  # слитое из main — тесты на слитом коде
                return None
            st.go("body")
            return None
        (self.root / core.ANSWERS / "sync.md").unlink(missing_ok=True)  # объяснение прошлого догона
        base = core.git("rev-parse", "origin/main")
        install = ".canon.lock" in core.git("diff", "--name-only", "origin/main...HEAD").split()
        message = f"#{self.n}: догон origin/main"
        merge = core.run("git", "merge", "--no-edit", "-m", message, "origin/main")
        record = {"base": base, "conflict": merge.returncode != 0, "install": install}
        st["sync"] = record
        if merge.returncode == 0:
            st.go("checks")
            return None
        unresolved = core.git("diff", "--name-only", "--diff-filter=U").splitlines()
        if not unresolved:
            why = tail(merge.stderr or merge.stdout, 5)
            return self.stop(f"git merge origin/main отказал: {why}")
        record["files"] = unresolved
        attempt = st.attempt("sync")
        if attempt > LIMIT:
            core.run("git", "merge", "--abort")
            return self.stop(f"конфликт с origin/main {attempt}-й раз")
        st.go("sync")
        return self.conflict_task(unresolved)

    def conflict_task(self, files: list[str]):
        install = (self.state["sync"] or {}).get("install")
        body = CONFLICT_TASK.format(
            files=", ".join(f"`{f}`" for f in files),
            vendored=VENDORED_INSTALL if install else VENDORED,
        )
        return self.turn("Конфликт при догоне базы", body, long="тесты — до 45 минут")

    # --- тело PR и финиш ---

    def step_body(self):
        if self.state["heads"].get("body") != self.core.head():
            self.state.go("checks")  # коммит на шаге тела — проверки, /verify и догон заново
            return None
        summary = self.core.read_answer(self.root, "summary.md")
        if not summary:
            return self.turn("Что сделано", SUMMARY_TASK, long="финиш — push и PR, до минуты")
        (self.state.dir / "pr-body.md").write_text(self.pr_body(summary), encoding="utf-8")
        self.state.go("finish")
        return None

    def review_line(self) -> str:
        st = self.state
        review = st["review"] or {}
        if review.get("skipped"):
            return "Ревью ошибок: пропущено — только документы"
        decisions = st["decisions"] or {}
        count = {kind: 0 for kind in KINDS}
        for kind, _ in decisions.values():
            count[kind] += 1
        debt = (st["techdebt"] or {}).get("url")
        canon = (st["canon_issue"] or {}).get("url")
        parts = [
            f"исправлено {count['исправлено']}",
            f"отклонено {count['отклонено']}",
            f"техдолг {count['техдолг']}" + (f" ({debt})" if debt else ""),
            f"тикет канона {count['канон']}" + (f" ({canon})" if canon else ""),
            f"не записано {count['не записано']}",
        ]
        cost = st["cost"].get("review") or 0
        found = review.get("findings", 0)
        return f"Ревью ошибок: найдено {found} — {', '.join(parts)}; {cost:.2f} USD"

    def checks_line(self) -> str:
        checks = self.state["checks"] or {}
        results = "; ".join(
            f"`{r['name']}` — " + (r["note"] if r.get("note") else f"код {r['code']}")
            for r in checks.get("results", [])
        )
        return f"Тесты и линтеры на `{checks.get('head', '?')[:7]}`: {results}"

    def verify_line(self) -> str:
        decision = self.state["verify"] or {}
        if not decision.get("needed"):
            return f"/verify не нужен: {decision.get('why', '')}"
        cost = self.state["cost"].get("verify") or 0
        return f"/verify: {decision.get('verdict')} ({cost:.2f} USD)"

    def pr_body(self, summary: str) -> str:
        core, st = self.core, self.state
        lines = [summary, "", f"Closes #{self.n}", "", "## Evidence", ""]
        lines.append(f"- {self.checks_line()}")
        if note := core.read_answer(self.root, "checks.md"):
            lines.append(f"- Красное по пути: {note}")
        lines.append(f"- {self.verify_line()}")
        verify_md = st.dir / "verify.md"
        if (st["verify"] or {}).get("verdict") == "PASS" and verify_md.exists():
            report = verify_md.read_text(encoding="utf-8").strip()
            lines += ["", "<details><summary>Что проверил /verify</summary>", "", report]
            lines += ["", "</details>", ""]
        lines.append(f"- {self.review_line()}")
        for n, (kind, said) in (st["decisions"] or {}).items():
            if kind == "отклонено":
                lines.append(f"  - отклонено {n}: {said}")
        lines += [f"  - {w}" for w in st["review_warnings"] or []]
        if answer := core.read_answer(self.root, "answer.md"):
            lines.append(f"- Ответ владельца на вопрос по находке: {answer}")
        if sync := st["sync"]:
            text = f"- Догон базы: origin/main на `{sync['base'][:7]}`"
            files = ", ".join(sync.get("files", []))
            text += f", конфликт в {files}" if sync["conflict"] else ", без конфликта"
            if how := core.read_answer(self.root, "sync.md"):
                text += f"; {how}"
            lines.append(text)
        return "\n".join(lines) + "\n"

    def step_finish(self):
        core, st = self.core, self.state
        body = st.dir / "pr-body.md"
        if self.reviewed() is None:
            st.go("review")  # отчёт ревью устарел (ветка переписана) — ревью заново
            return None
        if not body.exists() or (st["heads"].get("body") != core.head()):
            st.go("checks")  # коммит после сборки тела — проверки, догон и тело заново
            return None
        issue = core.gh_json("issue", "view", str(self.n), "--json", "title") or {}
        args = ["--title", f"#{self.n}: {issue.get('title', '')}".rstrip(": ")]
        args += ["--body-file", str(body), "--review-report", str(st.dir / "review.json")]
        if st["weakening"]:
            args += ["--weakening", st["weakening"]]
        if st["ready_for_human"]:
            args += ["--ready-for-human", st["ready_for_human"]]
        res = core.canon("ship-finish", *args)
        core.say(res.stdout.rstrip())
        if res.returncode != 0:
            return self.stop(f"ship-finish отказал (код {res.returncode}): {tail(res.stderr, 5)}")
        url = re.search(r"https://\S+/pull/\d+", res.stdout)
        st["pr"] = url.group(0) if url else st["pr"]
        st["finish_line"] = tail(res.stdout, 1)
        st["finished_head"] = core.head()
        st["attempts"] = {}
        st.go("done", core.CI)
        return self.result()

    def result(self):
        st = self.state
        lines = [
            f"- PR: {st['pr']} — {st['finish_line']}",
            f"- {self.review_line()}",
            f"- {self.checks_line()}",
            f"- {self.verify_line()}",
        ]
        if sync := st["sync"]:
            how = "конфликт разрешён" if sync["conflict"] else "без конфликта"
            lines.append(f"- Догон базы: {how}")
        if st["weakening"]:
            lines.append(f"- Ослабление защиты: {st['weakening']}")
        body = (
            "Итог для владельца — передай одним сообщением:\n\n" + "\n".join(lines) + "\n\n"
            f"{BIND}\n\n{WAIT.format(long=self.core.LONG_CALL)}"
        )
        wait = f"scripts/canon/ticket wait {self.n}"
        return (self.core.DONE, f"Тикет #{self.n}: PR {st['pr']}", body, (wait,))

    # --- остановка ---

    def stop(self, reason: str):
        st = self.state
        st["stop"] = {"reason": reason, "step": st.step}
        st.go("stop")
        return self.step_stop()

    def step_stop(self):
        core, st = self.core, self.state
        reason = st["stop"]["reason"]
        question = core.read_answer(self.root, "stop.md")
        if not question:
            return self.turn(
                "Отчёт об остановке",
                f"Довести нельзя: {reason}.\nДопиши для владельца в `.ticket/stop.md`: вопрос на "
                "языке поведения продукта, варианты и рекомендацию — без имён функций и файлов. "
                "Данные маршрута (шаг, тесты, ревью, /verify) соберёт и опубликует скрипт.",
            )
        text = self.stop_report(reason, question)
        saved = st.dir / "stop-report.md"
        st.dir.mkdir(parents=True, exist_ok=True)
        saved.write_text(text, encoding="utf-8")
        where = self.publish_stop(text, saved)
        (self.root / core.ANSWERS / "stop.md").replace(st.dir / f"stop-{core.now()}.md")
        st["stop"]["where"] = where
        st.go("stopped", core.OWNER)
        body = (
            f"Отчёт об остановке — {where}, метка {LABEL}. Передай владельцу одним сообщением: "
            f"{reason}; вопрос — в отчёте. Ответил «продолжай» — продолжить маршрут:"
        )
        return (core.STOPPED, f"Тикет #{self.n}: остановка", body, (f"{self.cmd} --again",))

    def stop_report(self, reason: str, question: str) -> str:
        st = self.state
        lines = [f"Отчёт об остановке: {reason}", "", question, "", "## Данные маршрута", ""]
        step = (st["stop"] or {}).get("step")
        name = self.core.STEP_NAMES.get(step, step)
        lines.append(f"- Ветка `{st['branch']}`, head `{self.core.head()[:7]}`, шаг «{name}»")
        if st["review"]:
            lines.append(f"- {self.review_line()}")
        if run := st["checks_run"]:
            results = "; ".join(
                f"`{r['name']}` — код {r['code']}" + (f" (лог: {r['log']})" if r.get("log") else "")
                for r in run["results"]
            )
            lines.append(f"- Тесты и линтеры на `{run['head'][:7]}`: {results}")
        if st["verify"]:
            lines.append(f"- {self.verify_line()}")
        total = sum(v for v in st["cost"].values() if isinstance(v, (int, float)))
        lines.append(f"- Расход: {total:.2f} USD")
        return "\n".join(lines) + "\n"

    def publish_stop(self, text: str, saved: Path) -> str:
        """`ship-finish --unconfirmed`; не прошёл — то же кодом, без `gh pr merge` (#152):
        PR в черновик (черновик не сливается и с включённым авто-мёржем), метка, комментарий."""
        core = self.core
        res = core.canon("ship-finish", "--unconfirmed", text)
        if res.returncode == 0:
            m = re.search(r"отчёт об остановке — (.*?), ждёт", res.stdout)
            return m.group(1) if m else "опубликован"
        try:
            fields = ["--state", "open", "--json", "number,url,isDraft"]
            prs = core.gh_json("pr", "list", "--head", self.state["branch"], *fields)
            if prs:
                pr = prs[0]
                number = str(pr["number"])
                if not pr.get("isDraft"):
                    core.gh("pr", "ready", number, "--undo")
                core.gh("pr", "edit", number, "--add-label", LABEL)
                core.gh("pr", "comment", number, "--body", text)
                return f"{pr['url']} (черновик)"
            core.gh("issue", "edit", str(self.n), "--add-label", LABEL)
            core.gh("issue", "comment", str(self.n), "--body", text)
            return f"тикет #{self.n}"
        except core.Failure as exc:
            raise core.Failure(
                f"отчёт об остановке не опубликован (ship-finish: {tail(res.stderr, 2)}; {exc}); "
                f"текст — {saved}: передай владельцу в чате"
            ) from None


def main(argv: list[str], core) -> int:
    parser = argparse.ArgumentParser(prog="ticket ship", description=__doc__.split("\n\n")[0])
    parser.add_argument("ticket", type=int, help="номер тикета")
    parser.add_argument(
        "--again", action="store_true", help="продолжить после остановки (владелец ответил)"
    )
    args = parser.parse_args(argv)
    return Route(args.ticket, core).run(args.again)
