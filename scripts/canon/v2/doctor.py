"""doctor — «доктор» среды и проверка в песочнице программой пути v2 (#342; спека #329, «Ворота»).

**«Доктор»** — перед каждой сессией тикета пути v2 (`orch.launch`, до первой команды внутри:
приведения рабочей копии и входа возврата), вердикт — один на такт (`orch.health`):

- песочница жива — команда внутри (`true`) проходит; затем подготовка такта (`orch.prepare`:
  образы, браузер, база-шаблон; её сбой — сбой такта, как раньше);
- база-шаблон есть и свежая (если она у проекта есть, `canon.sandboxSeedFrom` и
  `canon.sandboxSeedInto`): перенос записан для этой песочницы (`id` из `sbx ls`), снимок не
  старше `COPY_STALE`, а без времени снимка — перенос не раньше вчерашних суток;
- приложение поднимается и инструмент проверки отвечает (если он у проекта есть) — команда
  проекта `canon.sandboxDoctor` внутри песочницы, в клоне, оболочкой; код 0 — здоров.

**Проверка в песочнице** — после «код готов» и после каждой починки (шаг «сессия» кончился на
«код готов» с новым head, `orch.ready_step`), до ревью и отправки: оркестратор, а не модель,
гоняет в worktree тикета внутри песочницы быстрые проверки (блок ```fast-checks, кроме форматера
`format`, — он правит файлы) и проверки ветки (блок ```checks: тесты, линтер, формат, секреты)
профиля из `origin/main` своего клона — ветка список не подменит; `secrets` — помощником копии
канона, а не файлом ветки (`core.canon_secrets`, #283). Аргумент `{files}` — файлы диффа ветки с
`origin/main`. Worktree не на head «код готов» или с незакоммиченными правками отслеживаемых
файлов — красная: проверялся бы не тот код. Это фильтр перед ревью, не допуск: доверенной не
считается, минут GitHub не тратит. Красная — **возврат** свежей сессии с причинами
(`SANDBOX_TASK`, тот же счёт `RETURNS`, что у красного CI; возврат, отложенный паузой, помнит
красный head — `sandbox_red`, без повторного прогона); зелёная — дальше (ревью, отправка); тот
же head второй раз не проверяется (`checked` в своём состоянии тикета).

**Код «среда».** Красный «доктор», «не проверить» (команда проверки не запустилась, ответ
проверки не разобран, сбой `sbx`, список профиля `main` испорчен) — не ошибка кода: возврат не
тратится. Оркестратор пробует рецепты (`RECIPES`, по порядку — те, что лечат вид проблемы; после
рецепта, который что-то сделал, — заново): перенести шаблон базы; пересоздать песочницу
(`sandbox rm` и `up`, без токенов GitHub в окружении) — только при полном простое: ни живого
запуска, ни тикета старого пути с сессией (её транскрипт удалился бы с песочницей). Не помогло —
остановка тикета с кодом `среда` (отчёт об остановке; у проверки — шаг `settle`: `agent:go`
проверит тот же head без новой сессии) и пауза очереди `environment`: новые тикеты не берутся.
Пауза — общая для тикетов, остановленных «средой» (признак `STOPPED` в состоянии тикета, причина —
по каждому, `whys`); снимает её владелец — `agent:go` или `agent:retry` на любом из них
(`release`), посторонний тикет — нет. Сбой
`git fetch` своего клона — сбой такта, не «среда»: следующий такт повторит.

Удалить: выключить переключатель, затем этот файл; вызовы `orch.environment()` в `launch` и
`ready_step` оркестратора, `health` в `reset`, параметр `task` у `give_back`, `release` в
`resume_stopped` и `retry`. Старый путь и скрипт `sandbox` не менялись.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import os
import sys
import types
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCTOR_KEY = "canon.sandboxDoctor"  # команда проекта: приложение поднимается, инструмент отвечает
PROFILE = "docs/agents/profile.md"
CODE = "среда"
PAUSE = "environment"  # пауза очереди после остановки «среда»
STOPPED = "environment_stop"  # признак в состоянии тикета: остановлен «средой»
DOCTOR_TIMEOUT = 600  # команда «доктора» дольше — красный
CHECK_TIMEOUT = 1800  # одна проверка в песочнице дольше — красная (не кончилась)
RUN_MARGIN = 600  # сверх суммы пределов проверок — на дифф, git и запуск `sbx exec`
FORMAT = "format"  # форматер ```fast-checks правит файлы — в проверке не идёт
SHOWN = 6000  # знаков причин в задании возврата — не больше
NOT_STARTED = 127  # код `checks.run`: команда не запустилась — «не проверить»
# Виды проблем среды — по ним рецепт решает, лечит ли он её.
SANDBOX, TEMPLATE, APP, RUNNER, TOOL = "sandbox", "template", "app", "runner", "tool"
# Рецепты самопочинки среды — одним списком, по порядку: имя, что делает, какие виды лечит.
RECIPES = (
    ("seed", "перенести шаблон базы", {TEMPLATE, APP}),
    ("recreate", "пересоздать песочницу", {SANDBOX, APP, RUNNER}),
)
SANDBOX_TASK = """Оркестратор: проверка в песочнице на `{head}` красная — тикет #{n} вернулся тебе.
Проверки профиля, которые упали (команда, код, конец вывода):

{why}

Почини в worktree тикета и закоммить (сообщение одной строкой `-m`). Затем вызов `{ship}` ({long})
и его задания — до {until}: оркестратор проверит новый head снова. Без владельца не починить —
вопрос только `{ask}`."""
DIRTY = (
    "проверка «рабочая копия» — worktree не на head «код готов» `{head}` (там `{have}`) или с "
    "незакоммиченными правками отслеживаемых файлов: проверялся бы не тот код — закоммить или "
    "откати их"
)


def release(orch, n: int, t: dict) -> None:
    """`agent:go` или `agent:retry` на тикете `n`, остановленном «средой» (признак `STOPPED` в
    его состоянии), — пауза снята: владелец посмотрел среду. Снимает любой такой тикет, в том
    числе остановленный до того, как паузу сняли и поставили снова; посторонний — нет.
    Признак тикета снимается."""
    marked = t.pop(STOPPED, False)
    pause = orch.data["pauses"].get(PAUSE)
    if pause is None:
        return
    # Запись паузы первой версии (#384) — номер тикета в `ticket`, None — любой.
    if marked or ("ticket" in pause and pause["ticket"] in (None, n)):
        orch.clear_pause(PAUSE)


def hold(orch, n: int, t: dict, why: str) -> None:
    """Пауза очереди после остановки «среда» тикета `n` (отчёт уже в тикете). Пауза — общая для
    тикетов, остановленных «средой»: снимет любой из них (`release`). Причина — по каждому
    тикету: прежние остановки не теряются. Зовёт и проверка постановки (`v2/intake.py`, #355)."""
    t[STOPPED] = True
    old = orch.data["pauses"].get(PAUSE) or {}
    whys = {k: v for k, v in (old.get("whys") or {}).items() if k != str(n)}
    whys[str(n)] = why
    listed = "; ".join(f"#{k}: {v}" for k, v in whys.items())
    orch.set_pause(
        PAUSE,
        f"среда — {listed} — снимет `agent:go` или `agent:retry` на любом из этих тикетов",
    )
    orch.data["pauses"][PAUSE]["whys"] = whys


class Environment:
    """«Доктор» и проверка в песочнице для оркестратора `orch`; `tools` — его помощники: `core`,
    `say`, `tail`, `now`, `local_now`, `snapshot_time`, `group_alive`, `Failure`, `TOKENS`,
    `COPY_STALE`, `SEED_FROM_KEY`, `SEED_INTO_KEY`."""

    def __init__(self, orch, tools):
        self.orch, self.tools = orch, tools

    # --- «доктор» ---

    def examine(self) -> list[tuple[str, str]]:
        """Проблемы среды — вид и текст; пусто — здорова. Сбой подготовки — `Failure` такта."""
        orch, tools = self.orch, self.tools
        try:
            alive = orch.inside("true", timeout=DOCTOR_TIMEOUT)
        except tools.Failure as exc:
            return [(SANDBOX, f"песочница не отвечает: {exc}")]
        if alive.returncode != 0:
            why = tools.tail(alive.stderr or alive.stdout) or f"код {alive.returncode}"
            return [(SANDBOX, f"песочница не отвечает: {why}")]
        orch.prepare()
        problems = []
        if why := self.template():
            problems.append((TEMPLATE, why))
        if command := orch.config(DOCTOR_KEY):
            try:
                res = orch.inside("sh", "-c", command, workdir=orch.clone, timeout=DOCTOR_TIMEOUT)
            except tools.Failure as exc:
                problems.append((APP, f"`{DOCTOR_KEY}`: {exc}"))
            else:
                if res.returncode != 0:
                    out = tools.tail(res.stderr or res.stdout) or "вывода нет"
                    problems.append((APP, f"`{DOCTOR_KEY}` — код {res.returncode}: {out}"))
        return problems

    def seeding(self) -> bool:
        orch, tools = self.orch, self.tools
        return bool(orch.config(tools.SEED_FROM_KEY) and orch.config(tools.SEED_INTO_KEY))

    def template(self) -> str | None:
        """База-шаблон есть в этой песочнице и свежая; у проекта её нет — None."""
        orch, tools = self.orch, self.tools
        if not self.seeding():
            return None
        seeded = orch.data.get("sandbox", {}).get("seeded")
        box = orch.sandbox_entry().get("id")
        if not (isinstance(seeded, dict) and seeded.get("box") == box):
            return "базы-шаблона в песочнице нет (перенос в эту песочницу не записан)"
        if seeded.get("stamp"):
            when = tools.snapshot_time(seeded["stamp"])
            if when is None or (tools.now() - when).total_seconds() > tools.COPY_STALE:
                return f"база-шаблон устарела: снимок от {seeded['stamp']}"
            return None
        yesterday = (tools.local_now().date() - timedelta(days=1)).isoformat()
        if str(seeded.get("day") or "") < yesterday:
            return f"база-шаблон устарела: перенесена {seeded.get('day')}"
        return None

    def doctor(self, n: int, t: dict) -> bool:
        """«Доктор» перед сессией тикета `n`: здорова — True; красный — рецепты, не помогли —
        остановка «среда» и пауза очереди (False): сессия не запускается, возврат не тратится.
        Вердикт — один на такт: второй запуск такта команды и рецепты не повторяет."""
        orch = self.orch
        if orch.health is True:
            return True
        if orch.health is not None:
            self.down(n, t, f"{orch.health} (тот же такт)", [])
            return False
        problems = self.examine()
        tried = []
        if problems:
            self.tools.say(f"#{n}: «доктор» среды красный — {texts(problems)}")
            problems, tried = self.heal(problems, self.examine)
        if not problems:
            orch.health = True
            return True
        orch.health = f"«доктор» среды красный: {texts(problems)}"
        self.down(n, t, orch.health, tried)
        return False

    def heal(self, problems: list, again) -> tuple[list, list[str]]:
        """Рецепты по порядку — те, что лечат вид хоть одной проблемы; после рецепта, который
        что-то сделал, — `again()`. Итог — оставшиеся проблемы и что пробовали (с исходом)."""
        tried = []
        for name, what, kinds in RECIPES:
            if not kinds & {kind for kind, _ in problems}:
                continue
            try:
                found = getattr(self, f"recipe_{name}")()
            except self.tools.Failure as exc:
                found = (f"сбой: {exc}", False)
            if found is None:
                continue  # рецепт к этой среде не применим
            outcome, done = found
            tried.append(f"{what} — {outcome}")
            self.tools.say(f"рецепт среды: {what} — {outcome}")
            if not done:
                continue  # среда не менялась — проверять заново нечего
            problems = again()
            if not problems:
                return [], tried
        return problems, tried

    def recipe_seed(self) -> tuple[str, bool] | None:
        """Шаблон базы — заново, хоть сегодня его уже переносили. Время снимка в этой песочнице
        остаётся: обновление на Mac не удалось — переноса нет, «доктор» снова красный."""
        orch = self.orch
        if not self.seeding():
            return None
        record = orch.data.setdefault("sandbox", {})
        for key in ("seed_tried", "seed_tried_stamp"):
            record.pop(key, None)
        seeded = record.get("seeded")
        if isinstance(seeded, dict) and seeded.get("box") == orch.sandbox_entry().get("id"):
            seeded["day"] = None  # шаблон этой песочницы освежается сейчас, а не завтра
        else:
            record.pop("seeded", None)
        before = record.get("seeded")
        before = dict(before) if isinstance(before, dict) else None
        orch.seed()
        moved = record.get("seeded") != before
        return ("перенесён" if moved else "не перенесён"), moved

    def recipe_recreate(self) -> tuple[str, bool]:
        """Песочница — заново (`sandbox rm`, `up`) без токенов GitHub в окружении; подготовка
        такта — снова (её делает «доктор»). Только при полном простое: транскрипты сессий старого
        пути удалились бы с ней."""
        orch, tools = self.orch, self.tools
        if busy := self.busy():
            return f"не пересоздана: {busy}", False
        saved = {k: os.environ.pop(k) for k in tools.TOKENS if k in os.environ}
        try:
            try:
                orch.box.remove()
            except Exception as exc:  # noqa: BLE001 — `sandbox.Failure` и сбой `sbx`
                return f"не пересоздана: {exc}", False
            orch.entry, orch.procs, orch.prepared, orch.env = None, None, False, {}
            try:
                orch.box.up(None)
            except Exception as exc:  # noqa: BLE001
                return f"удалена, но не поднята: {exc}", True
        finally:
            os.environ.update(saved)
        orch.entry = None
        return "пересоздана", True

    def busy(self) -> str:
        """Что держит пересоздание: тикеты старого пути с сессией и живые запуски. Процессы
        внутри не прочитаны (песочница не отвечает) — живые запуски по `sbx exec` на Mac."""
        orch = self.orch
        risk = orch.at_risk()
        try:
            live = orch.running_runs()
        except self.tools.Failure:
            live = [
                f"#{key}"
                for key, t in orch.data["tickets"].items()
                if t.get("runs")
                and "ended" not in t["runs"][-1]
                and self.tools.group_alive(t["runs"][-1].get("pid") or 0)
            ]
        busy = risk + live
        return f"в работе {', '.join(busy)}" if busy else ""

    def down(
        self,
        n: int,
        t: dict,
        why: str,
        tried: list[str],
        settle: bool = False,
        pause: bool = True,
    ) -> None:
        """Остановка «среда» и пауза очереди (после отчёта: его сбой паузу не оставит). `settle`
        — `agent:go` разберёт итог той же сессии (проверит тот же head) без нового запуска.
        `pause` False — дело только этого тикета («не проверить» пункт `/verify`, #351): код тот
        же, очередь идёт."""
        orch = self.orch
        if not pause:
            reason = (
                f"не проверить — код `{CODE}`: {why}. Возврат не потрачен; очередь не на паузе: "
                "проверить не удалось пункт этого тикета, а не среду"
            )
            orch.stop(n, t, reason)
            return
        done = f"; рецепты: {'; '.join(tried)}" if tried else "; рецептов к этой среде нет"
        reason = (
            f"сбой среды — код `{CODE}`: {why}{done}. Возврат не потрачен; очередь на паузе, "
            f"пока на этом или другом тикете, остановленном «средой», нет `agent:go` или "
            f"`agent:retry`"
        )
        phase = t["phase"]
        if settle:
            t["phase"] = "settle"
        try:
            orch.stop(n, t, reason)
        except BaseException:
            t["phase"] = phase  # следующий такт повторит разбор
            raise
        t["environment_stops"] = t.get("environment_stops", 0) + 1  # метрики тикета (#356)
        hold(orch, n, t, why)

    # --- проверка в песочнице ---

    def listed(self) -> list[tuple[str, list[str]]]:
        """Проверки профиля из `origin/main` своего клона (клон уже свежий): ```fast-checks без
        форматера, затем ```checks; одинаковые команды — один раз. Блок испорчен — `Failure`."""
        orch, core = self.orch, self.tools.core
        shown = orch.git("show", f"origin/main:{PROFILE}")
        text = shown.stdout if shown.returncode == 0 else ""
        try:
            fast = [c for c in core.fast_checks(text, PROFILE) or [] if c[0] != FORMAT]
            if core.CHECKS_BLOCK.search(text):
                branch = core.parse_checks(text, PROFILE)
            else:
                try:  # профиль до #154 — таблица «Команды»
                    branch = core.parse_checks(text, PROFILE)
                except core.Failure:
                    branch = []  # ни блока, ни строк таблицы — только быстрые
        except (core.Failure, ValueError) as exc:
            raise self.tools.Failure(f"список проверок профиля `main` не разобран: {exc}") from None
        out, seen = [], set()
        for name, command in fast + branch:
            if tuple(command) not in seen:
                seen.add(tuple(command))
                out.append((name, command))
        return out

    def run_checks(self, worktree: Path, checks: list, head: str) -> dict:
        """Проверки внутри песочницы в worktree тикета: `{"red": [...], "broken": [...]}`.
        Сбой `sbx` или ответ не разобран — `Failure` («не проверить»). Предел — сумма пределов
        проверок с запасом: прогон внутри кончится сам раньше, чем его оборвёт `sbx`."""
        orch, tools = self.orch, self.tools
        cmd = ["python3", str(HERE / "doctor.py"), "run", str(worktree), head, json.dumps(checks)]
        timeout = CHECK_TIMEOUT * len(checks) + RUN_MARGIN
        res = orch.inside(*cmd, workdir=worktree, timeout=timeout)
        lines = (res.stdout or "").strip().splitlines()
        try:
            found = json.loads(lines[-1])
            assert res.returncode == 0 and isinstance(found, dict)
        except (IndexError, ValueError, AssertionError):
            raise tools.Failure(
                f"проверка в песочнице не ответила: {tools.tail(res.stderr or res.stdout)}"
            ) from None
        return found

    def verify(self, n: int, t: dict, st) -> bool:
        """Проверка в песочнице head «код готов»: зелёная (или уже была на этом head) — True;
        красная — возврат; «не проверить» — рецепты, затем остановка «среда» (False)."""
        orch, tools = self.orch, self.tools
        head = (st["ready"] or {}).get("head")
        if not head or t.get("checked") == head:
            return True
        red = t.get("sandbox_red") or {}
        if red.get("head") == head:  # возврат ждал конца паузы — без второго прогона
            orch.give_back(n, t, st, red["why"], red["names"], task=red["task"])
            return False
        orch.own_repo()  # сбой `git fetch` — сбой такта, повтор на следующем
        worktree = orch.worktree(n)
        try:
            checks = self.listed()
            if checks and worktree is None:
                raise tools.Failure("worktree тикета в песочнице нет")
        except tools.Failure as exc:
            self.down(n, t, f"не проверить в песочнице: {exc}", [], settle=True)
            return False
        if not checks:
            tools.say(f"#{n}: проверки в песочнице нет — в профиле `main` нет проверок")
            t["checked"] = head
            return True
        found, problems, tried = self.attempt(worktree, checks, head)
        if problems:
            why = f"не проверить в песочнице: {texts(problems)}"
            self.down(n, t, why, tried, settle=True)
            return False
        if found.get("red"):
            text = "\n\n".join(found["red"])
            if len(text) > SHOWN:
                text = text[:SHOWN] + "\n…"
            names = [line.split("»")[0].removeprefix("проверка «") for line in found["red"]]
            task = SANDBOX_TASK.format(n=n, head=head[:7], why=text, **orch.task_words(n))
            why = "проверка в песочнице красная"
            t["sandbox_red"] = {"head": head, "why": why, "names": names, "task": task}
            orch.give_back(n, t, st, why, names, task=task)
            return False
        t.pop("sandbox_red", None)
        t["checked"] = head
        tools.say(f"#{n}: проверка в песочнице зелёная на {head[:7]}")
        return True

    def attempt(self, worktree: Path, checks: list, head: str) -> tuple[dict, list, list[str]]:
        """Прогон и, если «не проверить» (сбой `sbx`, проверка не запустилась, а красных нет), —
        рецепты с прогоном заново. Итог — ответ последнего прогона, проблемы среды, рецепты."""
        latest = {}

        def again() -> list:
            latest.clear()
            try:
                latest.update(self.run_checks(worktree, checks, head))
            except self.tools.Failure as exc:
                return [(RUNNER, str(exc))]
            if latest.get("red"):
                return []  # красное — дело кода, а не среды
            return [(TOOL, line) for line in latest.get("broken") or []]

        problems = again()
        tried = []
        if problems:
            problems, tried = self.heal(problems, again)
        return latest, problems, tried


def texts(problems: list) -> str:
    return "; ".join(text for _, text in problems)


# --- внутри песочницы ---


def checks_module():
    path = HERE / "checks.py"
    module = types.ModuleType("canon_v2_checks_doctor")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def inside(worktree: Path, head: str, checks: list) -> dict:
    """Проверки в worktree по порядку, все: красные — причины (как у хука, секреты без
    значения), не запустившиеся — `broken`. Worktree не на `head` или с правками отслеживаемых
    файлов — красная «рабочая копия», без прогона."""
    mod = checks_module()
    mod.TIMEOUT = CHECK_TIMEOUT
    ticket_core = mod.core()
    have = mod.git(worktree, "rev-parse", "HEAD").stdout.strip()
    dirty = mod.git(worktree, "status", "--porcelain", "--untracked-files=no")
    if have != head or dirty.returncode != 0 or dirty.stdout.strip():
        return {"red": [DIRTY.format(head=head[:7], have=have[:7] or "?")], "broken": []}
    diff = mod.git(
        worktree, "diff", "--name-only", "--diff-filter=ACMR", "-z", "origin/main...HEAD"
    )
    if diff.returncode != 0:
        return {"red": [], "broken": [f"дифф ветки с origin/main: {diff.stderr.strip()}"]}
    files = [f for f in diff.stdout.split("\0") if f]
    red, broken = [], []
    for name, listed_command in checks:
        command = mod.expand(listed_command, files)
        if command is None:
            continue
        command = ticket_core.canon_secrets(command, HERE.parent)  # помощник копии, не ветки
        code, output = mod.run(command, worktree)
        if code == NOT_STARTED and output.startswith("команда "):
            broken.append(f"проверка «{name}»: {output}")
        elif code != 0:
            red.append(mod.reason(name, command, code, output, ticket_core))
    return {"red": red, "broken": broken}


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] != "run":
        print("doctor.py run <worktree> <head> <проверки JSON>", file=sys.stderr)
        return 2
    print(json.dumps(inside(Path(argv[1]), argv[2], json.loads(argv[3])), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
