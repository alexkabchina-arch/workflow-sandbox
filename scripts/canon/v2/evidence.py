"""evidence — доказательства `/verify` у оркестратора и условие допуска 4 (#352; спека #329,
«Допуск», «Проверка запуском и инструмент проверки»).

**Где хранятся.** `/verify` идёт в сессии в песочнице и пишет доказательства рядом с состоянием
маршрута (`verify.json` — ответ по схеме: пункт, исход, команды инструмента и их вывод). В выводе
команд бывают секреты, а на снимках — данные кабинета, поэтому на GitHub они не выкладываются, а
песочница — не хранилище: её пересоздают. Когда сессия сдала «код готов» после разбора, а её
`/verify` засчитан (`verify.verdict` — PASS), оркестратор забирает ответ (`keep`) в свой каталог
на Mac — вне песочницы и вне ветки: `evidence/ticket-N/<head>.json` (права 0600), целиком, без
маски. Запись — о версии, которую сдала сессия (`heads`; догон основы на сервере, не задевший
файлы тикета, — та же версия, `catchup.version`).

**Как обезличиваются.** Правилом, а не маской: в PR и карточку уходит только **сводка без
данных** (`v2/criteria.py`, `summary`) — номер пункта, исход и число команд по месту. Команды,
их вывод, заметки и отчёт модели не уходят никогда: на пути v2 маршрут отчёт `/verify` в тело PR
не кладёт (`ticket_ship.pr_body`), оркестратор ставит туда сводку (`scrub`); отчёт в теле всё же
есть (`<details>` «Что проверил /verify», маршрут старой версии) — вырезается до последнего
`</details>`. Отказ допуска называет только номера пунктов и исходы; текст сбоя чтения (пути Mac)
— только в журнал такта.

**Условие 4** (`check`): на этой версии есть доказательство по каждому критерию и примеру
замысла из снимка тикета (`snapshot.points`) и по каждой находке своего отчёта ревью со
сценарием `/verify` (`Н<номер>`), текст этих пунктов у прогона тот же (`canon_points` отчёта
`verify`), и итог по ним, посчитанный программой заново (`judge`, со сверкой чисел «должно
быть»), — PASS. Пунктов нет или дифф PR — только документы (маршрут `/verify` не гоняет) —
доказывать запуском нечего, условие выполнено.

Удалить: выключить переключатель, затем этот файл; `evidence()` и `points` снимка в
`orchestrator`, условие 4 в `v2/gates.py`, вызовы `keep` и `scrub` в `v2/send.py`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import os

KEY = "verify"  # строка «Сводки доказательств» карточки
# Отчёт `/verify` в теле PR от маршрута (`ticket_ship.pr_body`) — до последнего `</details>`: в
# отчёте модели свой `</details>` не оставит хвост с данными; закрытия нет — до конца тела.
OPEN = "<details><summary>Что проверил /verify</summary>"
CLOSE = "</details>"
STORE = "в PR не выкладываются: в выводе команд бывают секреты, на снимках — данные кабинета"


class Evidence:
    """Доказательства `/verify` тикета пути v2. `tools` — общее оркестратора: `core`, `card`,
    `criteria`, `snapshot`, `read_inside`, `say`, `stamp`."""

    def __init__(self, orch, tools):
        self.orch, self.t = orch, tools

    def path(self, n: int, head: str):
        return self.orch.dir / "evidence" / f"ticket-{n}" / f"{head}.json"

    # --- хранение ---

    def keep(self, n: int, t: dict, st, head: str) -> dict | None:
        """Ответ `/verify` сессии — в своё хранилище, если маршрут его засчитал; итог — запись
        (нет — None, причина — в `t["evidence"]`)."""
        decision = st.data.get("verify")
        decision = decision if isinstance(decision, dict) else {}
        if decision.get("verdict") != "PASS":
            why = str(decision.get("why") or "") if decision.get("needed") is False else ""
            said = f"/verify не засчитан маршрутом ({decision.get('verdict') or 'итога нет'})"
            t["evidence"] = {"heads": [head], "missing": f"{said}{': ' + why if why else ''}"}
            return None
        try:
            raw = json.loads(self.t.read_inside(st.clone, st.rel / "verify.json"))
            output = raw["structured_output"]
            results = output["results"]
            if not isinstance(results, list):
                raise TypeError("results — не список")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Текст сбоя — только в журнал такта: в нём пути Mac, а причина уходит на GitHub.
            said = "ответ /verify в песочнице не прочитан (подробности — в журнале оркестратора)"
            t["evidence"] = {"heads": [head], "missing": said}
            self.t.say(f"#{n}: доказательства /verify не забраны — {exc}")
            return None
        heads = [head]
        if isinstance(caught := (st["ready"] or {}).get("caught_from"), str):
            heads.append(caught)
        record = {
            "ticket": n,
            "heads": heads,
            "verify_head": decision.get("head"),
            "at": self.t.stamp(),
            "results": results,
            "points": raw.get("canon_points"),  # пункты прогона, их пишет `verify`
            "report": output.get("report"),
            "cost_usd": raw.get("total_cost_usd"),
        }
        path = self.path(n, head)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(record, out, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        t["evidence"] = {"heads": heads, "file": str(path.relative_to(self.orch.dir))}
        self.t.say(f"#{n}: доказательства /verify — у оркестратора, {path.name}")
        return record

    def record(self, t: dict) -> dict | None:
        held = t.get("evidence") or {}
        if not held.get("file"):
            return None
        try:
            return json.loads((self.orch.dir / held["file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # --- пункты и условие 4 ---

    def points(self, n: int, t: dict) -> list[dict]:
        """Пункты, которым нужно доказательство: критерии и примеры из снимка тикета и находки
        своего отчёта ревью со сценарием `/verify`. Снимок до #352 — пункты по телу тикета (оно
        равно снимку: правку тела ловит «тикет изменён»)."""
        snap = t.get("snapshot") or {}
        listed = snap.get("points")
        if not isinstance(listed, list):
            body = (self.t.core.gh_json("issue", "view", str(n), "--json", "body") or {}).get(
                "body"
            )
            listed = self.ticket_points(body)
        return [*listed, *self.t.criteria.findings(self.orch.fixes().reviewed(n))]

    def ticket_points(self, body: str | None) -> list[dict]:
        c = self.t.criteria
        return c.points(self.t.snapshot.criteria(c.without_after(body)), body)[0]

    def check(self, n: int, t: dict, head: str) -> str | None:
        """Условие 4: почему доказательств на версии `head` не хватает; хватает — None."""
        listed = self.points(n, t)
        if not listed:
            return None
        held = t.get("evidence") or {}
        record = self.record(t)
        if record is None:
            if self.docs_only(head):  # маршрут /verify не гоняет — доказывать запуском нечего
                return None
            why = held.get("missing") or "оркестратор их не забирал"
            return f"доказательств /verify у оркестратора нет — {why}"
        heads = record.get("heads") or []
        version = (t.get("catchup") or {}).get("version") or {}
        if head not in heads and not (
            version.get("head") == head and version.get("ready") in heads
        ):
            return f"доказательства /verify — о версии `{str((heads or ['?'])[0])[:7]}`, не об этой"
        if other := self.other_points(listed, record):
            return f"/verify проверял другой текст пунктов {', '.join(other)}, чем в снимке тикета"
        ids = {p["id"] for p in listed}
        rows = [r for r in record.get("results") or [] if isinstance(r, dict) and rid(r) in ids]
        verdict, judged, errors = self.t.criteria.judge(listed, {"results": rows})
        if errors:
            return "; ".join(errors)
        if verdict != "PASS":
            left = [f"{r['id']} — {r['outcome']}" for r in judged if r["outcome"] != "прошло"]
            return f"не доказаны пункты: {', '.join(left)}"
        return None

    @staticmethod
    def other_points(listed: list[dict], record: dict) -> list[str]:
        """Номера пунктов, текст которых у прогона `/verify` (`points` записи) не тот, что у
        допуска: тикет постороннего — критерии из комментариев, снимок — из тела. Пунктов в
        записи нет (отчёт до #352) — не сверяется."""
        seen = record.get("points")
        if not isinstance(seen, list):
            return []
        texts = {str(p.get("id")): p.get("text") for p in seen if isinstance(p, dict)}
        return [p["id"] for p in listed if p["id"] in texts and texts[p["id"]] != p["text"]]

    def docs_only(self, head: str) -> bool:
        """Дифф PR (`origin/main...head`) — только документы: маршрут /verify не гоняет."""
        res = self.orch.git("diff", "--name-only", "-z", "--no-renames", f"origin/main...{head}")
        files = [f for f in res.stdout.split("\0") if f] if res.returncode == 0 else []
        return bool(files) and all(self.t.is_docs(f) for f in files)

    # --- PR и карточка ---

    def lines(self, n: int, t: dict) -> list[str]:
        """Сводка без данных по пунктам тикета; доказательств нет — строка об этом."""
        record = self.record(t)
        if record is None:
            missing = (t.get("evidence") or {}).get("missing") or "нет"
            return [f"доказательств у оркестратора нет — {missing}"]
        return self.t.criteria.summary(self.points(n, t), record.get("results") or [])

    def scrub(self, n: int, t: dict, body: str) -> str:
        """Тело PR без отчёта `/verify` маршрута: на его месте — сводка без данных."""
        rows = "\n".join(f"  - {line}" for line in self.lines(n, t))
        said = f"- /verify, сводка без данных (доказательства — у оркестратора, {STORE}):\n{rows}"
        return cut(body, said)

    def mark(self, n: int, t: dict, head: str) -> None:
        """Строка «Сводки доказательств» карточки PR: пункты и исходы, без данных."""
        record = self.record(t)
        if record is None:
            text = f"/verify на `{head[:7]}`: {self.lines(n, t)[0]}"
        else:
            listed = self.points(n, t)
            lines = self.t.criteria.summary(listed, record.get("results") or [])
            passed = sum(" прошло — " in line for line in lines)
            text = (
                f"/verify на `{head[:7]}`: пунктов {len(listed)}, прошло {passed} — "
                f"доказательства у оркестратора, {STORE}"
            )
        self.orch.sender().mark(n, t, self.t.card.EVIDENCE, text, key=KEY, publish=False)


def rid(row: dict) -> str:
    """Номер пункта итога строкой — как у `judge`."""
    return str(row.get("id") or "")


def cut(body: str, said: str) -> str:
    """`body` с отчётом `/verify` маршрута (`OPEN` … последний `CLOSE`, без него — до конца),
    заменённым на `said`; отчёта нет — `said` в конце."""
    start = body.find(OPEN)
    if start < 0:
        return f"{body.rstrip()}\n\n{said}\n"
    end = body.rfind(CLOSE, start)
    rest = body[end + len(CLOSE) :] if end >= 0 else "\n"
    return body[:start] + said + rest
