"""ticket_core — каркас `scripts/canon/ticket`: состояние тикета, задания агенту, команды профиля.

**Состояние тикета** — `<общий каталог git>/canon-ticket/<N>/state.json` на машине владельца, не
в репозитории: общий для главного чекаута и всех worktree проекта. Рядом — отчёты ревью и
`/verify`, критерии, тело PR и отчёт об остановке: обрыв сессии не теряет работу, ревью второй
раз не оплачивается, в `/tmp` ничего не копится. Пишет его только скрипт (атомарной заменой):
изоляция worktree приложения не даёт агенту писать вне своего worktree (прогон B0).

**Ответы агента** — файлы в `.ticket/` корня worktree тикета (`Write` внутри worktree); каталог
исключён из git через `info/exclude` общего каталога, в коммиты не попадает.

**Задание** — шаг с суждением: заголовок, текст, команды строками `  ▶ <команда>` и последняя
строка — статус. Команды заданий — простые (без `$(…)`, переменных, `cd`, кавычек и составных):
приложение в режиме В отклоняет «слишком сложные» (ретро A4, 71 отказ на 14 тикетов).
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

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
ANSWERS = ".ticket"
TICKET_BRANCH = re.compile(r"^ticket-(\d+)(?:-.+)?$")
TURN, DONE, STOPPED = "ход агента", "готово", "остановка"
# Конец сессии под оркестратором (#214): ветка готова к его ревью или к отметке.
READY = "код готов"
AGENT, OWNER, CI, ORCHESTRATOR = "агент", "владелец", "CI", "оркестратор"
# Bash агента по умолчанию обрывает команду через 2 минуты; долгие шаги — с таймаутом 45 минут.
LONG_CALL = "обычный Bash, не в фоне, timeout 2700000"
NOT_SIMPLE = re.compile(r"[$`'\"\n;&|<>]|\bcd\b")
# Шаги маршрута для заданий и `ticket status`; шаг модуля, которого здесь нет, — своим именем.
STEP_NAMES = {
    "review": "ревью ошибок",
    "findings": "разбор",
    "checks": "тесты и линтеры",
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
    *cmd: str, cwd: Path | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
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
# Режим маршрута «под оркестратором» (#214): флаг окружения от оркестратора. Сессия кончается на
# «код готов» или на вопросе; ревью ошибок, отметку, авто-мёрж, ожидание и уборку делает он.
ORCHESTRATED = "CANON_ORCHESTRATOR"


def orchestrated() -> bool:
    return os.environ.get(ORCHESTRATED) == "1"


# Вопрос владельцу под оркестратором — тремя разделами (спека #208): владелец отвечает с
# телефона, не зная хода сессии.
ORCHESTRATED_ASK = (
    " Под оркестратором — тремя разделами: «Причина» (что неясно и почему без ответа не "
    "сделать), «Сделано» (что уже готово), «Доступно» (варианты, что будет при каждом, "
    "рекомендация)."
)


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


def keep(st, path: Path, name: str) -> None:
    """Файл ответа агента — в каталог состояния `st`: в worktree он не повторится на новом круге."""
    if path.exists():
        st.dir.mkdir(parents=True, exist_ok=True)
        path.replace(st.dir / name)


def relay_reply(number: int, reply: str) -> str:
    """Ответ владельца из чата (`.ticket/reply.md`) — комментарием в тикет; ссылка на него (`gh`
    её печатает) или «тикет #N»."""
    body = f"{REPLY_MARK}\n**Ответ владельца из чата** (передан сессией)\n\n{reply}"
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
    """Начало задания, пока вопрос `ticket ask` ждёт ответа (`ticket start`, `ticket ship`; #196):
    вопрос владельцу в чат полным текстом из состояния, как в `ticket ask`, — владелец отвечает
    там, не открывая GitHub. У вопроса, заданного до #194, текста в состоянии нет —
    к комментарию."""
    url = question["url"]
    if text := question.get("text"):
        return (
            f"Вопрос владельцу ждёт ответа: {url}. Владельцу в чат — этот вопрос полным текстом "
            f"и ссылка на него, обычным сообщением, без `AskUserQuestion`:\n\n{quote(text)}\n\n"
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
    checks = []
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, command = line.partition(":")
        if not sep or not name.strip() or not command.strip():
            raise Failure(f"профиль {profile}: строка блока checks не «имя: команда»: {line}")
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


def secrets_command(text: str | None, profile) -> list[str] | None:
    """Команда проверки `secrets` из текста профиля (#258); профиля или блока `checks` нет — None.
    Строка блока не разобрана — `Failure`: защитная проверка из-за опечатки не открывается молча."""
    if text is None or not CHECKS_BLOCK.search(text):
        return None
    return dict(parse_checks(text, profile)).get("secrets")


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
