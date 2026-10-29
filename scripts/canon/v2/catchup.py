"""catchup — догон основы на сервере и слияние по одному на пути v2 (#348; спека #329,
«Слияние и догон»).

Защита `main` проекта v2 требует **«ветку свежую»** (`protect-main --v2`): слить можно, только если
в ветке последняя основа. Отставшую ветку оркестратор **догоняет на сервере**: слияние `main` в
ветку силами GitHub (`PUT …/pulls/N/update-branch` с `expected_head_sha`) токеном лица агента —
без песочницы и без сессии; ветки, куда `main` приносит правки CI, — его правом `workflows`
(`app-token --kind orchestrator`). Новый head ветки — коммит слияния GitHub: первый родитель —
head до догона, второй — коммит `main`; он становится «последним отправленным» (`sent`) ворот
отправки (`v2/send.py`), а рабочая копия в песочнице перематывается к нему перед новой сессией
(`align`). Чужой коммит вместо слияния — остановка с кодом `чужой-коммит`.

Когда:

1. **до ревью** — ветка «код готов» отправлена, PR открыт, а `main` ушёл вперёд: ревью идёт на
   той основе, на которую ляжет код (`review` оркестратора);
2. **перед сессией после ревью** — та, что разбирает находки, гоняет тесты и `/verify` маршрута,
   работает на свежей основе (`launch`); конфликт здесь не держит: сессия идёт на прежней
   основе, его разберёт догон на шаге CI;
3. **на шагах CI и мёржа** — основа ушла (слит сосед, `BEHIND` у GitHub): вместо отказа допуска
   «ветка отстала» (`catch_up` оркестратора). Дальше — полный CI на новом head и допуск заново;
   отметка — на новый коммит. На мёрже тикет возвращается на шаг CI.

**«Эта версия»** — head «код готов» сессии или он же плюс догон на сервере, не задевший файлы
тикета (`overlay`: head догона подменяет head «код готов» и тестов маршрута в чтении состояния
маршрута). Догон после `/verify` (шаг 3, ревью уже было) **задел файлы тикета** (`touched`), если
`main` принёс: файл диффа PR, прямую зависимость такого файла (строка импорта в нём называет
модуль), lockfile, файл CI или миграцию, — тогда версия новая: свежая сессия с заданием
`VERIFY_TASK` (маршрут прогонит тесты и `/verify` на новом head), PR — черновик. Не задел —
`/verify` не повторяется, полный CI и допуск — на новом head.

**Конфликт** — свежая сессия с заданием догона (`CATCH_UP` оркестратора, правила шага маршрута
«догон базы»); конфликтов на тикет — не больше предела `conflicts` (`v2/limits.py`, #349: с
запасом от `agent:go`), следующий — остановка с отчётом.
**Догонов без конфликта подряд** (`clean`, сбрасывает конфликт) — до предела `clean` той же
таблицы: на последнем
оркестратор держит остальные слияния (`merge_first` в своём состоянии), пока этот тикет не
сольётся или не встанет. **Слияние по одному:** отметку `review` с авто-мёржем получает один
тикет пути v2 за раз (`merging`); остальные ждут перед отметкой (`waits`). Слитый или вставший
тикет место отпускает. PR Dependabot — своей дорожкой (#350, `v2/dependabot.py`), но место
слияния у них с тикетами общее.

Слияние GitHub делает не сразу: новый head ждётся до `POLL` секунд в такте; не пришёл — догон
висит (`pending`), такт тикета ждёт его до `LATE`, дольше — остановка.

Удалить: выключить переключатель, затем этот файл; `catchup()`, ветку v2 в `catch_up`, вызовы в
`review`, `launch`, `route` и шаге CI оркестратора и в `v2/steps.py`; режим «ветка свежая» в
`protect-main`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import PurePosixPath

POLL = float(os.environ.get("CANON_CATCHUP_POLL") or 120)  # ожидание слияния GitHub в такте, с
POLL_STEP = 2.0
LATE = 900  # догон висит дольше — остановка
CONFLICT = re.compile(r"merge conflict", re.I)  # ответ GitHub 422 на `update-branch`
REFUSALS = 3  # отказов GitHub на `update-branch` подряд (не конфликт) — затем остановка
CODES = {
    "conflicts": "конфликт-догона",
    "late": "догон-не-пришёл",
    "foreign": "чужой-коммит",
    "refused": "догон-отказан",
}
# Что значит «догон задел файлы тикета», кроме файлов диффа PR и их прямых зависимостей.
LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
    "poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock", "Cargo.lock", "go.sum",
    "Gemfile.lock", "composer.lock", "mix.lock", "pubspec.lock", "packages.lock.json",
}  # fmt: skip
REQUIREMENTS = re.compile(r"^requirements.*\.(txt|in)$|^constraints.*\.txt$")
MIGRATIONS = {"migrations", "migration", "alembic", "migrate"}
CODE = {
    ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".go", ".rs", ".rb",
    ".java", ".kt", ".swift", ".php", ".ex", ".exs", ".dart", ".cs", ".scala",
}  # fmt: skip
IMPORT = re.compile(r"\b(import|from|require|include|use|mod)\b")
GENERIC = {"__init__", "index", "main", "mod", "lib", "app", "utils", "types", "conftest"}
ACTIVE = ("queued", "session", "settle", "rtest", "ci", "merge")  # тикет держит место слияния
BOT_ACTIVE = ("queued", "merge")  # PR Dependabot держит место (`HOLDING` у `v2/dependabot.py`)
VERIFY_TASK = """Оркестратор: `main` ушёл вперёд — основу тикета #{n} я догнал на сервере
(`{old}` → `{new}`), и догон задел файлы тикета: {files}. Рабочая копия уже на `{new}` — ничего
не сливай и не догоняй. Вызов `{ship}` ({long}) и его задания — до {until}: маршрут прогонит
тесты и `/verify` на новом head; ревью ошибок не повторяется. Вопрос владельцу — только
`{ask}`."""


def overlay(data: dict, t: dict) -> None:
    """«Эта версия» в чтении состояния маршрута: head догона на сервере, не задевшего файлы
    тикета, — вместо head «код готов» и тестов маршрута, если они на версии до догона."""
    version = (t.get("catchup") or {}).get("version")
    if not t.get("v2") or not isinstance(version, dict):
        return
    for key in ("ready", "checks"):
        record = data.get(key)
        if isinstance(record, dict) and record.get("head") == version.get("ready"):
            data[key] = {**record, "head": version["head"], "caught_from": version["ready"]}


def lockfile(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in LOCKFILES or bool(REQUIREMENTS.match(name))


def migration(path: str) -> bool:
    return any(part.lower() in MIGRATIONS for part in PurePosixPath(path).parts[:-1])


COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--)")


def imports(text: str, stem: str) -> bool:
    """Импорт в тексте называет модуль `stem` словом: в строке импорта или в продолжении её
    скобок (многострочный импорт); строки-комментарии не в счёт."""
    word = re.compile(rf"(?<![\w-]){re.escape(stem)}(?![\w-])")
    depth = 0  # открытые скобки импорта: строки до закрытия — его продолжение
    for line in text.splitlines():
        if COMMENT.match(line):
            continue
        if depth or IMPORT.search(line):
            if word.search(line):
                return True
            depth = max(
                0, depth + line.count("(") + line.count("{") - line.count(")") - line.count("}")
            )
    return False


class Catchup:
    """Догон на сервере и очередь слияния пути v2 для оркестратора `orch`; `tools` — его
    помощники: `core`, `card`, `send`, `limits` (пределы кругов, #349), `say`, `stamp`,
    `seconds_since`."""

    def __init__(self, orch, tools):
        self.orch, self.t = orch, tools

    @staticmethod
    def state(t: dict) -> dict:
        return t.setdefault("catchup", {"clean": 0, "conflicts": 0})

    # --- догон ---

    def behind(self, n: int, t: dict, st, pr: dict) -> bool:
        """Шаги CI и мёржа: основа ушла, head PR её не содержит — догон на сервере. True — шаг
        такта сделан (догон, ожидание, остановка); False — конфликт в пределе: догон сессией
        (`catch_up` оркестратора)."""
        got = self.server(n, t, st, pr["headRefOid"], "ci")
        if got != "conflict":
            return True
        cu, limits = self.state(t), self.t.limits
        allowed = limits.allowed(t, "conflicts")  # с запасом от `agent:go` (#349)
        if cu["conflicts"] >= allowed:
            why = f"конфликт с новой основой {cu['conflicts'] + 1}-й раз — догон сессией уже был"
            why = f"{why} {allowed} раза"
            limits.hit(t, "conflicts", self.reason("conflicts", why))  # `agent:go` — ещё догон
            self.halt(n, t, "conflicts", why)
            return True
        return False

    def conflict_started(self, n: int, t: dict) -> None:
        """Сессия догона по конфликту начата: конфликт засчитан (пауза, не начавшая запуск, —
        нет), счёт догонов подряд — с нуля."""
        cu = self.state(t)
        cu["conflicts"] += 1
        cu["clean"] = 0
        allowed = self.t.limits.allowed(t, "conflicts")
        self.mark(n, t, f"Догон основы — конфликт ({cu['conflicts']}/{allowed}): свежая сессия")

    def before_review(self, n: int, t: dict, st) -> bool:
        """До ревью: ветка отправлена и PR открыт, а основа ушла — догон, ревью на новом head.
        Конфликт — ревью на прежнем head (его разберёт шаг CI). False — ревью ждать: слияние не
        пришло за `POLL` (разбор — `settle` на следующем такте) или остановка."""
        head = (st["ready"] or {}).get("head")
        if not t.get("pr") or not isinstance(head, str) or self.fresh(head):
            return True
        got = self.server(n, t, st, head, "review")
        if got == "conflict":
            self.t.say(f"#{n}: догон до ревью — конфликт; ревью на прежней основе")
        return got in ("done", "conflict") and t["phase"] != "stopped"

    def before_session(self, n: int, t: dict) -> bool:
        """Перед новой сессией после ревью: висящий догон разобран, отставшая ветка догнана —
        если рабочая копия в песочнице ровно на отправленном (неотправленные коммиты передачи
        перемотка к слиянию не сохранит) и на этой основе конфликта уже не было. False —
        остановка (сессию не начинать)."""
        cu, got = self.state(t), None
        if "pending" in cu:
            got = self.arrival(n, t, self.orch.route(n), "session")
        elif (sent := t.get("sent")) and t.get("reviewed") and t.get("pr"):
            if not self.fresh(sent) and cu.get("conflict") != self.base() and self.at(n, sent):
                got = self.server(n, t, self.orch.route(n), sent, "session")
        if got == "pending":  # слияние ещё не пришло: копию к ветке не привести — остановка
            cu["pending"]["at"] = None  # `agent:go` — новое окно ожидания (`settle`)
            self.halt(n, t, "late", "слияние основы в ветку не пришло до новой сессии")
        return got not in ("pending", "stopped")

    def base(self) -> str:
        return self.orch.git("rev-parse", "origin/main").stdout.strip()

    def at(self, n: int, sent: str) -> bool:
        """Рабочая копия тикета в песочнице — ровно на `sent`."""
        o = self.orch
        worktree = o.worktree(n)
        if worktree is None:
            return False
        res = o.inside("git", "rev-parse", "HEAD", workdir=worktree, env=o.session_env(reader=True))
        return res.returncode == 0 and res.stdout.strip() == sent

    def fresh(self, head: str) -> bool:
        """В `head` последняя основа (`origin/main` — свежий, с GitHub)."""
        self.orch.own_repo(refetch=True)
        return self.orch.ancestor("origin/main", head)

    def server(self, n: int, t: dict, st, head: str, stage: str) -> str:
        """Слияние `main` в ветку на GitHub: `done` — новый head пришёл и разобран, `conflict`,
        `pending` — ещё не пришёл (ждём на следующем шаге), `stopped`. Прочий отказ GitHub —
        `Failure` (такт повторит); `REFUSALS`-й подряд — остановка с ответом GitHub."""
        core, o, cu = self.t.core, self.orch, self.state(t)
        number = str(t["pr"]).rstrip("/").rsplit("/", 1)[-1]
        base = self.base()
        try:
            core.gh(
                "api", "-X", "PUT", f"repos/{o.repo}/pulls/{number}/update-branch",
                "-f", f"expected_head_sha={head}",
            )  # fmt: skip
        except core.Failure as exc:
            if CONFLICT.search(str(exc)):
                cu["conflict"] = base  # на этой основе догон перед сессией не повторяется
                self.t.say(f"#{n}: догон основы {base[:7]} на сервере — конфликт")
                return "conflict"
            cu["refusals"] = cu.get("refusals", 0) + 1
            if cu["refusals"] >= REFUSALS:
                cu.pop("refusals")
                said = " ".join(str(exc).split())[-300:]
                self.halt(n, t, "refused", f"GitHub {REFUSALS} раза подряд отказал: {said}")
                return "stopped"
            raise
        cu.pop("refusals", None)
        cu["pending"] = {"from": head, "base": base, "at": self.t.stamp()}
        self.t.say(f"#{n}: догон основы {base[:7]} на сервере — слияние в {st.branch()}")
        deadline = time.monotonic() + POLL
        while True:
            if (got := self.arrival(n, t, st, stage)) != "pending":
                return got
            if time.monotonic() >= deadline:
                return "pending"
            time.sleep(POLL_STEP)
            while self.ref(st.branch()) == head and time.monotonic() < deadline:
                time.sleep(POLL_STEP)  # дёшево, без `git fetch`: ветка ещё не сдвинулась

    def ref(self, branch: str) -> str | None:
        """Head ветки на GitHub одним запросом API; сбой — None (проверит `arrival`)."""
        core = self.t.core
        try:
            out = core.gh(
                "api", f"repos/{self.orch.repo}/git/ref/heads/{branch}", "--jq", ".object.sha"
            )
        except core.Failure:
            return None
        return out.strip() or None

    def settle(self, n: int, t: dict, stage: str) -> bool:
        """Висящий догон (слияние GitHub не пришло в такте): пришёл — разбор; нет — ждать до
        `LATE`, дольше — остановка (окно ожидания после неё — заново: `agent:go` ждёт снова, а
        не встаёт тут же). True — можно идти дальше (висящего нет)."""
        cu = self.state(t)
        if "pending" not in cu:
            return True
        got = self.arrival(n, t, self.orch.route(n), stage)
        if got == "pending":
            pending = cu["pending"]
            if not pending.get("at"):  # после остановки — новое окно
                pending["at"] = self.t.stamp()
            if self.t.seconds_since(pending["at"]) > LATE:
                pending["at"] = None
                self.halt(n, t, "late", f"слияние основы в ветку не пришло за {LATE // 60} мин")
            return False
        return got == "done" and t["phase"] not in ("stopped", "session")

    def arrival(self, n: int, t: dict, st, stage: str) -> str:
        """Ветка на сервере после `update-branch`: прежний head — `pending`; коммит слияния
        GitHub (родители — прежний head и коммит `main`) — разбор догона, `done`; иное —
        остановка «чужой коммит», `stopped`."""
        o, cu = self.orch, self.state(t)
        old = cu["pending"]["from"]
        o.own_repo(refetch=True)
        tip = o.remote_tip(st.branch())
        if tip == old:
            return "pending"
        parents = o.git("rev-list", "--parents", "-n", "1", tip).stdout.split()[1:]
        if len(parents) != 2 or parents[0] != old or not o.ancestor(parents[1], "origin/main"):
            cu.pop("pending")
            why = f"после догона на сервере ветка — `{tip[:7] or 'нет'}`, а не слияние `{old[:7]}`"
            self.halt(n, t, "foreign", why)
            return "stopped"
        cu.pop("pending")
        self.caught(n, t, st, old, tip, stage)
        return "done"

    def caught(self, n: int, t: dict, st, old: str, tip: str, stage: str) -> None:
        """Догон пришёл: `tip` — последнее отправленное; счёт подряд и очередь слияния; «эта
        версия» или свежая сессия с `/verify`, если догон после `/verify` задел файлы тикета."""
        o, cu = self.orch, self.state(t)
        t["sent"] = tip
        t.pop("behind", None)
        t.pop("ci_since", None)
        cu.pop("conflict", None)
        line = f"Догон основы на сервере: `{old[:7]}` → `{tip[:7]}`"
        if stage == "ci":  # подряд считаются догоны тикета, который ждёт слияния
            cu["clean"] += 1
            line += f" (подряд {cu['clean']})"
            clean = self.t.limits.LIMITS["clean"]
            if cu["clean"] >= clean and self.first(n):
                line += f" — {clean}-й подряд: остальные слияния держатся до слияния тикета"
        # Перед сессией после ревью — тоже: не начнётся — шаг CI получит долг `/verify` или эту
        # версию, а не head «код готов» до догона.
        files = self.touched(old, tip) if stage != "review" and t.get("reviewed") else []
        ready = (st["ready"] or {}).get("head")
        if files:
            shown = ", ".join(f"`{f}` ({why})" for f, why in files[:10])
            more = " и др." if len(files) > 10 else ""
            cu["verify"] = {"from": old, "to": tip, "files": shown + more}
            line += f"; задел файлы тикета: {shown}{more} — /verify заново"
        elif ready == old:
            raw = (st["ready"] or {}).get("caught_from") or old
            cu["version"] = {"ready": raw, "head": tip}
            if stage == "ci":
                line += "; файлы тикета не задеты — /verify не повторяется, полный CI"
        if t["phase"] == "merge":
            t["phase"] = "ci"  # отметка — на новый коммит
        self.mark(n, t, line, key=f"catchup-{tip}")
        self.t.say(f"#{n}: {line}")
        if stage == "review" and (problem := o.sender().align(n, t)):
            o.stop(n, t, problem)
        elif files and stage == "ci":
            self.verify_again(n, t)  # перед сессией — она сама и прогонит `/verify`

    def verify_again(self, n: int, t: dict) -> bool:
        """Догон задел файлы тикета: свежая сессия с `/verify` на новом head, PR — черновик.
        Места нет (пауза) — долг, на следующем шаге. True — долга нет."""
        cu, o = self.state(t), self.orch
        owed = cu.get("verify")
        if not owed:
            return True
        if o.hold(n, "/verify после догона"):
            return False
        o.draft(o.route(n), True)
        t["undraft"] = True
        words = o.task_words(n)
        task = VERIFY_TASK.format(
            n=n, old=owed["from"][:7], new=owed["to"][:7], files=owed["files"], **words
        )
        o.launch(n, task=task)
        if t["phase"] == "session" or t.get("task") == task:
            cu.pop("verify", None)  # остановка «среда» держит задание в `task`: долг — у него
        return False

    def started(self, t: dict) -> None:
        """Сессия начата: долг `/verify` после догона — у неё (маршрут прогонит его на head)."""
        (t.get("catchup") or {}).pop("verify", None)

    def touched(self, old: str, new: str) -> list[tuple[str, str]]:
        """Что из принесённого догоном `old` → `new` задевает тикет: `(файл, почему)`."""
        o = self.orch
        parents = o.git("rev-list", "--parents", "-n", "1", new).stdout.split()
        base = o.git("merge-base", old, parents[-1]).stdout.strip()
        mine = self.names(base, old)
        came = self.names(old, new)
        texts = {}
        for path in mine:
            if PurePosixPath(path).suffix in CODE:
                texts[path] = o.git("show", f"{new}:{path}").stdout
        found = []
        for path in came:
            stem = PurePosixPath(path).stem
            if path in mine:
                found.append((path, "файл диффа PR"))
            elif lockfile(path):
                found.append((path, "lockfile"))
            elif path.startswith(self.t.send.CI_PATHS):
                found.append((path, "CI"))
            elif migration(path):
                found.append((path, "миграция"))
            elif (
                PurePosixPath(path).suffix in CODE
                and stem not in GENERIC
                and len(stem) > 2
                and any(imports(text, stem) for text in texts.values())
            ):
                found.append((path, "зависимость"))
        return found

    def names(self, start: str, end: str) -> list[str]:
        res = self.orch.git("diff", "--name-only", "-z", "--no-renames", start, end)
        if res.returncode != 0:
            raise self.t.core.Failure(f"дифф `{start[:7]}..{end[:7]}` не прочитан")
        return sorted(f for f in res.stdout.split("\0") if f)

    # --- очередь слияния ---

    def first(self, n: int) -> bool:
        """Тикет держит остальные слияния (предел `clean` догонов подряд), если их не держит
        другой."""
        data = self.orch.data
        other = data.get("merge_first")
        if other not in (None, n) and self.active(other):
            return False
        data["merge_first"] = n
        return True

    def active(self, m) -> bool:
        """Тикет пути v2 на шаге, держащем место, или PR Dependabot (#350, `v2/dependabot.py`)
        либо обслуживания инструмента проверки (#353, `v2/upkeep.py`), занявший место: номера PR
        и тикетов у GitHub не пересекаются."""
        t = self.orch.data["tickets"].get(str(m))
        if t:
            return bool(t.get("v2")) and t.get("phase") in ACTIVE
        bot = (self.orch.data.get("dependabot") or {}).get(str(m)) or {}
        if bot.get("phase") in BOT_ACTIVE:
            return True
        upkeep = (self.orch.data.get("upkeep") or {}).get("pr") or {}  # PR обслуживания (#353)
        return upkeep.get("number") == m and upkeep.get("phase") in BOT_ACTIVE

    def waits(self, n: int) -> str | None:
        """Почему отметку с авто-мёржем ставить рано: сливается другой тикет или другой держит
        слияния; None — можно."""
        data = self.orch.data
        merging, first = data.get("merging"), data.get("merge_first")
        if merging == n:
            return None  # место слияния — за ним: держатель слияний его не отнимает
        if merging is not None and self.active(merging):
            return f"слияние по одному — сливается #{merging}"
        if first not in (None, n) and self.active(first):
            clean = self.t.limits.LIMITS["clean"]
            return f"слияния держит #{first}: он догонял основу {clean} раза подряд"
        return None

    def marked(self, n: int) -> None:
        self.orch.data["merging"] = n

    def release(self, n: int) -> None:
        """Тикет слит, встал или начат заново: место слияния и удержание — свободны (после
        `agent:go` он берёт их снова отметкой и догонами)."""
        for key in ("merging", "merge_first"):
            if self.orch.data.get(key) == n:
                self.orch.data.pop(key)

    # --- вывод ---

    def mark(self, n: int, t: dict, text: str, key: str | None = None) -> None:
        card = self.t.card
        self.orch.sender().mark(n, t, card.DONE, text, key=key or f"catchup-{self.t.stamp()}")

    @staticmethod
    def reason(code: str, why: str) -> str:
        return f"догон основы — код `{CODES[code]}`: {why}"

    def halt(self, n: int, t: dict, code: str, why: str) -> None:
        line = f"Остановка — код `{CODES[code]}`: {why}; отчёт в тикете"
        self.orch.sender().mark(n, t, self.t.card.ATTENTION, line, key="stop")
        self.orch.stop(n, t, self.reason(code, why))
