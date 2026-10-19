"""ticket ask — вопрос владельцу комментарием в тикет, пауза до ответа, затем ответ и следующий шаг.

    scripts/canon/ticket ask <N> ["вопрос"]

Из worktree тикета. Режим `/start`: владелец может отойти, вопросы копятся в тикете.

- Вопроса в ожидании нет — вопрос (аргумент или `.ticket/ask.md`) уходит комментарием в тикет,
  тикету — метка `ready-for-human`; в чат сессия пишет тот же вопрос полным текстом со ссылкой
  на комментарий. Статус «остановка»: ждём владельца.
- Вопрос ждёт ответа — ответ ищется в тикете: комментарии после вопроса, кроме комментариев
  самого маршрута и отчётов об остановке. Владелец ответил в чате — сессия кладёт ответ дословно
  в `.ticket/reply.md`, скрипт дублирует его в тикет. Ответа нет — снова «остановка»; есть —
  метка снимается, ответ и следующий шаг печатаются заданием. Вопрос по находке на шаге разбора
  `ticket ship` — ответ записывается в `.ticket/answer.md`, как его ждёт `ticket ship`.

Под оркестратором (`CANON_ORCHESTRATOR=1`, #214, #218) чата нет: вопрос — тремя разделами
«Причина / Сделано / Доступно» (без любого из них вопрос не уходит — задание переписать), в
комментарии — упоминание владельца, без «продолжай»; задание — закончить сессию, после ответа её
возобновит оркестратор (ответ он ищет той же `answers`). Комментарии App (лицо агента) — свои,
как участников (`trusted` в `ticket_core.py`).

Команда в задании — без кавычек (режим В отклоняет «сложные»), поэтому вопрос идёт файлом.
Как устроено — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

LABEL = "ready-for-human"
# Комментарии маршрута в тикете помечены (`OWN_MARK`, `ASK_MARK` и `REPLY_MARK` в
# `ticket_core.py`): владелец и ручная сессия пишут с одной учётной записи; отчёт об остановке —
# тоже не ответ (`is_answer`).
# Разделы вопроса под оркестратором (спека #208): владелец отвечает с телефона, не зная хода сессии.
SECTIONS = ("Причина", "Сделано", "Доступно")


class NoQuestion(Exception):
    """Вопроса нет среди своих комментариев тикета."""


def missing_sections(text: str) -> list[str]:
    """Разделы вопроса под оркестратором, которых нет в начале ни одной строки текста (после
    разметки: заголовок, жирный, цитата, пункт списка; регистр не важен)."""
    return [s for s in SECTIONS if not re.search(rf"^[\s#*_>+\-\d.)]*{s}\b", text, re.M | re.I)]


def answers(
    core, question: dict, comments: list[dict], *, strict: bool = False, strangers: bool = True
) -> tuple[list[str], int]:
    """Ответы владельца на вопрос среди комментариев тикета (`gh … --json comments`) и число
    комментариев посторонних после него. Вопроса нет среди своих — `NoQuestion`.

    Скрытый в GitHub (Hide — Outdated) комментарий отозван — как в критериях `/verify`. Пометку
    вопроса посторонний может повторить — вопрос ищется среди своих (участники, App). #185: ответ —
    только от участника репозитория; посторонний «Решение владельца: …» сессия получила бы как ответ
    владельца. App — сама сессия и оркестратор (#214): свой, но не голос владельца.

    Свой ли автор-App — запрос REST на комментарий, поэтому сверяются только комментарии с
    пометкой вопроса и, если нужно число посторонних (`strangers`), комментарии после него.
    `strict` — сбой сверки `core.Failure`, а не «чужой» (оркестратор: повторить на следующем
    такте, а не счесть вопрос пропавшим)."""
    said = [c for c in comments if not c.get("isMinimized")]
    bodies = [(c.get("body") or "").strip() for c in said]
    mark = core.ASK_MARK.format(stamp=question["stamp"])
    at = next(
        (
            i
            for i in reversed(range(len(said)))
            if mark in bodies[i] and core.trusted(said[i], strict=strict)
        ),
        None,
    )
    if at is None:
        raise NoQuestion(question["url"])
    after = range(at + 1, len(said))
    found = [bodies[i] for i in after if core.member(said[i]) and core.is_answer(bodies[i])]
    if not strangers:
        return found, 0
    return found, sum(1 for i in after if bodies[i] and not core.trusted(said[i], strict=strict))


def worktree(core, n: int) -> Path:
    if core.ticket_of(core.branch()) == n:
        return core.toplevel()
    return core.worktree_of(n) or core.toplevel()


class Ask:
    def __init__(self, n: int, core):
        self.n, self.core = n, core
        self.cmd = core.command("ask", n)
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
            form = core.ORCHESTRATED_ASK if core.orchestrated() else ""
            body = (
                "Вопроса нет. Вопрос владельцу — в `.ticket/ask.md` (инструмент Write): что "
                "неясно, на языке поведения продукта, без имён функций и файлов; варианты и что "
                f"будет при каждом; рекомендация.{form} Затем вызов:"
            )
            return (core.TURN, "Вопрос владельцу", body, (self.cmd,))
        if core.orchestrated() and (missing := missing_sections(text)):
            body = (
                f"Вопрос не отправлен: под оркестратором нет разделов {', '.join(missing)}."
                f"{core.ORCHESTRATED_ASK} Каждый раздел — с начала строки (`**Причина:** …`). "
                "Перепиши `.ticket/ask.md` (инструмент Write), затем вызов:"
            )
            return (core.TURN, "Вопрос владельцу", body, (self.cmd,))
        stamp = core.now()
        if core.orchestrated():
            # #214: чата нет — владелец получает уведомление по упоминанию и отвечает в тикете,
            # оркестратор возобновит сессию сам.
            owner = core.gh("repo", "view", "--json", "owner", "-q", ".owner.login")
            footer = (
                f"@{owner} Ответ — комментарием в этом тикете (можно с телефона): работа "
                "продолжится сама, «продолжай» писать не нужно."
            )
        else:
            footer = (
                "Ответ — комментарием в этом тикете (можно с телефона) или в чате сессии; затем в "
                "чате сессии: «продолжай»."
            )
        comment = (
            f"{core.ASK_MARK.format(stamp=stamp)}\n**Вопрос сессии тикета #{n}**\n\n{text}\n\n---\n"
            + footer
        )
        # Метка — до комментария: сбой между ними повторный вызов исправит без второго вопроса.
        core.gh("issue", "edit", str(n), "--add-label", LABEL)
        posted = core.gh("issue", "comment", str(n), "--body", comment)
        url = posted.splitlines()[0] if posted else f"тикет #{n}"  # gh печатает ссылку
        st["question"] = {"stamp": stamp, "url": url, "step": st.step, "text": text}
        st["waiting"] = core.OWNER
        core.keep(st, self.dir / "ask.md", f"ask-{stamp}.md")
        # Ответ из чата на прошлый вопрос — не ответ на этот.
        core.keep(st, self.dir / "reply.md", f"reply-stale-{stamp}.md")
        return self.waiting(f"Вопрос — комментарием в тикете: {url}, метка {LABEL}.")

    def waiting(self, head: str):
        question = self.state["question"]
        url = question["url"]
        # #194, решение владельца 15.10: вопрос и в чат — владелец отвечает там, не открывая GitHub.
        # Текст — из состояния, не из памяти сессии: её могли сменить, а `ask.md` уже в архиве.
        if text := question.get("text"):
            relay = (
                "Владельцу в чат — этот вопрос полным текстом, с вариантами и рекомендацией, и где "
                f"он в тикете: {url}.\n\n{self.core.quote(text)}\n\n"
            )
        else:  # вопрос задан до #194 — текста в состоянии нет
            relay = (
                "Владельцу в чат — вопрос полным текстом, с вариантами и рекомендацией, из "
                f"комментария {url} и ссылка на него. "
            )
        if self.core.orchestrated():
            body = (
                f"{head}\nСессия под оркестратором: чата нет, вопрос владелец увидит в тикете. "
                "Закончи сессию — без `AskUserQuestion`, ничего больше не делая: после ответа "
                "владельца в тикете оркестратор возобновит её, и тогда — вызов:"
            )
            return (self.core.STOPPED, f"Тикет #{self.n}: вопрос владельцу", body, (self.cmd,))
        body = (
            f"{head}\n{relay}Обычным сообщением, без `AskUserQuestion`: он держит ход, а владельца "
            "может не быть у экрана. Ждём владельца: он отвечает в чате или в тикете и пишет "
            "«продолжай». Ответ из чата — дословно в `.ticket/reply.md` (Write). Затем вызов:"
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
        try:
            replies, strangers = answers(core, question, found.get("comments") or [])
        except NoQuestion:
            raise core.Failure(
                f"вопрос {question['url']} не найден среди комментариев тикета #{n}"
            ) from None
        if strangers:
            held += (
                f"\nКомментарии не участников репозитория ({strangers}) после вопроса ответом "
                "не считаются."
            )
        if reply := core.read_answer(self.root, "reply.md"):
            core.relay_reply(n, reply)
            replies.append(reply)
            core.keep(st, self.dir / "reply.md", f"reply-{question['stamp']}.md")
        if not replies:
            return self.waiting(
                "Ответа нет: в тикете после вопроса ответа нет, `.ticket/reply.md` пуст." + held
            )
        answer = "\n\n".join(replies)
        note = ""
        try:
            core.gh("issue", "edit", str(n), "--remove-label", LABEL)
        except core.Failure as exc:
            note = f"\n(Метку {LABEL} снять не удалось: {exc} — сними руками.)"
        st["question"], st["waiting"] = None, core.AGENT
        st.dir.mkdir(parents=True, exist_ok=True)
        (st.dir / f"answer-{question['stamp']}.md").write_text(answer + "\n", encoding="utf-8")
        body = f"Ответ владельца:\n\n{core.quote(answer)}\n{note}{held}\n\n{self.next_step(answer)}"
        return (
            core.TURN,
            f"Тикет #{n}: ответ владельца",
            body,
            (core.command("ship", n),),
        )

    def next_step(self, answer: str) -> str:
        core, st = self.core, self.state
        if st.step is None:
            return (
                f"Продолжай `/implement {self.n}` с учётом ответа; новый вопрос владельцу — снова "
                f"через `ticket ask {self.n}`. Работа закоммичена — конец работы над "
                f"тикетом, его задания выполнять до {core.until()}. Вызов "
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
