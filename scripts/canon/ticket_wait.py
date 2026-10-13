"""ticket wait — ожидание мёржа PR тикета и уборка сразу после него.

    scripts/canon/ticket wait <N> [--poll СЕК] [--timeout СЕК]

После `ticket ship N` («готово»): сессия сама дожидается слияния, владелец получает итог «PR слит
в ЧЧ:ММ». Опрос PR (`gh pr view`) раз в `--poll` секунд (по умолчанию 4 минуты), не дольше
`--timeout` (20 минут). По состоянию PR:

- слит — уборка механикой `session-start` (`retire_merged`): worktree, ветка, проектная
  `cleanup`, догон главного чекаута, метка `in-progress`; итог «PR слит в ЧЧ:ММ». Вызов из его
  worktree — метка снята, задание выйти из worktree и вызвать снова из главного чекаута;
- красный CI — задание починить и `ticket ship N` (отметка на новый head); в приложении с
  Auto-fix — конец хода, чинит разбуженная сессия;
- конфликт с `main` или отставание (`BEHIND`, кругом не считается) — маршрут на догон базы,
  задание `ticket ship N` (A2.4); зелёный без отметки `review` — на финиш;
- черновик, авто-мёрж выключен, закрыт — сразу «ждёт человека»;
- идёт CI или ждёт правил — ждать; по таймауту «ждёт: CI идёт» или «ждёт человека».

Красный CI и конфликт — не больше двух кругов каждого (head, увиденный ожиданием, — круг),
третий — остановка с отчётом через `ticket ship N` (решение `2026-10-12-zero-mechanisms.md`:
предел вместо счётчика возвратов). Прерванное ожидание остаётся ожиданием (шаг «готово»): уборку
слитого PR сделает следующий старт. Как устроено — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import time
import types
from datetime import datetime
from pathlib import Path

LIMIT = 2
POLL, TIMEOUT = 240.0, 1200.0
FIELDS = (
    "state,mergedAt,isDraft,autoMergeRequest,mergeable,mergeStateStatus,headRefOid,"
    "baseRefName,statusCheckRollup"
)
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
    def __init__(self, number: int, core):
        self.n, self.core = number, core
        self.state = core.State(number)
        # Разбор PR и уборка — те же, что у старта: красный CI, отметка, конфликт с `main`.
        self.start = load_session_start()
        self.ship = f"scripts/canon/ticket ship {number}"
        self.again = f"scripts/canon/ticket wait {number}"

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
            if out is None and time.monotonic() >= deadline:
                out = self.timed_out(pr)
            if out is not None:
                st.save()
                return core.emit(*out)
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
        here = Path.cwd().resolve()
        try:
            gone, problems = self.start.retire_merged(st["branch"], pr["headRefOid"], here)
        except self.start.StartError as exc:
            gone, problems = False, [f"сбой: {exc}"]
        line = f"PR {st['pr']} слит в {when}"
        path = core.worktree_of(self.n)
        if not gone and path is not None and here.is_relative_to(path.resolve()):
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
        self.state["waiting"] = self.core.OWNER
        line = f"PR {self.state['pr']} ждёт человека: {why}"
        return self.out_waiting(line)

    def timed_out(self, pr: dict):
        if running(pr):
            self.state["waiting"] = self.core.CI
            return self.out_waiting(f"PR {self.state['pr']} ждёт: CI идёт")
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
    parser.add_argument("--poll", type=float, default=POLL, help="пауза между опросами, сек")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, help="предел ожидания, сек")
    args = parser.parse_args(argv)
    return Wait(args.ticket, core).run(args.poll, args.timeout)
