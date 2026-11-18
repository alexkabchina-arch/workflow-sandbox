"""snapshot — снимок тикета пути v2 (#347; спека #329, «Снимок тикета» в словаре).

При занятии места оркестратор снимает **снимок тикета**: критерии приёмки, «Владения» и флаги
на этот момент, пункты приёмки с номерами — критерии и примеры замысла, как их видит `/verify`
(`points`, #352), — и отпечаток тела (`take`). Дальше допуск сверяет со снимком, а не с текущим
текстом (флаги — `Gates.flags`; критерии — условие 4, #352). Правка тела после старта — от кого
угодно — остановка с кодом `тикет-изменён` (`edited`): проверки не идут по старой версии.
`agent:go` после неё — проверка постановки заново (`v2/intake.py`, #355) и новый снимок.

Отпечаток — тело без отметок чек-листа (`[x]` = `[ ]`), концов строк и пробелов в конце строк:
отметка выполненного критерия — не правка постановки.

Удалить: выключить переключатель, затем этот файл; `snap` и `edited` оркестратора, их вызовы в
`begin`, `advance` и `resume_stopped`, ветку `edited` в `report`, снимок в `Gates.flags`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import hashlib
import re

CODE = "тикет-изменён"
GO = "проверка постановки заново и новый снимок тикета по его нынешнему тексту, затем {go}"
ADVICE = (
    "посмотрите правку тикета. Уточнение, которое не меняет замысел, — `agent:go`: работа "
    "продолжится и будет проверяться по новому тексту. Правка меняет критерии или «Владения» — "
    "`agent:retry`: сделанное по старой версии может не подходить. **Риск `agent:go`:** работа, "
    "начатая по старому тексту, встретит новые критерии только на допуске — расхождение всплывёт "
    "там отказом, а не сразу."
)
CRITERIA = ("Acceptance criteria", "Критерии приёмки")
BOX = re.compile(r"^(\s*[-*]\s*)\[[ xX]\]", re.M)
ITEM = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(.+?)\s*$", re.M)


def normalized(body: str | None) -> str:
    """Тело для отпечатка: отметки чек-листа сброшены, концы строк — `\\n`, без пробелов в конце
    строк."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = BOX.sub(r"\1[ ]", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def digest(body: str | None) -> str:
    return hashlib.sha256(normalized(body).encode("utf-8")).hexdigest()


def criteria(body: str | None) -> list[str]:
    """Пункты чек-листа раздела критериев приёмки (`## Acceptance criteria` или
    `## Критерии приёмки`) — до следующего заголовка `##`."""
    for title in CRITERIA:
        pattern = rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)"
        if found := re.search(pattern, body or "", re.M | re.S | re.I):
            return ITEM.findall(found.group(1))
    return []


def take(
    body: str | None, claims: dict | None, flags: list[str], at: str, points: list | None = None
) -> dict:
    """Снимок: время, отпечаток тела, критерии, «Владения» (пути брифа; нет строк по образцу —
    None: тикет идёт один), флаги и пункты приёмки (`v2/criteria.py`, `points`; их считает
    оркестратор — условие допуска 4)."""
    snap = {
        "at": at,
        "digest": digest(body),
        "criteria": criteria(body),
        "paths": None if claims is None else list(claims.get("paths") or []),
        "flags": list(flags),
    }
    if points is not None:  # нет — допуск считает пункты по телу тикета
        snap["points"] = list(points)
    return snap


def edited(snap: dict | None, body: str | None) -> bool:
    """Тело тикета изменилось после снимка; снимка нет — не сверяется."""
    return bool(snap) and snap.get("digest") != digest(body)
