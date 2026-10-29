"""criteria — формат критериев тикета и три исхода `/verify` пути v2 (#351; спека #329,
«Проверка запуском и инструмент проверки», «Тикет v2»).

**Формат критериев.** Критерий — пункт чек-листа раздела `## Acceptance criteria` (или
`## Критерии приёмки`), как в снимке тикета (`v2/snapshot.py`). У каждого — **номер** и **вид
доказательства**:

- номер — явный в начале пункта (`1.`, `2)`, `К3:`, можно жирным) или первый свободный по порядку
  (явные номера пунктов без номера не занимают); явные номера не повторяются;
- вид — в скобках в конце пункта (`(тест)`, `(скриншот или число)`, `(команда)`) или после
  «чем доказать:»; виды — `KINDS`: тест, скриншот (снимок), число, команда. Скобки или «чем
  доказать:» без этих слов — часть текста, вида у пункта нет (`kinds` пуст): такой критерий
  `/verify` всё равно проверяет, а проверка постановки (#355) его отклонит.

Пункты «после выката» (#301: «После выката: …», «… на проде после выката», подраздел «После
выката») — не критерий PR: в пункты `/verify` не входят, их переносит в тело PR маршрут.

**Примеры замысла** — пункты раздела `## Сейчас → должно быть` (или `## Примеры`), номера `П1`,
`П2`…: «должно быть» из них `/verify` сверяет с выводом инструмента, а число «должно быть» сверяет
программа (#352, `checked`): первое число после «должно быть» (нет его — после последней
стрелки; «не должно быть …» не сверяется); пример «прошло», а числа нет в выводе команд на
ветке, — «не прошло». **Находка ревью со сценарием `/verify`** (`canon_review.v2.verify`) — тоже
пункт, номер `Н<номер находки>` (`findings`, #352).

**Три исхода** по каждому критерию и примеру — `прошло`, `не прошло`, `не проверить`, каждый с
доказательством: записанные команды инструмента проверки с машиночитаемым выводом, где они шли
(ветка или `main` — «до» бага). Номера пунктов — в схеме ответа прогона (`schema`). Итог прогона
считает программа (`judge`), а не модель: есть «не прошло» — FAIL (возврат на починку); иначе
есть «не проверить» — BLOCKED без возврата; иначе — PASS. Ответ без итога по пункту, с лишним или
повторным номером, без доказательства — не по схеме: итог не засчитан.

Причина остановки маршрута при BLOCKED начинается с `UNVERIFIABLE` (`unverifiable`): после неё —
`POINTS` (пункт не проверить — остановка только этого тикета) или причина среды (нет инструмента,
красный «доктор» — код «среда» и пауза очереди), `kind` их различает.

**Сводка без данных** (`summary`, #352) — для PR и карточки: только номер пункта, исход и число
команд по месту. Команды, их вывод, заметки и отчёт модели в неё не входят никогда: доказательства
целиком хранит оркестратор (`v2/evidence.py`).

Удалить: выключить переключатель, затем этот файл; ветку пути v2 в `verify` и вызов
`unverifiable` в `session_over` оркестратора.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import copy
import json
import re

PASSED, FAILED, UNVERIFIABLE_OUTCOME = "прошло", "не прошло", "не проверить"
OUTCOMES = (PASSED, FAILED, UNVERIFIABLE_OUTCOME)
WHERE = ("ветка", "main")  # где шла команда: «после» — на ветке, «до» бага — на `main`
# Вид доказательства по началу слова: «тест», «скриншот»/«снимок», «число»/«числа», «команда».
KINDS = (
    ("test", re.compile(r"\bтест", re.I)),
    ("screenshot", re.compile(r"\b(?:скриншот|снимок|снимк)", re.I)),
    ("number", re.compile(r"\bчисл", re.I)),
    ("command", re.compile(r"\bкоманд", re.I)),
)
NAMES = {"test": "тест", "screenshot": "скриншот", "number": "число", "command": "команда"}
NUMBER = re.compile(r"^(?:\*\*|__)?(?:К\s*)?(\d+)\s*[.):]\s*(?:\*\*|__)?\s+", re.I)
PROOF = re.compile(r"\s*[—–-]?\s*чем доказать:\s*(.+?)\s*\.?\s*$", re.I)
TAIL = re.compile(r"\s*\(([^()]*)\)\s*\.?\s*$")
EXAMPLES = ("Сейчас → должно быть", "Сейчас -> должно быть", "Примеры")
BULLET = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$", re.M)
# «После выката» (#301) — как у маршрута (`ticket_ship.WHOLE`, `INSIDE`, подраздел).
AFTER_DEPLOY = re.compile(r"(?:на\s+проде\s+)?после\s+выката\s*:|на\s+проде\s+после\s+выката", re.I)
AFTER_SECTION = re.compile(
    r"^#{2,6}\s*\**после\s+выката\**\s*$.*?(?=^#{1,6}\s|\Z)", re.M | re.S | re.I
)
# Причина остановки маршрута на `/verify`, когда проверить не удалось (её пишет `verify`, её же
# ищет маршрут `ticket ship` — `BLOCKED_MARK`, тест держит их равными).
UNVERIFIABLE = "/verify не смог проверить (BLOCKED)"
POINTS = "не проверить — пункты"  # BLOCKED по пунктам: дело тикета, а не среды
OUTPUT_LIMIT = 2000  # знаков вывода одной команды в отчёте: `.md` на PASS уходит в тело PR
# Маска значений, как у причины остановки маршрута (`ticket_ship.ASSIGNMENT`, `URL_PASSWORD`).
ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*=)\S+")
URL_PASSWORD = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]*:)\S+(@)")
# Число «должно быть» примера (#352): первое после «должно быть» (нет — после последней
# стрелки); «не должно быть» — запрет, а не число.
SHOULD = re.compile(r"(?<!не )должн\w*\s+быть\s*:?(.*)$", re.I | re.S)
NOT = re.compile(r"\bне\s+должн", re.I)
ARROW = re.compile(r"(?:→|->)(?!.*(?:→|->))(.*)$", re.S)
FIGURE = re.compile(r"(?<![\w.,])-?\d+(?:[.,]\d+)?(?![\w])")


def kinds(text: str) -> list[str]:
    return [name for name, pattern in KINDS if pattern.search(text)]


def only_kinds(text: str) -> list[str]:
    """Виды, только если в тексте нет ничего, кроме видов и союзов: «(нынешние тесты зелёные)» —
    текст пункта, а не вид."""
    rest = text
    for _, pattern in KINDS:
        rest = re.sub(pattern.pattern + r"\w*", "", rest, flags=re.I)
    if re.sub(r"\b(?:или|и|либо)\b|[,;/+.\s]", "", rest, flags=re.I):
        return []
    return kinds(text)


def item(text: str) -> tuple[str | None, str, list[str]]:
    """Пункт критериев: явный номер (нет — None), текст без номера и вида, виды."""
    number, body = None, text.strip()
    if found := NUMBER.match(body):
        number, body = found.group(1), body[found.end() :]
    got = []
    for pattern in (PROOF, TAIL):
        if (found := pattern.search(body)) and only_kinds(found.group(1)):
            got, body = only_kinds(found.group(1)), body[: found.start()]
            break
    return number, body.strip().rstrip("."), got


def after_deploy(text: str) -> bool:
    return bool(AFTER_DEPLOY.search(text))


def without_after(body: str | None) -> str:
    """Тело без подразделов «После выката»: их пункты — не критерий PR."""
    return AFTER_SECTION.sub("", body or "")


def parse(items: list[str]) -> tuple[list[dict], list[str]]:
    """Критерии из пунктов чек-листа (`snapshot.criteria`) без пунктов «после выката»: с номером
    и видами; ошибки — повторный явный номер."""
    parsed = [item(t) for t in items if not after_deploy(t)]
    errors, seen = [], set()
    for number, _, _ in parsed:
        if number is None:
            continue
        if number in seen:
            errors.append(f"критерий {number}: номер повторяется")
        seen.add(number)
    out, free = [], 1
    for number, text, got in parsed:
        if number is None:
            while str(free) in seen:
                free += 1
            number = str(free)
            seen.add(number)
        out.append({"id": number, "text": text, "kinds": got})
    return out, errors


def examples(body: str | None) -> list[dict]:
    """Примеры замысла: пункты раздела `## Сейчас → должно быть` (`## Примеры`) — `П1`, `П2`…"""
    for title in EXAMPLES:
        pattern = rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)"
        if found := re.search(pattern, body or "", re.M | re.S | re.I):
            listed = BULLET.findall(found.group(1))
            return [
                {"id": f"П{i}", "text": t, "kinds": ["number"]} for i, t in enumerate(listed, 1)
            ]
    return []


def points(items: list[str], body: str | None) -> tuple[list[dict], list[str]]:
    """Пункты приёмки тикета: критерии из `items` (пункты чек-листа раздела критериев тела без
    подразделов «после выката» — `snapshot.criteria(without_after(body))`) и примеры замысла
    `body`; ошибки — как у `parse`. Один расчёт у `/verify` и у снимка тикета (#352): номера
    пунктов у них совпадают."""
    found, errors = parse(items)
    return found + examples(body), errors


def findings(review: dict | None) -> list[dict]:
    """Пункты находок ревью со сценарием `/verify` (`canon_review.v2.verify`): номер
    `Н<номер находки>`, текст — где и сценарий. Нет блока — пусто."""
    listed = ((review or {}).get("v2") or {}).get("verify") if isinstance(review, dict) else None
    out = []
    for f in listed if isinstance(listed, list) else []:
        if isinstance(f, dict) and str(f.get("n") or "").isdigit():
            where, scenario = str(f.get("where") or "").strip(), str(f.get("scenario") or "")
            text = f"находка ревью {f['n']}" + (f" ({where})" if where else "")
            out.append({"id": f"Н{f['n']}", "text": f"{text}: {scenario.strip()}", "kinds": []})
    return out


def expected(text: str) -> list[str]:
    """Число «должно быть» примера замысла — первое после «должно быть», иначе после последней
    стрелки; нет его или пример — запрет («не должно быть …») — пусто: такой пример сверяет
    только `/verify`. Одно число: дальше в тексте бывают пояснения («10, а не 12»)."""
    if NOT.search(text):
        return []
    found = SHOULD.search(text) or ARROW.search(text)
    figures_of = FIGURE.findall(found.group(1)) if found else []
    return figures_of[:1]


def value(figure: str) -> float:
    return float(figure.replace(",", "."))


def figures(output: str) -> set[float]:
    """Числа вывода команды: машиночитаемый (JSON) — числа и числа в строках его значений; иначе
    — числа текста."""
    try:
        data = json.loads(output)
    except ValueError:
        return {value(f) for f in FIGURE.findall(output)}
    out: set[float] = set()
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, (int, float)):
            out.add(float(item))
        elif isinstance(item, str):
            out |= {value(f) for f in FIGURE.findall(item)}
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return out


def unconfirmed(point: dict, row: dict) -> list[str]:
    """Числа «должно быть» примера, которых нет в выводе ни одной команды на ветке; у критерия и
    находки — пусто."""
    if not point["id"].startswith("П"):
        return []
    got: set[float] = set()
    for e in row.get("evidence") or []:
        if isinstance(e, dict) and e.get("where") == "ветка" and isinstance(e.get("output"), str):
            got |= figures(e["output"])
    return [f for f in expected(point["text"]) if not any(abs(value(f) - g) < 1e-9 for g in got)]


def checked(point: dict, row: dict) -> dict:
    """Итог пункта после проверки программой: пример «прошло», а числа «должно быть» нет в
    выводе на ветке, — «не прошло» с заметкой программы; иначе — как есть."""
    if row.get("outcome") == PASSED and (missing := unconfirmed(point, row)):
        said = f"программа: «должно быть» {', '.join(missing)} нет в выводе команд на ветке"
        return {**row, "outcome": FAILED, "note": f"{said}. {row.get('note') or ''}".strip()}
    return row


def listing(points: list[dict]) -> str:
    """Пункты для задания `/verify`: номер, текст, вид доказательства."""
    lines = []
    for p in points:
        how = ", ".join(NAMES[k] for k in p["kinds"]) or "вид не указан"
        lines.append(f"- [{p['id']}] {p['text']} — чем доказать: {how}")
    return "\n".join(lines)


EVIDENCE = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "команда инструмента проверки как есть"},
        "output": {"type": "string", "description": "её машиночитаемый вывод (JSON) или ошибка"},
        "where": {"type": "string", "enum": list(WHERE)},
    },
    "required": ["command", "output", "where"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "description": "итог по каждому пункту задания — критерию и примеру, по его номеру",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "номер пункта без скобок: 1, 2, П1"},
                    "outcome": {
                        "type": "string",
                        "enum": list(OUTCOMES),
                        "description": "прошло — доказано; не прошло — проверка шла, и пункт не "
                        "подтвердился; не проверить — проверка не состоялась (среда, инструмент, "
                        "данные), это не ошибка кода",
                    },
                    "evidence": {"type": "array", "minItems": 1, "items": EVIDENCE},
                    "note": {"type": "string", "description": "что показали команды, коротко"},
                },
                "required": ["id", "outcome", "evidence", "note"],
                "additionalProperties": False,
            },
        },
        "report": {
            "type": "string",
            "description": "отчёт в markdown: шаги, пробы мимо счастливого пути",
        },
    },
    "required": ["results", "report"],
    "additionalProperties": False,
}


def schema(points: list[dict]) -> dict:
    """Схема ответа прогона: номера пунктов — `enum`, итогов — ровно столько, сколько пунктов."""
    out = copy.deepcopy(SCHEMA)
    results = out["properties"]["results"]
    results["minItems"] = results["maxItems"] = len(points)
    results["items"]["properties"]["id"]["enum"] = [p["id"] for p in points]
    return out


def judge(points: list[dict], output: dict) -> tuple[str, list[dict], list[str]]:
    """Итог прогона программой: (PASS | FAIL | BLOCKED, итоги по пунктам в их порядке, ошибки
    схемы). Ошибки есть — итог не засчитан."""
    results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        return "", [], ["нет списка results"]
    known = {p["id"] for p in points}
    got, errors, broken = {}, [], set()
    for r in results:
        if not isinstance(r, dict):
            errors.append("итог пункта — не объект")
            continue
        rid = str(r.get("id") or "")
        if rid not in known:
            errors.append(f"пункт {rid or '?'}: такого номера в задании нет")
        elif rid in got:
            errors.append(f"пункт {rid}: итог повторяется")
        elif r.get("outcome") not in OUTCOMES:
            errors.append(f"пункт {rid}: исход {r.get('outcome')!r} — не из {', '.join(OUTCOMES)}")
            broken.add(rid)
        elif not proofs(r.get("evidence")):
            errors.append(f"пункт {rid}: нет доказательства — записанной команды с выводом")
            broken.add(rid)
        else:
            note = r.get("note")
            got[rid] = {**r, "id": rid, "note": note if isinstance(note, str) else ""}
    missing = [p["id"] for p in points if p["id"] not in got and p["id"] not in broken]
    if missing:
        errors.append(f"нет итога по пунктам {', '.join(missing)}")
    if errors:
        return "", [], errors
    rows = [checked(p, got[p["id"]]) for p in points]  # число «должно быть» — программа (#352)
    outcomes = {r["outcome"] for r in rows}
    if FAILED in outcomes:
        return "FAIL", rows, []
    if UNVERIFIABLE_OUTCOME in outcomes:
        return "BLOCKED", rows, []
    return "PASS", rows, []


def proofs(evidence) -> bool:
    return (
        isinstance(evidence, list)
        and bool(evidence)
        and all(
            isinstance(e, dict)
            and str(e.get("command") or "").strip()
            and isinstance(e.get("output"), str)
            and e.get("where") in WHERE
            for e in evidence
        )
    )


def masked(text: str, limit: int | None = None) -> str:
    """Значения присваиваний и пароли в URL — маской; длиннее `limit` — обрезано."""
    text = ASSIGNMENT.sub(r"\1***", URL_PASSWORD.sub(r"\1***\2", text))
    if limit is not None and len(text) > limit:
        text = text[:limit] + "\n… [обрезано]"
    return text


def render(points: list[dict], rows: list[dict], verdict: str, report: str) -> str:
    """Отчёт `.md`: таблица исходов, доказательства по пунктам (вывод — с маской и до
    `OUTPUT_LIMIT`), отчёт модели, строка итога."""
    by = {r["id"]: r for r in rows}
    lines = ["## Исходы по пунктам", "", "| № | Пункт | Исход |", "|---|---|---|"]
    for p in points:
        text = p["text"].replace("|", "\\|")
        lines.append(f"| {p['id']} | {text} | {by[p['id']]['outcome']} |")
    lines += ["", "## Доказательства"]
    for p in points:
        r = by[p["id"]]
        lines += ["", f"### [{p['id']}] {r['outcome']}", "", masked(r["note"].strip()) or "—"]
        for e in r["evidence"]:
            lines += [
                "",
                f"`{masked(e['command'])}` ({e['where']}):",
                "",
                "```",
                masked(e["output"].rstrip(), OUTPUT_LIMIT),
                "```",
            ]
    lines += ["", "## Отчёт", "", masked(report.strip()), "", f"Итог: {verdict}"]
    return "\n".join(lines)


def summary(points: list[dict], rows: list[dict]) -> list[str]:
    """Сводка без данных (#352): по пункту — номер, исход и число команд по месту. Ни команд, ни
    вывода, ни заметок: в PR и карточку уходит только она. Исход — после проверки программой
    (`checked`), как у допуска."""
    by = {str(r.get("id") or ""): r for r in rows if isinstance(r, dict)}
    lines = []
    for p in points:
        r = by.get(p["id"])
        if r is None:
            lines.append(f"[{p['id']}] доказательства нет")
            continue
        r = checked(p, r)
        evidence = [e for e in r.get("evidence") or [] if isinstance(e, dict)]
        where = {w: sum(e.get("where") == w for e in evidence) for w in WHERE}
        shown = ", ".join(f"{w} {k}" for w, k in where.items() if k)
        lines.append(f"[{p['id']}] {r.get('outcome')} — команд {len(evidence)} ({shown or '—'})")
    return lines


def unverifiable(stop: dict | None) -> bool:
    """Маршрут остановился на `/verify`, потому что проверить не удалось."""
    if not isinstance(stop, dict):
        return False
    return stop.get("step") == "verify" and str(stop.get("reason") or "").startswith(UNVERIFIABLE)


def kind(reason: str) -> str:
    """Вид BLOCKED по причине остановки: `points` — пункт не проверить (только этот тикет),
    `environment` — среда (нет инструмента, красный «доктор»)."""
    rest = reason.removeprefix(UNVERIFIABLE).lstrip(" :")
    return "points" if rest.startswith(POINTS) else "environment"
