"""checks — хук быстрых проверок на коммите в песочнице пути v2 (#338; спека #329, «Контракт CI
проектов и быстрые проверки»).

Список — блок ```fast-checks профиля проекта (`docs/agents/profile.md`): строки `имя: команда`,
как у ```checks (разбор — `ticket_core.fast_checks`). Проверка `format` — форматер: правит файлы
сам, исправленное уходит в тот же коммит. Остальные (линтер, типы, синтаксис JSON / YAML / TOML,
маркеры конфликтов, секреты) идут по порядку все, и хоть одна красная — коммит остановлен с
причиной: имя, команда, код и конец вывода каждой красной (у `secrets` — только «файл:строка»,
без значения). Команды — из корня worktree, без оболочки, не дольше `TIMEOUT` (по нему
завершается вся группа процессов команды). Аргумент `{files}` — файлы коммита, `{files:*.py,…}` —
только подходящие под шаблоны; таких файлов нет — проверка пропускается. Без `{files}` команда
идёт как написана (обычно по всему дереву). Коммит слияния (`MERGE_HEAD`, догон основы) своих
проверок не проходит: в нём код `main`, а не сессии.

Ставит хук `session-run` в worktree сессии, когда оркестратор ведёт тикет путём v2 (`HOOK_ENV` в
окружении сессии): `core.hooksPath` этого worktree (`git config --worktree`, в общем конфиге —
`extensions.worktreeConfig`) — каталог-переходник `canon-hooks/` в личном каталоге git worktree.
В нём на каждый вид хука — вызов `hooks/relay` копии канона: у `pre-commit` сначала быстрые
проверки, затем — у всех видов — прежний хук проекта (`core.hooksPath` общего конфига, например
husky, иначе `hooks/` общего каталога git, например pre-commit): свои хуки проекта не
выключаются. Главный чекаут клона и worktree старого пути переходника не получают. Блока в
профиле нет — хук не ставится (поставленный раньше снимается), предупреждение в вывод сессии.
Профиль читается на каждом коммите из worktree: правка списка в ветке действует сразу; блок
испорчен — коммит остановлен с причиной.

Хук — фильтр, а не допуск: `git commit --no-verify` его обходит, проверки видят рабочую копию, а
не снимок индекса. Тот же список гоняет проверка в песочнице программой (#342). Форматер
исправляет файл целиком: незастейдженные правки застейдженного файла уходят в коммит вместе с
исправлением.

Удалить: не ставить хук (`HOOK_ENV` в `orchestrator` и вызов в `session-run`), затем этот файл и
`hooks/`; блок в профиле остаётся.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import signal
import subprocess
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELAY = HERE / "hooks/relay"
PROFILE = Path("docs/agents/profile.md")
HOOK_ENV = "CANON_CHECKS_HOOK"  # «1» — `session-run` ставит хук в worktree сессии
HOOKS_DIR = "canon-hooks"  # переходник — в личном каталоге git worktree
FORMAT, SECRETS = "format", "secrets"
FILES = re.compile(r"^\{files(?::([^}]*))?\}$")
TIMEOUT = 600
TAIL = 20  # строк вывода красной проверки в причине
# Виды хуков на стороне клиента (`git help hooks`): переходник передаёт каждый прежнему хуку.
HOOK_NAMES = (
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "pre-auto-gc",
    "post-rewrite",
    "sendemail-validate",
    "fsmonitor-watchman",
    "reference-transaction",
    "post-index-change",
)


def core():
    """`ticket_core` копии канона — разбор блока профиля, фильтр вывода gitleaks, `Failure`."""
    path = HERE.parent / "ticket_core.py"
    module = types.ModuleType("canon_ticket_core_checks")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, errors="replace"
    )


def listed(worktree: Path, ticket_core) -> tuple[list | None, str]:
    """Быстрые проверки из профиля worktree: список (None — блока нет) и причина, если блок
    испорчен (тогда список пуст)."""
    try:
        text = (worktree / PROFILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    try:
        return ticket_core.fast_checks(text, PROFILE), ""
    except (ticket_core.Failure, ValueError) as exc:  # ValueError — кавычки без пары (`shlex`)
        return [], str(exc)


def install(worktree: Path) -> tuple[bool, str]:
    """Хук в worktree сессии; итог — поставлен ли и строка для вывода сессии."""
    checks, broken = listed(worktree, core())
    if checks is None:
        git(worktree, "config", "--worktree", "--unset", "core.hooksPath")  # прошлой установки
        return False, (
            f"предупреждение: в {PROFILE} нет блока ```fast-checks — хук быстрых проверок не "
            "поставлен, формат и линт поймает только проверка после «код готов»"
        )
    gitdir = git(worktree, "rev-parse", "--absolute-git-dir")
    if gitdir.returncode != 0:
        return False, f"предупреждение: хук быстрых проверок не поставлен — {gitdir.stderr.strip()}"
    hooks = Path(gitdir.stdout.strip()) / HOOKS_DIR
    try:
        hooks.mkdir(exist_ok=True)
        for name in HOOK_NAMES:
            path = hooks / name
            path.write_text(
                f'#!/bin/sh\nexec python3 {shlex.quote(str(RELAY))} {name} "$@"\n', encoding="utf-8"
            )
            path.chmod(0o755)
    except OSError as exc:
        return False, f"предупреждение: хук быстрых проверок не поставлен — {hooks}: {exc}"
    for args in (
        ("config", "extensions.worktreeConfig", "true"),
        ("config", "--worktree", "core.hooksPath", str(hooks)),
    ):
        res = git(worktree, *args)
        if res.returncode != 0:
            return False, f"предупреждение: хук быстрых проверок не поставлен — git {args[0]}: " + (
                res.stderr.strip() or f"код {res.returncode}"
            )
    if broken:
        return True, f"хук быстрых проверок поставлен, но список не разобран: {broken}"
    return True, f"хук быстрых проверок поставлен ({', '.join(name for name, _ in checks)})"


def prior_hooks(worktree: Path) -> Path:
    """Каталог прежних хуков проекта: `core.hooksPath` общего конфига (относительный — от корня
    worktree), иначе `hooks/` общего каталога git."""
    own = git(worktree, "config", "--local", "--get", "core.hooksPath")
    if own.returncode == 0 and own.stdout.strip():
        return worktree / Path(own.stdout.strip()).expanduser()
    common = git(worktree, "rev-parse", "--git-common-dir").stdout.strip() or ".git"
    return worktree / common / "hooks"


def relay(name: str, args: list[str], worktree: Path) -> int:
    """Хук вида `name`: у `pre-commit` — быстрые проверки; затем прежний хук проекта, если он есть
    и исполняемый (вход и выход — его)."""
    if name == "pre-commit" and (code := hook(worktree)) != 0:
        return code
    prior = prior_hooks(worktree) / name
    if prior.parent.name == HOOKS_DIR or not (prior.is_file() and os.access(prior, os.X_OK)):
        return 0
    try:
        return subprocess.run([str(prior), *args], cwd=worktree).returncode
    except OSError as exc:
        print(f"хук проекта {prior}: {exc}", file=sys.stderr)
        return 1


def expand(command: list[str], files: list[str]) -> list[str] | None:
    """`{files}` и `{files:шаблоны}` — файлы коммита; подходящих нет — None (пропуск)."""
    out = []
    for arg in command:
        m = FILES.match(arg)
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


def run(command: list[str], worktree: Path) -> tuple[int, str]:
    """Команда проверки из корня worktree своей группой процессов: код и вывод (stdout и stderr
    вместе); по `TIMEOUT` завершается вся группа — внуки не правят файлы после хука."""
    try:
        proc = subprocess.Popen(
            command,
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        return 127, f"команда {command[0]!r} не запустилась: {exc.strerror or exc}"
    try:
        output, _ = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        return 124, f"не кончилась за {TIMEOUT} с"
    return proc.returncode, output


def reason(name: str, command: list[str], code: int, output: str, ticket_core) -> str:
    if name == SECRETS:  # значение секрета — ни в вывод коммита, ни в транскрипт
        found = ticket_core.secret_findings(output)
        output = (
            ticket_core.leaks_line(found) if found else ticket_core.without_secret_fields(output)
        )
    lines = output.rstrip().splitlines()[-TAIL:]
    head = f"проверка «{name}» ({' '.join(command)}) — код {code}"
    return "\n".join([head, *(f"  {line}" for line in lines)])


def hook(worktree: Path) -> int:
    """Быстрые проверки коммита: 0 — коммит идёт, 1 — остановлен; причина — в stderr."""
    if git(worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        return 0  # коммит слияния: код `main`, а не сессии
    ticket_core = core()
    checks, broken = listed(worktree, ticket_core)
    if broken:
        return stop([f"список быстрых проверок не разобран: {broken}"])
    if not checks:
        return 0  # блок убран в ветке — проверять нечего
    staged = git(worktree, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    if staged.returncode != 0:
        return stop([f"git diff --cached: {staged.stderr.strip()}"])
    files = [f for f in staged.stdout.split("\0") if f]
    failed = []
    for name, listed_command in checks:
        command = expand(listed_command, files)
        if command is None or (name == FORMAT and not files):
            continue
        code, output = run(command, worktree)
        if code != 0:
            failed.append(reason(name, command, code, output, ticket_core))
        elif name == FORMAT:
            # Исправленное форматером — в тот же коммит: только файлы, уже бывшие в нём, по
            # буквальным именам (`[`, `*` в имени не захватят соседние файлы).
            present = [f for f in files if (worktree / f).exists()]
            added = git(worktree, "--literal-pathspecs", "add", "--", *present) if present else None
            if added is not None and added.returncode != 0:
                failed.append(f"проверка «{name}»: git add — {added.stderr.strip()}")
    return stop(failed) if failed else 0


def stop(problems: list[str]) -> int:
    print(f"Коммит остановлен хуком быстрых проверок ({PROFILE}, ```fast-checks):", file=sys.stderr)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1
