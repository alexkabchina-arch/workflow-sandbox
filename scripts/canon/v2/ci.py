"""ci — причины красного CI на пути v2 по отчёту о каждом тесте (#335; спека #329, «Контракт CI
проектов»).

CI проекта кладёт отчёт по тестам файлом прогона: артефакт с именем `test-report…`, внутри —
JUnit XML (контракт — раздел «Ожидаемые job CI» шаблона профиля). Красный CI на head «код готов»
оркестратор разбирает, когда прогоны с красными задачами закончены целиком, по порядку:

- линия `main` остановлена (красный полный прогон `main`, #357, `v2/mainline.py`), а это не
  тикет «`main` красный», — ожидание: не возврат, не перезапуск и не остановка (тикет на починку
  уже заведён); зелёный `main` — разбор заново;
- упали только тесты, которые падают и на последнем законченном прогоне `main` того же
  workflow, а линия идёт (`main` другого workflow или ночь ещё не прошла), — «`main` красный»:
  остановка с отчётом владельцу; прогон старше последнего полного прогона `main`
  (`canon-mainline`, #430: ночь и ручной прогон, проектный CI под его вызовом) им вытеснен и
  не читается — иначе последняя ночь `CI` до переноса судила бы PR вечно; тикет «`main` красный» эту
  сверку не проходит — он эти тесты и чинит;
- упало вне диффа ветки, а ветка отстала от `main`, — догон основы на сервере (`v2/catchup.py`,
  #348), затем полный CI на новом head;
- иначе — один перезапуск упавших задач на том же коммите (`gh run rerun --failed`), не возврат:
  так отмена, превышение времени и сбой раннера не тратят возвраты;
- красный и после перезапуска — возврат той же сессии с именами упавших тестов.

Тест, позеленевший при перезапуске на том же коммите, — нестабильный: имя — в состояние тикета
(`flaky`), строка — в тикет. Карантин — только тикетом со сроком и «да» владельца: оркестратор
тест не отключает и тикетов не заводит.

Упавшие тесты по именам (`classname.name`) — в своём состоянии тикета (`red`); разбор того же
head и той же попытки отчёты PR заново не качает (тикет, придержанный паузой); последний прогон
`main` читается каждый раз — после `agent:go` при «`main` красный» нужен свежий. В
комментарии и задания — не больше `SHOWN` имён. Отчёта нет — имён нет: перезапуск и возврат идут
по именам задач, как раньше.

Удалить: выключить переключатель, затем этот файл и `"ci"` в `v2/steps.py` (шаг — снова нынешний
`orch.ci`); параметр `cause` у `orch.ci` — вместе с ним. Отчёт по тестам в CI ничему не мешает.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path, PurePosixPath

REPORT = "test-report*"  # имя артефакта отчёта по тестам (шаблон `gh run download --pattern`)
REPORT_LIMIT = 50 * 2**20  # файл отчёта больше — не читается
GH_TIMEOUT = 300
RERUN_WAIT = 1800  # перезапуск не дал нового итога за полчаса — красный, как после перезапуска
SHOWN = 20  # имён тестов в комментарии и задании — не больше
MISSING = "could not find any workflows"  # `gh run list --workflow` без такого workflow


def failed_tests(folder: Path) -> dict[str, str]:
    """Упавшие тесты в отчётах JUnit XML под `folder`: `<failure>` или `<error>` у `<testcase>`;
    имя (`classname.name`) — его место (`classname`; пустой — ошибка сбора pytest, место —
    `name`); файл отчёта не XML — пропуск."""
    found = {}
    for path in sorted(folder.rglob("*.xml")):
        if path.stat().st_size > REPORT_LIMIT:
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                classname, name = case.get("classname", ""), case.get("name", "")
                found[f"{classname}.{name}" if classname else name] = classname or name
    return found


def missing(exc: Exception) -> bool:
    """Сбой `gh` — workflow с таким именем в репозитории нет (не прогонов, а самого файла)."""
    return MISSING in str(exc)


def fetch(core, repo: str, run_id: str, folder: str) -> None:
    """Отчёт по тестам прогона — в `folder` (`gh run download`, артефакт `REPORT`); его разбор —
    у вызывающего: упавшие (`failed_tests`) здесь, итог каждого (`spot.outcomes`) у допуска
    (`v2/gates.py`). Сбой — `core.Failure`."""
    core.gh(
        "run", "download", run_id, "--pattern", REPORT, "--dir", folder, "-R", repo,
        timeout=GH_TIMEOUT,
    )  # fmt: skip


def touches(place: str, changed: list[str]) -> bool:
    """Тест из файла диффа. Место — путь через точки (`tests.test_calc`, pytest): любой его
    префикс совпал с файлом диффа без расширения; или путь файла (`tests/calc.test.js`,
    `example.spec.ts` — от каталога тестов): файл диффа — он или кончается им."""
    stems = {str(PurePosixPath(f).with_suffix("")) for f in changed}
    parts = place.split(".")
    if any("/".join(parts[:i]) in stems for i in range(1, len(parts) + 1)):
        return True
    return any(f == place or f.endswith(f"/{place}") for f in changed)


def listed(names: list[str]) -> str:
    """Имена через запятую, не больше `SHOWN`; остальные — числом."""
    more = len(names) - SHOWN
    return ", ".join(names[:SHOWN]) + (f" и ещё {more}" if more > 0 else "")


def moment(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


class Cause:
    """Красный и зелёный CI тикета пути v2 — хуки шага `ci` оркестратора (`orch.ci(…, cause)`)."""

    def __init__(self, orch, start, say, stamp):
        self.orch, self.start, self.core = orch, start, start.core
        self.say, self.stamp = say, stamp

    def gh(self, *args: str) -> str:
        return self.core.gh(*args, "-R", self.orch.repo, timeout=GH_TIMEOUT)

    def report(self, run_id: str) -> dict[str, str]:
        """Упавшие тесты прогона по его отчёту; отчёта нет или сбой скачивания — пусто."""
        with tempfile.TemporaryDirectory(prefix="canon-report-") as tmp:
            try:
                fetch(self.core, self.orch.repo, run_id, tmp)
            except self.core.Failure as exc:
                self.say(f"отчёт по тестам прогона {run_id} не скачан: {exc}")
                return {}
            return failed_tests(Path(tmp))

    def main_failed(self, workflows: set[str], full: str) -> tuple[set[str], list[str]]:
        """Тесты, упавшие на последнем законченном прогоне `main` каждого workflow, и сами
        прогоны (id и коммит). Прогон старше последнего полного прогона `main` (workflow `full`;
        его в репозитории нет — без него) вытеснен им."""
        latest = {}
        for name in sorted(workflows | {full}):
            args = ("--branch", "main", "--workflow", name, "--status", "completed")
            try:
                out = self.gh(
                    "run", "list", *args, "--limit", "1",
                    "--json", "databaseId,headSha,conclusion,createdAt",
                )  # fmt: skip
                latest[name] = json.loads(out) if out else []
            except (self.core.Failure, ValueError) as exc:
                if not (name == full and missing(exc)):
                    self.say(f"прогоны `main` workflow {name} не прочитаны: {exc}")
        since = max((r.get("createdAt") or "" for r in latest.get(full) or []), default="")
        failed, seen = set(), []
        for name, found in latest.items():
            for item in found or []:
                if name != full and (item.get("createdAt") or "") < since:
                    continue  # вытеснен полным прогоном `main`
                seen.append(f"{item['databaseId']} на {str(item.get('headSha'))[:7]}")
                if item.get("conclusion") != "success":
                    failed |= set(self.report(str(item["databaseId"])))
        return failed, seen

    def runs(self, pr: dict) -> tuple[dict[str, str], bool]:
        """Прогоны Actions этого репозитория с красными задачами — {id: workflow} — и закончены ли
        они целиком: GitHub не перезапускает идущий прогон, а отчёт пишется в конце."""
        rollup = pr.get("statusCheckRollup") or []
        runs, done = {}, True
        for c in rollup:
            match = self.start.GUARD_RUN.fullmatch(c.get("detailsUrl") or "")
            if not match or match.group(1) != self.orch.repo or c.get("name") == self.start.GUARD:
                continue
            if self.start.is_red(c):
                runs[match.group(2)] = c.get("workflowName")
        for c in rollup:
            match = self.start.GUARD_RUN.fullmatch(c.get("detailsUrl") or "")
            if match and match.group(2) in runs and (c.get("status") or "COMPLETED") != "COMPLETED":
                done = False
        return runs, done

    def red(self, n: int, t: dict, st, pr: dict, red: list[str]) -> None:
        orch, head = self.orch, pr["headRefOid"]
        runs, done = self.runs(pr)
        if not done:
            return  # другие задачи тех же прогонов ещё идут: ждём их итога
        line = orch.mainline()
        if why := line.holds(n):  # линия `main` остановлена (#357): ожидание, не возврат
            if t.get("line_wait") != head:
                t["line_wait"] = head
                self.say(f"#{n}: красный CI на {head[:7]} — ожидание, не возврат: {why}")
            return
        t.pop("line_wait", None)
        items = [c for c in pr.get("statusCheckRollup") or [] if self.start.is_red(c)]
        rerun = t.get("rerun") or {}
        if rerun.get("head") == head:
            since = moment(rerun.get("at"))
            finished = [moment(c.get("completedAt")) for c in items]
            stale = since and any(d and d < since for d in finished)
            if stale and (moment(self.stamp()) - since).total_seconds() < RERUN_WAIT:
                return  # на PR — итог попытки до перезапуска: ждём новый
        key = [head, rerun.get("at")]
        known = t.get("red") or {}
        if known.get("key") == key:  # тот же итог — отчёты уже прочитаны (тикет придержан паузой)
            places = known["places"]
        else:
            places = {}
            for run_id in runs:
                places.update(self.report(run_id))
        tests = sorted(places)
        t["red"] = {"head": head, "key": key, "checks": red, "tests": tests, "places": places}
        workflows = {w for w in runs.values() if w}
        # Тикет «`main` красный» (#357) чинит эти тесты сам: сверка с `main` — не для него.
        mine = tests and not line.fixes(n)
        on_main, seen = self.main_failed(workflows, line.workflow) if mine else (set(), [])
        names = red + ([f"тесты: {listed(tests)}"] if tests else [])
        if tests and set(tests) <= on_main:
            t["red"]["cause"] = "main"
            return orch.stop(
                n,
                t,
                f"«`main` красный»: {listed(tests)} падают и на последнем прогоне `main` "
                f"({', '.join(seen)}) — чинит не этот тикет; `agent:go` — снова проверка CI",
            )
        if tests:
            orch.own_repo(refetch=True)
            if not orch.ancestor("origin/main", head):
                diff = orch.git("diff", "--name-only", f"origin/main...{head}").stdout.split()
                if not any(touches(places[x], diff) for x in tests):
                    t["red"]["cause"] = "behind"
                    t["behind"] = True
                    # Ложь — основа уже в ветке (свежий `fetch` догона): дальше перезапуск.
                    if orch.catch_up(n, t, st, pr):
                        return
        if rerun.get("head") == head or not runs:
            t["red"]["cause"] = "code"
            why = f"красный CI на {head[:7]}" + (" и после перезапуска" if runs else "")
            return orch.give_back(n, t, st, why, names)
        # Перезапуск засчитан до вызовов: частичный сбой не даст второго перезапуска тех же
        # прогонов, а время — раньше любого нового итога.
        t["red"]["cause"] = "rerun"
        t["rerun"] = {"head": head, "tests": tests, "at": self.stamp()}
        for run_id in sorted(runs):
            try:
                self.gh("run", "rerun", run_id, "--failed")
            except self.core.Failure as exc:
                return orch.stop(n, t, f"красный CI: перезапуск прогона {run_id} не удался: {exc}")
        self.say(f"#{n}: красный CI на {head[:7]} — перезапуск упавших задач, не возврат")

    def green(self, n: int, t: dict, pr: dict) -> None:
        """Зелёный CI: тесты, упавшие до перезапуска на том же коммите, — нестабильные. Запись
        перезапуска снимается после строки в тикет: сбой комментария повторит её на такте."""
        rerun = t.get("rerun") or {}
        if rerun.get("head") == pr["headRefOid"] and (flaky := rerun.get("tests")):
            t["flaky"] = sorted({*t.get("flaky", []), *flaky})
            self.orch.comment(
                n,
                f"Оркестратор: {listed([f'`{x}`' for x in flaky])} — нестабильные: упали и "
                f"позеленели при перезапуске на том же коммите {pr['headRefOid'][:7]}. Карантин "
                "— только тикетом со сроком и «да» владельца; оркестратор тест не отключает.",
            )
            self.say(f"#{n}: нестабильные тесты — {listed(flaky)}")
            send = self.orch.sender()  # список карантина — в карточке PR (#347)
            line = (
                f"{listed([f'`{x}`' for x in t['flaky']])} — нестабильные (позеленели при "
                "перезапуске): не блокируют, остаются в списке на починку; карантин — тикетом со "
                "сроком и «да» владельца"
            )
            send.mark(n, t, send.t.card.QUARANTINE, line, key="flaky")
        t.pop("rerun", None)
