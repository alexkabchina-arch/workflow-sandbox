"""upkeep — обслуживание инструмента проверки раз в сутки (#353; спека #329, «Проверка запуском и
инструмент проверки»; решение 23.10 `v2-verify-tool-maintenance`).

Инструмент проверки и карта функций — критическая инфраструктура: устаревший инструмент молча
превращает `/verify` в «не проверить». Раз в сутки (первый такт местных суток не раньше
`HOUR` часов, паузы его держат) оркестратор проекта на пути v2 с блоком ```verify-tool в профиле
`main` запускает **обслуживание** — сессию в песочнице (`inside`, этот файл скриптом): worktree
от `origin/main` на ветке `canon/upkeep-<сутки>`, затем `claude -p` (роль `upkeep` таблицы
`v2/roles.py`) готовит окружение приложения по разделу «Окружение» профиля, прогоняет **всю
карту функций** командами инструмента и чинит только файлы инструмента и карты, с коммитом;
после неё — «доктор» из блока. Итог считает программа (`decide`), а не модель:

- **«заблокировано»** — модель так сказала, функция «не работает», правки не закоммичены,
  «поправлено» без коммитов, сбой среды или сессии — **тикет на владельца** (`BLOCKED_TICKET`):
  причина, номера и имена функций, без вывода команд (в нём бывают данные, #352);
- **«чисто»** — коммитов нет, все функции работают: строка такта;
- **«поправлено»** — коммиты есть: оркестратор забирает их бандлом (`check_secrets` — gitleaks
  профиля), **сверяет пути** на Mac (`outside`: файл не под путём `tool` или `map` блока
  ```verify-tool `main` своего клона — правка отвергается, тикет на владельца, ветка не
  уходит) и отправляет ветку токеном оркестратора.

**PR обслуживания — мимо одобрения `CODEOWNERS`, но не мимо ревью.** Инструмент и карта —
пути-ограничители (`CODEOWNERS` проекта): PR лица агента на них ждёт одобрения владельца. PR
обслуживания открывает `gh` **учёткой владельца на Mac** (без токена оркестратора, как
`@dependabot rebase`): GitHub не требует одобрения код-оунера от автора-код-оунера (проба Г2,
`docs/protect-main.md`). Набор правил и `CODEOWNERS` не меняются: PR тикетов (лицо агента) на
инструмент и карту по-прежнему ждут одобрения. Защиту даёт программа: в ветке только файлы
инструмента и карты (сверка — на каждом head), и ревью ошибок (`review`, вход — описание
обслуживания, обязательный вопрос об ослаблении защиты) на каждое новое содержимое инструмента
и карты (`content`: деревья путей на head) — догон основы ревью не повторяет:

- **ослабления нет** — полный CI зелёный, ветка свежая (догон — `gh pr update-branch`, раз на
  основу), очередь слияния дошла (место общее с тикетами и Dependabot, `v2/catchup.py`) —
  отметка `review` **токеном оркестратора** (лицо агента, `protect-main --v2`) и авто-мёрж
  `--match-head-commit`; сливает GitHub. Одобрения владельца нет;
- **ослабление или сбой ревью** — вопрос владельцу комментарием в PR (`ASK`): «да» — то же
  слияние, «нет» — PR не трогаю (закрывает владелец).

Красный CI, конфликт, правка вне каталога на новом head, авто-мёрж не слил за `MERGE_WAIT` —
тикет на владельца, раз на head; место слияния свободно. Пока PR обслуживания открыт, новое
обслуживание не начинается: открытый PR — его ещё не кончившиеся сутки.

Удалить: выключить суточный запуск (`upkeep()` и вызов в `tick` оркестратора), затем этот
файл; роль `upkeep` в `v2/roles.py`; PR обслуживания в `active` у `v2/catchup.py`.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

CLEAN, FIXED, BLOCKED = "чисто", "поправлено", "заблокировано"
WORKS, MENDED, BROKEN = "работает", "поправлено", "не работает"
ROLE = "upkeep"  # роль таблицы `v2/roles.py`; `claude_run.role` берёт её по имени скрипта
HOUR = 3  # суточный запуск — не раньше 03:00 местного времени: ночью сессий меньше
BRANCH = "canon/upkeep-{day}"
WORKTREE = Path(".claude/worktrees/upkeep")  # в клоне песочницы
OUT = Path(".git/canon-session/upkeep")  # итог сессии и бандл коммитов — в клоне песочницы
RESULT = "result.json"
SEND_REF = "refs/canon-upkeep"  # коммиты обслуживания в своём клоне — до отправки
PROFILE = "docs/agents/profile.md"
TOOL_BLOCK = re.compile(r"^```verify-tool[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
TOOL_ENV, APP_ENV = "CANON_VERIFY_TOOL", "CANON_VERIFY_APP"  # как у `/verify`
SHA = re.compile(r"[0-9a-f]{40}")
LIMIT = 10  # USD на сессию обслуживания
DOCTOR_TIMEOUT = 600
WAIT = 3 * 3600 + DOCTOR_TIMEOUT  # сессия и «доктор» после неё дольше — «заблокировано»
MERGE_WAIT = 3600  # авто-мёрж не слил после отметки — тикет на владельца
CATCH_WAIT = 1800  # догон на сервере не пришёл — PR встал
TRIES = 3  # запуск сессии или отправка ветки не удались столько раз подряд — «заблокировано»
PGID = "pgid"  # группа процессов сессии внутри песочницы — её снимает `kill_inside`
# Исход сессии — словом журнала пути v2 (`v2/journal.py`).
JOURNAL = {CLEAN: "ok", FIXED: "ok", BLOCKED: "error"}
TAIL = 300  # знаков причины в тикете
LIST = 30  # функций в тикете
FIELDS = (
    "number,url,state,headRefName,headRefOid,baseRefName,mergeable,mergeStateStatus,"
    "statusCheckRollup,autoMergeRequest,comments"
)
HOLDING = ("queued", "merge")  # PR обслуживания держит место слияния (`v2/catchup.py`)
DESCRIPTION = {
    False: "Обслуживание инструмента проверки: ревью без ослабления, полный CI зелёный",
    True: "Обслуживание инструмента проверки: ослабление — «да» владельца, полный CI зелёный",
}
SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {
            "type": "string",
            "enum": [CLEAN, FIXED, BLOCKED],
            "description": f"{CLEAN} — вся карта работает без правок; {FIXED} — инструмент или "
            f"карта поправлены и закоммичены; {BLOCKED} — функцию не починить правкой "
            "инструмента и карты (код продукта, среда, данные)",
        },
        "functions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "функция из карты"},
                    "outcome": {"type": "string", "enum": [WORKS, MENDED, BROKEN]},
                    "command": {"type": "string", "description": "команда инструмента"},
                    "note": {"type": "string", "description": "что поправлено или сломано"},
                },
                "required": ["name", "outcome", "command"],
                "additionalProperties": False,
            },
        },
        "why": {"type": "string", "description": f"при {BLOCKED} — что мешает, без данных"},
        "report": {"type": "string", "description": "отчёт в markdown: что прогнано и правлено"},
    },
    "required": ["outcome", "functions", "report"],
    "additionalProperties": False,
}
TASK = """Обслуживание инструмента проверки проекта — раз в сутки.
Инструмент `{tool}` и карта функций `{map}` — в рабочей копии (каталог ${tool_env}, он же
приложение ${app_env}), ветка `{branch}` от {base}.

0. Подготовь окружение приложения в рабочей копии — по разделу «Окружение» профиля
   `{profile}`; файлы окружения (`.venv` и т. п.) не коммитить и после прогона НЕ удалять — по ним
   программа после сессии гоняет «доктора». Затем {doctor}. Приложение не запускается и с
   окружением — сразу outcome {blocked}: инструмент и карту не чинить.
1. Прогони ВСЮ карту функций: каждую функцию — командами инструмента, как в карте; разрушающие —
   только пробным режимом инструмента.
2. Функция не работает из-за инструмента или карты (команда устарела, путь, формат вывода,
   функции нет в карте или она в карте лишняя) — поправь инструмент или карту и прогони её снова.
3. Править можно ТОЛЬКО файлы под `{tool}` и `{map}`: код продукта, тесты, CI, профиль и прочее —
   никогда; правка вне этих путей отвергается вся. Кроме окружения по профилю ничего не ставить;
   без фоновых средств харнеса; тестовые записи откатить; приложение и поднятые службы погасить
   после (файлы окружения оставить).
4. Правки — коммитом в эту ветку (`git commit`), без push и смены ветки.

Итог — структурированным ответом: outcome — {clean} (всё работает, правок нет), {fixed}
(поправлено и закоммичено) или {blocked} (функцию не починить правкой инструмента и карты: код
продукта, среда, данные — причина в why, без данных); functions — по каждой функции карты исход и
команда; report — что прогнано и что поправлено.
"""
BLOCKED_TICKET = """## Откуда

Суточное обслуживание инструмента проверки ({day}) — **заблокировано**: {why}
{functions}
## Что сделать

Пока инструмент или карта не в порядке, `/verify` тикетов будет давать «не проверить» по этим
функциям. Поломка в коде продукта или в среде — поручите починку тикетом; правка инструмента —
следующее обслуживание или тикет. Повтор — завтра сам. Подробности (слова сессии, вывод команд) —
в журнале такта и своём состоянии оркестратора на Mac: на GitHub они не уходят.

@{owner}"""
ASK = """{mark}
**Слить PR обслуживания инструмента проверки?**

Правки — только в инструменте и карте функций (`{tool}`, `{map}`). Полный CI на `{sha}` зелёный.
{review}

@{owner} Ответ — комментарием в этом PR:
- «да» — сольётся по очереди слияния;
- «нет» — PR останется как есть: оркестратор его не трогает, закройте его сами.
{report}"""
PR_BODY = """Суточное обслуживание инструмента проверки ({day}, #353): прогон всей карты функций,
правки только в `{tool}` и `{map}`.

{functions}

Сливается без одобрения владельца, если ревью ошибок не нашло ослабления защиты (решение 23.10
`v2-verify-tool-maintenance`); иначе — вопрос владельцу в этом PR."""
STUCK = {
    "red": "красный CI",
    "conflict": "PR в конфликте с `main`",
    "outside": "на head правки вне инструмента и карты",
    "unmerged": f"отметка и авто-мёрж стоят, а GitHub не слил PR за {MERGE_WAIT // 60} мин",
    "late": f"догон основы на сервере не пришёл за {CATCH_WAIT // 60} мин",
}


def load(name: str, rel: str):
    """Модуль канона рядом (`claude_run.py`) — без `sys.path` и `__pycache__`."""
    path = Path(__file__).resolve().parent.parent / rel
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def config(profile: str | None) -> dict | None:
    """Блок ```verify-tool профиля: `tool`, `map` и необязательный `doctor` (как у `verify`);
    нет блока или пути — None: проверять и обслуживать нечего."""
    found = TOOL_BLOCK.search(profile or "")
    if found is None:
        return None
    lines = {}
    for line in found.group(1).splitlines():
        key, _, value = line.strip().partition(" ")
        if key:
            lines[key] = value.strip()
    return lines if lines.get("tool") and lines.get("map") else None


def outside(paths: list[str], conf: dict) -> list[str]:
    """Файлы не под путём инструмента или карты — правка, которую обслуживание делать не вправе."""
    roots = [conf[k].strip("/") for k in ("tool", "map")]
    return [p for p in paths if not any(p == r or p.startswith(f"{r}/") for r in roots)]


def decide(answer: dict | None, commits: int, dirty: bool) -> tuple[str, str]:
    """Исход сессии — программой, а не словом модели: (исход, причина). Причина — текст
    программы: слова модели (`why`, заметки) на GitHub не уходят."""
    if not isinstance(answer, dict) or answer.get("outcome") not in (CLEAN, FIXED, BLOCKED):
        return BLOCKED, "ответ сессии обслуживания не по схеме"
    functions = [f for f in answer.get("functions") or [] if isinstance(f, dict)]
    broken = [f for f in functions if f.get("outcome") == BROKEN]
    if answer["outcome"] == BLOCKED:
        return BLOCKED, "сессия не смогла починить карту правкой инструмента и карты"
    if not functions:
        return BLOCKED, "сессия не назвала ни одной функции карты — прогон не доказан"
    if broken:
        names = ", ".join(str(f.get("name")) for f in broken)
        return BLOCKED, f"функции не работают: {names}"
    if dirty:
        return BLOCKED, "правки обслуживания не закоммичены"
    if not commits and any(f.get("outcome") == MENDED for f in functions):
        return BLOCKED, "функции «поправлено», а коммитов нет"
    if commits:
        return FIXED, ""
    if answer["outcome"] == FIXED:
        return BLOCKED, "сессия сказала «поправлено», а коммитов нет"
    return CLEAN, ""


def listing(functions) -> str:
    """Функции карты с исходом — строками; без команд, вывода и заметок модели: текст уходит
    на GitHub (#352)."""
    rows = [
        f"- {' '.join(str(f.get('name')).split())[:80]} — {f.get('outcome')}"
        for f in (functions or [])[:LIST]
        if isinstance(f, dict)
    ]
    more = len(functions or []) - LIST
    return "\n".join(rows + ([f"- … ещё {more}"] if more > 0 else []))


# --- внутри песочницы ---


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")


class Blocked(Exception):
    """«Заблокировано»: `why` — причина для GitHub (текст программы), `detail` — подробность
    только для Mac."""

    def __init__(self, why: str, detail: str = ""):
        super().__init__(why)
        self.why, self.detail = why, detail


def doctor(conf: dict, env: dict, tree: Path, base: str, root: Path) -> None:
    """«Доктор» блока после сессии — инструментом **из основы** (копия путей `tool` и `map` во
    временном каталоге, как у `/verify`), приложение — рабочая копия с окружением сессии: правка
    инструмента сессией не подменит проверку. Красный — «заблокировано»."""
    command = conf.get("doctor")
    if not command:
        return
    with tempfile.TemporaryDirectory(prefix="canon-upkeep-") as tmp:
        packed = subprocess.run(
            ["git", "archive", base, "--", conf["tool"], conf["map"]], cwd=root,
            capture_output=True,
        )  # fmt: skip
        unpacked = subprocess.run(
            ["tar", "-x", "-C", tmp], input=packed.stdout, capture_output=True
        )
        if packed.returncode != 0 or unpacked.returncode != 0:
            raise Blocked("инструмент из основы для «доктора» не скопирован")
        run_doctor(command, {**env, TOOL_ENV: tmp, APP_ENV: str(tree)}, Path(tmp))


def run_doctor(command: str, env: dict, where: Path) -> None:
    try:
        res = subprocess.run(
            ["sh", "-c", command], cwd=where, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=DOCTOR_TIMEOUT,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise Blocked(f"«доктор» после сессии не кончился за {DOCTOR_TIMEOUT} с") from None
    if res.returncode != 0:  # вывод «доктора» — только на Mac: в нём бывают данные
        text = (res.stderr.strip() or res.stdout.strip()).splitlines()
        tail = text[-1].strip()[-TAIL:] if text else "вывода нет"
        raise Blocked(f"«доктор» после сессии красный — код {res.returncode}", tail)


def inside(args, root: Path) -> dict:
    """Сессия обслуживания в клоне песочницы: worktree от основы, `claude -p` (окружение по
    профилю, прогон карты, правки), «доктор» и итог программой. Сбой любого шага —
    «заблокировано» с причиной; подробность (вывод, слова модели) — в `detail`: она остаётся на
    Mac."""
    result = {"run": args.run, "outcome": BLOCKED, "why": "", "head": None, "functions": []}
    try:
        if git("fetch", "-q", "origin", "main", cwd=root).returncode != 0:
            raise Blocked("`git fetch origin main` в песочнице не удался")
        base = git("rev-parse", args.base, cwd=root).stdout.strip()
        shown = git("show", f"{base}:{PROFILE}", cwd=root)
        conf = config(shown.stdout if shown.returncode == 0 else None)
        if conf is None:
            raise Blocked(f"в {PROFILE} основы нет блока verify-tool со строками tool и map")
        tree = root / WORKTREE
        git("worktree", "remove", "--force", str(tree), cwd=root)
        git("worktree", "prune", cwd=root)
        made = git("worktree", "add", "-q", "-B", args.branch, str(tree), base, cwd=root)
        if made.returncode != 0:
            raise Blocked("worktree обслуживания не создан", made.stderr.strip()[-TAIL:])
        claude_run = load("canon_claude_run_upkeep", "claude_run.py")
        env = {TOOL_ENV: str(tree), APP_ENV: str(tree)}
        os.environ[claude_run.roles.V2_ENV] = "1"  # модель и усилие — роль `upkeep`
        os.chdir(tree)
        # Worktree свежий: окружение приложения (`.venv` и т. п.) готовит сессия по профилю, как
        # сессия тикета; «доктор» — программой после неё (прогон #353: до сессии он красный всегда).
        text = TASK.format(
            tool=conf["tool"], map=conf["map"], tool_env=TOOL_ENV, app_env=APP_ENV,
            branch=args.branch, base=args.base, clean=CLEAN, fixed=FIXED, blocked=BLOCKED,
            profile=PROFILE,
            doctor=f"`{conf['doctor']}` — приложение запускается" if conf.get("doctor")
            else "проверь командой инструмента из карты, что приложение запускается",
        )  # fmt: skip
        report = root / OUT / "session.json"
        # Поиск в интернете закрыт, как всем сессиям пути v2 (`v2/task.py`, #340).
        denied = load("canon_v2_task_upkeep", "v2/task.py").DENIED
        try:
            data = claude_run.run_claude(
                [*denied, "--permission-mode", "auto", text], report, args.max_budget_usd,
                SCHEMA, env=env,
            )  # fmt: skip
        except claude_run.ChildError as exc:
            raise Blocked("сессия `claude` обслуживания не состоялась", str(exc)[-TAIL:]) from None
        result["cost"] = data.get("total_cost_usd")
        answer = data.get("structured_output")
        count = git("rev-list", "--count", f"{base}..HEAD", cwd=tree).stdout.strip()
        # Не закоммичено — правки и новые файлы под инструментом и картой; прочее (lock-файлы
        # окружения) в PR не уходит: уходят только коммиты, их пути сверяет Mac.
        new = git("status", "--porcelain", "--", conf["tool"], conf["map"], cwd=tree)
        dirty = bool(new.stdout.strip())
        result["outcome"], result["why"] = decide(answer, int(count or 0), dirty)
        if isinstance(answer, dict) and answer.get("why"):
            result["detail"] = str(answer["why"])[-TAIL:]
        result["functions"] = (answer or {}).get("functions") or []
        if result["outcome"] != BLOCKED:  # итог сессии «заблокировано» — его причина главная
            try:
                doctor(conf, claude_run.child_env(env), tree, base, root)
            except Blocked as exc:
                result["functions"] = []  # слова модели «работает» опровергнуты «доктором»
                if result["outcome"] == FIXED:
                    exc.why += " — правки сессии не отправлены"
                raise
        if result["outcome"] == FIXED:
            result["head"] = git("rev-parse", "HEAD", cwd=tree).stdout.strip()
    except Blocked as exc:
        result.update(outcome=BLOCKED, head=None, why=exc.why, detail=exc.detail)
    except Exception as exc:  # noqa: BLE001 — любой сбой — «заблокировано» с причиной
        why = f"сбой сессии обслуживания ({type(exc).__name__})"
        result.update(outcome=BLOCKED, head=None, why=why, detail=str(exc))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="upkeep", description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["inside"])
    parser.add_argument("--run", required=True, help="метка запуска")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--max-budget-usd", default=f"{LIMIT:.2f}")
    args = parser.parse_args(argv)
    sys.argv[0] = ROLE  # `claude_run.role` берёт роль по имени скрипта
    root = Path.cwd()  # клон песочницы: сессия уходит в worktree
    out = root / OUT
    out.mkdir(parents=True, exist_ok=True)
    try:  # своя группа процессов: Mac снимет её целиком (`kill_inside`), если сессия зависла
        os.setsid()
    except OSError:
        pass
    (out / PGID).write_text(str(os.getpgid(0)), encoding="utf-8")
    result = inside(args, root)
    (out / RESULT).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"upkeep: {result['outcome']} {result['why']}".rstrip())
    return 0


# --- на Mac ---


class Upkeep:
    """Суточное обслуживание инструмента проверки для оркестратора `orch`; `tools` — его
    помощники: `core`, `asks`, `send`, `start`, `journal`, `roles`, `ci_green`, `run`, `alive`,
    `started_at`, `kill_group`, `without_tokens`, `read_inside`, `local_now`, `say`, `stamp`,
    `seconds_since`, `tail`, `HERE`, `BROWSER_ENV`, `REVIEW_LIMIT`, `REVIEW_TIMEOUT`."""

    def __init__(self, orch, tools):
        self.orch, self.t = orch, tools

    @property
    def held(self) -> dict:
        return self.orch.data.setdefault("upkeep", {})

    def owner(self) -> str:
        return self.orch.repo.split("/")[0]

    def poll(self) -> list[str]:
        """Шаг обслуживания за такт: PR → отправка → сессия → суточный запуск. Сбой — строка
        такта, повтор — следующий такт."""
        e = self.held
        try:
            if e.get("pr"):
                self.step(e)
            elif e.get("ship"):
                self.ship(e)
            elif e.get("run"):
                self.running(e)
            elif self.due(e):
                self.start(e)
        except Exception as exc:  # noqa: BLE001 — сбой обслуживания не роняет такт тикетов
            self.t.say(f"обслуживание инструмента проверки: шаг не выполнен — {exc}")
            return [f"обслуживание инструмента проверки: {exc}"]
        return []

    def conf(self) -> dict | None:
        """Блок ```verify-tool профиля в `origin/main` своего клона."""
        self.orch.own_repo()
        shown = self.orch.git("show", f"origin/main:{PROFILE}")
        return config(shown.stdout if shown.returncode == 0 else None)

    # --- суточный запуск ---

    def due(self, e: dict) -> bool:
        """Пора: новые местные сутки, не раньше `HOUR`, без пауз и со свободным местом сессии
        (сессии тикетов и обслуживание делят песочницу); сутки засчитывает запуск."""
        now = self.t.local_now()
        day = now.date().isoformat()
        if e.get("day") == day or now.hour < HOUR:
            return False
        if pauses := self.orch.data.get("pauses"):
            reason = next(iter(pauses.values())).get("reason")
            if e.get("held") != day:
                self.t.say(f"обслуживание инструмента проверки ждёт конца паузы — {reason}")
                e["held"] = day
            return False
        if len(self.orch.running_runs()) >= self.orch.capacity():
            if e.get("held") != f"{day} места":
                self.t.say("обслуживание инструмента проверки ждёт свободного места сессии")
                e["held"] = f"{day} места"
            return False
        if self.conf() is None:
            e["day"] = day
            self.t.say("обслуживание инструмента проверки: в профиле `main` нет блока verify-tool")
            return False
        return True

    def start(self, e: dict) -> None:
        """Запуск сессии; сбой — повтор следующим тактом, `TRIES` подряд — «заблокировано» на эти
        сутки (тикет до записи суток: сбой тикета повторит его)."""
        try:
            self.begin(e)
        except Exception as exc:
            e["tries"] = e.get("tries", 0) + 1
            if e["tries"] < TRIES:
                raise
            self.blocked(e, f"сессия обслуживания не запущена {TRIES} раза подряд", str(exc))
            e["day"] = self.t.local_now().date().isoformat()
            e.pop("tries", None)

    def begin(self, e: dict) -> None:
        """Сессия обслуживания в песочнице — `sbx exec` этого файла, такт её не ждёт."""
        o, t = self.orch, self.t
        o.sandbox_entry()
        o.prepare()
        day, tag = self.t.local_now().date().isoformat(), os.urandom(8).hex()
        branch = BRANCH.format(day=day)
        logs = o.dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"upkeep-{day}-{tag[:8]}.log"
        env = o.session_env(reader=True)  # у сессии токен на чтение: ветку отправит оркестратор
        if browser := o.data.get("sandbox", {}).get("browser"):
            env[t.BROWSER_ENV] = browser[
                "image"
            ]  # UI-функции карты — драйвером в нём, как `/verify`
        if snapshot := o.snapshot():
            env[t.core.PROD_COPY] = snapshot  # время копии прода, как у сессий тикетов
        cmd = ["sbx", "exec", "-w", str(o.clone)]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        script = [str(t.HERE / "v2/upkeep.py"), "inside", "--run", tag, "--branch", branch]
        cmd += [o.box.name, "python3", *script, "--max-budget-usd", f"{LIMIT:.2f}"]
        with open(log, "w", encoding="utf-8") as out:
            proc = subprocess.Popen(
                cmd, cwd=o.dir, env=t.without_tokens(), stdin=subprocess.DEVNULL, stdout=out,
                stderr=subprocess.STDOUT, start_new_session=True,
            )  # fmt: skip
        o.entry["status"] = "running"
        e.update(day=day)
        e.pop("tries", None)
        e["run"] = {
            "run": tag, "pid": proc.pid, "pid_started": t.started_at(proc.pid),
            "log": str(log), "started": t.stamp(), "branch": branch,
        }  # fmt: skip
        o.save()
        t.say(f"обслуживание инструмента проверки: сессия запущена ({branch}, pid {proc.pid})")

    def running(self, e: dict) -> None:
        """Сессия идёт — ждём (до `WAIT`); кончилась — итог из клона песочницы."""
        r, t = e["run"], self.t
        outcome = None
        if t.alive(r):
            if t.seconds_since(r["started"]) <= WAIT:
                return
            self.kill(r)
            outcome = "timeout"
            result = {"outcome": BLOCKED, "why": f"сессия не кончилась за {WAIT // 3600} ч"}
        else:
            try:
                result = json.loads(t.read_inside(self.orch.clone, OUT / RESULT))
                if not isinstance(result, dict) or result.get("run") != r["run"]:
                    raise ValueError("итог — не этой сессии")  # итог прошлых суток не в счёт
            except (OSError, ValueError) as exc:
                result = {"outcome": BLOCKED, "why": "итога сессии нет", "detail": str(exc)}
                result["detail"] += f"; лог — {r['log']}"
        if not r.get("logged"):  # событие — раз, даже если тикет ниже не создался
            out = outcome or JOURNAL.get(result.get("outcome"), "error")
            self.log(ROLE, result.get("cost"), LIMIT, r["started"], out)
            r["logged"] = True
        e["last"] = {"day": e.get("day"), "outcome": result.get("outcome"), "at": t.stamp()}
        if result.get("outcome") == FIXED:
            e["ship"] = {**result, "branch": r["branch"]}
            e.pop("run")
            return self.ship(e)
        if result.get("outcome") == CLEAN:
            e.pop("run")
            return t.say("обслуживание инструмента проверки: чисто — вся карта работает")
        why = str(result.get("why") or "причина не названа")
        self.blocked(e, why, str(result.get("detail") or ""), result.get("functions"))
        e.pop("run")  # после тикета: сбой тикета повторит его следующим тактом

    def kill(self, r: dict) -> None:
        """Зависшая сессия: группа процессов внутри песочницы (номер — в клоне), затем её
        `sbx exec` на Mac."""
        try:
            pgid = int(self.t.read_inside(self.orch.clone, OUT / PGID).decode().strip())
        except (OSError, ValueError) as exc:
            self.t.say(f"обслуживание инструмента проверки: группа внутри не найдена — {exc}")
        else:
            self.orch.kill_inside(pgid)
        self.t.kill_group(r["pid"])

    def blocked(self, e: dict, why: str, detail: str = "", functions=None) -> None:
        """«Заблокировано» — тикет на владельца: причина программы и функции карты; подробность
        (`detail`: вывод, слова модели) — только в своём состоянии и журнале такта."""
        day = e.get("day") or self.t.local_now().date().isoformat()
        e["last"] = {**(e.get("last") or {}), "outcome": BLOCKED, "why": why[:TAIL]}
        if detail:
            e["last"]["detail"] = detail[-TAIL:]
            self.t.say(f"обслуживание инструмента проверки: подробность — {detail[-TAIL:]}")
        listed = listing(functions)
        body = BLOCKED_TICKET.format(
            day=day, why=why[:TAIL], owner=self.owner(),
            functions=f"\nФункции карты:\n\n{listed}\n" if listed else "",
        )  # fmt: skip
        e["last"]["ticket"] = self.issue(
            f"Инструмент проверки: обслуживание {day} — {BLOCKED}", body
        )
        self.t.say(f"обслуживание инструмента проверки: {BLOCKED} — {why[:TAIL]}; тикет владельцу")

    def issue(self, title: str, body: str) -> str:
        out = self.t.core.gh("issue", "create", "--title", title, "--body", body)
        found = re.search(r"https://\S+/issues/\d+", out)
        return found.group(0) if found else out.strip()[:200]

    # --- отправка и PR ---

    def ship(self, e: dict) -> None:
        """«Поправлено»: коммиты — бандлом в свой клон (gitleaks профиля), пути — на Mac, ветка —
        токеном оркестратора, PR — учёткой владельца."""
        o, s = self.orch, e["ship"]
        head, branch = str(s.get("head") or ""), s["branch"]
        if not SHA.fullmatch(head):
            return self.refuse(e, "head обслуживания — не коммит", repr(head))
        conf = self.conf()
        if conf is None:
            return self.refuse(e, "в профиле `main` больше нет блока verify-tool")
        place = types.SimpleNamespace(rel=OUT)
        if problem := o.check_secrets("upkeep", place, head, o.clone / WORKTREE, keep=SEND_REF):
            return self.refuse(e, "проверка `secrets` не пропустила коммиты обслуживания", problem)
        if wrong := outside(self.changed(head), conf):
            return self.refuse(e, f"правка вне инструмента и карты отвергнута: {', '.join(wrong)}")
        if problem := o.sender().push(head, branch):
            s["tries"] = s.get("tries", 0) + 1
            if s["tries"] >= TRIES:
                return self.refuse(e, f"ветка обслуживания не отправлена {TRIES} раза", problem)
            raise self.t.core.Failure(problem)  # повтор — следующий такт
        o.git("update-ref", "-d", SEND_REF)
        url = self.open_pr(e, branch, conf, s.get("functions"))
        e.pop("ship")
        e["pr"] = {"url": url, "head": head, "branch": branch}
        self.t.say(f"обслуживание инструмента проверки: {FIXED} — PR {url}")

    def refuse(self, e: dict, why: str, detail: str = "") -> None:
        """Ветка не уходит: «заблокировано» (тикет до снятия `ship`: сбой тикета повторит шаг)."""
        self.blocked(e, why, detail, e["ship"].get("functions"))
        self.orch.git("update-ref", "-d", SEND_REF)
        e.pop("ship")

    def changed(self, head: str) -> list[str]:
        res = self.orch.git("diff", "--name-only", "-z", f"origin/main...{head}")
        if res.returncode != 0:
            raise self.t.core.Failure(f"дифф обслуживания не прочитан: {self.t.tail(res.stderr)}")
        return [p for p in res.stdout.split("\0") if p]

    def open_pr(self, e: dict, branch: str, conf: dict, functions) -> str:
        """PR учёткой владельца (`gh` без токена оркестратора): автор — код-оунер, одобрения
        `CODEOWNERS` GitHub от него не ждёт. Уже открытый на ветке — он."""
        core, t = self.t.core, self.t
        if found := core.gh_json(
            "pr", "list", "--head", branch, "--state", "open", "--json", "url"
        ):
            return found[0]["url"]
        body = PR_BODY.format(
            day=e.get("day"), tool=conf["tool"], map=conf["map"], functions=listing(functions)
        )
        title = f"Обслуживание инструмента проверки {e.get('day')}"
        res = t.run(
            "gh", "pr", "create", "--head", branch, "--base", "main", "--title", title,
            "--body", body, cwd=self.orch.main, env=t.without_tokens(),
        )  # fmt: skip
        if res.returncode != 0 or not (m := re.search(r"https://\S+/pull/\d+", res.stdout)):
            raise core.Failure(f"PR обслуживания не открыт: {t.tail(res.stderr or res.stdout)}")
        return m.group(0)

    def step(self, e: dict) -> None:
        """PR обслуживания по шагам; первое невыполненное — его действие и конец шага."""
        core, p = self.t.core, e["pr"]
        pr = core.gh_json("pr", "view", p["url"], "--json", FIELDS) or {}
        p["number"] = pr.get("number") or p.get("number")
        if pr.get("state") in ("MERGED", "CLOSED"):
            return self.gone(e, pr)
        head = pr["headRefOid"]
        if p.get("declined") or (p.get("stuck") or {}).get("head") == head:
            return self.release(e)
        p.pop("stuck", None)
        self.orch.own_repo(refetch=True)
        conf = self.conf()
        if conf is None or outside(self.changed(head), conf):
            return self.stuck(e, pr, "outside")
        if red := self.t.start.failed_checks(pr):
            if p.get("red") != head:  # тикет — раз на head; зелёный перезапуск — PR идёт дальше
                self.stuck(e, pr, "red", red, park=False)
            return self.release(e)
        if not self.t.ci_green(pr):
            return
        key = self.content(head, conf)
        if (p.get("reviewed") or {}).get("key") != key:
            for stale in ("asked", "yes"):
                p.pop(stale, None)
            self.release(e)
            return self.review(e, pr, key)
        r = p["reviewed"]
        if (r.get("weakening") or r.get("failed")) and not p.get("yes"):
            return self.answer(e, pr, conf)
        number = int(p["number"])
        if why := self.orch.catchup().waits(number):
            if p.get("held_by") != why:
                self.t.say(f"PR обслуживания: слияние ждёт — {why}")
            p["held_by"] = why
            return
        p.pop("held_by", None)
        if self.t.start.is_conflicting(pr):
            return self.stuck(e, pr, "conflict")
        if not self.orch.ancestor("origin/main", head):
            return self.catch_up(e, pr)
        if p.get("phase") == "merge" and p.get("marked") == head:
            if not pr.get("autoMergeRequest"):
                p["declined"] = True  # владелец сам выключил авто-мёрж — PR придержан
                return self.release(e)
            if self.t.seconds_since(p.get("marked_at") or self.t.stamp()) > MERGE_WAIT:
                self.stuck(e, pr, "unmerged")
            return
        self.mark(e, head)

    def content(self, head: str, conf: dict) -> str:
        """Содержимое инструмента и карты на head — деревья их путей: ревью — на него, догон
        основы его не меняет."""
        trees = [
            self.orch.git("rev-parse", f"{head}:{conf[k]}").stdout.strip() for k in ("tool", "map")
        ]
        return ":".join(trees)

    def gone(self, e: dict, pr: dict) -> None:
        self.release(e)
        merged = pr.get("state") == "MERGED"
        if merged:
            for other in self.orch.data["tickets"].values():
                other["behind"] = True  # основа ушла у тикетов
        e["last"] = {**(e.get("last") or {}), "pr": e["pr"]["url"], "merged": merged}
        e.pop("pr")
        self.t.say(f"PR обслуживания: {'слит' if merged else 'закрыт без слияния'}")

    def stuck(self, e: dict, pr: dict, why: str, red=None, park: bool = True) -> None:
        """PR встал на этом head (`park`; иначе — красный CI, раз на head): авто-мёрж выключен,
        место свободно, тикет на владельца."""
        p, head = e["pr"], pr["headRefOid"]
        if pr.get("autoMergeRequest"):
            self.t.core.gh("pr", "merge", p["url"], "--disable-auto")
        self.release(e)
        listed = "".join(f"\n- {r}" for r in red or [])
        body = (
            f"## Откуда\n\nPR обслуживания инструмента проверки {p['url']} встал на "
            f"`{head[:7]}`: {STUCK[why]}.{listed}\n\n## Что сделать\n\nПосмотрите PR: поправьте "
            "или закройте его. Новый коммит в PR — оркестратор поведёт его снова; пока PR открыт, "
            f"новое обслуживание не начинается.\n\n@{self.owner()}"
        )
        self.issue(f"Инструмент проверки: PR обслуживания встал — {STUCK[why]}", body)
        if park:  # после тикета: сбой тикета повторит его следующим тактом
            p["stuck"] = {"head": head, "why": why}
        else:
            p["red"] = head
        self.t.say(f"PR обслуживания: встал — {STUCK[why]}; тикет владельцу")

    def review(self, e: dict, pr: dict, key: str) -> None:
        """Ревью ошибок PR обслуживания — на содержимое инструмента и карты; паузы его держат.
        Сбой — в вопросе владельцу, а не повтор за деньги."""
        o, t, p = self.orch, self.t, e["pr"]
        if o.data.get("pauses"):
            return
        head = pr["headRefOid"]
        reviews = o.dir / "reviews"
        reviews.mkdir(parents=True, exist_ok=True)
        ticket = reviews / "upkeep.issue.md"
        ticket.write_text(
            f"# Обслуживание инструмента проверки {e.get('day')}\n\n"
            "Суточное обслуживание правит только инструмент проверки и карту функций проекта. "
            "Главный вопрос — ослабляет ли PR защиту: инструмент перестаёт ловить поломки, "
            "команды проверки стали мягче, разрушающая команда без пробного режима, данные "
            "уходят наружу.\n",
            encoding="utf-8",
        )
        report = reviews / "upkeep.json"
        report.unlink(missing_ok=True)
        checkout = o.checkout("upkeep", 0, head)
        env = t.without_tokens()
        env["PATH"] = f"{o.claude_shim()}:{env.get('PATH', '')}"
        env[t.roles.V2_ENV] = "1"
        budget = f"{t.REVIEW_LIMIT:.2f}"
        argv = [sys.executable, str(t.HERE / "review"), str(report), "--max-budget-usd", budget]
        t.say(f"PR обслуживания: ревью ошибок head {head[:7]}")
        started = t.stamp()
        try:
            res = t.run(
                *argv, "--ticket", str(ticket), cwd=checkout, env=env, timeout=t.REVIEW_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            res = None
        finally:
            o.drop_checkout(checkout)
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        result = {"key": key, "head": head}
        canon = (data or {}).get("canon_review") if isinstance(data, dict) else None
        canon = canon if isinstance(canon, dict) else {}
        if res is None or res.returncode != 0 or not isinstance(data, dict):
            said = "не кончилось вовремя" if res is None else t.tail(res.stderr or res.stdout)
            result["failed"] = said or "отчёта нет"
        elif canon.get("skipped"):
            # Пути блока ```verify-tool основы `review` считает кодом (#414); пропуск — значит,
            # правило его не увидело, а решение 23.10 требует ревью с вопросом об ослаблении:
            # пропуск — как сбой ревью, вопрос владельцу.
            result["failed"] = (
                "ревью пропущено: в PR только документы — вопрос об ослаблении не задан"
            )
        else:
            cost = data.get("total_cost_usd")
            result["cost"] = cost if isinstance(cost, (int, float)) else 0
            result["weakening"] = canon.get("weakening") or None
            md = report.with_suffix(".md")
            result["text"] = md.read_text(encoding="utf-8") if md.exists() else ""
            result["findings"] = canon.get("findings") or 0
        p["reviewed"] = result
        outcome = "error" if "failed" in result else "ok"
        self.log("review", result.get("cost"), t.REVIEW_LIMIT, started, outcome, data)
        if result.get("findings") and not result.get("weakening"):
            # Находки без ослабления слияние не держат (следующее обслуживание прогонит карту
            # снова), но видны в PR.
            text = result["text"][:50000]
            body = f"Оркестратор: отчёт ревью ошибок PR обслуживания на `{head[:7]}`.\n\n{text}"
            self.t.core.gh("pr", "comment", p["url"], "--body", body)

    def answer(self, e: dict, pr: dict, conf: dict) -> None:
        """Ослабление или сбой ревью — «да» владельца: вопрос — раз на ревью, ответ — комментарием
        в PR."""
        core, asks, send, p = self.t.core, self.t.asks, self.t.send, e["pr"]
        if p.get("asked"):
            question = {"stamp": p["asked"], "url": "вопрос в PR обслуживания"}
            try:
                replies, _ = asks.answers(
                    core, question, pr.get("comments") or [], strict=True, strangers=False
                )
            except asks.NoQuestion:
                p.pop("asked")  # вопрос удалён — задать снова
            else:
                decided = [d for d in (send.Send.decide(send.MERGE, r) for r in replies) if d]
                if decided and decided[-1] == "allow":
                    p["yes"] = True
                    self.t.say("PR обслуживания: «да» владельца — в очередь слияния")
                elif decided:
                    p["declined"] = True
                    text = "Оркестратор: «нет» записано — PR не трогаю, закройте его сами."
                    core.gh("pr", "comment", p["url"], "--body", text)
                    self.t.say("PR обслуживания: «нет» владельца")
                return
        stamp, r = self.t.stamp(), p["reviewed"]
        if r.get("failed"):
            review = f"Ревью ошибок не состоялось: {r['failed'][:TAIL]}."
            report = ""
        else:
            review = (
                f"**Ревью ошибок ({r.get('cost', 0):.2f} USD) нашло ослабление защиты:** "
                f"{str(r['weakening'])[:TAIL]}."
            )
            report = (
                f"\n<details><summary>Отчёт ревью</summary>\n\n{r.get('text', '')[:50000]}\n\n"
                "</details>\n"
            )
        body = ASK.format(
            mark=core.ASK_MARK.format(stamp=stamp), tool=conf["tool"], map=conf["map"],
            sha=pr["headRefOid"][:7], review=review, owner=self.owner(), report=report,
        )  # fmt: skip
        core.gh("pr", "comment", p["url"], "--body", body)
        p["asked"] = stamp
        self.t.say("PR обслуживания: ревью нашло ослабление — вопрос «слить?» владельцу в PR")

    def catch_up(self, e: dict, pr: dict) -> None:
        """Ветка отстала — догон на сервере (`update-branch` с `expected_head_sha`), раз на
        основу; место — за PR; новый head не пришёл за `CATCH_WAIT` — PR встал."""
        p, head = e["pr"], pr["headRefOid"]
        base = self.orch.git("rev-parse", "origin/main").stdout.strip()
        self.hold(e)
        caught = p.get("caught") or {}
        if caught.get("base") == base and caught.get("head") == head:
            if self.t.seconds_since(caught["at"]) > CATCH_WAIT:
                self.stuck(e, pr, "late")
            return
        self.t.core.gh(
            "api", "-X", "PUT", f"repos/{self.orch.repo}/pulls/{p['number']}/update-branch",
            "-f", f"expected_head_sha={head}",
        )  # fmt: skip
        p["caught"] = {"base": base, "head": head, "at": self.t.stamp()}
        self.t.say(f"PR обслуживания: догон основы {base[:7]} на сервере")

    def hold(self, e: dict) -> None:
        p = e["pr"]
        if p.get("phase") not in HOLDING:
            p["phase"] = "queued"
        self.orch.catchup().marked(int(p["number"]))

    def mark(self, e: dict, head: str) -> None:
        """Отметка `review` токеном оркестратора (лицо агента) на head и авто-мёрж этого head."""
        core, p = self.t.core, e["pr"]
        asked = bool(p["reviewed"].get("weakening") or p["reviewed"].get("failed"))
        self.hold(e)
        core.gh(
            "api", "-X", "POST", f"repos/{self.orch.repo}/statuses/{head}",
            "-f", "state=success", "-f", f"context={self.t.start.REVIEW}",
            "-f", f"description={DESCRIPTION[asked]}",
        )  # fmt: skip
        core.gh("pr", "merge", p["url"], "--auto", "--squash", "--match-head-commit", head)
        p.update(phase="merge", marked=head, marked_at=self.t.stamp())
        self.t.say(f"PR обслуживания: отметка review и авто-мёрж на {head[:7]}")

    def release(self, e: dict) -> None:
        """Место слияния — свободно."""
        p = e.get("pr") or {}
        if p.get("phase") in HOLDING:
            p["phase"] = None
            for stale in ("marked", "marked_at"):
                p.pop(stale, None)
        if p.get("number"):
            self.orch.catchup().release(int(p["number"]))

    def log(self, role: str, usd, budget: float, started: str, outcome: str, data=None) -> None:
        """Запуск роли — событием в журнал пути v2 (`upkeep` — без номера тикета); без итога
        расход — оценка пределом."""
        t, known = self.t, isinstance(usd, (int, float))
        try:
            t.journal.write(
                self.orch.dir,
                {
                    "at": t.stamp(),
                    **t.journal.event(
                        t.roles, 0, role, data, t.seconds_since(started), upkeep=True,
                        started=started, ended=t.stamp(), usd=usd if known else budget,
                        estimated=not known, outcome=outcome,
                    ),
                },
            )  # fmt: skip
        except OSError as exc:
            t.say(f"обслуживание инструмента проверки: журнал v2 не записан — {exc}")


if __name__ == "__main__":
    sys.exit(main())
