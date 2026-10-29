"""card — карточка PR пути v2 (#341, полный состав — #347; спека #329, «Карточка PR» в словаре).

Карточка — один комментарий оркестратора в PR простыми словами, который он обновляет по ходу
тикета: что сделано, сводка доказательств, находки без блокировки, выход за «Владения», решения
судьи, список карантина. Открывается разделом «Требует внимания» — владелец читает важное, а не
всё; дальше — все разделы состава по порядку, пустой — «нет». Данные — в
своём состоянии тикета (`t["card"]`): разделы по порядку, в разделе — строки с необязательным
ключом; строка с тем же ключом заменяет прежнюю, пустой текст её убирает. Следующие тикеты
перестройки пишут свои разделы и строки тем же `line`; порядок разделов — `ORDER`, раздел не из
него — в конце, в порядке появления.

Удалить: выключить переключатель, затем этот файл и вызовы `card` в `v2/send.py`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

MARK = "<!-- canon-card -->"  # первая строка карточки: по ней её узнают свои
ATTENTION = "Требует внимания"
DONE = "Что сделано"
EVIDENCE = "Сводка доказательств"  # тесты ревьюера в полном CI (#346), `/verify` без данных (#352)
FINDINGS = "Находки без блокировки"
BEYOND = "Выход за «Владения»"  # файлы диффа вне путей снимка тикета (#347, `v2/gates.py`)
JUDGE = "Решения судьи"  # спор с находкой ревью (#345, `v2/fixes.py`)
QUARANTINE = "Карантин"  # нестабильные тесты: позеленели при перезапуске (`v2/ci.py`)
ORDER = (ATTENTION, DONE, EVIDENCE, FINDINGS, BEYOND, JUDGE, QUARANTINE)
NOTHING = "ничего — тикет идёт сам"
EMPTY = "нет"


def blank() -> dict:
    """Пустая карточка: id комментария ещё нет, разделов нет."""
    return {"id": None, "sections": {}}


def line(card: dict, section: str, text: str | None, key: str | None = None) -> bool:
    """Строка в раздел: с ключом — заменяет прежнюю с тем же ключом (`text` пустой — убирает);
    без ключа — добавляет, если такой строки ещё нет. Итог — карточка изменилась."""
    rows = card.setdefault("sections", {}).setdefault(section, [])
    before = [dict(r) for r in rows]
    if key is not None:
        rows[:] = [r for r in rows if r.get("key") != key]
    if text and not any(r["text"] == text and r.get("key") == key for r in rows):
        rows.append({"text": text, **({"key": key} if key is not None else {})})
    return rows != before


def render(card: dict, n: int) -> str:
    """Текст комментария: метка, заголовок, «Требует внимания» всегда первым (пусто — «ничего»),
    затем все разделы состава по `ORDER` (пустой — «нет») и прочие, непустые."""
    sections = card.get("sections") or {}
    names = [*ORDER, *(s for s in sections if s not in ORDER)]
    parts = [MARK, f"## Карточка PR тикета #{n}"]
    for name in names:
        rows = sections.get(name) or []
        if not rows and name not in ORDER:
            continue
        body = (
            "\n".join(f"- {r['text']}" for r in rows)
            or f"- {NOTHING if name == ATTENTION else EMPTY}"
        )
        parts.append(f"### {name}\n\n{body}")
    parts.append("_Карточку ведёт оркестратор: она обновляется по ходу тикета._")
    return "\n\n".join(parts) + "\n"
