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
AGENT, OWNER, CI = "агент", "владелец", "CI"
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
    "stop": "остановка",
    "stopped": "остановка",
}
CHECKS_BLOCK = re.compile(r"^```checks[ \t]*\n(.*?)^```", re.M | re.S)
# Профиль до блока `checks` (проекты на каноне до #154): строки таблицы «Команды».
TABLE_CHECKS = {"Тесты": "tests", "Линтер": "lint", "Формат (проверка)": "format"}
TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|", re.M)


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


def run(*cmd: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def git(*args: str, cwd: Path | None = None) -> str:
    result = run("git", *args, cwd=cwd)
    if result.returncode != 0:
        raise Failure(f"git {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def git_ok(*args: str) -> bool:
    return run("git", *args).returncode == 0


def gh(*args: str) -> str:
    try:
        result = run("gh", *args)
    except FileNotFoundError:
        raise Failure("не найден `gh` на PATH") from None
    if result.returncode != 0:
        raise Failure(f"gh {' '.join(args[:2])}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def gh_json(*args: str):
    out = gh(*args)
    try:
        return json.loads(out) if out else None
    except ValueError:
        raise Failure(f"gh {' '.join(args[:2])}: не JSON: {out[:200]}") from None


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
            stamp = now()
            self.data = {"ticket": number, "step": None, "since": stamp, "updated": stamp}
        except (OSError, ValueError) as exc:
            raise Failure(f"файл состояния {self.path}: {exc}") from None
        for key in ("heads", "attempts", "cost"):
            self.data.setdefault(key, {})
        self.data.setdefault("waiting", AGENT)

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
