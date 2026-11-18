"""ticket_core — каркас `scripts/canon/ticket`: состояние тикета, задания агенту, команды профиля.

**Состояние тикета** — `<общий каталог git>/canon-ticket/<N>/state.json` на машине владельца, не
в репозитории: общий для главного чекаута и всех worktree проекта. Рядом — отчёты ревью и
`/verify`, критерии, тело PR и отчёт об остановке: обрыв сессии не теряет работу, ревью второй
раз не оплачивается, в `/tmp` ничего не копится. Пишет его только скрипт (атомарной заменой):
изоляция worktree приложения не даёт агенту писать вне своего worktree (прогон B0).

**Ответы агента** — файлы в `.ticket/` корня worktree тикета (запись файла внутри worktree); каталог
исключён из git через `info/exclude` общего каталога, в коммиты не попадает.

**Задание** — шаг с суждением: заголовок, текст, команды строками `  ▶ <команда>` и последняя
строка — статус. Команды заданий — простые (без `$(…)`, переменных, `cd`, кавычек и составных):
приложение в режиме В отклоняет «слишком сложные» (ретро A4, 71 отказ на 14 тикетов).
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_DIR = "canon-ticket"
# Номер формата состояния тикета: у тега другой — выпуск несовместим, обновление копии канона
# ждёт полного простоя (#311). Совместимая правка формата номер не меняет: образцы прошлого
# формата в `tests/states/` должны читаться (`tests/test_canon_release.py`).
STATE_VERSION = 1
ANSWERS = ".ticket"
TICKET_BRANCH = re.compile(r"^ticket-(\d+)(?:-.+)?$")
TURN, DONE, STOPPED = "ход агента", "готово", "остановка"
# Конец сессии под оркестратором (#214): ветка готова к его ревью или к отметке.
READY = "код готов"
AGENT, OWNER, CI, ORCHESTRATOR = "агент", "владелец", "CI", "оркестратор"
# Вызов команды агента (Bash у Claude Code) по умолчанию обрывается через 2 минуты; долгие шаги —
# с таймаутом 45 минут. Без имени инструмента и без скобок: задание читает любой агент, а
# константа стоит внутри скобок задания (#453). Таймаут — параметр вызова, не команда `timeout`.
LONG_CALL = "команда не в фоне, таймаут вызова 45 минут = 2700000 мс"
NOT_SIMPLE = re.compile(r"[$`'\"\n;&|<>]|\bcd\b")
# Шаги маршрута для заданий и `ticket status`; шаг модуля, которого здесь нет, — своим именем.
STEP_NAMES = {
    "checks": "допуск",
    "review": "ревью ошибок",
    "findings": "разбор",
    "retest": "тесты исправлений",
    "verify": "/verify",
    "sync": "догон базы",
    "body": "тело PR",
    "finish": "финиш",
    "done": "готово",
    "ready": "код готов",
    "stop": "остановка",
    "stopped": "остановка",
}
# Пометка вопроса `ticket ask` в комментарии; по ней вопрос находит и Stop-хук.
ASK_MARK = "<!-- canon-ticket-ask {stamp} -->"
CHECKS_BLOCK = re.compile(r"^```checks[ \t]*\n(.*?)^```", re.M | re.S)
# Быстрые проверки пути v2 (#338): хук на коммите в песочнице; `format` правит, прочие блокируют.
FAST_CHECKS_BLOCK = re.compile(r"^```fast-checks[ \t]*\n(.*?)^```", re.M | re.S)
# Аргумент `{files}` проверки (#464, как у пути v2 — `v2/checks.py`): файлы диффа ветки с
# `origin/main`, `{files:шаблоны}` — только подходящие (через запятую).
FILES_ARG = re.compile(r"^\{files(?::([^}]*))?\}$")
AFFECTED_OUT = "CANON_AFFECTED_OUT"  # куда команда задетых тестов пишет свой отбор (#464)
# Профиль до блока `checks` (проекты на каноне до #154): строки таблицы «Команды».
TABLE_CHECKS = {"Тесты": "tests", "Линтер": "lint", "Формат (проверка)": "format"}
TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|", re.M)
# Поля находки `gitleaks -v`, которые можно показать: блок находки кончается `Fingerprint`.
LEAK_FIELD = re.compile(r"^(RuleID|File|Line|Commit|Fingerprint):\s*(.*)$")
SECRET_FIELD = re.compile(r'^"?(Finding|Secret|Match)"?\s*:', re.I)
OTHER_FIELD = re.compile(r'^"?[A-Z][A-Za-z]+"?\s*:\s')  # следующее поле находки — конец значения
LEAKS_SHOWN = 10
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Failure(Exception):
    """Сбой механики: git, gh, файл состояния (код выхода 1)."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def elapsed(since: str | None) -> str:
    """«2 ч 05 мин» от метки `now()` до сейчас."""
    if not since:
        return "—"
    try:
        minutes = int((datetime.now(timezone.utc) - datetime.fromisoformat(since)).total_seconds())
    except ValueError:
        return "—"
    minutes //= 60
    return f"{minutes // 60} ч {minutes % 60:02d} мин" if minutes >= 60 else f"{minutes} мин"


def run(
    *cmd: str, cwd: Path | None = None, timeout: float | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def git(*args: str, cwd: Path | None = None) -> str:
    result = run("git", *args, cwd=cwd)
    if result.returncode != 0:
        raise Failure(f"git {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def git_ok(*args: str) -> bool:
    return run("git", *args).returncode == 0


def gh(*args: str, cwd: Path | None = None, timeout: float | None = None) -> str:
    try:
        result = run("gh", *args, cwd=cwd, timeout=timeout)
    except FileNotFoundError:
        raise Failure("не найден `gh` на PATH") from None
    except subprocess.TimeoutExpired:
        raise Failure(f"gh {' '.join(args[:2])}: дольше {timeout} с") from None
    if result.returncode != 0:
        raise Failure(f"gh {' '.join(args[:2])}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def gh_json(*args: str, cwd: Path | None = None, timeout: float | None = None):
    out = gh(*args, cwd=cwd, timeout=timeout)
    try:
        return json.loads(out) if out else None
    except ValueError:
        raise Failure(f"gh {' '.join(args[:2])}: не JSON: {out[:200]}") from None


# Доверенный автор тикета и комментария — участник репозитория (#181, #185): владелец, член
# организации, коллаборатор. Текст постороннего в публичном репозитории — не решение владельца, а
# маршрут отдаёт его агенту в задании, в том числе `claude -p` в режиме auto. Общий список: его
# берут и `session-start`, и `stop-check` (#214).
TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
# Лицо агента — GitHub App владельца (#214): его тикеты и комментарии свои (не посторонние),
# хотя участником репозитория GitHub его не считает; но голос владельца — только участники: App —
# это сама сессия и оркестратор, его комментарий не ответ владельца. Логин App (slug, без
# `[bot]`) — в git config проекта `canon.agentApp`; нет — App чужой.
APP_CONFIG = "canon.agentApp"
COMMENT_ID = re.compile(r"#issuecomment-(\d+)$")
_app_comments: dict[tuple[str, str], bool] = {}
_apps: dict[str, str | None] = {}
# Комментарии маршрута в тикете помечены (`ticket ask`), отчёт об остановке узнаётся по началу.
OWN_MARK = "<!-- canon-ticket"
REPORT_MARK = "Отчёт об остановке"
# Ответ владельца из чата, переданный сессией в тикет (`ticket ask`): помечен, чтобы не
# прочитаться вторым ответом. Решение о `/verify` из чата не передаётся (#249).
REPLY_MARK = "<!-- canon-ticket-reply -->"
# Форма ответа владельца из правил координатора (#498): «Ответ владельца: «…»».
OWNER_REPLY = re.compile(r"Ответ владельца: «.*»", re.S)
# Режим маршрута «под оркестратором» (#214): флаг окружения от оркестратора. Сессия кончается на
# «код готов» или на вопросе; ревью ошибок, отметку, авто-мёрж, ожидание и уборку делает он.
ORCHESTRATED = "CANON_ORCHESTRATOR"
# Время снимка копии прода в песочнице (#307): оркестратор передаёт его сессии окружением, маршрут
# пишет его в свидетельства тикета (`/verify`, тело PR).
PROD_COPY = "CANON_PROD_COPY"
# Ответ оркестратора на запрос свежей копии прода (`ticket fresh`) — файл рядом с состоянием.
FRESH_ANSWER = "fresh.json"
# Своя сессия `session-run` (#452): случайная метка, которую `session-run` после своего старта
# пишет в файл замка worktree (`SESSION_LOCK` личного каталога git) и передаёт `claude`
# окружением. Совпали — `ticket start` внутри этой сессии подготовку не повторяет.
SESSION_LOCK = "canon-session.lock"
SESSION_MARK = "CANON_SESSION_MARK"


def session_mark() -> str | None:
    """Метка из файла замка этого worktree; файла нет или он пуст — None."""
    lock = Path(git("rev-parse", "--path-format=absolute", "--git-dir")) / SESSION_LOCK
    try:
        return lock.read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):  # нечитаемый файл — не своя сессия: старт проверит сам
        return None


def orchestrated() -> bool:
    return os.environ.get(ORCHESTRATED) == "1"


# Сессия кода пути v2 (#340): признак окружения от `session-run` — задание сессии v2
# (`v2/task.py`) вместо `/implement` с самопроверкой `/code-review`.
V2_TASK = "CANON_V2_TASK"


def v2_task() -> bool:
    return orchestrated() and os.environ.get(V2_TASK) == "1"


def v2_rules(n: int) -> str:
    """Правила задания сессии v2 из `v2/task.py` рядом (той же версии), с командами маршрута."""
    path = HERE / "v2" / "task.py"
    module: dict = {"__file__": str(path), "__name__": "canon_v2_task_core"}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module)
    return module["rules"](n, f"{scripts_dir()}/ticket")


def prod_copy(st) -> str | None:
    """Время снимка копии прода, на которой идёт сессия (#307): из состояния тикета — его пишут
    старт работы над тикетом (`ticket start`: базу сессии клонируют в начале работы) и запрос
    свежей копии (`ticket fresh`, пусто — база из шаблона без времени), — иначе из окружения
    запуска (`CANON_PROD_COPY` от оркестратора); не под оркестратором или неизвестно — None."""
    if not orchestrated():
        return None
    if st["copy"] is None:
        return os.environ.get(PROD_COPY) or None
    return st["copy"] or None


def copy_phrase(copy: str | None) -> str:
    """«снимок от …» копии прода или «время снимка неизвестно» (#307)."""
    return f"снимок от {copy}" if copy else "время снимка неизвестно"


# Вопрос владельцу под оркестратором — тремя разделами (спека #208): владелец отвечает с
# телефона, не зная хода сессии.
ORCHESTRATED_ASK = (
    " Под оркестратором — тремя разделами: «Причина» (что неясно и почему без ответа не "
    "сделать), «Сделано» (что уже готово), «Доступно» (варианты, что будет при каждом, "
    "рекомендация)."
)


# Поправки ручного пути к концу `/implement` Мэтта (#453; решение владельца 30.10 о проверках,
# #450): копия скилла не правится — правила в задании `/start` и в задании после ответа. Под
# оркестратором — свои (`ORCHESTRATED_TASK` в `ticket_start`).
MANUAL_END = """- полный набор тестов в конце `/implement` не гонять: в маршруте его нет — только
  CI на PR и ночной прогон (у самого воркфлоу, пока его CI выключен, — ночная служба и
  выпуск); `ticket ship` до ревью гоняет допуск — быстрые проверки хука и задетые тесты по
  профилю; по ходу — тесты, задетые правкой;
- порядок конца: сначала коммит, затем самопроверка скиллом Мэтта `code-review`
  (`.claude/skills/code-review/SKILL.md`, оси Spec и Standards; не встроенное ревью ошибок —
  его ведёт `ticket ship`): точка — основная ветка (`origin/main`), спека — тикет #{n}; до
  коммита ей нечего смотреть; её находки по делу — исправить следующим коммитом, сверх тикета
  молча не править (запахи — на усмотрение);"""


def until() -> str:
    """До какого статуса выполнять задания маршрута: под оркестратором сессия кончается на
    «код готов» — ревью, отметку и ожидание мёржа ведёт он."""
    return f"«{READY}» или «{STOPPED}»" if orchestrated() else f"«{DONE}» или «{STOPPED}»"


def agent_app(cwd: Path | None = None) -> str | None:
    """Логин App из `git config canon.agentApp` (без `app/` и `[bot]`); не задан — None.
    Читается раз на процесс и каталог."""
    key = str(cwd)
    if key not in _apps:
        try:
            out = run("git", "config", "--get", APP_CONFIG, cwd=cwd).stdout.strip()
        except OSError:
            out = ""
        _apps[key] = out.removeprefix("app/").removesuffix("[bot]") or None
    return _apps[key]


def member(item: dict) -> bool:
    """Автор — участник репозитория (`authorAssociation` GitHub): его голос — голос владельца."""
    return item.get("authorAssociation", item.get("author_association")) in TRUSTED


def is_answer(body: str) -> bool:
    """Комментарий — не служебный маршрута и не отчёт об остановке: может быть ответом."""
    return bool(body) and OWN_MARK not in body and not body.startswith(REPORT_MARK)


def trusted(
    item: dict, cwd: Path | None = None, *, strict: bool = False, timeout: float | None = None
) -> bool:
    """Автор тикета или комментария — свой: участник репозитория или App (лицо агента).

    `item` — ответ REST (`author_association`, `user`) или `gh … --json comments`
    (`authorAssociation`, `author`). В REST бот App — `<slug>[bot]` типа `Bot`. В `gh --json`
    логин бота без `[bot]` и неотличим от пользователя с таким же логином: совпадение по логину
    сверяется REST по id комментария из его ссылки. Сбой `gh` — не свой; `strict` — `Failure`
    (Stop-хук: проверка не состоялась, а не «чужой»)."""
    if member(item):
        return True
    app = agent_app(cwd)
    if not app:
        return False
    user = item.get("user")
    if isinstance(user, dict):
        return user.get("login") == f"{app}[bot]" and user.get("type") == "Bot"
    login = ((item.get("author") or {}).get("login") or "").removeprefix("app/")
    match = COMMENT_ID.search(item.get("url") or "")
    if login.removesuffix("[bot]") != app or not match:
        return False
    key = (str(cwd), match.group(1))
    if key not in _app_comments:
        try:
            path = f"repos/{{owner}}/{{repo}}/issues/comments/{match.group(1)}"
            data = gh_json("api", path, cwd=cwd, timeout=timeout) or {}
        except Failure:
            if strict:
                raise
            data = {}
        user = data.get("user") if isinstance(data, dict) else None
        _app_comments[key] = isinstance(user, dict) and trusted({"user": user}, cwd)
    return _app_comments[key]


# Отчёт об остановке узнаётся по началу (разметка заголовка впереди не мешает); сигнал перерасхода
# `ship-finish` («ready-for-human: …») — служебный, не ответ.
REPORT_HEAD = re.compile(r"^[#*\s]*" + REPORT_MARK)
SIGNAL = "ready-for-human: "
ANSWERS_SHOWN = 5


def open_pr(branch: str) -> dict | None:
    """Открытый PR ветки (номер, ссылка, черновик ли) или None."""
    fields = ["--state", "open", "--json", "number,url,isDraft"]
    prs = gh_json("pr", "list", "--head", branch, *fields) or []
    return prs[0] if prs else None


def after_report(n: int, branch: str, pr: dict | None | bool = True) -> list[dict]:
    """Комментарии тикета и открытого PR ветки после последнего отчёта об остановке в каждом:
    граница — сам отчёт, не часы машины. Где отчёта нет — оттуда ничего. Отчёт — только от
    своих (`trusted`), как у Stop-хука и `session-start`: «Отчёт об остановке» постороннего
    сдвинул бы границу за решение владельца (#249). Скрытый свой отчёт — тоже граница:
    владелец прячет отработанный отчёт, а остановка от этого не раньше. Сбой сверки автора
    — `Failure`: «не свой» молча пропустил бы ответ. `pr` — открытый PR ветки, если его уже
    нашёл вызывающий (None — нет); по умолчанию — `open_pr`."""
    if pr is True:
        pr = open_pr(branch)
    sources = [("issue", str(n))] + ([("pr", str(pr["number"]))] if pr else [])
    found = []
    for kind, ref in sources:
        said = (gh_json(kind, "view", ref, "--json", "comments") or {}).get("comments") or []
        at = next(
            (
                i
                for i in reversed(range(len(said)))
                if REPORT_HEAD.match(said[i].get("body") or "") and trusted(said[i], strict=True)
            ),
            None,
        )
        if at is not None:
            found += said[at + 1 :]
    return found


def report_answers(comments: list[dict]) -> list[dict]:
    """Ответы владельца на отчёт об остановке среди комментариев после него (#459, слабое
    место 8): участник репозитория, не скрытый; не служебный маршрута и не сигнал
    перерасхода — кроме ответа из чата, переданного сессией (`REPLY_MARK`): он тоже голос
    владельца. App (сессия, оркестратор) — свой, но не ответ (#214). По времени."""
    found = []
    for c in sorted(comments, key=lambda c: c.get("createdAt") or ""):
        body = (c.get("body") or "").strip()
        if c.get("isMinimized") or not member(c) or not body:
            continue
        if REPLY_MARK in body or (is_answer(body) and not body.startswith(SIGNAL)):
            found.append(c)
    return found


def quoted_answers(answers: list[dict]) -> str:
    """Ответы цитатой со ссылкой — последние `ANSWERS_SHOWN`: строка ответа не станет командой
    задания."""
    shown = answers[-ANSWERS_SHOWN:]
    parts = [
        f"{c.get('url') or 'комментарий'}:\n{quote(c['body'].replace(REPLY_MARK, '').strip())}"
        for c in shown
    ]
    if len(answers) > len(shown):
        parts.append(f"(ранних ответов не показано: {len(answers) - len(shown)})")
    return "\n\n".join(parts)


def keep(st, path: Path, name: str) -> None:
    """Файл ответа агента — в каталог состояния `st`: в worktree он не повторится на новом круге."""
    if path.exists():
        st.dir.mkdir(parents=True, exist_ok=True)
        path.replace(st.dir / name)


# Метка триажа «можно брать» (`docs/agents/triage-labels.md`): тикет, который ждёт человека
# (`ready-for-human` — вопрос или отчёт об остановке), её не держит (#500).
READY_FOR_AGENT = "ready-for-agent"


def untriage(number: int) -> str:
    """Снять с тикета `READY_FOR_AGENT`, если она есть. Сбой `gh` не держит вопрос или отчёт:
    итог — примечание для задания (пусто — всё снято или снимать нечего)."""
    try:
        issue = gh_json("issue", "view", str(number), "--json", "labels") or {}
        if any(lb.get("name") == READY_FOR_AGENT for lb in issue.get("labels") or []):
            gh("issue", "edit", str(number), "--remove-label", READY_FOR_AGENT)
    except Failure as exc:
        return f"\n(Метку {READY_FOR_AGENT} снять не удалось: {exc} — сними руками.)"
    return ""


def relay_reply(number: int, reply: str) -> str:
    """Ответ владельца из чата (`.ticket/reply.md`) — комментарием в тикет; ссылка на него (`gh`
    её печатает) или «тикет #N». Форма — «Ответ владельца: «…»», как правила координатора
    называют ответ владельца (#498): второй комментарий не нужен. Ответ уже в этой форме не
    оборачивается второй раз. Ответ в несколько строк или со ссылкой — кавычки отдельными
    абзацами: Markdown ответа не ломается, и «»» не прилипнет к ссылке. Строка после ответа
    говорит, что его передала сессия: под учёткой владельца это не его собственный комментарий."""
    text = mask(reply.strip())
    if not OWNER_REPLY.fullmatch(text):
        inline = "\n" not in text and "://" not in text
        text = f"Ответ владельца: «{text}»" if inline else f"Ответ владельца: «\n\n{text}\n\n»"
    body = f"{REPLY_MARK}\n{text}\n\n_Передан сессией тикета из чата._"
    posted = gh("issue", "comment", str(number), "--body", body)
    return posted.splitlines()[0] if posted else f"тикет #{number}"


def issue_head(number: int) -> dict:
    """Заголовок и тело тикета; у тикета не участника — пустые, `trusted` — False.

    Тело правят только автор и участники репозитория: тикет участника посторонний не перепишет,
    а свой посторонний перепишет в любой момент, и после того, как владелец взял его в работу.
    `gh issue view` связи автора с репозиторием не отдаёт — поэтому REST."""
    data = gh_json("api", f"repos/{{owner}}/{{repo}}/issues/{number}") or {}
    ok = trusted(data)
    return {
        "title": (data.get("title") or "") if ok else "",
        "body": (data.get("body") or "") if ok else "",
        "trusted": ok,
    }


def quote(text: str) -> str:
    """Цитата Markdown: строка текста в задании не начнётся с `▶` команды."""
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def pending(question: dict) -> str:
    """Начало задания, пока вопрос `ticket ask` ждёт ответа (`ticket ship`; #196):
    вопрос владельцу в чат полным текстом из состояния, как в `ticket ask`, — владелец отвечает
    там, не открывая GitHub. У вопроса, заданного до #194, текста в состоянии нет —
    к комментарию."""
    url = question["url"]
    if text := question.get("text"):
        return (
            f"Вопрос владельцу ждёт ответа: {url}. Владельцу в чат — этот вопрос полным текстом "
            f"и ссылка на него, обычным сообщением, не формой выбора (AskUserQuestion):\n\n"
            f"{quote(text)}\n\n"
        )
    return (
        f"Вопрос владельцу ждёт ответа: {url}. Владельцу в чат — вопрос полным текстом, с "
        f"вариантами и рекомендацией, из комментария {url} и ссылка на него. "
    )


def common_dir() -> Path:
    return Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))


def toplevel() -> Path:
    return Path(git("rev-parse", "--show-toplevel"))


def branch() -> str | None:
    result = run("git", "symbolic-ref", "-q", "--short", "HEAD")
    return result.stdout.strip() or None


def head() -> str:
    return git("rev-parse", "HEAD")


def ticket_of(name: str | None) -> int | None:
    match = TICKET_BRANCH.match(name or "")
    return int(match.group(1)) if match else None


def worktree_of(number: int) -> Path | None:
    """Worktree ветки `ticket-N` или `ticket-N-…`, если есть."""
    path = None
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :])
        elif line.startswith("branch refs/heads/") and ticket_of(line[18:]) == number:
            return path
    return None


class State:
    """Файл состояния тикета: шаг, head на каждом шаге, итоги проверок, отчёты, расход, PR,
    попытки. Поля — в `data`; `save()` пишет атомарно."""

    def __init__(self, number: int):
        self.number = number
        self.dir = common_dir() / STATE_DIR / str(number)
        self.path = self.dir / "state.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data = self.blank()
        except (OSError, ValueError) as exc:
            raise Failure(f"файл состояния {self.path}: {exc}") from None
        for key in ("heads", "attempts", "cost"):
            self.data.setdefault(key, {})
        self.data.setdefault("waiting", AGENT)

    def blank(self) -> dict:
        stamp = now()
        return {
            "ticket": self.number,
            "step": None,
            "since": stamp,
            "updated": stamp,
            **{key: {} for key in ("heads", "attempts", "cost")},
            "waiting": AGENT,
        }

    def retire(self, root: Path) -> None:
        """PR маршрута слит, а работа по тикету идёт дальше (тикет переоткрыт, новые коммиты) —
        маршрут с нуля, новым PR (#190, п. 5). Состояние, отчёт ревью и ответы агента в `.ticket/`
        worktree `root` — в архив рядом (`<имя>-<время>`): круги, догон, тикеты техдолга, расход,
        «Что сделано» и пометка «слит» прошлого маршрута к новой работе не относятся. Остаются
        ветка и PR прошлых маршрутов (`past_prs`): у новой ветки то же имя, и поиск PR по ветке
        нашёл бы слитый."""
        stamp = now()
        self.dir.mkdir(parents=True, exist_ok=True)
        answers = root / ANSWERS
        old = sorted(answers.glob("*.md")) if answers.is_dir() else []
        for path in [self.path, self.dir / "review.json", *old]:
            if path.exists():
                path.replace(self.dir / f"{path.stem}-{stamp}{path.suffix}")
        past = list(self.data.get("past_prs") or [])
        if self.data.get("pr"):
            past.append(self.data["pr"])
        kept = {"branch": self.data.get("branch"), "past_prs": past}
        self.data = self.blank() | {key: value for key, value in kept.items() if value}

    def __getitem__(self, key):
        return self.data.get(key)

    def __setitem__(self, key, value):
        self.data[key] = value

    @property
    def step(self) -> str | None:
        return self.data["step"]

    def go(self, step: str, waiting: str = AGENT) -> None:
        """Переход на шаг: время на шаге считается заново, head запоминается."""
        if step != self.data["step"]:
            self.data["since"] = now()
        self.data.update(step=step, waiting=waiting)
        self.data["heads"][step] = head()

    def attempt(self, kind: str) -> int:
        self.data["attempts"][kind] = self.data["attempts"].get(kind, 0) + 1
        return self.data["attempts"][kind]

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.data["updated"] = now()
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(self.data, out, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


def all_states() -> list[dict]:
    states = []
    for path in sorted((common_dir() / STATE_DIR).glob("*/state.json")):
        try:
            states.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            states.append({"ticket": path.parent.name, "step": "файл состояния не прочитан"})
    return sorted(states, key=lambda s: int(s["ticket"]) if str(s["ticket"]).isdigit() else 0)


def answers(root: Path) -> Path:
    """Каталог ответов агента в worktree; исключён из git для всех worktree проекта."""
    exclude = common_dir() / "info" / "exclude"
    line = f"/{ANSWERS}/"
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if line not in text.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(text + ("" if not text or text.endswith("\n") else "\n") + line + "\n")
    return root / ANSWERS


def read_answer(root: Path, name: str) -> str:
    path = root / ANSWERS / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def canon(name: str, *args: str) -> subprocess.CompletedProcess:
    """Скрипт канона рядом с `ticket` (той же версии)."""
    return run(sys.executable, str(HERE / name), *args)


def scripts_dir() -> str:
    """Каталог скриптов канона от корня их репозитория: `scripts/canon` в проекте,
    `project/scripts/canon` в самом каноне — он ведёт свои тикеты тем же `ticket` из своего кода
    (#159). Корень — ближний каталог с `.git` (у worktree это файл)."""
    for root in HERE.parents:
        if (root / ".git").exists():
            return HERE.relative_to(root).as_posix()
    return "scripts/canon"


def command(*args: object) -> str:
    """Команда задания `ticket …` — от корня worktree, путём скрипта в его репозитории: в каноне
    команда проектов `scripts/canon/ticket` не нашлась бы."""
    return " ".join([f"{scripts_dir()}/ticket", *map(str, args)])


def profile_checks(profile: Path) -> list[tuple[str, list[str]]]:
    """Проверки ветки из блока ```checks профиля: строки `имя: команда`, по порядку.

    Команда — без оболочки (`shlex`): нужна оболочка — `sh -c '…'`. Обёртку `run-log` добавляет
    `ticket`; команда, уже начатая с `scripts/canon/run-log`, берётся как есть. Блока нет —
    строки «Тесты», «Линтер», «Формат (проверка)» таблицы «Команды» (профиль до #154).
    """
    try:
        text = profile.read_text(encoding="utf-8")
    except OSError as exc:
        raise Failure(f"профиль {profile}: {exc.strerror}") from None
    return parse_checks(text, profile)


def parse_checks(text: str, profile) -> list[tuple[str, list[str]]]:
    """Проверки из текста профиля (`profile_checks`); `profile` — где он, для ошибок."""
    block = CHECKS_BLOCK.search(text)
    if not block:
        rows = [
            (TABLE_CHECKS[m[1]], m[2]) for m in TABLE_ROW.finditer(text) if m[1] in TABLE_CHECKS
        ]
        if not rows:
            raise Failure(f"в профиле {profile} нет блока ```checks (раздел «Команды»)")
        return [(name, shlex.split(command)) for name, command in rows]
    return block_lines(block.group(1), profile, "checks")


def branch_files(base: str = "origin/main") -> list[str]:
    """Изменённые файлы ветки — дифф `base...HEAD`, как у пути v2 (`v2/doctor.py`): добавленные,
    изменённые, переименованные; удалённых нет — линтер на них упал бы."""
    out = git("diff", "--name-only", "--diff-filter=ACMR", "-z", f"{base}...HEAD")
    return [p for p in out.split("\0") if p]


def expand_files(argv: list[str], files: list[str]) -> list[str] | None:
    """`{files}` и `{files:шаблоны}` в команде проверки — файлы ветки; подходящих нет — None
    (проверка пропускается), как у пути v2."""
    out = []
    for arg in argv:
        m = FILES_ARG.match(arg)
        if not m:
            out.append(arg)
            continue
        patterns = [p.strip() for p in (m[1] or "").split(",") if p.strip()]
        chosen = [
            f
            for f in files
            if not patterns
            or any(fnmatch.fnmatch(f, p) or fnmatch.fnmatch(Path(f).name, p) for p in patterns)
        ]
        if not chosen:
            return None
        out += chosen
    return out


def fast_checks(text: str, profile) -> list[tuple[str, list[str]]] | None:
    """Быстрые проверки пути v2 из блока ```fast-checks профиля (#338): строки `имя: команда`, как
    у ```checks; `format` — форматер, правит файлы сам. Блока нет — None."""
    block = FAST_CHECKS_BLOCK.search(text)
    return None if block is None else block_lines(block.group(1), profile, "fast-checks")


def block_lines(body: str, profile, kind: str) -> list[tuple[str, list[str]]]:
    """Строки `имя: команда` блока профиля `kind` по порядку; пустые и `#` — пропуск."""
    checks = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, command = line.partition(":")
        if not sep or not name.strip() or not command.strip():
            raise Failure(f"профиль {profile}: строка блока {kind} не «имя: команда»: {line}")
        checks.append((name.strip(), shlex.split(command)))
    return checks


def secret_findings(text: str) -> list[str]:
    """Находки `gitleaks -v` из вывода проверки `secrets` (#258): «файл:строка (правило, коммит)»
    — только эти поля, без `Finding`/`Secret`/`Match`: значение секрета не уходит ни в вывод,
    ни в отчёт на GitHub, даже если профиль забыл `--redact`."""
    found, item = [], {}
    for raw in text.splitlines():
        m = LEAK_FIELD.match(ANSI.sub("", raw).strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "Fingerprint":
            if item.get("File"):
                rule = item.get("RuleID", "?")
                commit = item.get("Commit", "")[:7]
                where = f"{item['File']}:{item.get('Line', '?')}"
                found.append(f"{where} ({rule}" + (f", коммит {commit})" if commit else ")"))
            item = {}
        else:
            item[key] = value
    return found


def leaks_line(found: list[str]) -> str:
    """Находки для отчёта: первые `LEAKS_SHOWN` и «и ещё N» — комментарий GitHub не бесконечен."""
    shown = "; ".join(found[:LEAKS_SHOWN])
    more = len(found) - LEAKS_SHOWN
    return f"секрет в коммитах ветки: {shown}" + (f" и ещё {more}" if more > 0 else "")


def without_secret_fields(text: str) -> str:
    """Вывод gitleaks без значения: поле `Finding`/`Secret`/`Match` (и в JSON) вместе со
    строками-продолжениями многострочного значения (тело PEM) — до следующего поля. Для строки
    ошибки в отчёте, когда находки не разобраны (другой формат вывода)."""
    kept, hiding = [], False
    for raw in text.splitlines():
        line = ANSI.sub("", raw).strip()
        if SECRET_FIELD.match(line):
            hiding = True
        elif hiding and (LEAK_FIELD.match(line) or OTHER_FIELD.match(line) or not line):
            hiding = False
        if not hiding:
            kept.append(raw)
    return "\n".join(kept)


# Маска секретов в тексте для GitHub (#446; #287 п. 5, #294 п. 3) — общая для отчёта об остановке
# и вопроса владельцу. Пароль в адресе — при любом символе перед схемой (`DB_URL_postgresql://…`,
# `Ошибкаpostgresql://…`) и при `@` в логине: пароль — до последнего `@` слова (в нём бывают `/`
# и `@`; лишнее под маской не вредит). Схема начинается не внутри схемы (`(?<!…)`): иначе длинное
# слово без `://` проверялось бы с каждой буквы — квадратичное время на длинной строке лога.
URL_PASSWORD = re.compile(r"(?<![A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]*://[^\s/:]*:)\S+(@)")
# Запись `ИМЯ: значение`, `ИМЯ=значение`, `"имя": "значение"` с именем секрета; значение — в
# кавычках целиком (с пробелами) или до пробела, со схемой `Bearer`/`Basic`/`Token` впереди.
# Значение — в просмотре вперёд: запись с несекретным именем (`Error: GH_TOKEN=…`) его не
# забирает, и секретная запись внутри найдётся. Имя — не продолжение слова и не часть пути
# (`config/token.py:12` — файл и строка, а не запись).
SECRET_RECORD = re.compile(
    r"""(?<![A-Za-z0-9_.\-/])(["']?)([A-Za-z_][A-Za-z0-9_.-]*)\1[ \t]*[:=][ \t]*"""
    r"""(?=("[^"\n]*"|'[^'\n]*'|(?:(?:Bearer|Basic|Token)[ \t]+)?[^\s"',;&]+))""",
    re.I,
)
# Имя секрета: слово внутри имени (`PGPASSWORD`, `GH_TOKEN`; не `tokens`) или часть имени между
# `_`, `-`, `.` (`api_key`, `KEY`, но не `monkey`, не `author`, не `pass`/`auth` итогов тестов).
SECRET_WORDS = re.compile(
    r"token(?!s)|secret|passw(?:or)?d|credential|authorization|dsn|apikey", re.I
)
SECRET_PARTS = frozenset({"key", "pwd", "pw", "cookie"})
# Короткое число у имени `key` (`key: 3`) — итог, а не секрет; прочие числа (`DB_PWD=123456`,
# `api_key=12345678`) — секрет. Файл и строка (`token.py:12`) — не запись.
SMALL = re.compile(r"^\d{1,4}[).\]]*$")
FILE_LINE = re.compile(r"\.[A-Za-z0-9]+$")
# Токен после `Bearer`/`Basic` — похожий на токен: с цифрой, заглавной внутри или длинный
# (`Basic dXNlcjpwYXNz` — да, `Basic functionality` — нет).
BEARER = re.compile(r"\b(Bearer|Basic)([ \t]+)([A-Za-z0-9._~+/=-]{6,})", re.I)
MASK = "***"


def secret_name(name: str) -> bool:
    return bool(SECRET_WORDS.search(name)) or any(
        part.lower() in SECRET_PARTS for part in re.split(r"[_.-]", name)
    )


def mask(text: str) -> str:
    """Текст без паролей и токенов: пароль в адресе, значения записей с именем секрета, токен
    после `Bearer`/`Basic` — `***`. Строки без секретов не меняются."""
    text = URL_PASSWORD.sub(rf"\1{MASK}\2", text)
    hidden: list[tuple[int, int]] = []
    for m in SECRET_RECORD.finditer(text):
        name, value = m.group(2), m.group(3)
        start = m.end()
        if not secret_name(name) or value == MASK or (hidden and start < hidden[-1][1]):
            continue
        if SMALL.match(value) and (name.lower() == "key" or FILE_LINE.search(name)):
            continue
        hidden.append((start, start + len(value)))
    for start, end in reversed(hidden):
        text = text[:start] + MASK + text[end:]

    def bearer(m: re.Match) -> str:
        token = m.group(3)
        if not re.search(r"\d|.[A-Z]", token) and len(token) < 20:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{MASK}"

    return BEARER.sub(bearer, text)


def secrets_command(text: str | None, profile) -> list[str] | None:
    """Команда проверки `secrets` из текста профиля (#258); профиля или блока `checks` нет — None.
    Строка блока не разобрана — `Failure`: защитная проверка из-за опечатки не открывается молча."""
    if text is None or not CHECKS_BLOCK.search(text):
        return None
    return dict(parse_checks(text, profile)).get("secrets")


SECRETS = "secrets"
MAIN = "origin/main"


def main_text(path, cwd: Path | None = None) -> str | None:
    """Файл `path` (от корня) из основной ветки `origin/main`, а не из ветки тикета (#459): ветка
    не уберёт проверку секретов из профиля и не поменяет её команду. Файла нет — None; нет
    самой `origin/main` — `Failure`: «нет проверки» молча открыло бы push."""
    if run("git", "rev-parse", "--verify", "-q", MAIN, cwd=cwd).returncode != 0:
        raise Failure(f"нет {MAIN} — профиль основной ветки не прочитать (git fetch)")
    listed = git("ls-tree", "--name-only", MAIN, "--", str(path), cwd=cwd)
    if not listed:
        return None
    return git("show", f"{MAIN}:{path}", cwd=cwd) + "\n"


def secrets_line(text: str | None, profile, block: str) -> list[str] | None:
    """Команда `secrets` блока `block` (`checks` или `fast-checks`) текста профиля; нет — None."""
    if text is None:
        return None
    if block == "fast-checks":
        return dict(fast_checks(text, profile) or []).get(SECRETS)
    return secrets_command(text, profile)


def trusted_secrets(root: Path, profile, block: str = "checks") -> tuple[list[str] | None, str]:
    """Команда `secrets` блока `block` и откуда она (#459): из профиля основной ветки
    `origin/main` — ветка её не уберёт и не подменит («main»); там её нет — из профиля ветки
    («branch»): ветка, которая проверку добавляет, — не тупик, и проверка лучше, чем никакой;
    нет нигде — (None, «main», если профиль в основной ветке есть, иначе «»: первая установка
    воркфлоу, профиль приносит сам PR). Политика одна для хука, допуска и `ship-finish`. Нет
    `origin/main` или строка блока не разобрана — `Failure`."""
    main = main_text(profile, root)
    if (command := secrets_line(main, profile, block)) is not None:
        return command, "main"
    try:
        own = (root / profile).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        own = None
    if (command := secrets_line(own, profile, block)) is not None:
        return command, "branch"
    return None, "main" if main is not None else ""


def canon_secrets(argv: list[str], here: Path) -> list[str]:
    """Помощник канона `secrets` в команде профиля (`python3 scripts/canon/secrets`, в самом
    каноне — `project/scripts/canon/secrets`, и под обёрткой `run-log`) — копией в `here` рядом
    с вызывающим скриптом, под его Python (#283): как идёт проверка, решает не файл ветки.
    Прочие команды — как есть."""
    for i, arg in enumerate(argv):
        if Path(arg).parts[-3:] == ("scripts", "canon", "secrets"):
            start = i  # интерпретатор профиля (`python3.12`, `python3 -u`) — тоже под замену
            while start and (
                Path(argv[start - 1]).name.startswith("python")
                or (argv[start - 1].startswith("-") and argv[start - 1] != "--")
            ):
                start -= 1
            return [*argv[:start], sys.executable, str(here / "secrets"), *argv[i + 1 :]]
    return argv


def say(text: str) -> None:
    print(text, flush=True)


def emit(status: str, title: str, body: str = "", commands: tuple[str, ...] = ()) -> int:
    """Задание или итог: заголовок, текст, команды, статус последней строкой."""
    for command in commands:
        if NOT_SIMPLE.search(command):
            raise Failure(f"команда задания не простая (режим В её отклонит): {command}")
    say(f"\n== {title} ==")
    if body:
        say(body.rstrip())
    if commands:
        say("")
        for command in commands:
            say(f"  ▶ {command}")
    say(f"ticket: {status}")
    return 0


# Владения и флаги брифа (`.claude/canon/ticket-ownership.md`): по ним оркестратор решает, какие
# тикеты идут параллельно; тикеты с общим флагом параллельно не идут. Разбор общий (#289):
# оркестратор строки читает, `ticket ship` пишет их в тикет техдолга по путям находок. «Деньги»
# (#354) — из блока v2 (`ticket-ownership-v2.md`); разбор общий для обоих путей.
FLAGS = ("миграция", "ядро", "экран", "деньги")
OWNS_LINE = re.compile(r"^\*\*Владения:\*\*(.*)$", re.M)
FLAGS_LINE = re.compile(r"^\*\*Флаги:\*\*(.*)$", re.M)


def claim_path(raw: str) -> str | None:
    """Путь строки «Владения» от корня репозитория; маска (`src/*.py`), весь репозиторий (`.`) и
    `..` буквально с путями не сравнить — None."""
    path = re.sub(r"^(\./)+|^/+", "", raw.strip())
    parts = path.rstrip("/").split("/")
    if not path or re.search(r"[*?\[\]`\s]", path) or "." in parts or ".." in parts:
        return None
    return path


def claims(body: str | None) -> dict | None:
    """Владения и флаги брифа: по одной строке `**Владения:**` (пути в обратных кавычках через
    запятую) и `**Флаги:**` (`нет` или слова из `FLAGS`). Строки нет, их несколько или она не по
    образцу (слова вместо путей, маска, `.`) — None: тикет идёт один."""
    owns, flags = OWNS_LINE.findall(body or ""), FLAGS_LINE.findall(body or "")
    if len(owns) != 1 or len(flags) != 1:
        return None
    paths = []
    for item in owns[0].strip().rstrip(".").split(","):
        found = re.fullmatch(r"`([^`\s]+)`", item.strip())
        path = claim_path(found.group(1)) if found else None
        if path is None:
            return None
        paths.append(path)
    words = [w.strip().lower() for w in flags[0].strip().rstrip(".").split(",")]
    if words == ["нет"]:
        words = []
    if not all(w in FLAGS for w in words):
        return None
    return {"paths": paths, "flags": sorted(set(words))}


def owner_claims(comments: list[dict], owner: str) -> dict | None:
    """Владения и флаги из последнего нескрытого комментария владельца репозитория (логин
    `owner`; `gh … --json comments`) с обеими строками: владелец добавляет их, пока тикет в
    очереди. App, служебные комментарии маршрута (`ticket ask`, отчёт об остановке — в ручном
    режиме они от учётной записи владельца) и прочие участники — не владелец. Строки не по образцу
    — None."""
    for c in reversed(comments):
        body = c.get("body") or ""
        if c.get("isMinimized") or ((c.get("author") or {}).get("login") or "") != owner:
            continue
        if not is_answer(body.strip()):
            continue  # служебное маршрута: в ручном режиме сессия пишет токеном владельца
        if OWNS_LINE.search(body) and FLAGS_LINE.search(body):
            return claims(body)
    return None


def owns_line(paths: list[str]) -> str:
    return "**Владения:** " + ", ".join(f"`{p}`" for p in paths)


def add_owns(body: str, paths: list[str]) -> str:
    """Тело тикета, где строка «Владения» дополнена путями (`paths`, новые — в конец); строки
    нет — она и «Флаги: нет» (если нет и её) дописываются в конец тела. Строка не по образцу
    (слова, пометки) не переписывается: её правил владелец, тикет и так идёт один."""
    line = OWNS_LINE.search(body)
    if line is None:
        flags = "" if FLAGS_LINE.search(body) else "\n**Флаги:** нет"
        return f"{body.rstrip()}\n\n{owns_line(paths)}{flags}"
    have = []
    for item in line.group(1).strip().rstrip(".").split(","):
        found = re.fullmatch(r"`([^`\s]+)`", item.strip())
        path = claim_path(found.group(1)) if found else None
        if path is None:
            return body
        have.append(path)
    merged = have + [p for p in paths if p not in have]
    if merged == have:
        return body
    return body[: line.start()] + owns_line(merged) + body[line.end() :]
