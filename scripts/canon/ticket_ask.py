"""ticket ask — вопрос владельцу комментарием в тикет, пауза до ответа, затем ответ и следующий шаг.

    scripts/canon/ticket ask <N> ["вопрос"]

Из worktree тикета. Режим `/start`: владелец не в чате, вопросы копятся в тикете.

- Вопроса в ожидании нет — вопрос (аргумент или `.ticket/ask.md`) уходит комментарием в тикет,
  тикету — метка `ready-for-human`; в чат — только ссылка. Статус «остановка»: ждём владельца.
- Вопрос ждёт ответа — ответ ищется в тикете: комментарии после вопроса, кроме комментариев
  самого маршрута и отчётов об остановке. Владелец ответил в чате — сессия кладёт ответ дословно
  в `.ticket/reply.md`, скрипт дублирует его в тикет. Ответа нет — снова «остановка»; есть —
  метка снимается, ответ и следующий шаг печатаются заданием. Вопрос по находке на шаге разбора
  `ticket ship` — ответ записывается в `.ticket/answer.md`, как его ждёт `ticket ship`.

Команда в задании — без кавычек (режим В отклоняет «сложные»), поэтому вопрос идёт файлом.
Как устроено — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
from pathlib import Path

LABEL = "ready-for-human"
# Комментарии маршрута в тикете помечены: владелец и сессия пишут с одной учётной записи.
OWN = "<!-- canon-ticket"
ASK_MARK = OWN + "-ask {stamp} -->"
REPLY_MARK = OWN + "-reply -->"
# Отчёт об остановке (`ship-finish --unconfirmed`, `ticket ship`, Stop-хук) — тоже не ответ.
REPORT_MARK = "Отчёт об остановке"


def worktree(core, n: int) -> Path:
    if core.ticket_of(core.branch()) == n:
        return core.toplevel()
    return core.worktree_of(n) or core.toplevel()


def is_answer(body: str) -> bool:
    return bool(body) and OWN not in body and not body.startswith(REPORT_MARK)


def keep(st, path: Path, name: str) -> None:
    """Файл ответа агента — в каталог состояния: в worktree он не повторится на новом круге."""
    if path.exists():
        st.dir.mkdir(parents=True, exist_ok=True)
        path.replace(st.dir / name)


class Ask:
    def __init__(self, n: int, core):
        self.n, self.core = n, core
        self.cmd = f"scripts/canon/ticket ask {n}"
        self.state = core.State(n)
        self.root = worktree(core, n)
        self.dir = core.answers(self.root)

    def run(self, text: str | None) -> int:
        out = self.wait(text) if self.state["question"] else self.ask(text)
        # Без вопроса и без маршрута файл состояния не заводится: опечатка в номере не оставит
        # в `ticket status` строку тикета, которого маршрут не вёл.
        if self.state["question"] or self.state.path.exists():
            self.state.save()
        return self.core.emit(*out)

    def ask(self, text: str | None):
        core, st, n = self.core, self.state, self.n
        text = (text or "").strip() or core.read_answer(self.root, "ask.md")
        if not text:
            body = (
                "Вопроса нет. Вопрос владельцу — в `.ticket/ask.md` (инструмент Write): что "
                "неясно, на языке поведения продукта, без имён функций и файлов; варианты и что "
                "будет при каждом; рекомендация. Затем вызов:"
            )
            return (core.TURN, "Вопрос владельцу", body, (self.cmd,))
        stamp = core.now()
        comment = (
            f"{ASK_MARK.format(stamp=stamp)}\n**Вопрос сессии тикета #{n}**\n\n{text}\n\n---\n"
            "Ответ — комментарием в этом тикете (можно с телефона) или в чате сессии; затем в "
            "чате сессии: «продолжай»."
        )
        # Метка — до комментария: сбой между ними повторный вызов исправит без второго вопроса.
        core.gh("issue", "edit", str(n), "--add-label", LABEL)
        posted = core.gh("issue", "comment", str(n), "--body", comment)
        url = posted.splitlines()[0] if posted else f"тикет #{n}"  # gh печатает ссылку
        st["question"] = {"stamp": stamp, "url": url, "step": st.step}
        st["waiting"] = core.OWNER
        keep(st, self.dir / "ask.md", f"ask-{stamp}.md")
        # Ответ из чата на прошлый вопрос — не ответ на этот.
        keep(st, self.dir / "reply.md", f"reply-stale-{stamp}.md")
        return self.waiting(f"Вопрос — комментарием в тикете: {url}, метка {LABEL}.")

    def waiting(self, head: str):
        url = self.state["question"]["url"]
        body = (
            f"{head}\nВ чат вопрос не повторять — владельцу одна строка: «Вопрос по тикету "
            f"#{self.n} — в тикете: {url}». Ждём владельца: он отвечает в тикете или в чате и "
            "пишет «продолжай». Ответ из чата — дословно в `.ticket/reply.md` (Write). Затем вызов:"
        )
        return (self.core.STOPPED, f"Тикет #{self.n}: вопрос владельцу", body, (self.cmd,))

    def wait(self, text: str | None):
        core, st, n = self.core, self.state, self.n
        question = st["question"]
        held = ""
        if (text or "").strip() or core.read_answer(self.root, "ask.md"):
            held = (
                "\nНовый вопрос не отправлен: прежний ждёт ответа. Уточнение — после ответа, "
                "новым вызовом; `.ticket/ask.md` остаётся до него."
            )
        found = core.gh_json("issue", "view", str(n), "--json", "comments") or {}
        bodies = [(c.get("body") or "").strip() for c in found.get("comments") or []]
        mark = ASK_MARK.format(stamp=question["stamp"])
        at = max((i for i, b in enumerate(bodies) if mark in b), default=None)
        if at is None:
            raise core.Failure(f"вопрос {question['url']} не найден среди комментариев тикета #{n}")
        answers = [b for b in bodies[at + 1 :] if is_answer(b)]
        if reply := core.read_answer(self.root, "reply.md"):
            body = f"{REPLY_MARK}\n**Ответ владельца из чата** (передан сессией)\n\n{reply}"
            core.gh("issue", "comment", str(n), "--body", body)
            answers.append(reply)
            keep(st, self.dir / "reply.md", f"reply-{question['stamp']}.md")
        if not answers:
            return self.waiting(
                "Ответа нет: в тикете после вопроса ответа нет, `.ticket/reply.md` пуст." + held
            )
        answer = "\n\n".join(answers)
        note = ""
        try:
            core.gh("issue", "edit", str(n), "--remove-label", LABEL)
        except core.Failure as exc:
            note = f"\n(Метку {LABEL} снять не удалось: {exc} — сними руками.)"
        st["question"], st["waiting"] = None, core.AGENT
        st.dir.mkdir(parents=True, exist_ok=True)
        (st.dir / f"answer-{question['stamp']}.md").write_text(answer + "\n", encoding="utf-8")
        quoted = "\n".join(f"> {line}" if line else ">" for line in answer.splitlines())
        body = f"Ответ владельца:\n\n{quoted}\n{note}{held}\n\n{self.next_step(answer)}"
        return (
            core.TURN,
            f"Тикет #{n}: ответ владельца",
            body,
            (f"scripts/canon/ticket ship {n}",),
        )

    def next_step(self, answer: str) -> str:
        core, st = self.core, self.state
        if st.step is None:
            return (
                f"Продолжай `/implement {self.n}` с учётом ответа, без вопросов в чат (новый "
                f"вопрос — снова `ticket ask {self.n}`). Работа закоммичена — конец работы над "
                "тикетом, его задания выполнять до «готово» или «остановка». Вызов "
                f"({core.LONG_CALL}; ревью ошибок идёт 5–8 минут):"
            )
        if st.step == "findings" and st["asked"]:
            (self.dir / "answer.md").write_text(answer + "\n", encoding="utf-8")
            return (
                "Ответ записан в `.ticket/answer.md`. Решение по находке — в `.ticket/findings.md` "
                "по ответу: `исправлено` (доделано в ветке, коммит) или `техдолг` (отдельный "
                f"тикет заведёт скрипт). Затем вызов ({core.LONG_CALL}):"
            )
        name = core.STEP_NAMES.get(st.step, st.step)
        return f"Продолжай задание шага «{name}» с учётом ответа, затем вызов ({core.LONG_CALL}):"


def main(argv: list[str], core) -> int:
    parser = argparse.ArgumentParser(prog="ticket ask", description=__doc__.split("\n\n")[0])
    parser.add_argument("ticket", type=int, help="номер тикета")
    parser.add_argument("question", nargs="?", help="вопрос; без него — из `.ticket/ask.md`")
    args = parser.parse_args(argv)
    return Ask(args.ticket, core).run(args.question)
