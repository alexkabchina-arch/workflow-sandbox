"""spot — точечный прогон тестов ревьюера на пути v2 (#336; спека #329, «Контракт CI проектов»).

Оркестратор ставит на PR метку `LABEL`; CI проекта (`reviewer-tests.yml`, контракт — шаблон
профиля) идёт событием `pull_request_target`, поэтому файл CI берётся из `main` — ветка его не
подменит, — и гоняет только тесты ревьюера: на head PR и на основе (`main` с файлами тестов
ревьюера из head). Итог — отчёт по каждому тесту двумя артефактами: `test-report-reviewer-head`
и `test-report-reviewer-base` (JUnit XML). Сами тесты прогона не роняют: красный тест — итог в
отчёте, красный прогон — сбой среды.

Прогон узнаётся по имени (`run-name` контракта): `reviewer-tests #<PR> <head>` с id больше
последнего прогона того же workflow до метки — так прошлый прогон того же head не засчитается;
отменённый (следующей меткой той же группы `concurrency`) — тоже.
Метка снимается по итогу (и по сбою): следующая метка — новое событие `labeled`. Шаг, который
зовёт прогон и решает по итогу, — тест ревьюера (#344); здесь — запуск и чтение итога.

Удалить: этот файл и `spot` в `orchestrator`; в CI проекта — `reviewer-tests.yml` и метку.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

LABEL = "ci:reviewer-tests"
WORKFLOW = "reviewer-tests.yml"
NAME = "reviewer-tests"  # начало `run-name` прогона
SIDES = ("head", "base")  # артефакт `test-report-reviewer-<сторона>`
REPORT_LIMIT = 50 * 2**20  # файл отчёта больше — не читается
MESSAGE = 500  # символов сообщения и конца трассировки ошибки теста — не больше
START_WAIT = 600  # прогона по метке нет за 10 минут — сбой
RUN_WAIT = 1800  # прогон не кончился за полчаса — сбой


def outcomes(folder: Path) -> dict[str, dict]:
    """Итог каждого теста в отчётах JUnit XML под `folder`: имя (`classname.name`; пустой
    `classname` — ошибка сбора, имя — `name`) — `{"outcome": passed|failed|error|skipped}`, у
    упавших ещё `type`, `message` (вид и сообщение ошибки) и `text` — конец трассировки: у
    ошибки сбора pytest вид (`ImportError`) только в ней. По ним шаг теста ревьюера отличает
    провал проверки от ошибки импорта. Файл не XML — пропуск."""
    found = {}
    for path in sorted(folder.rglob("*.xml")):
        if path.stat().st_size > REPORT_LIMIT:
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            classname, name = case.get("classname", ""), case.get("name", "")
            item = {"outcome": "passed"}
            for tag, outcome in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
                if (bad := case.find(tag)) is not None:
                    item = {"outcome": outcome}
                    if tag != "skipped":
                        item["type"] = bad.get("type", "")
                        item["message"] = (bad.get("message") or "")[:MESSAGE]
                        item["text"] = (bad.text or "").strip()[-MESSAGE:]
                    break
            found[f"{classname}.{name}" if classname else name] = item
    return found


class Spot:
    """Запуск точечного прогона меткой и чтение его итога. `gh(*args) -> stdout` — `gh` на
    репозиторий проекта токеном оркестратора, сбой — исключение `failure`."""

    def __init__(self, gh, failure, say, stamp, seconds_since):
        self.gh, self.failure, self.say = gh, failure, say
        self.stamp, self.seconds_since = stamp, seconds_since

    def runs(self, limit: int) -> list[dict]:
        out = self.gh(
            "run", "list", "--workflow", WORKFLOW, "--limit", str(limit),
            "--json", "databaseId,displayTitle,status,conclusion",
        )  # fmt: skip
        return json.loads(out) if out else []

    def start(self, pr: str, head: str) -> dict:
        """Метка на PR (ссылка или номер) для его head; итог — запись прогона, которую вызывающий
        хранит в состоянии тикета и передаёт в `result`. Стоявшая метка сначала снимается:
        событие `labeled` даёт только новая."""
        self.gh(
            "label", "create", LABEL, "--force", "--color", "c5def5",
            "--description", "Оркестратор: точечный прогон тестов ревьюера",
        )  # fmt: skip
        after = max((r["databaseId"] for r in self.runs(1)), default=0)
        labels = json.loads(self.gh("pr", "view", pr, "--json", "labels") or "{}")
        if LABEL in {x.get("name") for x in labels.get("labels") or []}:
            self.gh("pr", "edit", pr, "--remove-label", LABEL)
        self.gh("pr", "edit", pr, "--add-label", LABEL)
        number = str(pr).rstrip("/").rsplit("/", 1)[-1]
        return {"pr": pr, "number": number, "head": head, "after": after, "at": self.stamp()}

    def result(self, spot: dict) -> dict | None:
        """Итог прогона по записи `start`: None — ещё идёт; `{"error": …}` — сбой (прогона нет,
        не кончился в срок, нет отчёта); иначе `{"run", "conclusion", "head", "base"}` — итог
        каждого теста на ветке и на основе (`outcomes`). По итогу и по сбою метка снимается."""
        title = f"{NAME} #{spot['number']} {spot['head']}"
        found = [
            r
            for r in self.runs(100)
            if r["databaseId"] > spot["after"]
            and r.get("displayTitle") == title
            and r.get("conclusion") != "cancelled"  # отменён следующей меткой того же head
        ]
        waited = self.seconds_since(spot["at"])
        if not found:
            if waited <= START_WAIT:
                return None
            return self.done(spot, {"error": f"прогона «{title}» по метке {LABEL} нет"})
        run = max(found, key=lambda r: r["databaseId"])
        if run["status"] != "completed":
            if waited <= RUN_WAIT:
                return None
            return self.done(spot, {"error": f"прогон {run['databaseId']} не кончился за 30 мин"})
        outcome = {"run": run["databaseId"], "conclusion": run.get("conclusion")}
        for side in SIDES:
            with tempfile.TemporaryDirectory(prefix="canon-spot-") as tmp:
                name = f"test-report-reviewer-{side}"
                try:
                    self.gh("run", "download", str(run["databaseId"]), "--name", name, "--dir", tmp)
                except self.failure as exc:
                    why = f"отчёт {name} прогона {run['databaseId']} не скачан: {exc}"
                    return self.done(spot, {"error": why, **outcome})
                outcome[side] = outcomes(Path(tmp))
        return self.done(spot, outcome)

    def done(self, spot: dict, found: dict) -> dict:
        """Итог — после снятия метки; не снялась — строка, итог тот же (следующий `start`
        снимет её сам)."""
        try:
            self.gh("pr", "edit", spot["pr"], "--remove-label", LABEL)
        except self.failure as exc:
            self.say(f"метка {LABEL} с PR {spot['pr']} не снята: {exc}")
        return found
