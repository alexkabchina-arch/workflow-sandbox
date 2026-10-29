"""handover — отчёт передачи и вход свежей сессии на возврате пути v2 (#339; спека #329,
«Возвраты, сбои, команды, пределы»).

На пути v2 продолжений бесед нет: любой возврат — новая сессия на той же ветке, а не
`--resume`. Её вход — промпт оркестратора (`prompt`): задание возврата, тикет, дифф ветки и
отчёт передачи прошлого запуска.

Правила работы сессии (`v2/task.py`, #340) — во входе после задания: среди них — вести отчёт
передачи по ходу в `NOTE` журналом решений.

Отчёт передачи — файл у оркестратора (`handovers/ticket-N.md` в каталоге его состояния, дифф —
рядом, `.diff`): вне песочницы и вне ветки. Собирает его оркестратор, когда кончился любой запуск
сессии тикета v2, — и оборванный (предел, лимит подписки, сон Mac, `agent:stop`): факты ветки
даёт `session-run --handover` изнутри песочницы (git в клоне сессий Mac не выполняет) —
незавершённую работу он коммитит в ветку только локально; шаг, проверки и вопрос — из состояния
маршрута. Отчёт, который вела сама сессия (`NOTE` в её worktree), входит в отчёт целиком и первым,
а из песочницы убирается, когда оркестратор сохранил отчёт; не вела — отчёт собран только из
коммитов, диффа и состояния.

Удалить: выключить переключатель, затем этот файл, `--handover` у `session-run` и ветки `v2` в
`launch` и `finish_run` оркестратора.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import re
from pathlib import Path

DIR = "handovers"
# Пределы частей промпта — в байтах UTF-8: промпт уходит одним аргументом `sbx exec`, а в Linux
# аргумент — до 128 КиБ (`MAX_ARG_STRLEN`); кириллица — 2 байта на знак. Сумма с запасом ниже.
DIFF_LIMIT, BODY_LIMIT, REPORT_LIMIT = 48_000, 12_000, 24_000
NOTE = ".ticket/handover.md"  # отчёт, который ведёт сессия, — путь в её worktree
WHOLE_DIFF = "`git diff origin/main...HEAD` в worktree"
DEFAULT_TASK = (
    "Задание: продолжи маршрут — скилл `/start` с аргументом `{n}`; маршрут сам найдёт свой шаг "
    "(разбор находок ревью, ответ владельца, продолжение после остановки)."
)
RETURN = """Оркестратор: возврат тикета #{n} — новая сессия на той же ветке `{branch}`. Прошлой
беседы нет: тикет, дифф ветки и отчёт передачи прошлого запуска — ниже. Исправляй точечно,
сделанное не переделывай; незавершённая работа прошлого запуска — в worktree (отслеживаемые
правки — коммитом в ветке, только локально; новые файлы — на месте, списком в отчёте).

{task}

{rules}

## Тикет #{n}: {title}

{body}

## Дифф ветки (`git diff origin/main...HEAD`)

{diff}

## Отчёт передачи

{report}
"""
MISSING = (
    "Отчёта передачи нет: сбор не удался. Картина ветки — `git log origin/main..HEAD`, "
    "`git status` и состояние маршрута."
)


def paths(directory: Path, n: int) -> tuple[Path, Path]:
    """Отчёт и дифф тикета `n` в каталоге состояния оркестратора."""
    base = directory / DIR / f"ticket-{n}"
    return base.with_suffix(".md"), base.with_suffix(".diff")


def save(directory: Path, n: int, report: str, diff: str | None) -> None:
    """Отчёт и дифф; дифф не собран (`None`) — файла диффа нет."""
    report_path, diff_path = paths(directory, n)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    if diff is None:
        diff_path.unlink(missing_ok=True)
    else:
        diff_path.write_text(diff, encoding="utf-8")


def load(directory: Path, n: int) -> tuple[str, str | None] | None:
    """Отчёт и дифф (`None` — не собран); отчёта нет — `None`."""
    report, diff = paths(directory, n)
    if not report.exists():
        return None
    return report.read_text(encoding="utf-8"), (
        diff.read_text(encoding="utf-8") if diff.exists() else None
    )


def drop(directory: Path, n: int) -> None:
    for path in paths(directory, n):
        path.unlink(missing_ok=True)


def cut(text: str, limit: int, whole: str) -> str:
    """Не длиннее `limit` байт UTF-8; обрезано — строка, где взять целиком (`whole`)."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    kept = data[:limit].decode("utf-8", errors="ignore")
    return f"{kept}\n… обрезано: {len(data) - limit} байт — целиком {whole}\n"


def fence(text: str, lang: str = "") -> str:
    """Блок кода с оградой длиннее любой серии обратных кавычек внутри."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    mark = "`" * max(3, longest + 1)
    return f"{mark}{lang}\n{text.rstrip()}\n{mark}"


def listed(items: list[str], limit: int = 30) -> list[str]:
    shown = [f"- `{x}`" for x in items[:limit]]
    return shown + ([f"- … и ещё {len(items) - limit}"] if len(items) > limit else [])


def compose(n: int, facts: dict, route: dict, why: str) -> str:
    """Отчёт передачи по фактам ветки (`session-run --handover`) и состоянию маршрута: `why` — чем
    кончился запуск. Отчёт сессии — первым и целиком (обрезается промпт, а не он); нет его —
    сказано, что отчёт собран маршрутом. Worktree нет (`worktree: false`) — фактов ветки нет, и
    отчёт говорит это, а не «коммитов нет»."""
    note = (facts.get("note") or "").strip()
    own = (
        note or "Сессия отчёта не оставила — отчёт собран маршрутом из коммитов, диффа и состояния."
    )
    if facts.get("worktree") is False:
        done = ["- worktree тикета нет — коммиты и дифф ветки не собраны"]
    else:
        done = listed(facts.get("commits") or []) or ["- коммитов в ветке от `origin/main` нет"]
    if saved := facts.get("saved"):
        done.append(
            f"- незавершённая работа сохранена коммитом `{saved[:7]}` — только локально, на "
            "GitHub не отправлена"
        )
    if stat := facts.get("stat"):
        done.append("\n" + fence(stat))
    step, waiting = route.get("step") or "нет", route.get("waiting") or "агент"
    left = [f"- шаг маршрута «{step}», ждёт: {waiting}"]
    if pr := route.get("pr"):
        left.append(f"- PR: {pr}")
    if unsaved := facts.get("unsaved"):
        left.append(f"- не сохранено коммитом: {unsaved}")
    if changed := facts.get("changed"):
        left += ["- незакоммиченные правки в worktree:", *listed(changed)]
    if untracked := facts.get("untracked"):
        left += ["- новые файлы в worktree (не в коммите — реши, нужны ли):", *listed(untracked)]
    checks = (route.get("checks") or {}).get("head")
    head = facts.get("head") or ""
    if checks and checks == head:
        checked = f"- тесты и линтеры маршрута пройдены на head `{head[:7]}`"
    elif checks:
        checked = f"- тесты и линтеры маршрута пройдены на `{checks[:7]}`, не на head `{head[:7]}`"
    else:
        checked = "- тесты и линтеры маршрута не пройдены"
    question = route.get("question") or {}
    asked = f"- вопрос владельцу: {question['url']}" if question.get("url") else "- нет"
    if question.get("stamp") and not question.get("url"):  # вопрос сессии v2 (#341)
        asked = "- вопрос владельцу — в тикете (его публикует оркестратор)"
    return "\n".join(
        [
            f"# Отчёт передачи — тикет #{n}",
            "",
            f"Прошлый запуск: {why}.",
            "",
            "## Отчёт сессии: решения и почему",
            own,
            "",
            "## Факты маршрута",
            "",
            "### Что сделано",
            *done,
            "",
            "### Что осталось",
            *left,
            "",
            "### Что проверено",
            checked,
            "",
            "### Открытые вопросы",
            asked,
            "",
        ]
    )


def prompt(
    n: int,
    branch: str,
    task: str | None,
    issue: dict,
    report: str | None,
    diff: str | None,
    *,
    rules: str,
) -> str:
    """Промпт новой сессии на возврате: задание (`task` оркестратора или продолжение маршрута),
    правила работы сессии v2 (`rules`, `v2/task.py`: среди них — вести отчёт передачи), тикет,
    дифф ветки (`None` — не собран), отчёт передачи (`None` — его нет)."""
    if diff is None:
        shown = f"Дифф не собран — {WHOLE_DIFF}."
    elif diff.strip():
        shown = fence(cut(diff, DIFF_LIMIT, f"— {WHOLE_DIFF}"), "diff")
    else:
        shown = "Диффа нет."
    return RETURN.format(
        n=n,
        branch=branch,
        task=task or DEFAULT_TASK.format(n=n),
        rules=rules.strip(),
        title=issue.get("title") or "",
        body=cut((issue.get("body") or "").strip(), BODY_LIMIT, f"— `gh issue view {n}`"),
        diff=shown,
        report=cut((report or MISSING).strip(), REPORT_LIMIT, "— `git log origin/main..HEAD`"),
    )
