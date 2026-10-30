"""gates — допуск к слиянию пути v2 каркасом (#346; спека #329, «Допуск»).

Слияние решает **допуск** — программа, а не модель: условия — список `CONDITIONS` в одном месте,
у каждого отказа свой код причины (`CODES`). «Эта версия» — head PR, который сессия сдала «код
готов» после разбора (догон основы на сервере, не задевший файлы тикета, — тоже она, с #348).
Условия этого тикета и #352 (условие 4); 6 и 8 добавят #355 и #356:

1. `ci` — доверенный CI зелёный на этой версии (полный прогон после перевода из черновика);
   тесты ревьюера подтверждённых находок — в его отчёте по именам как пройденные;
2. `review` — ревью видело эту версию, или изменения после него не больше порога строк кода, или
   пройдено короткое ревью этих изменений (`v2/fixes.py`, тот же счёт строк);
3. `findings` — подтверждённая находка закрыта тестом «красный → зелёный» (красный — точечный
   прогон, зелёный — условие 1) или судьёй; файл теста ревьюера на этой версии побайтно тот, что
   в коммите «только тест»; конфиг сборки тестов (`TEST_CONFIG`) ветка с того коммита не меняла;
4. `evidence` — на этой версии у оркестратора есть доказательство `/verify` по каждому критерию и
   примеру замысла из снимка тикета и по каждой находке со сценарием `/verify`, итог по ним —
   PASS (`v2/evidence.py`, #352). PR из `.github/` и документов без `/verify` (решение владельца
   30.10) — после зелёного полного CI (`proved`): его прогоны на этой версии, а при правке
   вызываемого `ci.yml` и стоящем `canon-mainline.yml` — ещё зелёный ручной прогон
   `canon-mainline` на ветке (запускает допуск, раз на версию);
5. `fresh` — в ветке есть последняя основа. Отставшую ветку шаг CI догоняет на сервере раньше
   допуска (`v2/catchup.py`, #348); отказ — только если основа ушла между догоном и допуском,
   `agent:go` после него — догон на сервере (`catch_up` оркестратора);
7. `weakening` — ослабления защиты, найденного ревью, нет или по нему есть решение разбора
   («ослабление — …»): ослабление, которого тикет не просит, блокирует.

Порядок — шаг CI (`orch.ci` с этим объектом): условия 2, 3, 4, 5, 7 (`admit`) → перевод PR из
черновика → полный CI → условие 4 PR из `.github/` и документов (`proved`) и 1 (`trusted`) →
отметка `review` и авто-мёрж режимом `ship-finish --mark`. Красный полный CI — PR снова черновик
(`give_back` оркестратора): починка без CI, с проверкой в песочнице; полный CI — при следующем
переводе. Любой новый коммит — допуск
заново (`admitted` — head, прошедший условия), отметка — на новый коммит.

Исключения из авто-мёржа: флаг «миграция» — отметка без авто-мёржа (`ship-finish --mark --hold`)
и вопрос владельцу по карточке PR; «да» — на этот head, затем авто-мёрж (`v2/send.py`, `MERGE`).
PR на пути-ограничители ждёт одобрения CODEOWNERS правилом GitHub: авто-мёрж его не обходит.

Отказ — отчёт об остановке с кодом, строка «Требует внимания» карточки PR, PR — черновик; что
сделает `agent:go` — `GO` по коду, совет и риск — `ADVICE` (четвёртый раздел отчёта, #347).
Флаги — из снимка тикета (`v2/snapshot.py`, #347); при допуске — строка «выход за «Владения»»
карточки по путям снимка (`beyond`, не блокирует).

Удалить: выключить переключатель, затем этот файл; `gates()`, параметр `gate` у `orch.ci`, ветку
`v2` в `give_back`, `GO` в `report` оркестратора; вопрос `MERGE` в `v2/send.py`;
флаг `--hold` у `ship-finish`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path, PurePosixPath

# Конфиг сборки тестов: по имени файла на любой глубине и файл CI точечного прогона.
TEST_CONFIG = ("conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "noxfile.py")
REVIEWER_CI = ".github/workflows/reviewer-tests.yml"
# Флаги брифа в тексте тикета — как у скрипта `review` (`FLAGS`), когда строк по образцу нет.
FLAGS = re.compile(r"^[\s*_]*Флаги[\s*_]*:[\s*_]*(.*)$", re.M | re.I)
MIGRATION = "миграция"
# Ответ `gh run download`, когда у прогона нет артефакта отчёта: не сбой, отчёта у него нет.
NO_REPORT = re.compile(r"no valid artifacts found|no artifact matches", re.I)
NONE: dict = {}  # итог `report` у прогона без отчёта: пусто, но узнаётся (`is NONE`)
# Правка вызываемого `ci.yml` при стоящем файле воркфлоу (#430): `workflow_call` проверяет только
# ручной прогон `canon-mainline` на ветке — прогон PR идёт по `pull_request`.
CALLED = ".github/workflows/ci.yml"
MAINLINE = ".github/workflows/canon-mainline.yml"
MAINLINE_RUN = "canon-mainline"  # имя workflow — как `WORKFLOW` в `v2/mainline.py`

CI, REVIEW, FINDINGS, FRESH, WEAKENING = "ci", "review", "findings", "fresh", "weakening"
EVIDENCE = "evidence"
# Условия допуска по номеру спеки: (номер, имя, что проверяет).
CONDITIONS = (
    (1, CI, "доверенный CI зелёный на этой версии, тесты ревьюера в его отчёте пройдены"),
    (2, REVIEW, "ревью видело эту версию, изменения после него меньше порога или короткое ревью"),
    (3, FINDINGS, "подтверждённые находки закрыты, тесты ревьюера и конфиг тестов не тронуты"),
    (4, EVIDENCE, "доказательство /verify по каждому критерию, примеру и находке со сценарием"),
    (5, FRESH, "ветка свежая — в ней последняя основа"),
    (7, WEAKENING, "нет ослабления защиты, которого тикет не просит"),
)
CODES = {
    "untested": "тесты-ревьюера-не-в-ci",  # 1
    "unreviewed": "ревью-не-видело",  # 2
    "test_changed": "тест-ревьюера-изменён",  # 3
    "config_changed": "конфиг-тестов-изменён",  # 3
    "unproved": "нет-доказательства",  # 4
    "stale": "ветка-отстала",  # 5
    "weakening": "ослабление-защиты",  # 7
}
RESTORE_GO = "новая сессия вернёт файлы как в коммите «только тест», затем допуск заново"
GO = {
    "untested": "снова условие 1 — на новом полном прогоне CI этой версии (прогоны отказа не "
    "в счёт); CI проекта должен гонять папку тестов ревьюера и класть отчёт по каждому тесту "
    "(контракт CI шаблона профиля)",
    "unreviewed": "владелец принимает изменения после ревью без короткого ревью (строкой в "
    "карточке PR), затем остальные условия допуска; отчёт ревью не о предке этой версии — нужен "
    "`agent:retry`",
    "test_changed": RESTORE_GO,
    "config_changed": RESTORE_GO,
    "unproved": "оркестратор заново забирает доказательства `/verify` из сессии тикета, затем "
    "допуск заново; их нет и там — отказ повторится (PR из `.github/` и документов — снова "
    "полный CI и ручной прогон `canon-mainline`)",
    "stale": "догон основы на сервере (конфликт — свежая сессия с заданием догона), затем "
    "полный CI и допуск заново",
    "weakening": "снова допуск — отказ повторится, пока ослабление не решено: `agent:go` его не "
    "снимает; убрать — `agent:retry` или правкой отдельным тикетом",
}
# Раздел «Рекомендация и риск» отчёта об отказе (#347): что ставить и чем это грозит.
RESTORE_ADVICE = (
    "`agent:go`: файлы вернутся, починка пойдёт заново. **Риск:** нет — тест ревьюера и конфиг "
    "тестов — защита от подгонки теста под код; оставить правку можно только отдельным тикетом."
)
ADVICE = {
    "untested": "проверьте CI проекта: гоняет ли он папку тестов ревьюера и кладёт ли отчёт. "
    "Поправлен — `agent:go`. **Риск:** без этого условие 1 не пройдёт ни на одном прогоне — "
    "`agent:go` повторит отказ.",
    "unreviewed": "посмотрите изменения после ревью в PR: мелкие и понятные — `agent:go` (вы "
    "их принимаете); крупные — `agent:retry`. **Риск `agent:go`:** эти строки никто не "
    "проверял ревью ошибок.",
    "test_changed": RESTORE_ADVICE,
    "config_changed": RESTORE_ADVICE,
    "unproved": "посмотрите, каких пунктов не хватает. Сбой чтения ответа `/verify` — `agent:go`; "
    "пункт `/verify` не проверял (сессия сочла `/verify` ненужным, находка ревью не попала в "
    "пункты) — `agent:retry`. PR из `.github/` и документов: ручной прогон `canon-mainline` не "
    "дождался или сбой запуска — `agent:go`; красный — откройте прогон: сломан `ci.yml` ветки — "
    "`agent:retry`. **Риск:** слить без доказательства нельзя — критерий был бы принят на слово.",
    "stale": "`agent:go`: оркестратор догонит основу на сервере. **Риск:** конфликт с новой "
    "основой — задание свежей сессии; не сведёт — снова остановка.",
    "weakening": "прочитайте ослабление в карточке PR. Тикет его не просит — `agent:retry` или "
    "отдельный тикет на ослабление. **Риск:** слияние сняло бы защиту, которую вы не "
    "отключали.",
}
RESTORE = ("test_changed", "config_changed")
RESTORE_TASK = """Оркестратор: допуск тикета #{n} отказал — код `{code}`: {why}. Тесты ревьюера и
конфиг сборки тестов после коммита «только тест» не меняй. Верни файлы как были:
`git checkout {commit} -- {files}` (файла в том коммите нет — `git rm` его) и коммит (сообщение
одной строкой `-m`); чини код. Затем вызов `{ship}` ({long}) и его задания — до {until}."""
MERGE_ASK = """**Причина:** у тикета флаг «миграция»: такое слияние трудно откатить, поэтому
авто-мёржа без вашего «да» нет.

**Сделано:** допуск пройден на `{sha}` — ревью, тесты ревьюера, свежая ветка; полный CI зелёный,
отметка `review` стоит, авто-мёрж не включён. Что сделано и что требует внимания — в карточке PR
{pr}.

**Доступно** (ответ — комментарием в этом тикете):
- «да» — включу авто-мёрж (на этот коммит; новый коммит — снова вопрос);
- «нет» — отчёт об остановке: PR остаётся открытым.

Рекомендую «да», если карточка PR не требует внимания."""


def config_file(path: str, folder: str | None) -> bool:
    """Файл конфига сборки тестов: имя из `TEST_CONFIG`, файл CI точечного прогона или пакет
    папки тестов ревьюера (`__init__.py`)."""
    if path == REVIEWER_CI or PurePosixPath(path).name in TEST_CONFIG:
        return True
    return bool(folder) and path == f"{folder.rstrip('/')}/__init__.py"


def owned(path: str, paths: list[str]) -> bool:
    """Файл — во «Владениях»: путь совпадает или лежит в каталоге из них (путь без `/` в конце
    может оказаться каталогом — как у `conflict` оркестратора)."""
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in paths)


def passed(outcomes: dict) -> bool:
    """Тесты файла в отчёте есть, и все пройдены."""
    return bool(outcomes) and all(item["outcome"] == "passed" for item in outcomes.values())


def text_flags(text: str) -> list[str]:
    """Флаги из последней строки «Флаги: …» текста тикета; нет — пусто."""
    found = FLAGS.findall(text or "")
    if not found:
        return []
    return [p.strip(" «»\"'*_.").lower() for p in re.split(r"[,;/]", found[-1]) if p.strip()]


class Gates:
    """Допуск пути v2 для оркестратора `orch`; `tools` — его помощники: `core`, `card`, `ci`,
    `rtest`, `spot`, `start`, `send`, `say`, `stamp`, `seconds_since`, `CI_WAIT`."""

    def __init__(self, orch, tools):
        self.orch, self.t = orch, tools
        self.fx = orch.fixes()

    # --- отказ ---

    def refuse(self, n: int, t: dict, st, head: str | None, code: str, why: str) -> bool:
        """Отказ допуска с кодом на `head`: PR — черновик (не сольётся и с авто-мёржем; сбой —
        `Failure`, такт повторит), строка карточки, отчёт об остановке. Отказ помнится только
        вместе с отчётом: сбой отчёта не оставит его — иначе следующий такт счёл бы его
        `agent:go` владельца. Итог — False (шаг такта сделан)."""
        o = self.orch
        o.draft(st, True)
        t["undraft"] = True
        said = f"код `{CODES[code]}`: {why}"
        reason = f"допуск: отказ — {said}"
        t["refused"] = {"code": code, "head": head, "reason": reason}
        t.pop("admitted", None)
        try:
            o.sender().mark(n, t, self.t.card.ATTENTION, f"Отказ допуска — {said}", key="gates")
            o.stop(n, t, reason)
        except BaseException:
            t.pop("refused", None)
            raise
        return False

    # --- условия 2, 3, 4, 5, 7: до перевода из черновика ---

    def admit(self, n: int, t: dict, st, pr: dict) -> bool:
        """Условия до перевода PR из черновика. True — пройдены (на этом head — уже раньше, PR
        снят с черновика), дальше перевод и CI; False — отказ или возврат. Отказ на том же head
        в состоянии — это `agent:go` владельца после него: действие его кода (`GO`)."""
        head = pr["headRefOid"]
        if t.get("admitted") == head and not pr.get("isDraft"):
            return True
        refused = t.get("refused") or {}
        if refused.get("head") == head and refused.get("code") in RESTORE:
            return self.restore(n, t, st, head, refused["code"])
        if refused.get("head") == head and refused.get("code") == "unproved":
            # PR из `.github/` и документов: ручной прогон — заново, срок ожидания — с нуля.
            t.pop("dispatched", None)
            t.pop("trusted_since", None)
            evidence = self.orch.evidence()  # `agent:go`: забрать заново (#352), сводку — тоже
            evidence.keep(n, t, st, head)
            evidence.mark(n, t, head)
            self.orch.sender().body(n, t, st, head)
        if refused.get("head") == head and refused.get("code") == "unreviewed":
            t["accepted"] = head
            line = f"Владелец принял версию `{head[:7]}` без ревью изменений после ревью ошибок"
            self.orch.sender().mark(n, t, self.t.card.DONE, line, key="accepted", publish=False)
        t.pop("refused", None)
        for code, why in self.failed(n, t, st, head):
            return self.refuse(n, t, st, head, code, why)
        t["admitted"] = head
        send = self.orch.sender()
        send.mark(n, t, self.t.card.ATTENTION, None, key="gates", publish=False)
        send.mark(n, t, self.t.card.BEYOND, self.beyond(t, head), key="beyond", publish=False)
        line = (
            f"Допуск на `{head[:7]}`: условия 2, 3, 4, 5, 7 выполнены — PR из черновика, полный CI"
        )
        send.mark(n, t, self.t.card.DONE, line, key=f"admit-{head}")
        self.t.say(f"#{n}: допуск на {head[:7]} — условия 2, 3, 4, 5, 7 выполнены")
        return True

    def beyond(self, t: dict, head: str) -> str | None:
        """Строка «выход за «Владения»» карточки: файлы диффа PR вне путей снимка тикета; путей в
        снимке нет (тикет идёт один) или дифф не прочитан — так и сказано. Не блокирует."""
        paths = (t.get("snapshot") or {}).get("paths")
        if paths is None:
            return "в снимке тикета нет «Владений» (или снимка нет) — сверять не с чем"
        res = self.orch.git("diff", "--name-only", "-z", "--no-renames", f"origin/main...{head}")
        if res.returncode != 0:
            return f"дифф `origin/main...{head[:7]}` не прочитан — выход за «Владения» не проверен"
        files = sorted(f for f in res.stdout.split("\0") if f and not owned(f, paths))
        if not files:
            return None
        shown = ", ".join(f"`{f}`" for f in files[:20]) + (" и др." if len(files) > 20 else "")
        return f"файлы вне «Владений» тикета: {shown}"

    def failed(self, n: int, t: dict, st, head: str):
        """Невыполненные условия до перевода — `(код, почему)` по порядку условий; следующее
        считается, только если прошлое выполнено."""
        if why := self.unreviewed(n, t, head):
            yield "unreviewed", why
        if why := self.tests_changed(t, head):
            yield "test_changed", why
        if why := self.config_changed(t, head):
            yield "config_changed", why
        if why := self.orch.evidence().check(n, t, head):
            yield "unproved", why
        if why := self.stale(head):
            t["behind"] = True  # `agent:go` — догон на сервере (`catch_up`)
            yield "stale", why
        if why := self.weakened(n, st):
            yield "weakening", why

    def unreviewed(self, n: int, t: dict, head: str) -> str | None:
        """Условие 2: ревью видело эту версию или её предка, а изменения после него не требуют
        короткого ревью (`Fixes.excess` — то же правило, что у короткого ревью); или владелец
        принял эту версию (`agent:go` после отказа)."""
        review = self.fx.reviewed(n)
        if review is None:
            return "нет годного отчёта ревью ошибок"
        seen = review.get("head")
        if not isinstance(seen, str) or not self.orch.ancestor(seen, head):
            return f"head отчёта ревью `{str(seen)[:7]}` — не эта версия и не её предок"
        if t.get("accepted") == head:  # владелец принял изменения после ревью
            return None
        found = self.fx.excess(n, t, head)
        if found is None:
            return None
        base = found["base"]
        if found["lines"] is None:
            return f"дифф `{base[:7]}..{head[:7]}` не прочитан"
        why = "после ослабления защиты" if found["weakened"] else f"порог {self.fx.limit()}"
        return (
            f"после ревью `{base[:7]}` изменено {found['lines']} строк кода ({why}), короткого "
            "ревью этих изменений не было"
        )

    def tests_changed(self, t: dict, head: str) -> str | None:
        """Условие 3: файл теста ревьюера подтверждённой находки — побайтно как в коммите
        «только тест» (`Fixes.changed_tests` — то же правило, что у ворот после разбора)."""
        changed = self.fx.changed_tests(t, head)
        if not changed:
            return None
        commit = t["rtest"]["commit"]
        files = ", ".join(f"`{f}`" for f in changed)
        return f"тест ревьюера {files} не тот, что в коммите «только тест» `{commit[:7]}`"

    def config_changed(self, t: dict, head: str) -> str | None:
        """Условие 3: конфиг сборки тестов ветка после коммита «только тест» не меняла; правка
        `main`, пришедшая догоном, — не в счёт."""
        r = t.get("rtest") or {}
        if not r.get("commit") or not self.fx.held(t):
            return None
        files = self.config_files(r["commit"], head, r.get("folder"))
        if files is None:
            return f"дифф `{r['commit'][:7]}..{head[:7]}` не прочитан"
        if not files:
            return None
        shown = ", ".join(f"`{f}`" for f in files)
        return f"после коммита «только тест» `{r['commit'][:7]}` изменён конфиг тестов: {shown}"

    def config_files(self, commit: str, head: str, folder: str | None) -> list[str] | None:
        """Файлы конфига тестов, которые ветка изменила после `commit`. Догон основы после него
        — сравнение с тем, что дало бы слияние `commit` и `origin/main` без правок ветки
        (`git merge-tree`): правка `main` не засчитывается; конфликт — сравнение с `commit`."""
        start = self.fx.merged_base(commit, head)
        res = self.orch.git("diff", "--name-only", "-z", "--no-renames", start, head)
        if res.returncode != 0:
            return None
        return sorted(f for f in res.stdout.split("\0") if f and config_file(f, folder))

    def stale(self, head: str) -> str | None:
        """Условие 5: в ветке последняя основа (`origin/main` — свежий, с GitHub)."""
        o = self.orch
        o.own_repo(refetch=True)
        if o.ancestor("origin/main", head):
            return None
        base = o.git("rev-parse", "origin/main").stdout.strip()
        return f"в ветке нет последней основы `{base[:7]}`: основа ушла после догона"

    def weakened(self, n: int, st) -> str | None:
        """Условие 7: ослабление защиты из ревью ошибок без решения разбора («ослабление — …»)
        или из короткого ревью (решения после него нет: его ослабление — всегда отказ)."""
        review = self.fx.reviewed(n) or {}
        found = review.get("weakening") if not st["weakening"] else None
        if not found:
            short = self.orch.dir / "reviews" / f"ticket-{n}.short.json"
            found = (self.fx.report(short) or {}).get("weakening")
        if not found:
            return None
        said = " / ".join(x.strip() for x in str(found).splitlines() if x.strip())
        return f"ревью нашло ослабление защиты без решения разбора: {said[:300]}"

    def restore(self, n: int, t: dict, st, head: str, code: str) -> bool:
        """`agent:go` после отказа по тесту ревьюера или конфигу тестов: возврат с заданием
        вернуть файлы как в коммите «только тест». Отказ снимается, только когда запуск начат:
        пауза держит возврат — снова на следующем такте."""
        r = t["rtest"]
        if code == "test_changed":
            files, why = self.fx.changed_tests(t, head), self.tests_changed(t, head)
        else:
            files = self.config_files(r["commit"], head, r.get("folder")) or []
            why = self.config_changed(t, head)
        task = RESTORE_TASK.format(
            n=n, code=CODES[code], why=why or "файлы уже как в коммите «только тест»",
            commit=r["commit"][:12], files=" ".join(files), **self.orch.task_words(n),
        )  # fmt: skip
        self.fx.back(n, t, st, f"допуск: код `{CODES[code]}`", files, task)
        if t["phase"] == "session":
            t.pop("refused", None)
        else:
            self.fx.state(t).pop("owed", None)  # долг — у отказа: он повторит возврат
        return False

    # --- условие 1: после зелёного полного CI ---

    def trusted(self, n: int, t: dict, st, pr: dict) -> bool:
        """Условие 1 на зелёном CI: эта версия прошла допуск, а тесты ревьюера подтверждённых
        находок — в отчёте полного прогона по именам как пройденные. True — к отметке; False —
        отказ или ожидание (нового прогона после отказа, отчёта при сбое скачивания — до
        `CI_WAIT`). Итог на тот же head и те же прогоны помнится: отчёты второй раз не качаются."""
        head = pr["headRefOid"]
        if t.get("admitted") != head and not self.admit(n, t, st, {**pr, "isDraft": True}):
            return False  # новый коммит без допуска — допуск заново
        if not self.proved(n, t, st, pr):
            return False
        held = self.fx.held(t)
        if not held:
            return True
        runs = self.runs(pr)
        key = [head, runs]
        if (t.get("trusted") or {}).get("key") == key:
            return True
        if set(runs) <= set(t.get("untested_runs") or ()) and "untested_runs" in t:
            return self.wait(n, t, st, head, "нового полного прогона CI после отказа нет")
        outcomes, empty = {}, []
        for run_id in runs:
            got = self.report(n, run_id)
            if got is None:
                return self.wait(n, t, st, head, f"отчёт по тестам прогона {run_id} не скачан")
            if got is NONE:
                empty.append(run_id)
            outcomes.update(got)
        missing = [
            i["file"]
            for i in held
            if not passed(self.t.rtest.cases(outcomes, Path(i["file"]).stem))
        ]
        t.pop("trusted_since", None)
        if missing:
            files = ", ".join(f"`{f}`" for f in missing)
            why = f"в отчёте полного CI на `{head[:7]}` нет пройденных тестов ревьюера {files}"
            if len(empty) == len(runs):  # отчёта нет ни у одного прогона — так и сказать
                why = (
                    f"у прогонов полного CI на `{head[:7]}` ({', '.join(runs) or 'нет'}) нет "
                    "отчёта по тестам (артефакт `test-report*`: CI его не кладёт или истёк срок "
                    f"хранения) — тесты ревьюера {files} не проверить"
                )
            t["untested_runs"] = runs  # `agent:go` — по новому полному прогону, не по этим
            return self.refuse(n, t, st, head, "untested", why)
        t.pop("untested_runs", None)
        t["trusted"] = {"key": key}
        line = f"Тесты ревьюера пройдены в полном CI на `{head[:7]}`: {len(held)}"
        self.orch.sender().mark(n, t, self.t.card.EVIDENCE, line, key=f"trusted-{head}")
        return True

    def wait(self, n: int, t: dict, st, head: str, why: str, code: str = "untested") -> bool:
        """Ожидание условия 1 (или 4 на CI) — до `CI_WAIT`; дольше — отказ с причиной. Итог —
        False."""
        since = t.setdefault("trusted_since", self.t.stamp())
        if self.t.seconds_since(since) > self.t.CI_WAIT:
            t.pop("trusted_since")
            return self.refuse(n, t, st, head, code, f"{why} {self.t.CI_WAIT // 60} мин")
        number = 4 if code == "unproved" else 1
        self.t.say(f"#{n}: условие допуска {number} ждёт — {why}")
        return False

    # --- условие 4 PR из `.github/` и документов: на зелёном полном CI ---

    def proved(self, n: int, t: dict, st, pr: dict) -> bool:
        """PR из `.github/` и документов без `/verify` (условие 4 при допуске отложено):
        доказательство — прогоны полного CI на этой версии, а правка `CALLED` при `MAINLINE` на
        ней — ещё зелёный ручной прогон на ветке. True — доказано (или не тот случай); False —
        отказ или ожидание."""
        head = pr["headRefOid"]
        ev = self.orch.evidence()
        if not ev.deferred(t, head):
            return True
        runs = self.runs(pr)
        if not runs:
            why = f"PR из `.github/`, а прогонов полного CI на `{head[:7]}` нет"
            return self.refuse(n, t, st, head, "unproved", why)
        manual = None
        if self.calls(ev, head):
            manual = self.manual(n, t, st, head)
            if not manual:
                return False
        t.pop("trusted_since", None)
        ev.by_ci(n, t, head, runs, manual)
        self.orch.sender().body(n, t, st, head)  # сводка в теле PR — та же, что в карточке
        self.t.say(f"#{n}: условие допуска 4 — полный CI на {head[:7]} вместо /verify")
        return True

    def calls(self, ev, head: str) -> bool:
        """Дифф правит вызываемый `ci.yml` (не прочитан — считается, что правит), а файл
        воркфлоу на этой версии стоит."""
        files = ev.files(head)
        if files is not None and CALLED not in files:
            return False
        return self.orch.git("cat-file", "-e", f"{head}:{MAINLINE}").returncode == 0

    def manual(self, n: int, t: dict, st, head: str) -> str | None:
        """Зелёный ручной прогон `canon-mainline` на ветке этой версии — его номер; нет — запуск
        (раз на версию), ожидание до `CI_WAIT` или отказ (красный — `agent:go` запустит заново)."""
        core, branch = self.t.core, st.branch()

        def gh(*args: str) -> str:
            return core.gh(*args, "-R", self.orch.repo, timeout=self.t.ci.GH_TIMEOUT)

        try:
            out = gh(
                "run", "list", "--workflow", MAINLINE_RUN, "--branch", branch, "--event",
                "workflow_dispatch", "--limit", "20",
                "--json", "databaseId,headSha,status,conclusion",
            )  # fmt: skip
            listed = json.loads(out) if out.strip() else []
        except (core.Failure, ValueError) as exc:
            why = f"ручной прогон `{MAINLINE_RUN}` не прочитан — {str(exc)[:200]}"
            return self.wait(n, t, st, head, why, "unproved") or None
        red = t.get("manual_red") or []
        found = [
            r
            for r in listed
            if isinstance(r, dict)
            and r.get("headSha") == head
            and str(r.get("databaseId")) not in red
        ]
        if not found:
            if t.get("dispatched") != head:
                try:
                    gh("workflow", "run", MAINLINE_RUN, "--ref", branch)
                except core.Failure as exc:
                    why = f"ручной прогон `{MAINLINE_RUN}` на `{branch}` не запущен — {exc}"
                    return self.refuse(n, t, st, head, "unproved", why) or None
                t["dispatched"] = head
                self.t.say(f"#{n}: ручной прогон {MAINLINE_RUN} на {branch} ({head[:7]})")
            why = f"ручного прогона `{MAINLINE_RUN}` на `{head[:7]}` ещё нет"
            return self.wait(n, t, st, head, why, "unproved") or None
        run, number = found[0], str(found[0].get("databaseId"))
        if run.get("status") != "completed":
            why = f"ручной прогон `{MAINLINE_RUN}` {number} идёт"
            return self.wait(n, t, st, head, why, "unproved") or None
        if run.get("conclusion") != "success":
            t["manual_red"] = [*red, number]
            t.pop("dispatched", None)
            why = (
                f"ручной прогон `{MAINLINE_RUN}` {number} на `{head[:7]}` — {run.get('conclusion')}"
            )
            return self.refuse(n, t, st, head, "unproved", why) or None
        return number

    def runs(self, pr: dict) -> list[str]:
        """Прогоны Actions этого репозитория на PR — без сторожа, точечного прогона и пропущенных
        задач (`SKIPPED`: работы не было — отчёта нет; живой прогон #346 — `dependabot-review`
        на PR тикета)."""
        start, found = self.t.start, []
        for c in pr.get("statusCheckRollup") or []:
            match = start.GUARD_RUN.fullmatch(c.get("detailsUrl") or "")
            if not match or match.group(1) != self.orch.repo or c.get("name") == start.GUARD:
                continue
            if c.get("workflowName") == start.SPOT_WORKFLOW or c.get("conclusion") == "SKIPPED":
                continue
            if match.group(2) not in found:
                found.append(match.group(2))
        return sorted(found)

    def report(self, n: int, run_id: str) -> dict | None:
        """Итог каждого теста прогона по его отчёту (`ci.fetch`); артефакта отчёта у прогона нет
        (другой workflow — линтер, сторож отметки) — `NONE`: тесты ревьюера ищутся в прочих, а
        нет отчёта ни у одного — отказ так и говорит; сбой скачивания — None."""
        core = self.t.core
        with tempfile.TemporaryDirectory(prefix="canon-gates-") as tmp:
            try:
                self.t.ci.fetch(core, self.orch.repo, run_id, tmp)
            except core.Failure as exc:
                if NO_REPORT.search(str(exc)):
                    self.t.say(f"#{n}: у прогона {run_id} нет отчёта по тестам")
                    return NONE
                self.t.say(f"#{n}: отчёт по тестам прогона {run_id} не скачан — {exc}")
                return None
            return self.t.spot.outcomes(Path(tmp))

    # --- отметка: исключения из авто-мёржа ---

    def flags(self, n: int) -> list[str]:
        """Флаги тикета: снимок со строками брифа по образцу (#347, `v2/snapshot.py`) — его флаги;
        иначе строки брифа по образцу (`claims_of`), а их нет — последняя строка «Флаги: …»
        тикета целиком (`reviews/ticket-N.issue.md`, вход ревью, с комментариями), как у
        `review`, вместе с флагами снимка: флаг, данный комментарием, авто-мёрж держит."""
        snap = self.orch.ticket(n).get("snapshot")
        if snap is not None and snap.get("paths") is not None:
            return list(snap.get("flags") or [])
        if snap is not None:
            return sorted({*(snap.get("flags") or []), *self.text_flags(n)})
        claims = self.orch.claims_of(n)
        if claims is not None:
            return claims.get("flags") or []
        return self.text_flags(n)

    def text_flags(self, n: int) -> list[str]:
        """Флаги из строки «Флаги: …» тикета целиком (вход ревью)."""
        try:
            text = (self.orch.dir / "reviews" / f"ticket-{n}.issue.md").read_text(encoding="utf-8")
        except OSError:
            text = ""
        return text_flags(text)

    def hold(self, n: int, t: dict, head: str) -> bool:
        """Отметка без авто-мёржа: флаг «миграция», а «да» владельца на этот head ещё нет."""
        return MIGRATION in self.flags(n) and t.get("merge_yes") != head

    def ask_merge(self, n: int, t: dict, head: str) -> None:
        """Вопрос владельцу «да» на слияние тикета с флагом «миграция» — после отметки."""
        text = MERGE_ASK.format(sha=head[:7], pr=t.get("pr") or "")
        self.orch.sender().ask(n, t, self.t.send.MERGE, head, text)
