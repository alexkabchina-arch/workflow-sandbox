"""mainline — остановка линии при красном ночном `main` на пути v2 (#357; спека #329, «Ночной CI
и учёт минут»).

Полный прогон `main` — workflow канона `canon-mainline` (`WORKFLOW`, #430; ставит `canon-sync`
проекту на пути v2) на ветке `main`: ночной (`schedule`) или ручной (`workflow_dispatch`); проверки
в нём — проектный `ci.yml`, вызванный через `workflow_call` (задачи `ci / <задача>`). **Решающий**
— последний законченный из них, в котором шли проверки: ночь, пропущенная задачей `night` (`main`
не менялся с последнего зелёного, не дольше 3 суток), — не итог, у неё каждая задача, кроме
`night`, пропущена; отменённый прогон — тоже не итог. Прогоны на push и PR линию не решают.

Переход (#430): копия оркестратора встаёт на выпуск раньше, чем PR установки сливается в проект,
а у проекта с `ci.yml` без `workflow_call` файла `canon-mainline` нет вовсе (`canon-sync` его не
ставит). Поэтому полные прогоны читаются у обоих — `canon-mainline` и прежнего workflow `CI`
проекта (`LEGACY`, ночь в его `ci.yml`), новые первыми; workflow, которого в репозитории нет, —
без прогонов, не сбой. Ручной прогон — `canon-mainline`, нет его — `CI`.

Каждый такт (`poll`, до шагов мест) оркестратор проекта на пути v2 читает решающий прогон.

**Красный** — линия остановлена (`mainline.red` своего состояния — раньше любого вызова
GitHub, кроме чтения прогонов):

- авто-мёрж снят с готовых PR: тикет на мёрже — снова на шаге CI, PR Dependabot и обслуживания
  на месте слияния — без места (`stop`); место слияния свободно;
- отметок нет (`holds` в `waits` у `v2/catchup.py`): шаг CI и дорожки ждут перед отметкой;
- красный CI соседних PR — ожидание, а не возврат, перезапуск или тикет владельцу (`holds` у
  `v2/ci.py` и дорожек Dependabot и обслуживания);
- тикет «`main` красный» (`TICKET`) заводит сам оркестратор: упавшие тесты по отчёту прогона и
  слияния в `main` с последнего зелёного; критерий — упавшие тесты зелёные, остальное не
  меняется. Тикет — в очередь первым и берёт место сверх числа мест (если места вообще есть),
  без сверки владений (`first`); строк «Владения» в нём нет — пока он на месте, новые тикеты
  места не берут. Его отметку не держат ни линия, ни очередь слияния: он сливается, пока
  остальные ждут. Проверку постановки (#355) ему пропускает #355.
  Новый красный прогон при открытом тикете — комментарий в нём, если упали другие тесты; тикет
  или комментарий не записан — повтор на следующем такте (`told`).

Тикет «`main` красный» слит или закрыт — **ручной полный прогон `main`** (`gh workflow run`, раз
на коммит `main`; не запустился — строка в лог раз на коммит, ждём ночи или запуска владельцем
— Actions → «Run workflow»). Выход — зелёный решающий прогон: линия идёт, отметки и авто-мёрж —
обычным шагом CI. Красный — новый тикет. Переключатель выключен — линии нет (`clear`).

Удалить: выключить переключатель, затем этот файл; `mainline()` и его вызовы в `tick`, `fill` и
`blockers` оркестратора, в `waits` у `v2/catchup.py`, в `red` у `v2/ci.py` и в `step` у
`v2/dependabot.py` и `v2/upkeep.py`. Пропуск ночи — задача `night` файла `canon-mainline`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

WORKFLOW = "canon-mainline"  # workflow полного прогона — `name` файла канона (#430)
LEGACY = "CI"  # полный прогон до #430 и у проекта без `canon-mainline`: ночь в `ci.yml` проекта
NIGHT_JOB = "night"  # задача пропуска ночи без изменений `main` (файл `canon-mainline`)
EVENTS = ("schedule", "workflow_dispatch")
RED = ("failure", "timed_out", "startup_failure")
RUNS_LIMIT = "30"  # прогонов `main` в чтении: ночи без изменений — пропуски, решающий — дальше
KNOWN_LIMIT = 50  # запомненных пропусков ночи — не больше
SHOWN = 30  # строк тестов и слияний в тикете — не больше
ISSUE = re.compile(r"https://\S+/issues/(\d+)")
TITLE = "`main` красный: полный прогон {sha}"
TICKET = """## Откуда

Полный прогон `main` красный: {url} (workflow `{workflow}`, коммит `{sha}`). Линия остановлена:
оркестратор снял авто-мёрж с готовых PR и не ставит отметок, пока `main` не зелёный; красный CI
соседних PR — ожидание, не возврат. Тикет завёл оркестратор: он идёт первым и сливается, пока
остальные ждут.

## Упавшие тесты

{tests}

## Слияния в `main` с последнего зелёного прогона

{merges}

## What to build

Починить `main`: упавшие тесты — зелёные. Только починка: без новых возможностей и правок, не
нужных ей.

## Acceptance criteria

- [ ] {criterion}
- [ ] Остальное не меняется: другие тесты и поведение вне починки — как на `main`.

После слияния оркестратор запускает полный прогон `main` вручную: зелёный — линия снова идёт,
красный — новый тикет.
"""


def first(data: dict, n: int) -> bool:
    """Тикет «`main` красный» при остановленной линии: в очереди первый, место — сверх числа
    мест и без сверки владений, отметку не держат ни линия, ни очередь слияния."""
    line = data.get("mainline") or {}
    return bool(line.get("red")) and line.get("ticket") == n


def lines(items: list[str]) -> str:
    more = len(items) - SHOWN
    return "\n".join(f"- {x}" for x in items[:SHOWN]) + (f"\n- … и ещё {more}" if more > 0 else "")


class Mainline:
    """Линия `main` пути v2. `tools` — `core`, `ci` (`v2/ci.py`), `fresh` (новый тикет очереди),
    `say`, `stamp`, `READY` и `QUEUED` (метки)."""

    workflow = WORKFLOW  # полный прогон `main`: он заменяет более старые прогоны у `v2/ci.py`

    def __init__(self, orch, tools):
        self.orch, self.t = orch, tools
        self.present: list[str] = []  # workflow полного прогона, которые есть в репозитории

    @property
    def line(self) -> dict:
        return self.orch.data.setdefault("mainline", {})

    def gh(self, *args: str) -> str:
        return self.t.core.gh(*args, "-R", self.orch.repo, timeout=self.t.ci.GH_TIMEOUT)

    # --- для шагов ---

    def holds(self, n: int) -> str | None:
        """Почему отметку ставить и красный CI разбирать нельзя: линия остановлена, а это не
        тикет «`main` красный»; None — можно."""
        line = self.orch.data.get("mainline") or {}
        if not line.get("red") or line.get("ticket") == n:
            return None
        who = f" — чинит #{line['ticket']}" if line.get("ticket") else ""
        return f"линия остановлена: `main` красный (прогон {line['red']['run']}){who}"

    def fixes(self, n: int) -> bool:
        """Тикет чинит остановленную линию: падающие и на `main` тесты — не повод его
        остановить (`v2/ci.py`), очередь слияния его не держит (`v2/catchup.py`)."""
        return first(self.orch.data, n)

    # --- такт ---

    def poll(self) -> list[str]:
        """Решающий прогон `main`: красный — остановка линии и тикет, зелёный — линия идёт. Любой
        сбой — строкой итога: шаги мест идут по состоянию линии, повтор — на следующем такте.
        Красный записан и авто-мёрж снят раньше тикета: сбой тикета их не отменяет."""
        try:
            run, green = self.decisive()
            if run is None:
                return []
            if run["conclusion"] == "success":
                self.clear(f"прогон {run['databaseId']} зелёный — линия идёт")
                return []
            self.mark(run)
            self.stop()
            self.tell(run, green)
            return self.dispatch()
        except Exception as exc:  # noqa: BLE001 — сбой линии не держит места (как у дорожек)
            self.t.say(f"линия `main`: шаг не выполнен — {exc}")
            return [f"линия `main`: {exc}"]

    def runs(self) -> list[dict]:
        """Законченные прогоны `main` обоих workflow полного прогона, новые первыми; какие из них
        есть в репозитории — в `present` (ручной прогон — по нему)."""
        found, self.present = [], []
        for name in (WORKFLOW, LEGACY):
            try:
                out = self.gh(
                    "run", "list", "--branch", "main", "--workflow", name, "--status",
                    "completed", "--limit", RUNS_LIMIT,
                    "--json", "databaseId,attempt,headSha,conclusion,event,url,createdAt",
                )  # fmt: skip
            except self.t.core.Failure as exc:
                if self.t.ci.missing(exc):
                    continue
                raise
            self.present.append(name)
            found += json.loads(out) if out.strip() else []
        return sorted(found, key=lambda r: r.get("createdAt") or "", reverse=True)

    def decisive(self) -> tuple[dict | None, dict | None]:
        """Решающий прогон `main` и последний зелёный до него (у зелёного решающего — None)."""
        found, green = None, None
        for run in self.runs():
            if run.get("event") not in EVENTS:
                continue
            outcome = run.get("conclusion")
            if outcome not in RED and (outcome != "success" or self.skipped(run)):
                continue
            if found is None:
                found = run
                if outcome == "success":
                    break
            elif outcome == "success":
                green = run
                break
        return found, green

    def skipped(self, run: dict) -> bool:
        """Ночь пропущена задачей `night`: каждая другая задача прогона пропущена. Итог помнится
        по прогону и попытке (`known`): перезапуск — новая попытка, читается заново."""
        rid = str(run["databaseId"])
        key, known = f"{rid}/{run.get('attempt') or 1}", self.line.setdefault("known", {})
        if key not in known:
            out = self.gh("run", "view", rid, "--json", "jobs")
            jobs = (json.loads(out) if out.strip() else {}).get("jobs") or []
            checks = [j for j in jobs if j.get("name") != NIGHT_JOB]
            known[key] = all(j.get("conclusion") == "skipped" for j in checks)
            for old in list(known)[:-KNOWN_LIMIT]:
                known.pop(old)
        return known[key]

    def clear(self, why: str) -> None:
        """Линия идёт: запись красного и тикета снята."""
        line = self.orch.data.get("mainline") or {}
        if line.pop("red", None) is None:
            return
        for key in ("ticket", "told", "tests", "dispatched"):
            line.pop(key, None)
        self.t.say(f"линия `main`: {why}")

    def mark(self, run: dict) -> None:
        """Красный решающий прогон — в состояние до любого вызова GitHub: отметки ждут уже в
        этом такте."""
        key, known = str(run["databaseId"]), self.line.get("red") or {}
        if known.get("run") != key:
            self.line["red"] = {
                "run": key, "sha": run["headSha"], "url": run.get("url"),
                "since": known.get("since") or self.t.stamp(),
            }  # fmt: skip

    def tell(self, run: dict, green: dict | None) -> None:
        """Тикет «`main` красный» или комментарий в открытом — раз на прогон (`told`, после
        записи в тикет: сбой повторится на такте)."""
        line, key = self.line, str(run["databaseId"])
        n = line.get("ticket")
        if line.get("told") == key and n is not None:
            return
        tests = self.failed(key)
        if n is not None and str(n) in self.orch.data["tickets"]:
            if tests != line.get("tests"):
                self.orch.comment(
                    n,
                    f"Оркестратор: полный прогон `main` снова красный — {run.get('url')} "
                    f"(коммит `{run['headSha'][:7]}`).\n\n## Упавшие тесты\n\n"
                    f"{self.shown(tests, run)}\n",
                )
            self.t.say(f"линия `main`: прогон {key} красный — чинит #{n}")
        else:
            self.open_ticket(run, green, tests)
        line["tests"], line["told"] = tests, key

    def failed(self, run_id: str) -> list[str] | None:
        """Упавшие тесты прогона по его отчёту; отчёта нет — None."""
        ci = self.t.ci
        with tempfile.TemporaryDirectory(prefix="canon-mainline-") as tmp:
            try:
                ci.fetch(self.t.core, self.orch.repo, run_id, tmp)
            except self.t.core.Failure as exc:
                self.t.say(f"линия `main`: отчёт прогона {run_id} не скачан — {exc}")
                return None
            return sorted(ci.failed_tests(Path(tmp)))

    @staticmethod
    def shown(tests: list[str] | None, run: dict) -> str:
        if tests:
            return lines([f"`{x}`" for x in tests])
        return f"Отчёта по тестам у прогона нет — упавшие задачи в прогоне: {run.get('url')}."

    def merges(self, green: dict | None, sha: str) -> str:
        """Коммиты `main` (первые родители) от последнего зелёного прогона до красного."""
        if green is None:
            return f"Зелёного полного прогона среди последних {RUNS_LIMIT} нет."
        self.orch.own_repo(refetch=True)
        res = self.orch.git(
            "log", "--first-parent", "--format=`%h` %s", f"{green['headSha']}..{sha}"
        )
        found = res.stdout.splitlines() if res.returncode == 0 else []
        since = f"Последний зелёный — {green.get('url')} (коммит `{green['headSha'][:7]}`)."
        if res.returncode != 0:
            return f"{since} Слияния не прочитаны: коммитов нет в клоне оркестратора."
        return f"{since}\n\n{lines(found)}" if found else f"{since} Слияний нет: тот же коммит."

    def open_ticket(self, run: dict, green: dict | None, tests: list[str] | None) -> None:
        """Тикет «`main` красный» — в очередь первым. Номер — в состояние сразу после создания:
        сбой меток не заведёт второй тикет."""
        sha = run["headSha"]
        criterion = "Упавшие тесты выше — зелёные." if tests else "Полный прогон `main` — зелёный."
        body = TICKET.format(
            url=run.get("url"), workflow=WORKFLOW, sha=sha[:7], tests=self.shown(tests, run),
            merges=self.merges(green, sha), criterion=criterion,
        )  # fmt: skip
        out = self.t.core.gh(
            "issue", "create", "--title", TITLE.format(sha=sha[:7]), "--body", body
        )
        found = ISSUE.search(out)
        if not found:
            raise self.t.core.Failure(f"`gh issue create` не дал ссылки на тикет: {out.strip()}")
        n, data = int(found.group(1)), self.orch.data
        self.line["ticket"] = n
        data["tickets"][str(n)] = self.t.fresh(v2=True)
        data["queue"].insert(0, n)
        self.t.say(f"линия `main`: прогон {run['databaseId']} красный — тикет #{n} первым")
        self.orch.labels(n, add=(self.t.READY, self.t.QUEUED))

    def stop(self) -> None:
        """Авто-мёрж — снят с готовых PR оркестратора, место слияния свободно. PR, уже слитый
        GitHub, — итог его шага: сбой снятия — только в лог, повтор на такте."""
        data, n_red = self.orch.data, self.line.get("ticket")
        found = []
        for key, t in data["tickets"].items():
            if t.get("v2") and t.get("phase") == "merge" and int(key) != n_red and t.get("pr"):
                found.append((t["pr"], f"#{key}", lambda t=t, k=key: self.back(int(k), t)))
        bots = self.orch.dependabot()
        for key, e in (data.get("dependabot") or {}).items():
            if e.get("phase") == "merge":
                found.append((e["url"], f"PR Dependabot #{key}", lambda k=key: bots.release(k)))
        upkeep = data.get("upkeep") or {}
        if (upkeep.get("pr") or {}).get("phase") == "merge":
            tool = self.orch.upkeep()
            found.append((upkeep["pr"]["url"], "PR обслуживания", lambda: tool.release(upkeep)))
        for url, who, release in found:
            try:
                self.t.core.gh("pr", "merge", url, "--disable-auto")
            except self.t.core.Failure as exc:
                self.t.say(f"{who}: авто-мёрж не снят — {exc}")
                continue
            release()
            self.t.say(f"{who}: `main` красный — авто-мёрж снят, отметка ждёт зелёного `main`")

    def back(self, n: int, t: dict) -> None:
        """Тикет — снова на шаге CI: отметка и авто-мёрж — заново, когда `main` зелёный."""
        t["phase"] = "ci"
        self.orch.catchup().release(n)

    def dispatch(self) -> list[str]:
        """Тикет «`main` красный» слит или закрыт — ручной полный прогон `main`, раз на коммит:
        сбой запуска (нет `workflow_dispatch` у запускаемого) — строкой итога раз на коммит."""
        line = self.line
        n = line.get("ticket")
        if n is None or str(n) in self.orch.data["tickets"]:
            return []
        self.orch.own_repo(refetch=True)
        sha = self.orch.git("rev-parse", "origin/main").stdout.strip()
        if not sha or line.get("dispatched") == sha:
            return []
        line["dispatched"] = sha
        name = (self.present or [WORKFLOW])[0]
        try:
            self.gh("workflow", "run", name, "--ref", "main")
        except self.t.core.Failure as exc:
            why = (
                f"#{n} больше не ведётся, а ручной полный прогон на {sha[:7]} не запущен — {exc}; "
                "линия ждёт ночного прогона или запуска владельцем (Actions → «Run workflow»)"
            )
            self.t.say(f"линия `main`: {why}")
            return [f"линия `main`: {why}"]
        self.t.say(f"линия `main`: #{n} больше не ведётся — ручной полный прогон на {sha[:7]}")
        return []
