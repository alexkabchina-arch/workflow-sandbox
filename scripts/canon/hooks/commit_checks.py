"""commit_checks — стандартный хук перед коммитом воркфлоу (#463; решение владельца 30.10 о
проверках на пути `/start`, `docs/decisions/2026-10-30-checks-on-start-path.md`).

Срабатывает у любого агента и у человека: это git-хук, а не настройка конкретного агента. На
каждом коммите рабочей копии тикета, по порядку:

1. Часть воркфлоу в `AGENTS.md` (между метками `canon:process`) совпадает с
   `.claude/canon/process.md` установленной версии — то же правило, что у `check_lock`
   (`process_problems`). Не совпало — коммита нет: эту часть ставит установка воркфлоу
   (`canon-sync`), правила проекта пишутся вне меток.
2. Быстрые проверки — блок ```fast-checks профиля проекта (`docs/agents/profile.md`): строки
   `имя: команда`, как у ```checks (разбор — `ticket_core.fast_checks`). Формат, линтер, типы —
   по всему проекту (команда без `{files}`), секреты — в изменённом (`{files}` — файлы коммита,
   `{files:*.py,…}` — только подходящие; подходящих нет — проверка пропущена). Команды — из корня
   рабочей копии, без оболочки, не дольше `TIMEOUT`. Проверка красная, если её код не 0 или она
   изменила файлы рабочей копии (форматер в режиме правки): исправление не уходит в коммит молча —
   его смотрят и добавляют (`git add`), затем коммит повторяют. Красные идут все; хоть одна —
   коммита нет, причина — имя, команда, код и конец вывода (у `secrets` — только «файл:строка»,
   без значения).
   Строка `secrets` — из профиля основной ветки `origin/main`, не из рабочей копии (#459): ветка
   не уберёт её и не подменит команду; там её нет — строка ветки (`trusted_secrets`).
   Строка `gitleaks` идёт с настройками основной ветки —
   конфиг `.gitleaks.toml` из `origin/main`, без `.gitleaksignore` и `gitleaks:allow` ветки, у
   подкоманды `git` источник — каталог git рабочей копии (`secrets.trusted_settings`); помощник
   канона `scripts/canon/secrets` — копией рядом; прочие команды идут как написаны (команда,
   что запускает файл рабочей копии или gitleaks с источником `.`, остаётся во власти ветки —
   профили воркфлоу и шаблона так не пишут). Строки `secrets` нет нигде, а в основной ветке блок
   есть, — коммита нет (и подготовка — стоп). Находка — не «почини»: вопрос владельцу отчётом об
   остановке (`.ticket/stop.md`, затем `ticket ship N`), без чистки и глушения; сбой самой
   проверки (нет программы, настройки не собраны) — обычная причина.
3. Коммит слияния (`MERGE_HEAD`, догон основы) — те же проверки, но `{files}` — файлы, отличные
   от обоих родителей: разрешённые конфликты и свои правки в коммите слияния (`conflict_files`);
   команда без `{files}` идёт как написана; у `gitleaks` — находки только в этих файлах (решение
   владельца 30.10, п. 1: «коммит слияния — по файлам конфликта»; `--staged` видит весь влитой
   дифф `main`, а его код проверен своим путём). Конфликтов не
   было — git зовёт не `pre-commit`, а `pre-merge-commit`: быстрые проверки пропускаются (код
   обеих сторон уже проверен), сверка части воркфлоу (шаг 1) идёт.
4. Свой хук проекта того же вида (`core.hooksPath` общего конфига, например husky, иначе `hooks/`
   общего каталога git, например pre-commit) — после стандартных проверок; так у всех видов хуков.

Блока ```fast-checks в профиле нет — идут только шаги 1 и 4 (подготовка предупреждает). Профиль
читается на каждом коммите из рабочей копии; блок испорчен — коммита нет с причиной.

Ставит хук подготовка (`session-start`, функция `prepare`) в каждую рабочую копию тикета:
`core.hooksPath` этой копии (`git config --worktree`, в общем конфиге —
`extensions.worktreeConfig`) — каталог-переходник `canon-hooks/` в личном каталоге git рабочей
копии; на каждый вид хука — вызов `hooks/relay` копии воркфлоу главного чекаута. Главный чекаут
переходника не получает. Затем подготовка проверяет, что git зовёт именно его, и что команды
проверок запускаются (окружение из профиля поднято): нет — стоп с причиной. Путь v2 ставит свой
хук поверх (`v2/checks.py`, тот же каталог) — его не трогаем.

Хук — фильтр, а не допуск: `git commit --no-verify` его обходит, проверки видят рабочую копию, а
не снимок индекса. Допуск до ревью — программой маршрута (#465), секреты перед отправкой —
`ship-finish`.

Удалить: убрать вызов `prepare` из `session-start`, затем этот каталог; блок в профиле остаётся.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
RELAY = HERE / "relay"
PROFILE = Path("docs/agents/profile.md")
LOCK = ".canon.lock"
HOOKS_DIR = "canon-hooks"  # переходник — в личном каталоге git рабочей копии
SECRETS = "secrets"
FILES = re.compile(r"^\{files(?::([^}]*))?\}$")
OWN = ("pre-commit", "pre-merge-commit")  # виды со стандартными проверками
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


def load(name: str) -> types.ModuleType:
    """Модуль копии воркфлоу рядом (`ticket_core.py`, `check_lock.py`) без `sys.path`."""
    path = CANON / name
    module = types.ModuleType(f"canon_commit_checks_{path.stem}")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, errors="replace"
    )


def listed(worktree: Path, ticket_core) -> tuple[list | None, str]:
    """Быстрые проверки из профиля рабочей копии: список (None — блока нет) и причина, если блок
    испорчен (тогда список пуст)."""
    try:
        text = (worktree / PROFILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    try:
        return ticket_core.fast_checks(text, PROFILE), ""
    except (ticket_core.Failure, ValueError) as exc:  # ValueError — кавычки без пары (`shlex`)
        return [], str(exc)


# --- установка (подготовка) ---


def relay_path(worktree: Path) -> Path:
    """`relay` копии воркфлоу главного чекаута (`scripts/canon/hooks/relay`; у самого воркфлоу —
    по ссылке `scripts/canon` на `project/`), а не копии, откуда шла подготовка: рабочую копию
    другого тикета уберут. Его там ещё нет (версия без хука) — эта копия."""
    res = git(worktree, "worktree", "list", "--porcelain")
    heads = [line.removeprefix("worktree ") for line in res.stdout.splitlines()]
    first = next((h for h, line in zip(heads, res.stdout.splitlines()) if h != line), "")
    main = Path(first) / "scripts/canon/hooks/relay" if first else None
    return main if main is not None and main.is_file() else RELAY


def hook_text(name: str, relay_file: Path) -> str:
    """Скрипт переходника. Пропал `relay` (воркфлоу убран или откачен): проверки коммита —
    отказ с причиной (`--no-verify` обходит), прочие виды — пропуск: `reference-transaction`
    иначе остановил бы любое обновление ссылок без обхода."""
    q = shlex.quote(str(relay_file))
    missing = (
        f"echo 'стандартный хук воркфлоу: нет {q} — повторите подготовку (session-start)' >&2; "
        "exit 1"
        if name in OWN
        else "exit 0"
    )
    return f'#!/bin/sh\n[ -f {q} ] || {{ {missing}; }}\nexec python3 {q} {name} "$@"\n'


def kinds(worktree: Path) -> list[str]:
    """Виды хуков переходника: свои (`OWN`) и те, для которых у проекта есть свой хук — лишние
    виды (`reference-transaction`, `post-index-change`) зря запускали бы Python."""
    prior = prior_hooks(worktree)
    return [
        name
        for name in HOOK_NAMES
        if name in OWN or ((prior / name).is_file() and os.access(prior / name, os.X_OK))
    ]


def install(worktree: Path) -> str:
    """Переходник в рабочей копии; пусто — поставлен, иначе причина."""
    gitdir = git(worktree, "rev-parse", "--absolute-git-dir")
    if gitdir.returncode != 0:
        return gitdir.stderr.strip() or f"git rev-parse: код {gitdir.returncode}"
    hooks = Path(gitdir.stdout.strip()) / HOOKS_DIR
    relay_file, wanted = relay_path(worktree), kinds(worktree)
    try:
        hooks.mkdir(exist_ok=True)
        for name in HOOK_NAMES:
            path = hooks / name
            if name not in wanted:
                path.unlink(missing_ok=True)  # прошлой установки
                continue
            path.write_text(hook_text(name, relay_file), encoding="utf-8")
            path.chmod(0o755)
    except OSError as exc:
        return f"{hooks}: {exc}"
    for args in (
        ("config", "extensions.worktreeConfig", "true"),
        ("config", "--worktree", "core.hooksPath", str(hooks)),
    ):
        res = git(worktree, *args)
        if res.returncode != 0:
            return f"git {' '.join(args)}: " + (res.stderr.strip() or f"код {res.returncode}")
    return ""


def installed(worktree: Path) -> str:
    """Git зовёт перед коммитом именно стандартный хук: пусто — да, иначе причина."""
    res = git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "hooks/pre-commit")
    if res.returncode != 0:
        return res.stderr.strip() or f"git rev-parse: код {res.returncode}"
    path = Path(res.stdout.strip())
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"git зовёт {path}, а его нет"
    if text != hook_text("pre-commit", relay_path(worktree)) or not os.access(path, os.X_OK):
        return f"git зовёт {path}, а это не стандартный хук воркфлоу (или он не исполняемый)"
    return ""


def missing_tools(worktree: Path, checks: list) -> list[str]:
    """Проверки, чья программа не запустится: путь (`.venv/bin/ruff`) — от корня рабочей копии,
    имя — по `PATH`."""
    missing = []
    for name, command in checks:
        program = command[0] if command else ""
        if "/" in program:
            path = Path(program) if Path(program).is_absolute() else worktree / program
            found = path.is_file() and os.access(path, os.X_OK)
        else:
            found = bool(program) and shutil.which(program) is not None
        if not found:
            missing.append(f"«{name}»: нет программы {program!r}")
    return missing


def prepare(worktree: Path) -> tuple[bool, str]:
    """Подготовка: поставить хук, проверить, что git зовёт его, и что команды проверок есть.
    Итог — (готово ли, строка для вывода): не готово — стоп подготовки с этой причиной."""
    if why := install(worktree):
        return False, f"хук перед коммитом не поставлен — {why}"
    if why := installed(worktree):
        return False, f"хук перед коммитом не стоит — {why}"
    ticket_core = load("ticket_core.py")
    checks, broken = listed(worktree, ticket_core)
    if broken:
        return False, f"хук перед коммитом: список быстрых проверок не разобран — {broken}"
    checks, problem = with_main_secrets(worktree, checks, ticket_core)
    if problem:
        return False, f"хук перед коммитом: {problem}"
    if checks is None:
        return True, (
            f"хук перед коммитом поставлен, но в {PROFILE} нет блока ```fast-checks — формат, "
            "линтер и секреты на коммите не проверяются (образец блока — шаблон профиля воркфлоу)"
        )
    if missing := missing_tools(worktree, checks):
        return False, (
            "хук перед коммитом: окружение не поднято — "
            + "; ".join(missing)
            + f" (раздел «Окружение» {PROFILE}, scripts/session/setup)"
        )
    return True, f"хук перед коммитом поставлен ({', '.join(name for name, _ in checks)})"


# --- хук ---


def prior_hooks(worktree: Path) -> Path:
    """Каталог своих хуков: `core.hooksPath` общего конфига проекта, иначе пользователя или
    системы — без уровня рабочей копии, его занял переходник (относительный — от корня рабочей
    копии); иначе `hooks/` общего каталога git."""
    for scope in ("--local", "--global", "--system"):
        own = git(worktree, "config", scope, "--get", "core.hooksPath")
        if own.returncode == 0 and own.stdout.strip():
            return worktree / Path(own.stdout.strip()).expanduser()
    common = git(worktree, "rev-parse", "--git-common-dir").stdout.strip() or ".git"
    return worktree / common / "hooks"


def relay(name: str, args: list[str], worktree: Path) -> int:
    """Хук вида `name`: у `pre-commit` — стандартные проверки; затем свой хук проекта, если он
    есть и исполняемый (вход и выход — его)."""
    if name == "pre-commit" and (code := hook(worktree)) != 0:
        return code
    if name == "pre-merge-commit" and (problems := process_part(worktree)):
        return stop(problems)  # слияние без конфликтов: быстрые проверки не нужны, часть — да
    prior = prior_hooks(worktree) / name
    if prior.parent.name == HOOKS_DIR or not (prior.is_file() and os.access(prior, os.X_OK)):
        return 0
    try:
        return subprocess.run([str(prior), *args], cwd=worktree).returncode
    except OSError as exc:
        print(f"хук проекта {prior}: {exc}", file=sys.stderr)
        return 1


def process_part(worktree: Path) -> list[str]:
    """Шаг 1: часть воркфлоу в `AGENTS.md` — как в установленной версии."""
    lock_path = worktree / LOCK
    if not lock_path.is_file():
        return []
    check_lock = load("check_lock.py")
    try:
        locked = json.loads(lock_path.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"{LOCK} не прочитан: {exc}"]
    problems = check_lock.process_problems(worktree, locked)
    source = worktree / check_lock.PROCESS
    if check_lock.PROCESS in locked and source.is_file():
        if check_lock.file_hash(source.read_bytes()) != locked[check_lock.PROCESS]:
            problems.append(f"{check_lock.PROCESS}: изменён вручную (сумма не совпадает с {LOCK})")
    return [
        f"{problem}. Часть между метками canon:process ставит установка воркфлоу (canon-sync) — "
        "руками её не правят; правила проекта пишутся в AGENTS.md вне меток"
        for problem in problems
    ]


def conflict_files(worktree: Path) -> list[str]:
    """Файлы коммита слияния, отличные от обоих родителей: разрешённые конфликты, свои правки в
    коммите слияния и файлы, которые git свёл сам из правок обеих сторон. У чистого слияния
    без правок — пусто."""
    sides = []
    for parent in ("HEAD", "MERGE_HEAD"):
        res = git(worktree, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z", parent)
        sides.append({f for f in res.stdout.split("\0") if f})
    return sorted(sides[0] & sides[1])


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
    """Команда проверки из корня рабочей копии своей группой процессов: код и вывод; по
    `TIMEOUT` завершается вся группа — внуки не правят файлы после хука."""
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


def snapshot(worktree: Path, files: list[str]) -> dict[str, str]:
    """Суммы файлов, которые проверка могла бы изменить: правленые относительно индекса и файлы
    коммита. Изменённая сумма после проверки — проверка правила файлы."""
    res = git(worktree, "diff", "--name-only", "-z")
    names = {f for f in res.stdout.split("\0") if f} | set(files)
    sums = {}
    for name in names:
        try:
            sums[name] = hashlib.sha256((worktree / name).read_bytes()).hexdigest()
        except OSError:
            sums[name] = ""
    return sums


def edited_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Файлы, которые проверка изменила: новые в снимке или с другой суммой."""
    return sorted(name for name, digest in after.items() if before.get(name) != digest)


def reason(name: str, command: list[str], code: int, output: str, ticket_core) -> str:
    if name == SECRETS:  # значение секрета — ни в вывод коммита, ни в транскрипт
        found = ticket_core.secret_findings(output)
        output = (
            ticket_core.leaks_line(found) if found else ticket_core.without_secret_fields(output)
        )
    lines = output.rstrip().splitlines()[-TAIL:]
    head = f"проверка «{name}» ({' '.join(command)}) — код {code}"
    return "\n".join([head, *(f"  {line}" for line in lines)])


def with_main_secrets(worktree: Path, checks: list | None, ticket_core) -> tuple[list | None, str]:
    """Строка `secrets` быстрых проверок — из профиля основной ветки `origin/main`, а не из
    рабочей копии (#459): ветка не уберёт проверку и не подменит её команду. Там её нет — строка
    ветки (`ticket_core.trusted_secrets`, одна политика с допуском и `ship-finish`): ветка,
    которая проверку вводит, не тупик. Нет нигде, а в основной ветке блок ```fast-checks есть, —
    причина отказа коммиту; блока нет и там — секретов в хуке нет (подготовка предупреждает о
    блоке). Итог — список проверок и причина (пусто — нет)."""
    own = None if checks is None else [(n, a) for n, a in checks if n != SECRETS]
    try:
        command, where = ticket_core.trusted_secrets(worktree, PROFILE, "fast-checks")
        main = ticket_core.main_text(PROFILE, worktree) if command is None and where else None
        block = main is not None and ticket_core.fast_checks(main, PROFILE) is not None
    except (ticket_core.Failure, ValueError) as exc:
        return own, f"проверка секретов: профиль основной ветки не прочитан — {exc}"
    if command is not None:
        return [*(own or []), (SECRETS, command)], ""
    if block:
        return own, (
            f"в {PROFILE} нет строки `secrets` блока ```fast-checks — ни в основной ветке "
            "(origin/main), ни в ветке: коммит без проверки секретов не идёт; строку дописать в "
            "профиль ветки (образец — шаблон профиля воркфлоу)"
        )
    return own, ""


def run_secrets(command: list[str], worktree: Path, files: list[str], merging: bool, ticket_core):
    """Проверка секретов (#459): строка `gitleaks` — с настройками основной ветки (конфиг из
    `origin/main`, без `.gitleaksignore` и `gitleaks:allow` ветки; у подкоманды `git` источник —
    каталог git рабочей копии), как у помощника `secrets`; помощник канона — копией рядом. На
    коммите слияния находки — только в файлах конфликта: прочее — влитой код `main`, его
    проверил свой путь (`gitleaks --staged` видит весь влитой дифф). Итог — код и вывод."""
    if Path(command[0]).name != "gitleaks":
        return run(ticket_core.canon_secrets(command, CANON), worktree)
    helper = load("secrets")
    with tempfile.TemporaryDirectory(prefix="canon-hook-secrets-") as tmp:
        try:
            flags, gitdir = helper.trusted_settings(Path(tmp), cwd=worktree)
        except helper.SettingsError as exc:
            return 1, f"настройки gitleaks основной ветки не собраны: {exc}"
        argv = [*command, *flags]
        report = Path(tmp) / "report.json"
        if merging:
            argv += ["--report-format", "json", "--report-path", str(report)]
        if command[1:2] == ["git"]:
            argv.append(gitdir)
        code, output = run(argv, worktree)
        if merging and code != 0:
            code, output = conflict_findings(report, files, code, output)
    return code, output


def conflict_findings(report: Path, files: list[str], code: int, output: str) -> tuple[int, str]:
    """Находки отчёта gitleaks (JSON) только в `files`: нет — проверка зелёная; есть — вывод в
    виде `gitleaks -v` (поля без значения) для причины. Отчёт не прочитан — как было."""
    try:
        found = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return code, output
    if not isinstance(found, list):
        return code, output
    mine = [f for f in found if isinstance(f, dict) and f.get("File") in files]
    if not mine:
        return 0, ""
    lines = []
    for f in mine:
        lines += [
            f"RuleID: {f.get('RuleID', '?')}",
            f"File: {f.get('File')}",
            f"Line: {f.get('StartLine', '?')}",
            f"Fingerprint: {f.get('Fingerprint', '')}",
        ]
    return code, "\n".join(lines)


def hook(worktree: Path) -> int:
    """Стандартные проверки коммита: 0 — коммит идёт, 1 — остановлен; причина — в stderr."""
    failed = process_part(worktree)
    ticket_core = load("ticket_core.py")
    checks, broken = listed(worktree, ticket_core)
    if broken:
        return stop([*failed, f"список быстрых проверок не разобран: {broken}"])
    checks, problem = with_main_secrets(worktree, checks, ticket_core)
    if problem:
        failed.append(problem)
    merging = git(worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0
    if merging:
        files = conflict_files(worktree)
        if not files:
            checks = []  # слияние без конфликтов: код обеих сторон уже проверен
    else:
        staged = git(worktree, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        if staged.returncode != 0:
            return stop([*failed, f"git diff --cached: {staged.stderr.strip()}"])
        files = [f for f in staged.stdout.split("\0") if f]
    before = snapshot(worktree, files)
    for name, listed_command in checks or []:
        command = expand(listed_command, files)
        if command is None:
            continue
        if name == SECRETS:
            code, output = run_secrets(command, worktree, files, merging, ticket_core)
        else:
            code, output = run(command, worktree)
        after = snapshot(worktree, files)
        edited = edited_files(before, after)
        before = after  # снимок «после» — «до» следующей проверки
        if code != 0:
            failed.append(reason(name, command, code, output, ticket_core))
            if name == SECRETS and ticket_core.secret_findings(output):  # не сбой проверки
                failed.append(secrets_stop(worktree, ticket_core))
        elif edited:
            failed.append(
                f"проверка «{name}» ({' '.join(command)}) исправила файлы: {', '.join(edited)} — "
                "посмотри правку, добавь её в коммит (git add) и повтори коммит"
            )
    return stop(failed) if failed else 0


def secrets_stop(worktree: Path, ticket_core) -> str:
    """Что делать с красной проверкой секретов (#459, слабое место 7): не «почини», а вопрос
    владельцу отчётом об остановке маршрута."""
    branch = git(worktree, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    n = ticket_core.ticket_of(branch)
    then = (
        f"затем `{ticket_core.command('ship', n)}`: маршрут опубликует отчёт без отправки ветки"
        if n is not None
        else "затем передай вопрос владельцу"
    )
    return (
        "Секрет — не чинить и не глушить: не убирать строку ради проверки, не `.gitleaksignore`, "
        "не `gitleaks:allow`, не правка `.gitleaks.toml` или профиля, не `--no-verify`. Вопрос "
        "владельцу — ключ настоящий (отзывает владелец) или ложная тревога (правило в основной "
        f"ветке правит владелец) — в `.ticket/stop.md`, {then} (docs/secrets.md воркфлоу)."
    )


def stop(problems: list[str]) -> int:
    print(f"Коммит остановлен стандартным хуком воркфлоу ({PROFILE}):", file=sys.stderr)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1
