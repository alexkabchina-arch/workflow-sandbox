"""ticket wait — ожидание мёржа PR тикета и уборка сразу после него.

    scripts/canon/ticket wait <N> [--poll СЕК] [--timeout СЕК]

После `ticket ship N` («готово»): сессия сама дожидается слияния, владелец получает итог «PR слит
в ЧЧ:ММ». Опрос PR (`gh pr view`) раз в `--poll` секунд (по умолчанию минута), не дольше
`--timeout` (20 минут). Git и уборка — из главного чекаута, каталог сессии только запоминается:
worktree тикета может убрать чужая уборка слитых PR, пока сессия ждёт (#193). Убран — итог тот же,
с заданием выйти из удалённой папки; вызов из уже убранной — по пути скрипта главного чекаута. По
состоянию PR:

- слит — уборка механикой `session-start` (`retire_merged`): worktree, ветка, проектная
  `cleanup`, догон главного чекаута, метка `in-progress`; итог «PR слит в ЧЧ:ММ». Вызов из его
  worktree — метка снята, задание выйти из worktree и вызвать снова из главного чекаута;
- красный CI — задание починить и `ticket ship N` (отметка на новый head); в приложении с
  Auto-fix — конец хода, чинит разбуженная сессия;
- сторож `review-guard` красный при стоящей отметке — не красный CI (сбой GitHub или отметка позже
  его ожидания, #176): перезапуск упавшего прогона сторожа (`rerun_guard` из `session-start`), код
  не правится, ожидание идёт дальше. Перезапуск не помог (третья попытка, прогон не сторожа
  канона) — задание прочитать лог: причина в ветке — починка, иначе отчёт об остановке; круг, как
  у красного CI. Сбой `gh` на перезапуске — повтор на следующем опросе, по таймауту — то же
  задание;
- конфликт с `main` или отставание (`BEHIND`, кругом не считается) — маршрут на догон базы,
  задание `ticket ship N` (A2.4); зелёный без отметки `review` — на финиш;
- черновик, авто-мёрж выключен, закрыт — сразу «ждёт человека»;
- идёт CI или ждёт правил — ждать; по таймауту «ждёт: CI идёт» или «ждёт человека»;
- зелёный `CLEAN` с авто-мёржем, а GitHub не слил, — ждать; по таймауту (`CLEAN` на двух опросах
  подряд: первый на дедлайне — чаще миг до авто-мёржа) действие владельцу «GitHub не слил:
  нажмите Squash and merge» со ссылкой на PR. `CLEAN` при этом — не меньше `CLEAN_GRACE` (не
  больше таймаута): авто-мёрж под нагрузкой GitHub запаздывает на минуты, а опрос — раз в минуту.

Красный CI и конфликт — не больше двух кругов каждого (head, увиденный ожиданием, — круг),
третий — остановка с отчётом через `ticket ship N` (решение `2026-10-12-zero-mechanisms.md`:
предел вместо счётчика возвратов). Прерванное ожидание остаётся ожиданием (шаг «готово»): уборку
слитого PR сделает следующий старт. Как устроено — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import os
import time
import types
from datetime import datetime
from pathlib import Path

LIMIT = 2
POLL, TIMEOUT = 60.0, 1200.0
CLEAN_GRACE = 240.0  # «GitHub не слил» — после стольких секунд CLEAN подряд
FIELDS = (
    "state,mergedAt,isDraft,autoMergeRequest,mergeable,mergeStateStatus,headRefOid,"
    "baseRefName,statusCheckRollup"
)
MERGEABLE = {"CLEAN", "HAS_HOOKS"}  # сливать можно: проверки зелёные, требования выполнены
PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}

AUTOFIX = """В приложении с Auto-fix (включён после привязки PR) — закончи ход: событие CI разбудит
сессию, и делать это задание будет она. Без Auto-fix (терминал) — сейчас."""


def load_session_start() -> types.ModuleType:
    """`session-start` рядом со скриптом — его механика уборки."""
    path = Path(__file__).resolve().parent / "session-start"
    module = types.ModuleType("canon_session_start_for_ticket")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def session_dir() -> Path | None:
    """Каталог сессии; None — его уже нет (worktree убран): без него не работают ни git, ни
    `Path.cwd()`."""
    try:
        return Path.cwd().resolve()
    except FileNotFoundError:
        return None


def running(pr: dict) -> bool:
    for c in pr.get("statusCheckRollup") or []:
        if (c.get("status") or "").upper() not in ("COMPLETED", ""):
            return True
        if (c.get("conclusion") or c.get("state") or "").upper() in PENDING | {""}:
            return True
    return False


def local_time(stamp: str | None) -> str:
    try:
        return (
            datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
            .astimezone()
            .strftime("%H:%M")
        )
    except ValueError:
        return "?"


class Wait:
    def __init__(self, number: int, core, here: Path | None):
        self.n, self.core = number, core
        # Разбор PR и уборка — те же, что у старта: красный CI, отметка, конфликт с `main`.
        self.start = load_session_start()
        # Вся работа — из главного чекаута: worktree сессии (`here`) могут убрать во время ожидания.
        try:
            self.main = Path(self.start.worktrees()[0]["path"])
        except self.start.StartError as exc:
            raise core.Failure(str(exc)) from None
        os.chdir(self.main)
        self.here = here
        self.clean: tuple = ((), 0.0)  # (состояние слияния и head, с какого момента подряд)
        self.state = core.State(number)
        self.ship = core.command("ship", number)
        self.guard_error = ""  # сбой `gh` на последнем перезапуске сторожа
        self.prev: dict = {}  # PR на прошлом опросе
        self.again = core.command("wait", number)

    def run(self, poll: float, timeout: float) -> int:
        core, st = self.core, self.state
        if st.step != "done" or not st["pr"]:
            name = core.STEP_NAMES.get(st.step, st.step or "не начат")
            body = (
                f"Маршрут тикета #{self.n} на шаге «{name}», PR с отметкой ещё нет: сначала "
                f"`ticket ship {self.n}` до «готово», затем ожидание. Вызов из worktree тикета "
                f"({core.LONG_CALL}):"
            )
            return core.emit(core.TURN, f"Тикет #{self.n}: до ожидания", body, (self.ship,))
        st["wait"] = st["wait"] or {}  # круги; `ticket ship --again` их обнуляет
        st["waiting"] = core.CI
        st.save()
        deadline = time.monotonic() + timeout
        while True:
            try:
                pr = core.gh_json("pr", "view", st["pr"], "--json", FIELDS) or {}
            except core.Failure as exc:  # сбой сети на одном опросе — не конец ожидания
                core.say(f"ticket wait: опрос PR не удался — {exc}")
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll)
                continue
            out = self.decide(pr)
            key = (pr.get("mergeStateStatus"), pr.get("headRefOid"))
            if key != self.clean[0]:
                self.clean = (key, time.monotonic())
            if out is None and time.monotonic() >= deadline:
                out = self.timed_out(pr, min(CLEAN_GRACE, timeout))
            if out is not None:
                st.save()
                return core.emit(*out)
            self.prev = pr
            time.sleep(poll)

    # --- состояния PR ---

    def decide(self, pr: dict):
        state = pr.get("state")
        if state == "MERGED":
            return self.merged(pr)
        if state == "CLOSED":
            return self.human("PR закрыт без мёржа")
        if pr.get("isDraft"):
            return self.human("PR — черновик (отчёт об остановке или придержан владельцем)")
        # Придержанный PR в `ticket ship` не идёт: `ship-finish` включил бы авто-мёрж снова.
        if not pr.get("autoMergeRequest"):
            return self.human("авто-мёрж выключен — PR придержал владелец")
        head = pr.get("headRefOid") or ""
        if checks := self.start.failed_checks(pr):
            return self.red(head, checks)
        if (guard := self.start.failed_guard(pr)) and (out := self.guard(head, guard)):
            return out
        main_base = pr.get("baseRefName", "main") == "main"
        if self.start.is_conflicting(pr):
            return self.conflict(head)
        if main_base and pr.get("mergeStateStatus") == "BEHIND":
            self.to_step("sync")
            body = (
                f"Ветка отстала от main, а правила защиты требуют её догнать — авто-мёрж ждёт. "
                f"Вызов `ticket ship {self.n}` догонит базу мёржем (шаг «догон базы»), проверки "
                f"и отметка — на новый head; затем снова ожидание. {self.where()}"
            )
            return self.task("Догон базы", body)
        if not running(pr) and not self.start.is_marked(pr):
            self.to_step("finish")  # `ship-finish` идемпотентен: допоставит отметку
            body = (
                f"CI зелёный, а отметки ревью на head {head[:7]} нет — финиш оборвался. Вызов "
                f"`ticket ship {self.n}` поставит её. {self.where()}"
            )
            return self.task("Нет отметки ревью", body)
        return None

    def merged(self, pr: dict):
        core, st = self.core, self.state
        when = local_time(pr.get("mergedAt"))
        st["merged_at"] = pr.get("mergedAt")
        st["waiting"] = "—"
        here = self.here if self.here is not None and self.here.exists() else None
        try:
            gone, problems = self.start.retire_merged(st["branch"], pr["headRefOid"], here)
        except self.start.StartError as exc:
            gone, problems = False, [f"сбой: {exc}"]
        line = f"PR {st['pr']} слит в {when}"
        path = core.worktree_of(self.n)
        if not gone and path is not None and here and here.is_relative_to(path.resolve()):
            body = (
                f"{line}. Сессия ещё в worktree тикета ({path}) — его не убрать из-под неё. Выйди: "
                "ExitWorktree с action keep (в терминале, где сессия открыта в worktree, выйти "
                "нельзя — передай владельцу итог, уборку сделает следующий старт). Затем вызов из "
                "главного чекаута:"
            )
            return (core.TURN, f"Тикет #{self.n}: слит, уборка", body, (self.again,))
        tail = "worktree и ветка убраны, главный чекаут догнан"
        if problems:
            tail = f"уборка не доделана: {'; '.join(problems)}"
        body = f"Итог владельцу одной строкой: «{line}; {tail}»."
        if here is None:  # worktree сессии убрали раньше, чем дошла эта уборка
            body += (
                f" Папки сессии больше нет — уборка шла из главного чекаута ({self.main}). "
                "Выйди из неё: ExitWorktree с action keep (в терминале выйти нельзя — передай "
                "владельцу итог); дальнейшие вызовы — из главного чекаута."
            )
        return (core.DONE, f"Тикет #{self.n}: {line}", body, ())

    def red(self, head: str, checks: list[str]):
        k = self.round("ci", head)
        if k > LIMIT:
            return self.stop(f"красный CI {k}-й раз после финиша: {', '.join(checks)}")
        body = (
            f"Красный CI на {head[:7]}: {', '.join(checks)}. Круг {k} из {LIMIT}.\n{AUTOFIX}\n"
            f"Найди причину и почини в ветке тикета (коммит, сообщение одной строкой `-m`); "
            f"затем `ticket ship {self.n}` — проверки и отметка на новый head без второго "
            f"ревью — и снова ожидание. {self.where()}"
        )
        return self.task("Красный CI", body)

    def guard(self, head: str, item: dict):
        """Сторож упал при стоящей отметке: перезапуск его прогона; не помог — задание по логу."""
        core, guard = self.core, self.start.GUARD
        try:
            action, why = self.start.rerun_guard(item)
        except self.start.StartError as exc:  # сбой `gh` — следующий опрос попробует снова
            self.guard_error = f"перезапуск не удался — {exc}"
            core.say(f"ticket wait: сторож {guard} не перезапущен — {exc}")
            return None
        self.guard_error = ""
        if action == "stuck":
            return self.guard_stuck(head, item, why)
        if action == "rerun":
            core.say(f"ticket wait: сторож {guard} упал при стоящей отметке — прогон перезапущен")
        return None

    def guard_stuck(self, head: str, item: dict, why: str):
        k = self.round("guard", head)
        if k > LIMIT:
            return self.stop(
                f"сторож {self.start.GUARD} красный при стоящей отметке {k}-й раз: {why}"
            )
        task = self.start.guard_task(item, why, f"`ticket ship {self.n}`")
        return self.task("Сторож review-guard", f"{task} Круг {k} из {LIMIT}. {self.where()}")

    def conflict(self, head: str):
        k = self.round("conflict", head)
        if k > LIMIT:
            return self.stop(f"конфликт с main {k}-й раз после финиша")
        self.to_step("sync")
        body = (
            f"Конфликт с main на {head[:7]} — авто-мёрж его не сольёт. Круг {k} из {LIMIT}.\n"
            f"{AUTOFIX}\nВызов `ticket ship {self.n}` догонит базу мёржем (шаг «догон базы», "
            f"конфликт — по его заданию) и поставит отметку на новый head; затем снова "
            f"ожидание. {self.where()}"
        )
        return self.task("Конфликт с main", body)

    def human(self, why: str):
        return self.owner(f"PR {self.state['pr']} ждёт человека: {why}")

    def owner(self, line: str):
        self.state["waiting"] = self.core.OWNER
        return self.out_waiting(line)

    def timed_out(self, pr: dict, grace: float):
        if guard := self.start.failed_guard(pr):
            if self.guard_error:
                return self.guard_stuck(pr.get("headRefOid") or "", guard, self.guard_error)
            # Перезапуск только что ушёл, а сводка проверок ещё прежняя — CI идёт.
            self.state["waiting"] = self.core.CI
            return self.out_waiting(f"PR {self.state['pr']} ждёт: перезапуск сторожа review-guard")
        if running(pr):
            self.state["waiting"] = self.core.CI
            return self.out_waiting(f"PR {self.state['pr']} ждёт: CI идёт")
        # Сюда доходит PR с авто-мёржем, без красных проверок и с отметкой (иначе — `decide`):
        # CLEAN значит, что сливать можно, а GitHub не слил — владельцу нужна кнопка, не статус.
        # Впервые увиденный на дедлайне — чаще миг до авто-мёржа: ещё опросы, пока CLEAN не
        # продержится `grace` секунд (авто-мёрж под нагрузкой GitHub запаздывает на минуты).
        if pr.get("mergeStateStatus") in MERGEABLE:
            prev = self.prev
            if (prev.get("mergeStateStatus"), prev.get("headRefOid")) != self.clean[0]:
                return None
            if time.monotonic() - self.clean[1] < grace:
                return None
            line = f"PR {self.state['pr']} ждёт человека — GitHub не слил: нажмите Squash and merge"
            return self.owner(f"{line} (кнопки не видно — сначала Disable auto-merge)")
        why = "обязательная проверка или одобрение"
        if pr.get("mergeStateStatus") not in (None, "BLOCKED"):
            why = f"состояние слияния {pr['mergeStateStatus']}"
        return self.human(why)

    def out_waiting(self, line: str):
        body = (
            f"Итог владельцу одной строкой: «{line}». Ожидание прервано, маршрут остаётся на "
            f"ожидании: повторный `ticket wait {self.n}` или уборка следующего старта доведут "
            "его после мёржа."
        )
        return (self.core.STOPPED, f"Тикет #{self.n}: {line}", body, ())

    # --- круги и переходы ---

    def round(self, kind: str, head: str) -> int:
        """Круг — head, увиденный ожиданием красным (конфликтным); тот же head — тот же круг."""
        seen = self.state["wait"].setdefault(kind, [])
        if head not in seen:
            seen.append(head)
        return len(seen)

    def to_step(self, step: str) -> None:
        """Маршрут `ticket ship` — с шага `step`: «догон базы» (A2.4) или «финиш»."""
        st = self.state
        st["step"], st["since"], st["waiting"] = step, self.core.now(), self.core.AGENT

    def stop(self, reason: str):
        """Третий круг — остановка: отчёт собирает и публикует `ticket ship` (шаг «остановка»)."""
        core, st = self.core, self.state
        # После ответа владельца — финиш: `ship-finish` снимет черновик отчёта и включит авто-мёрж.
        st["stop"] = {"reason": reason, "step": "finish"}
        st["step"], st["since"], st["waiting"] = "stop", core.now(), core.AGENT
        body = (
            f"Довести нельзя: {reason}. Допиши для владельца в `.ticket/stop.md`: вопрос на языке "
            "поведения продукта, варианты и рекомендацию — без имён функций и файлов. Отчёт об "
            f"остановке опубликует `ticket ship {self.n}`. {self.where()}"
        )
        return self.task("Отчёт об остановке", body)

    def where(self) -> str:
        path = self.core.worktree_of(self.n)
        return f"Вызовы — из worktree тикета ({path})." if path else ""

    def task(self, title: str, body: str):
        core = self.core
        hint = f"Вызов ({core.LONG_CALL}):"
        return (core.TURN, f"Тикет #{self.n}: {title}", f"{body}\n\n{hint}", (self.ship,))


def main(argv: list[str], core) -> int:
    parser = argparse.ArgumentParser(prog="ticket wait", description=__doc__.split("\n\n")[0])
    parser.add_argument("ticket", type=int, help="номер тикета")
    parser.add_argument(
        "--poll",
        type=float,
        default=POLL,
        help="пауза между опросами, сек (по умолчанию %(default)g)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=TIMEOUT,
        help="предел ожидания, сек (по умолчанию %(default)g)",
    )
    args = parser.parse_args(argv)
    here = session_dir()
    if here is None:  # вызов из уже удалённой папки: главный чекаут ищется от чекаута скрипта
        os.chdir(Path(__file__).resolve().parent)
    return Wait(args.ticket, core, here).run(args.poll, args.timeout)
