"""intake — проверка постановки пути v2 до занятия места (#355; спека #329, «Тикет v2»).

Неисполнимый тикет платил полную цену до `/verify` (ozon#1141 — около 17 USD). Поэтому до того,
как тикет пути v2 займёт место, оркестратор гоняет **дешёвую проверку постановки**: `claude -p`
роли `intake` таблицы `v2/roles.py` (Opus medium) на Mac, только чтение (`TOOLS`), до `LIMIT` USD
и `TIMEOUT` секунд, в своём чекауте основы (`origin/main`), без настроек проекта (обёртка
`claude --setting-sources user`). Вход — тикет целиком (заголовок, тело, комментарии участников).
Модель проверяет: критерии и примеры замысла конкретны и проверяемы; нужные для проверки данные
есть в песочнице (живой API — только разведкой); тикет не противоречит коду, неясного не
осталось; тикет — бриф v2 (старый тикет переводится: модель предлагает блок брифа v2 — `brief`).
«Во «Владениях» нет путей-ограничителей» проверяет программа, а не модель: пути брифа против
`CODEOWNERS` основы (`restrictors`, `restricted`).

Итог:

- **замечаний нет** — тикет занимает место (снимок тикета, `v2/snapshot.py`);
- **замечания** — отчёт об остановке с кодом `CODE` и списком замечаний, `agent:stopped`, место
  не занято; замечания с кодом и текстом — ещё и файлом `intake/ticket-N.json` рядом с состоянием
  оркестратора (`remarks_file`): точка передачи доводке постановки агентом (#436). Владелец правит
  тикет и ставит `agent:go` — проверка заново и новый снимок — или `agent:as-is` — тикет идёт как
  есть (без проверки, пока тело не изменится; пути-ограничители из замечания разрешены допуску);
- **проверка сама упала или не уложилась** — один повтор на следующем такте, затем остановка
  «среда»: сбой среды (таймаут, нет итога) — с паузой очереди (`v2/doctor.py`, `hold`): Mac не
  дозвался модели — не дозовётся и для соседей, и до конца такта проверку не гоняют другие тикеты;
  сбой этого тикета (предел USD, ответ не по схеме, `own_fault`) — без паузы. Тело правили во
  время проверки — проверка заново на следующем такте: снимок не снимет непроверенный текст.

Без проверки идут: тикет «`main` красный» (#357, `v2/mainline.py`, `first`) и тикет, у которого
снимок уже есть (продолжение после остановки на CI или мёрже). Правка тела после старта (код
`тикет-изменён`) снимок снимает — `agent:go` после неё проходит проверку заново.

Удалить: выключить переключатель, затем этот файл; `setup_check()` и вызов `check` в `fill`
оркестратора, команду `agent:as-is` (`AS_IS` в `COMMANDS`, `as_is`), ветку `intake` в `report` и
`resume_stopped`; условие 6 допуска берёт `restrictors` и `restricted` отсюда — их перенести в
`v2/gates.py`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROLE = "intake"  # роль таблицы `v2/roles.py`; `claude_run.role` берёт её по имени скрипта
LIMIT = 1.0  # USD на проверку
# Секунды на проверку; `CANON_INTAKE_TIMEOUT` — для тестов таймаута.
TIMEOUT = int(os.environ.get("CANON_INTAKE_TIMEOUT") or 180)
TRIES = 2  # сбоев проверки подряд — затем остановка «среда»
CODE = "постановка"  # код остановки по замечаниям
AS_IS = "agent:as-is"
# Только чтение: встроенные инструменты — чтение файлов; оболочки, правок и сети нет; серверов
# MCP из настроек пользователя — тоже (`--strict-mcp-config` без `--mcp-config`).
TOOLS = ["--tools=Read,Grep,Glob", "--strict-mcp-config"]
PROFILE = "docs/agents/profile.md"
# `CODEOWNERS` — где его ищет GitHub, по порядку.
OWNERS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
RESTRICTED = "путь-ограничитель"  # код замечания программы
# Коды замечаний модели: (код, когда).
KINDS = (
    ("критерий", "критерий или пример замысла не конкретен или не проверяем (нет вида "
     "доказательства, «на глаз», зависит от соседнего)"),
    ("данные", "для проверки нужны данные, которых нет в песочнице; живой вызов внешнего API — "
     "не критерий, а тикет разведки"),
    ("код", "тикет противоречит коду: описанного «сейчас» в коде нет, названного файла или "
     "функции нет, образец не тот"),
    ("неясно", "осталось неясное: открытые вопросы, двусмысленность, решение, которого в тикете "
     "нет"),
    ("формат", "тикет не в формате брифа v2 (поля, строки «Владения» и «Флаги», критерии "
     "«КОГДА … ТО …» с номером и видом доказательства) — в `brief` предложи бриф v2"),
)  # fmt: skip
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "remarks"]},
        "remarks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "enum": [k for k, _ in KINDS]},
                    "text": {"type": "string"},
                },
                "required": ["code", "text"],
            },
        },
        "brief": {"type": "string"},
    },
    "required": ["verdict", "remarks"],
}
TASK = """Ты — проверка постановки тикета перед работой агента. Только читаешь: ничего не правь,
команд не запускай. Тикет целиком (заголовок, тело, комментарии участников) — в файле `{ticket}`;
рабочий каталог — основа проекта (`origin/main`), профиль проекта — `{profile}`, формат брифа v2 —
`.claude/canon/ticket-ownership-v2.md` (или `.claude/canon/ticket-ownership.md`).

Проверь и назови замечание на каждое нарушение (код — из списка):
{kinds}

Пути-ограничители во «Владениях» проверяет программа — их не называй. Замечание — одна-две фразы
словами владельца: что не так и как поправить (для критерия — как переписать). Не придирайся к
стилю: замечание — только то, из-за чего агент сделает не то или не сможет доказать сделанное.

Ответ — по схеме: `verdict` `ok` и пустой `remarks`, если замечаний нет; иначе `remarks` и
`verdict` `remarks`. Тикет не в формате брифа v2 — замечание `формат` и в `brief` бриф v2 этого
тикета целиком: все 11 полей по формату, с учётом остальных замечаний, готовый к вставке в тело
вместо нынешнего; неизвестное — в «Открытые вопросы». Тикет в формате v2 — `brief` пустой."""

ADVICE = (
    "прочитайте замечания. Поправили тикет — `agent:go`: проверка пройдёт заново по новому "
    "тексту. Замечание не по делу — `agent:as-is`: тикет пойдёт как есть. **Риск "
    "`agent:as-is`:** непроверяемый критерий агент не докажет — тикет дойдёт до приёмки и "
    "встанет там, потратив полную цену; путь-ограничитель из замечания допуск после него "
    "пропустит."
)
GO = "проверка постановки заново по нынешнему тексту тикета, затем снимок и место"
AS_IS_LINE = (
    f"- `{AS_IS}` — запустить как есть: без проверки постановки, пока тело тикета не изменится; "
    "пути-ограничители из замечаний допуск разрешит;\n"
)


def kinds() -> str:
    return "\n".join(f"- `{k}` — {why}" for k, why in KINDS)


# --- пути-ограничители ---


def restrictors(text: str | None) -> list[str]:
    """Образцы путей `CODEOWNERS` по порядку: первое слово каждой строки (`\\#` — знак `#`, а не
    комментарий); строка без владельцев снимает владение — образец с `!` в начале."""
    found = []
    for raw in (text or "").splitlines():
        line = re.split(r"(?<!\\)#", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        words = line.split()
        glob = words[0].replace("\\#", "#")
        found.append(glob if len(words) > 1 else f"!{glob}")
    return found


def pattern(glob: str) -> re.Pattern:
    """Образец `CODEOWNERS` — регулярка над путём от корня. С `/` в начале или в середине — от
    корня, иначе — на любой глубине; `/` в конце — каталог: всё под ним; последнее звено с `*`
    или `?` — только этот уровень (`docs/*` не берёт `docs/a/b.md`); иначе — файл или каталог;
    `*` — внутри имени, `**` — через каталоги."""
    anchored = "/" in glob.rstrip("/")
    body = glob.strip("/")
    out, i = [], 0
    while i < len(body):
        if body.startswith("**", i):
            out.append(".*")
            i += 2
            if body.startswith("/", i):
                out[-1] = "(?:.*/)?"
                i += 1
            continue
        c = body[i]
        out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    head = "" if anchored else "(?:.*/)?"
    last = body.rsplit("/", 1)[-1]
    if glob.endswith("/"):
        tail = "/.+"
    elif "*" in last or "?" in last:
        tail = ""
    else:
        tail = "(?:/.+)?"
    return re.compile(f"{head}{''.join(out)}{tail}")


def restricted(path: str, globs: list[str]) -> bool:
    """Путь — под образцом `CODEOWNERS` с владельцем; последнее совпавшее правило побеждает, как
    у GitHub. Путь «Владений» без `/` в конце может оказаться каталогом (как у `conflict`
    оркестратора); каталог — путь-ограничитель и тогда, когда образец от корня внутри него:
    тикет, владеющий `tools/`, правит и `tools/verify/`."""
    bare = path.strip().strip("/")
    hit = False
    for glob in globs:
        off = glob.startswith("!")
        rx = pattern(glob[1:] if off else glob)
        if rx.fullmatch(bare) or rx.fullmatch(f"{bare}/x"):
            hit = not off
        elif not off and "/" in glob.rstrip("/") and glob.lstrip("/").startswith(f"{bare}/"):
            hit = True
    return hit


# --- ответ модели ---


def answer(data: dict | None) -> dict | None:
    """Ответ по схеме из отчёта `claude -p`: `{"remarks": [...], "brief": str}`; не по схеме —
    None (сбой проверки). Вердикт `remarks` без замечаний — тоже сбой: модель противоречит себе."""
    out = (data or {}).get("structured_output")
    if not isinstance(out, dict) or out.get("verdict") not in ("ok", "remarks"):
        return None
    remarks = out.get("remarks")
    if not isinstance(remarks, list):
        return None
    codes = {k for k, _ in KINDS}
    clean = [
        {"code": r["code"], "text": " ".join(str(r["text"]).split())}
        for r in remarks
        if isinstance(r, dict) and r.get("code") in codes and str(r.get("text") or "").strip()
    ]
    if len(clean) != len(remarks) or (out["verdict"] == "remarks") != bool(clean):
        return None
    brief = out.get("brief")
    return {"remarks": clean, "brief": brief.strip() if isinstance(brief, str) else ""}


def own_fault(data: dict | None) -> bool:
    """Сбой проверки — дело этого тикета, а не среды: упор в предел USD, ответ не по схеме.
    Прочее (таймаут, нет итога, сбой до модели) — среда: не дозовётся и для соседей."""
    if not isinstance(data, dict):
        return False
    if data.get("subtype") in ("error_max_budget_usd", "error_max_structured_output_retries"):
        return True
    return isinstance(data.get("structured_output"), dict) and answer(data) is None


def remarks_text(remarks: list[dict], brief: str) -> str:
    """Замечания для отчёта: список с кодом; предложенный бриф v2 — свёрнутым блоком."""
    lines = "\n".join(f"- `{r['code']}` — {r['text']}" for r in remarks)
    text = f"**Замечания проверки постановки:**\n{lines}\n\n"
    if brief:
        text += (
            "<details><summary>Предложенный бриф v2 — вставьте в тело тикета</summary>\n\n"
            f"{brief}\n\n</details>\n\n"
        )
    return text


# --- скрипт: проверка на Mac ---


def load(name: str, filename: str):
    """Модуль канона рядом — без `sys.path` и `__pycache__`."""
    path = HERE.parent / filename
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=ROLE, description=__doc__.split("\n\n")[0])
    parser.add_argument("report", type=Path, help="сырой итог `claude -p` (.json)")
    parser.add_argument("--ticket", required=True, type=Path, help="тикет целиком (.md)")
    parser.add_argument("--max-budget-usd", default=f"{LIMIT:.2f}")
    args = parser.parse_args(argv)
    sys.argv[0] = ROLE  # `claude_run.role` берёт роль по имени скрипта
    claude_run = load("canon_claude_run_intake", "claude_run.py")
    os.environ[claude_run.roles.V2_ENV] = "1"  # модель и усилие — роль `intake`
    text = TASK.format(ticket=args.ticket, profile=PROFILE, kinds=kinds())
    try:
        data = claude_run.run_claude([*TOOLS, text], args.report, args.max_budget_usd, SCHEMA)
    except claude_run.ChildError as exc:
        print(f"intake: {exc}", file=sys.stderr)
        return 1
    got = answer(data)
    if got is None:
        print("intake: ответ не по схеме проверки постановки", file=sys.stderr)
        return 1
    print(f"intake: замечаний {len(got['remarks'])}")
    return 0


# --- на Mac: оркестратор ---


class Intake:
    """Проверка постановки для оркестратора `orch`; `tools` — его помощники: `core`, `doctor`
    (`v2/doctor.py`), `mainline` (`v2/mainline.py`), `snapshot` (`v2/snapshot.py`), `journal`,
    `roles`, `without_tokens`, `say`, `stamp`, `tail`, `QUEUED`. Сбой среды проверки за такт
    помнит сам оркестратор (`intake_down`): остальные тикеты такта проверку не гоняют."""

    def __init__(self, orch, tools):
        self.orch, self.t = orch, tools

    @staticmethod
    def state(t: dict) -> dict:
        return t.setdefault("intake", {})

    @staticmethod
    def stopped(t: dict) -> bool:
        """Тикет остановлен проверкой постановки (замечания или «среда»)."""
        return bool((t.get("intake") or {}).get("stopped"))

    def remarks_file(self, n: int) -> Path:
        """Замечания с кодом и текстом — точка передачи доводке постановки (#436)."""
        return self.orch.dir / "intake" / f"ticket-{n}.json"

    def restrictors(self) -> list[str]:
        """Образцы путей-ограничителей — `CODEOWNERS` основы своего клона (`origin/main`); его
        нет — пусто."""
        for path in OWNERS:
            res = self.orch.git("show", f"origin/main:{path}")
            if res.returncode == 0:
                return restrictors(res.stdout)
        return []

    def due(self, n: int, t: dict) -> bool:
        """Тикету нужна проверка перед местом: путь v2, снимка нет, не тикет «`main` красный»."""
        return (
            bool(t.get("v2"))
            and "snapshot" not in t
            and not self.t.mainline.first(self.orch.data, n)
        )

    def body_digest(self, n: int) -> str:
        body = (self.t.core.gh_json("issue", "view", str(n), "--json", "body") or {}).get("body")
        return self.t.snapshot.digest(body)

    def check(self, n: int, t: dict) -> bool:
        """Перед занятием места: True — тикет идёт (проверка пройдена или не нужна); False —
        место не занято (замечания, сбой проверки, остановка «среда» или правка тела во время
        проверки — тогда заново на следующем такте)."""
        if not self.due(n, t):
            return True
        o, s, say = self.orch, self.state(t), self.t.say
        issue = self.t.core.gh_json("issue", "view", str(n), "--json", "title,body,comments")
        issue = issue or {}
        digest = self.t.snapshot.digest(issue.get("body"))
        if s.get("as_is") == digest or s.get("passed") == digest:
            return True
        claims = o.claims_of(n)  # `blockers` только что прочитал бриф заново
        o.own_repo()
        globs = self.restrictors()
        flagged = [p for p in (claims or {}).get("paths") or [] if restricted(p, globs)]
        saved = s.get("answer") or {}
        if saved.get("digest") == digest:  # отчёт прошлого такта не ушёл — без новой проверки
            got = {"remarks": saved["remarks"], "brief": saved["brief"]}
        else:
            if getattr(o, "intake_down", False):
                say(f"#{n}: проверка постановки — не в этом такте: она уже не состоялась")
                return False
            got, why, mine = self.run(n, t, issue)
            if got is None:
                if not mine:
                    o.intake_down = True
                s["failures"] = s.get("failures", 0) + 1
                if s["failures"] < TRIES:
                    say(f"#{n}: проверка постановки не состоялась — {why}; повтор на такте")
                    return False
                s.pop("failures", None)
                self.environment(n, t, why, pause=not mine)
                return False
            s.pop("failures", None)
            if self.body_digest(n) != digest:  # снимок снял бы непроверенный текст
                say(f"#{n}: тело тикета правили во время проверки постановки — заново на такте")
                return False
        remarks = list(got["remarks"])
        if flagged:
            shown = ", ".join(f"`{p}`" for p in flagged)
            remarks.insert(
                0,
                {
                    "code": RESTRICTED,
                    "text": f"во «Владениях» пути-ограничители ({shown}): PR на них ждёт вашего "
                    "разрешения и одобрения CODEOWNERS; не нужны тикету — уберите их из "
                    "«Владений», нужны — `agent:as-is`",
                },
            )
        if not remarks:
            t["intake"] = {"passed": digest, "allowed": s.get("allowed") or []}
            self.remarks_file(n).unlink(missing_ok=True)  # прошлые замечания — не для доводки
            say(f"#{n}: проверка постановки — замечаний нет")
            return True
        s["answer"] = {"digest": digest, "remarks": got["remarks"], "brief": got["brief"]}
        self.stop(n, t, digest, remarks, got["brief"], flagged)
        t["intake_remarks"] = t.get("intake_remarks", 0) + len(remarks)  # метрики (#356)
        s.pop("answer", None)
        return False

    def run(self, n: int, t: dict, issue: dict) -> tuple[dict | None, str, bool]:
        """Проверка в своём чекауте основы: итог — ответ по схеме (или None), причина сбоя и
        «сбой — дело тикета» (`own_fault`). Таймаут — вся группа процессов (дочерний `claude`
        тоже). Запуск — в журнал пути v2 и в расход тикета (сбой — оценкой предела)."""
        o, tl = self.orch, self.t
        ticket = o.issue_file(n, o.dir / "intake" / f"ticket-{n}.issue.md", issue)
        report = o.dir / "intake" / f"ticket-{n}.report.json"
        report.unlink(missing_ok=True)
        base = o.git("rev-parse", "origin/main").stdout.strip()
        checkout = o.checkout("intake", n, base)
        env = tl.without_tokens()
        env["PATH"] = f"{o.claude_shim()}{os.pathsep}{os.environ.get('PATH', '')}"
        env.pop(tl.roles.V2_ENV, None)  # роль ставит себе скрипт
        argv = [sys.executable, str(HERE / "intake.py"), str(report), "--ticket", str(ticket)]
        argv += ["--max-budget-usd", f"{LIMIT:.2f}"]
        tl.say(f"#{n}: проверка постановки")
        started, clock = tl.stamp(), time.monotonic()
        data, code, exit_ = None, "no_result", None
        try:
            proc = subprocess.Popen(
                argv, cwd=checkout, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                start_new_session=True,
            )  # fmt: skip
            try:
                out, err = proc.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                proc.communicate()
                outcome, why = "timeout", f"проверка не кончилась за {TIMEOUT} с"
            else:
                exit_ = proc.returncode
                try:
                    data = json.loads(report.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = None
                code = (data or {}).get("subtype") or "no_result"
                if proc.returncode == 0 and answer(data) is not None:
                    outcome, why = "ok", ""
                else:
                    outcome = "error"
                    why = f"код {proc.returncode}: {tl.tail(err or out) or 'без вывода'}"
        finally:
            o.drop_checkout(checkout)
        usd = tl.journal.usd(data)
        cost = t.setdefault("cost", {})
        if usd is None:
            cost["intake_estimate"] = round((cost.get("intake_estimate") or 0) + LIMIT, 4)
        else:
            cost["intake"] = round((cost.get("intake") or 0) + usd, 4)
        o.log_role(
            t, ROLE, data, time.monotonic() - clock, started=started, ended=tl.stamp(),
            usd=LIMIT if usd is None else usd, estimated=usd is None, outcome=outcome,
            code=code, exit=exit_,
        )  # fmt: skip
        if outcome == "ok":
            return answer(data), why, False
        return None, why, own_fault(data)

    def leave_queue(self, n: int, t: dict, reason: str, stop) -> None:
        """Признак остановки и тикет — из очереди, затем `stop`; сбой — всё назад: следующий
        такт повторит."""
        o, s = self.orch, self.state(t)
        s["stopped"] = reason
        at = o.data["queue"].index(n) if n in o.data["queue"] else None
        if at is not None:
            o.data["queue"].remove(n)
        try:
            stop()
        except BaseException:
            for key in ("stopped", "text"):
                s.pop(key, None)
            if at is not None and n not in o.data["queue"]:
                o.data["queue"].insert(at, n)
            raise

    def stop(self, n: int, t: dict, digest: str, remarks: list, brief: str, flagged: list) -> None:
        """Замечания: файл передачи (#436), затем отчёт об остановке с кодом `CODE`, место не
        занято, тикет — из очереди."""
        o = self.orch
        path = self.remarks_file(n)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"ticket": n, "at": self.t.stamp(), "digest": digest, "code": CODE}
        data.update(remarks=remarks, brief=brief)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        codes = ", ".join(dict.fromkeys(r["code"] for r in remarks))
        reason = f"код `{CODE}`: проверка постановки — замечаний {len(remarks)} ({codes})"
        s = self.state(t)
        s.update(restricted=flagged, text=remarks_text(remarks, brief))
        s.pop("passed", None)
        self.leave_queue(n, t, reason, lambda: o.stop(n, t, reason, remove=(self.t.QUEUED,)))

    def environment(self, n: int, t: dict, why: str, pause: bool = True) -> None:
        """Проверка дважды не состоялась — остановка «среда»; сбой среды (`pause`) — и пауза
        очереди (`v2/doctor.py`), сбой этого тикета (предел, ответ не по схеме) — без неё."""
        o, doctor = self.orch, self.t.doctor
        reason = (
            f"сбой среды — код `{doctor.CODE}`: проверка постановки не состоялась {TRIES} раза "
        )
        if pause:
            reason += (
                f"подряд ({why}). Очередь на паузе, пока на этом или другом тикете, остановленном "
                "«средой», нет `agent:go` или `agent:retry`"
            )
        else:
            reason += f"подряд ({why}) — по этому тикету; очередь не на паузе"

        def stop() -> None:
            o.stop(n, t, reason, remove=(self.t.QUEUED,))
            if pause:
                doctor.hold(o, n, t, f"проверка постановки не состоялась — {why}")

        self.leave_queue(n, t, reason, stop)

    def said(self, t: dict, reason: str) -> tuple[str, str, str] | None:
        """Отчёт об остановке проверкой: что сделает `agent:go`, совет и риск, текст замечаний;
        не её остановка — None."""
        s = t.get("intake") or {}
        if not t.get("v2") or s.get("stopped") != reason:
            return None
        if not s.get("text"):  # «среда»: общий совет
            return None
        return GO, ADVICE, s["text"]

    def allow(self, n: int, t: dict) -> None:
        """`agent:as-is` на тикете, остановленном проверкой: тикет пойдёт без проверки, пока тело
        то же, а пути-ограничители замечания разрешены допуску. Признак остановки снимает
        продолжение (`resume`) — после меток: их сбой не потеряет команду."""
        digest = self.body_digest(n)
        s = self.state(t)
        s["as_is"] = digest
        s["allowed"] = sorted({*(s.get("allowed") or []), *(s.get("restricted") or [])})

    @staticmethod
    def resume(t: dict) -> None:
        """Продолжение после остановки проверкой сделано (`resume_stopped`): признак снят."""
        s = t.get("intake") or {}
        s.pop("stopped", None)
        s.pop("text", None)


if __name__ == "__main__":
    sys.exit(main())
