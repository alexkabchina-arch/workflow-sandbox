"""ticket fresh — запрос свежей копии прода под оркестратором: снимок на Mac, перенос в песочницу.

    scripts/canon/ticket fresh <N>

Из worktree тикета, только под оркестратором (`CANON_ORCHESTRATOR=1`, #307). Живого прода в
песочнице нет (решение `docs/decisions/2026-10-22-no-live-access-under-orchestrator.md` канона):
критерии «на проде» проверяются на обезличенной копии прода — базе-шаблоне, время её снимка сессия
получает окружением `CANON_PROD_COPY`. Нужны данные свежее снимка — этот вызов, а не своя
команда:

- запрос — в состояние тикета (`fresh`: время, копия сессии, прежнее ожидание; «Ждём:
  оркестратор»); вызов ждёт ответа до 40 минут (обычный Bash, не в фоне, timeout 2700000);
- оркестратор на следующем такте отвечает файлом `fresh.json` рядом с состоянием: снимок в
  песочнице новее копии сессии — сразу, иначе снимает копию на Mac командой проекта
  (`canon.sandboxSeedRefresh` с `CANON_SEED_FORCE=1`, принудительно — не чаще раза в час) и
  переносит её в песочницу; в ответе — время снимка, новее ли он копии сессии и что сделано;
- ответ есть — запрос снят, ожидание прежнее, задание — продолжить; снимок новее копии сессии —
  сначала пересоздать базу сессии из шаблона по правилам проекта, время снимка — в состояние
  (`copy`: его маршрут пишет в свидетельства тикета; неизвестно — пусто). Ответа за срок нет —
  запрос снят, сессия идёт с прежней копией.

Не под оркестратором — пояснение без запроса: копию снимает оркестратор.
Как устроено — `docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

# Такт с ревью соседа, снимок прода и перенос в песочницу — минуты; дольше — оркестратор не ответил.
WAIT = 40 * 60
POLL = 2
RECLONE = (
    "База сессии, клонированная из прежнего шаблона, — старая: пересоздай её из шаблона по "
    "правилам проекта (профиль) до проверок «на проде»."
)


def wait_limit() -> float:
    """Срок ожидания ответа, секунды: `CANON_FRESH_WAIT` (для тестов), иначе `WAIT`."""
    try:
        return float(os.environ.get("CANON_FRESH_WAIT") or WAIT)
    except ValueError:
        return WAIT


def span(seconds: float) -> str:
    return f"{int(seconds // 60)} мин" if seconds >= 60 else f"{int(seconds)} с"


def answer_for(path: Path, stamp: str, limit: float) -> dict | None:
    """Ответ оркестратора на запрос `stamp` — файл рядом с состоянием; за `limit` секунд нет —
    None. Ответ на прошлый запрос и битый файл — не ответ."""
    deadline = time.monotonic() + limit
    while True:
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found = None
        if isinstance(found, dict) and found.get("request") == stamp:
            return found
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        time.sleep(min(POLL, left))


def main(argv: list[str], core) -> int:
    parser = argparse.ArgumentParser(prog="ticket fresh", description=__doc__.split("\n\n")[0])
    parser.add_argument("ticket", type=int, help="номер тикета")
    n = parser.parse_args(argv).ticket
    if not core.orchestrated():
        body = (
            "Свежая копия прода — только под оркестратором: её снимает он на Mac и переносит в "
            "песочницу. В ручной сессии данные прода — по правилам проекта (профиль)."
        )
        return core.emit(core.TURN, "Свежая копия прода", body)
    st = core.State(n)
    stamp, before = core.now(), st["waiting"]
    # Копия сессии — оркестратору: снимок в песочнице новее её — ответ без снимка прода; прежнее
    # ожидание — вернуть после ответа (и `ticket start`, если запуск убьют посреди ожидания).
    st["fresh"] = {"stamp": stamp, "copy": core.prod_copy(st), "waiting": before}
    st["waiting"] = core.ORCHESTRATOR
    st.save()
    limit = wait_limit()
    core.say(
        "ticket: запрос свежей копии прода — оркестратор снимет её на Mac и перенесёт в "
        f"песочницу, до {span(limit)}, ждите"
    )
    found = answer_for(st.dir / core.FRESH_ANSWER, stamp, limit)
    st["fresh"], st["waiting"] = None, before
    ask, ship = core.command("ask", n), core.command("ship", n)
    go_on = (
        "Продолжай работу тикета с того места, где запросил копию; маршрут — дальше вызовом "
        f"({core.LONG_CALL}):"
    )
    if found is None:
        st.save()
        have = core.copy_phrase(core.prod_copy(st))
        body = (
            f"Оркестратор не ответил за {span(limit)} — запрос снят. Копия прода в песочнице "
            f"прежняя ({have}): продолжай с ней; без свежей критерий «на проде» не проверить — "
            f"вопрос владельцу (`{ask}`), запрос можно повторить (`{core.command('fresh', n)}`)."
            f"\n{go_on}"
        )
        return core.emit(core.TURN, f"Тикет #{n}: копия прода", body, (ship,))
    new = found.get("new_copy") is True
    if new:  # база сессии — из нового шаблона: его время, неизвестно — пусто (не прежнее)
        st["copy"] = found["copy"] if isinstance(found.get("copy"), str) else ""
    st.save()
    parts = [str(found.get("note") or "Оркестратор ответил на запрос.")]
    if new:
        parts.append(RECLONE)
    else:
        parts.append(
            "Свежей копии нет — продолжай с прежней; без свежей критерий «на проде» не "
            f"проверить — вопрос владельцу (`{ask}`)."
        )
    parts.append(go_on)
    return core.emit(core.TURN, f"Тикет #{n}: копия прода", "\n".join(parts), (ship,))
