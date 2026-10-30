"""ticket ship — конец работы над тикетом: от проверки ветки до PR с отметкой ревью и авто-мёржем.

    scripts/canon/ticket ship <N> [--again]

Из worktree тикета (ветка `ticket-N` или `ticket-N-…`), когда работа закоммичена. Шаги по
порядку; механику делает скрипт, на шаге с суждением печатает задание и выходит («ход агента»):

| Шаг | Скрипт | Агент (файл в `.ticket/`) |
|---|---|---|
| ревью ошибок | `review`, отчёт рядом с состоянием; «только документы» — без разбора | — |
| разбор | находки и правило разбора | решения, исправления (`findings.md`) |
| проверка разбора | все разобраны; тикеты техдолга и канона — сам | ответ владельца (`answer.md`) |
| тесты и линтеры | блок `checks` профиля через `run-log` | починка (`checks.md`) |
| `/verify` | критерии из тикета и его комментариев, `verify`, итог | FAIL — починить |
| догон базы | `git merge origin/main` | конфликт — разрешить (`sync.md`) |
| тело PR | факты: Closes, тесты, ревью, `/verify`, догон | «Что сделано» (`summary.md`) |
| финиш | `ship-finish`: push, PR, авто-мёрж, отметка ревью | привязка PR, Auto-fix, `ticket wait` |
| остановка | отчёт из данных, `ready-for-human` | вопрос и варианты (`stop.md`) |

Пределы — два круга исправлений каждого вида (красные проверки, FAIL `/verify`, догон), третий —
остановка. Новые коммиты после финиша (красный CI, конфликт) — тот же вызов: проверки, догон и
отметка на новый head без второго ревью; PR уже слит — маршрут с нуля (новая работа, новый PR).
Под оркестратором (`CANON_ORCHESTRATOR=1`, #214) ревью ошибок сессия не гоняет: без отчёта —
«код готов» (head в состоянии, шаг `ready`), отчёт кладёт оркестратор; финиш — `ship-finish
--pr-only` (push и PR без авто-мёржа и отметки) и снова «код готов». Отметку ставит оркестратор.
Время снимка копии прода в песочнице (#307) — в задании `/verify` и в теле PR («проверено на
копии прода от …»); снимок старше суток при критериях «на проде» — до `/verify` задание
`ticket fresh N` (#301). Пункты «после выката» (#301) — не критерий PR: `/verify` их не проверяет,
тело PR несёт их списком «После выката».
`--again` — продолжить после остановки (владелец ответил «продолжай»): метка `ready-for-human`
снимается, остановка после финиша — снова финиш; после остановки на `/verify` решение владельца
«verify — не нужен» (его комментарий в тикете или PR после отчёта; не `.ticket/reply.md` — его
пишет сама сессия, #249) снимает признак — финиш без `/verify` (#236). Как устроено и почему —
`docs/ticket.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIMIT = 2
GATES_RED = 3  # код `ship-finish`: красные местные ворота воркфлоу без CI (#328)
STOP_WIDTH = 300  # символов в строке ошибки отчёта об остановке
# Строка ошибки в выводе проверки: в отчёт об остановке на GitHub — только первая такая строка, не
# хвост вывода: тест, печатающий окружение или DSN, выложил бы их в комментарий (#275). Слова —
# целиком (`SENTRY_ERROR_DSN`, `FAILOVER_…` — не ошибка), исключения Python — по суффиксу.
ERROR_LINE = re.compile(
    r"\b(?:error|errors|fail|failed|failure|failures|traceback|ошибк\w*)\b|\w(?:Error|Exception)\b",
    re.IGNORECASE,
)
# Строка-итог упавшего теста (`FAILED x::y - …` pytest, `FAIL: test_x` unittest) — лучше любой
# другой: в ней имя теста и причина.
ERROR_SUMMARY = re.compile(r"^(?:FAILED|ERROR|FAIL:|ERROR:)\s+\S")
# Не строка ошибки: присваивание окружения (`ИМЯ=значение`) и баннер (`==== FAILURES ====`).
NOT_ERROR = re.compile(r"^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=|^[=\-_*#\s]+\w[\w ]*[=\-_*#\s]+$")
RUN_LOG_END = re.compile(r"^run-log: завершено с кодом \S+ · лог: ")
# Пароль в URL строки ошибки (`postgresql://u:pass@db`, `redis://:pass@host`) — маской до
# отчёта на GitHub (#283): схема, логин и хост остаются, пароль — `***`.
# Пароль — до последнего `@` в слове: в нём бывают `/` и `@`; лишнее под маской не вредит.
URL_PASSWORD = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]*:)\S+(@)")
TRACE = "verify: /verify оставил в рабочем дереве"  # след /verify; при коде 1 — в stderr
# `/verify` вернул BLOCKED (#292): строка `verify` в stderr, отчёт — `verify.raw.md`. Причина —
# из строки вердикта отчёта («**Verdict:** BLOCKED. …»; после BLOCKED пусто — следующая строка),
# иначе первая строка не-заголовок. Значения присваиваний (`ИМЯ=значение`) — маской, как
# пароль в URL: строка уходит на GitHub (#275).
BLOCKED_MARK = "/verify не смог проверить (BLOCKED)"
BLOCKED_REASON = re.compile(r"\bBLOCKED\b[\s*_.:;,—–-]*(.*)")
MARKDOWN = re.compile(r"^(?:[#>\s]+|[-*+]\s+|\d+[.)]\s+)+|\*\*")
ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*=)\S+")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CANON_REPO = "alexkabchina-arch/workflow"
LABEL = "ready-for-human"
PROFILE = Path("docs/agents/profile.md")
TRIAGE = Path("docs/agents/triage-labels.md")
KINDS = ("исправлено", "отклонено", "техдолг", "не записано", "канон", "вопрос")
NEEDS_TEXT = {"отклонено", "техдолг", "канон", "вопрос"}
DASH = r"\s*(?:[—–:-]+\s*)?"
DECISION = re.compile(rf"^(\d+)[.)]\s*({'|'.join(KINDS)}){DASH}(.*)$", re.I)
WEAKENING = re.compile(rf"^ослабление{DASH}(.+)$", re.I)
VERIFY = re.compile(rf"^/?verify{DASH}(не нужен|нужен){DASH}(.*)$", re.I)
# Решение владельца после остановки на `/verify` (#236): строка «verify — не нужен: почему» или
# «verify — нужен» в его комментарии тикета или PR. Ответ из чата (`.ticket/reply.md`) — не
# решение: файл пишет сама сессия (#249). Цитата (`>`) — не решение: владелец цитирует отчёт;
# строка с вопросом («verify не нужен ли?») — тоже.
DECIDED = re.compile(
    r"^[ \t*_]*/?verify[ \t—–:-]*(не нужен|нужен)\b[*_]*[ \t—–:,.-]*([^\n]*)$", re.I | re.M
)
# Чем продолжить после строки о `/verify` (#289): под оркестратором продолжает только метка
# `agent:go` владельца, «продолжай» — ответ в чат ручной сессии.
WAIVE_HINT = (
    "Проверка не нужна — строка «verify — не нужен: почему» комментарием в тикете (или под этим "
    "отчётом), затем {then}: маршрут пойдёт на финиш без /verify."
)


def waive_hint(core) -> str:
    return WAIVE_HINT.format(then="метка `agent:go`" if core.orchestrated() else "«продолжай»")


def with_mention(text: str, owner: str, need: str = f"метка `{LABEL}`") -> str:
    """Текст с упоминанием владельца в конце (`need` — что его ждёт), парная копия
    `with_mention` в `ship-finish`; владелец не известен или уже упомянут — как есть."""
    mentioned = re.compile(rf"(?<![\w@.-])@{re.escape(owner)}(?![\w-])", re.I)
    if not owner or mentioned.search(text):
        return text
    return f"{text.rstrip()}\n\n@{owner}, нужен человек: {need}."


ITEM = re.compile(r"^(\d+)\. (.*)$")
# Комментарии тикета в критериях `/verify` (#178): пометки маршрута (`ticket ask`) и подпись
# вопроса — служебное, отчёт об остановке — не критерий. Предел — `docs/ticket.md`, раздел
# `/verify`: критерии уходят в задание `claude` одним аргументом командной строки.
OWN_MARK = re.compile(r"<!--\s*canon-ticket[^>]*-->")
ASK_FOOTER = re.compile(r"\n-{3,}\s*\n(?:@\S+\s+)?Ответ — комментарием в этом тикете.*\Z", re.S)
REPORT = re.compile(r"^[#*\s]*Отчёт об остановке")
# Под оркестратором живого прода нет (#307, решение 22.10): критерии «на проде» — на обезличенной
# копии в песочнице; время её снимка — в задании `/verify` и свидетельствах тикета.
PROD_COPY_CRITERIA = (
    "Живого прода нет: данные прода — обезличенная копия прода в песочнице, снимок от {copy}. "
    "Критерии «на проде» проверяй на ней; в отчёте — строка «проверено на копии прода от {copy}»."
)
# Снимок копии старше суток, а в критериях «на проде» (#301): до `/verify` — задание запросить
# свежую (`ticket fresh`), а не остановка; раз на маршрут. Порог — как `COPY_STALE` оркестратора:
# сутки с часом запаса на суточное обновление шаблона, иначе каждая сессия к концу суток гоняла бы
# принудительный снимок прода.
COPY_AGE = timedelta(hours=25)
STALE_COPY = (
    "Копия прода в песочнице — снимок от {copy}, старше суток, а в критериях тикета — «на "
    "проде». До /verify запроси свежую копию вызовом ({long}) ниже и выполни его задание: "
    "оркестратор снимет её на Mac и ответит в том же вызове. Затем снова этот маршрут: /verify "
    "пойдёт на копии, какая будет; старая — с пометкой в свидетельствах, второй раз маршрут "
    "свежую не попросит."
)
STALE_NOTE = "снимок старше суток — свежей копии не дали"
# Критерии «после выката» (#301, решение 22.10): проверить их до слияния нельзя — не критерий
# PR. Формат — блок брифа `.claude/canon/ticket-ownership.md`: только строки-галочки (проза брифа
# и комментариев — не пункт); пункт «после выката» — галочка, начатая с «После выката:» или «На
# проде после выката:», галочка под заголовком «После выката» или часть галочки со слов «на проде
# после выката» до конца строки. Жирный шрифт не мешает, отметка галочки сохраняется.
CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX])\]\s+(.*?)\s*$")
HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)[\s#:]*$")
MARKUP = re.compile(r"\*\*|__")
AFTER_HEADING = re.compile(r"(?:на\s+проде\s+)?после\s+выката", re.I)
WHOLE = re.compile(r"(?:на\s+проде\s+)?после\s+выката\s*:", re.I)
INSIDE = re.compile(r"на\s+проде\s+после\s+выката", re.I)
ON_PROD = re.compile(r"на\s+проде", re.I)
AFTER_DEPLOY_CRITERIA = (
    "Пункты «после выката» — не критерий этого PR: их проверяет владелец после выката, маршрут "
    "переносит их в тело PR списком «После выката». Не проверяй их и не ставь из-за них FAIL "
    "или BLOCKED:\n{items}"
)
COMMENT_LIMIT = 4_000
COMMENT_HEAD = 1_000  # длинный комментарий — начало и конец: критерии брифа стоят в конце
COMMENTS_LIMIT = 20_000
CUT = "[обрезано — полный текст в тикете]"
STRANGER_HEAD = (
    "Заголовок и тело тикета не включены: автор тикета — не участник репозитория и может "
    "переписать их в любой момент. Критерии — комментарии участников ниже."
)

RULE = """Правило разбора — по каждой находке одно решение (сверить находку с кодом):
- исправлено — дефект вносит ветка: в изменённых строках или ветка делает его достижимым (новый
  вызов, новые входные данные). Чинить в этой ветке, отдельным коммитом; порога нет.
- отклонено — находка неверна: почему, одной строкой.
- вопрос — находка показывает, что вред, который чинит тикет, остался на другом пути (даже в
  старом коде). Это не техдолг: вопрос владельцу до финиша — доделать в этой ветке или отдельным
  тикетом.
- техдолг — посторонний дефект вне диффа, был до ветки, со сценарием вреда (неверный результат,
  потеря данных, красный шаг процесса, неверная команда агенту), прослеженным чтением или
  запуском. В ветке не чинить; тикет заведёт скрипт — сценарий и как прослежен.
- канон — такой же дефект в вендоренном файле канона (перечислен в `.canon.lock`): в проекте не
  правится, тикет в трекер канона заведёт скрипт.
- не записано — всё прочее: стиль, упрощения, дубли, «если когда-нибудь», непрослеженный
  сценарий. Никуда не пишется, в тело PR — только число.
Инструкция агенту (CLAUDE.md, скиллы, профиль, подсказки скриптов) — код: устаревшая, по которой
агент выполнит неверную команду, — дефект. Правку сверх тикета молча не делать: вопрос
владельцу или отступление в «Что сделано»."""

FORMAT = """Ответ — файл `.ticket/findings.md` (инструмент Write): строка на находку, строка verify:
    1. исправлено — что сделано
    2. отклонено — почему
    3. техдолг — сценарий вреда; как прослежен
    4. не записано
    verify — нужен: что проверить   (или: verify — не нужен: почему)
`/verify` нужен, когда ветка меняет наблюдаемое поведение: интерфейс, ответ API, записываемые
данные; иначе — не нужен с причиной («правка только документации»). Веб-интерфейс проверяется
только с рецептом из раздела профиля «Рецепт запуска для `/verify`» (файл
`.claude/skills/run-<проект>/SKILL.md` в git): ветка меняет веб-интерфейс, а рецепта нет —
вопрос в `.ticket/stop.md`, рецепт заводит владелец. Исправления — коммитами (сообщение одной
строкой `-m`)."""

WEAKENING_TASK = """Ревью нашло ослабление защиты (проверок, прав агента, правил или обезличивания):
{text}
Решение — строкой `ослабление — …` в том же файле: «запрошено тикетом: <цитата из тикета>»,
«убрано коммитом <sha>: …» (коммит после ревью) или «ослабления нет: <почему>». Убрать нельзя, не
сломав тикет, — вопрос владельцу в `.ticket/stop.md`."""

QUESTION_TASK = """Находка показывает, что вред, который чинит тикет, остался на другом пути. Это не
техдолг, а вопрос владельцу до финиша: после финиша PR сольётся без ответа.

{questions}

Спроси владельца в чате на языке поведения продукта: что увидит пользователь или что станет с
данными при каждом варианте, без имён функций и файлов. Варианты — доделать в этой ветке или
отдельным тикетом; рекомендация — твоя. Дождись ответа. Собеседника нет (дочерний `claude -p`) —
вопрос и варианты в `.ticket/stop.md`.
После ответа: ответ владельца дословно — в `.ticket/answer.md`; решение по находке в
`.ticket/findings.md` — `исправлено` (доделано в ветке, коммит) или `техдолг` (отдельный тикет
заведёт скрипт)."""

CHECKS_TASK = """Реши, чья вина, и почини — без вопроса владельцу:
- ветка: падает изменённый код или неизменённый, который его вызывает, — починить, коммит;
- окружение: тот же тест красный на origin/main, сбой соединения, порта, контейнера, базы, чужой
  файл вне ветки — в ветке не чинить. Неотслеживаемый файл не из ветки перенести в scratchpad
  сессии (`mv`, не `rm` — его блокирует классификатор), локальный кэш очистить, окружение своей
  сессии поправить; общее окружение и чужие сессии не трогать. Причину и что сделано — строкой в
  `.ticket/checks.md` (уйдёт в тело PR);
- линтер или формат красный только на файлах вне диффа (обновился инструмент) — не чинить:
  вопрос и варианты в `.ticket/stop.md`.
Попытка {attempt} из {limit}; дальше — отчёт об остановке. Полный вывод — в логе из строки
`run-log: завершено …`; длинный лог читать через docs-scout, не целиком."""

CONFLICT_TASK = """Ветка догоняет origin/main мёржем (не rebase: отчёт ревью должен остаться
предком HEAD) — конфликт в файлах: {files}.
Разреши, сохранив смысл обеих правок — и ветки, и main. Каждый участок читать с обеих сторон,
что и зачем меняла сторона — по её коммитам (`git log -p --merge -- <файл>`). Сторону целиком
брать, только если она уже содержит правку другой. Мелкие добавления в общий документ (строка
глоссария, строка карты README) — сохраняются обе.
{vendored}
Смысловой конфликт — две ветки по-разному меняют одну логику (правило, порог, поведение), и
сохранить обе нельзя: сам не выбирай — `git merge --abort`, вопрос владельцу (что меняла каждая
сторона, что увидит пользователь при каждом варианте, рекомендация) — в `.ticket/stop.md`.
Разрешил — `git add` этих файлов, `git commit --no-edit`; файлы и как сведены обе правки —
строкой в `.ticket/sync.md` (уйдёт в тело PR). Затем вызов: тесты пойдут на слитом коде."""

VENDORED = """Вендоренный канон (файлы из `.canon.lock` и сам `.canon.lock`) — сторона main
(`git checkout --theirs -- <файл>`): обычная ветка их не правит. Lock-файл зависимостей — сторона
main, затем пересборка командой проекта, которая его пишет."""

VENDORED_INSTALL = """Ветка ставит или обновляет канон (в её диффе `.canon.lock`): вендоренные
файлы и `.canon.lock` руками не сводить и сторону main не брать — это откатило бы обновление.
Взять сторону ветки (`git checkout --ours -- <файл>`), довести слияние коммитом и заново выполнить
`canon-sync` той же версии из клона канона на этом worktree — он пересоберёт `.claude/settings.json`
и сверит суммы; затем коммит. Клона канона нет — вопрос в `.ticket/stop.md`."""

SUMMARY_TASK = """Напиши «Что сделано» в `.ticket/summary.md`: 2–5 строк для владельца-неинженера —
что меняется в поведении продукта и зачем; отступления от тикета (правки сверх него) — здесь же.
Ветка меняет проверки (тесты, CI, ревью, хуки), права агента, правила (CLAUDE.md, скиллы,
профиль) или обезличивание — первая строка «Меняются правила процесса: <что и зачем>».
Closes, тесты, ревью, `/verify` и догон базы скрипт допишет сам."""

# Строки следа `verify` (`moved`): историю ветки сменил сам `/verify` — по `TRACE_TASK` вопрос.
REWRITTEN = ("история ветки переписана", "ветка сменилась")

TRACE_TASK = """Разобрать каждый пункт: рецепт `.claude/skills/run-*` — откатить (отслеживаемое —
`git checkout -- <путь>`, новое — удалить): итог проверки не засчитан, /verify пройдёт заново;
`.claude/skills/verify/` — удалить; тестовые файлы — откатить; коммиты /verify поверх ветки —
разобрать так же, лишнее откатить коммитом; «ветка сменилась» или «история переписана» — ничего
не откатывать, вопрос в `.ticket/stop.md`."""

BIND = """Привязка PR к сессии: в приложении Claude (инструменты ccd_pr; отложенные — загрузить
поиском инструментов) — get_status; этот PR не привязан — bind_pr со ссылкой выше, без вопроса
владельцу. Ошибка bind_pr — не стоп: в итоге «PR не привязан». В терминале — пропуск.
Затем Auto-fix (решение владельца 13.10, прогон B0): set_monitor с url PR, auto_fix и
address_comments true — без вопроса; событие CI разбудит сессию на красном CI или конфликте. PR не
привязан или в терминале — пропуск: красный CI и конфликт найдёт ожидание мёржа."""

WAIT = """Затем ожидание мёржа и уборка — сессия ждёт сама, до 20 минут ({long}); итог ожидания
передать владельцу вместе с итогом выше:"""


def load_review() -> types.ModuleType:
    """`review` рядом со скриптом — его `is_docs` и `needs_review`: пропуск тем же правилом."""
    path = Path(__file__).resolve().parent / "review"
    module = types.ModuleType("canon_review_for_ticket")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def first_error(text: str) -> str:
    """Строка ошибки вывода без цвета, обрезанная: первая строка-итог упавшего теста, иначе
    первая строка со словом ошибки (`ERROR_LINE`), кроме присваиваний и баннеров; нет — пустая.
    Пароль в URL — маской (`URL_PASSWORD`)."""
    found = []
    for raw in text.splitlines():
        line = URL_PASSWORD.sub(r"\1***\2", ANSI.sub("", raw).strip())
        if not line or RUN_LOG_END.match(line) or NOT_ERROR.match(line):
            continue
        if ERROR_SUMMARY.match(line):
            return line[:STOP_WIDTH]
        if ERROR_LINE.search(line):
            found.append(line)
    return found[0][:STOP_WIDTH] if found else ""


def failure(text: str, log, place: str = "на машине сессии") -> str:
    """Сбой команды для отчёта на GitHub (#275): первая строка ошибки и где полный вывод — сам
    вывод остаётся в логе `place` (под оркестратором сессия идёт в песочнице)."""
    error = first_error(text)
    said = f"первая строка ошибки: {error}" if error else "строки с ошибкой в выводе нет"
    where = f"лог `{log}` {place}" if log else "лога нет"
    return f"{said}; полный вывод — {where}"


def blocked_reason(report: str) -> str:
    """Причина BLOCKED из отчёта `/verify` для отчёта об остановке (#292): текст после BLOCKED в
    строке вердикта (после BLOCKED пусто — следующая строка не-заголовок), иначе первая строка не
    заголовок и не вердикт; без разметки, пароль в URL и значения присваиваний — маской, обрезано.
    Нечего взять — пустая."""
    lines = [MARKDOWN.sub("", ANSI.sub("", raw).strip()).strip() for raw in report.splitlines()]
    texts = [
        x for raw, x in zip(report.splitlines(), lines) if x and not raw.lstrip().startswith("#")
    ]
    found, after = "", False
    for line in texts:
        m = BLOCKED_REASON.search(line)
        if after and not m:
            found = line
            break
        if m:
            found = m.group(1).strip()
            if found:
                break
            after = True  # причина — ниже строки вердикта
    if not found and not after:
        found = next((x for x in texts if not BLOCKED_REASON.search(x)), "")
    found = ASSIGNMENT.sub(r"\1***", URL_PASSWORD.sub(r"\1***\2", found))
    return found[:STOP_WIDTH]


def parse_decisions(text: str, count: int, weakening_needed: bool):
    """Решения по находкам из `findings.md`: ({номер: (вид, текст)}, verify, ослабление, ошибки)."""
    decisions: dict[int, tuple[str, str]] = {}
    verify = weakening = None
    errors: list[str] = []
    last = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := DECISION.match(line):
            number, kind = int(m.group(1)), m.group(2).lower()
            if not 1 <= number <= count:
                errors.append(f"находка {number}: такой нет (находок {count})")
            elif number in decisions:
                errors.append(f"находка {number}: два решения")
            decisions[number] = (kind, m.group(3).strip())
            last = number
        elif m := VERIFY.match(line):
            verify = {"needed": m.group(1).lower() == "нужен", "why": m.group(2).strip()}
            last = None
        elif m := WEAKENING.match(line):
            weakening = m.group(1).strip()
            last = None
        elif last is not None:  # продолжение текста решения
            kind, said = decisions[last]
            decisions[last] = (kind, f"{said} {line}".strip())
    for number in range(1, count + 1):
        if number not in decisions:
            errors.append(f"находка {number}: нет решения")
        elif decisions[number][0] in NEEDS_TEXT and not decisions[number][1]:
            errors.append(f"находка {number}: «{decisions[number][0]}» — без пояснения")
    if verify is None:
        errors.append("нет строки «verify — нужен: …» или «verify — не нужен: почему»")
    elif not verify["needed"] and not verify["why"]:
        errors.append("verify — не нужен: без причины")
    if weakening_needed and not weakening:
        errors.append("нет строки «ослабление — …» с решением по ослаблению защиты")
    return decisions, verify, weakening, errors


def review_items(md: str) -> dict[int, str]:
    """Находки из `review.md` по номерам (с их строкой «Сценарий»)."""
    items: dict[int, str] = {}
    number = None
    for line in md.splitlines():
        if m := ITEM.match(line):
            number = int(m.group(1))
            items[number] = m.group(2)
        elif number is not None and line.startswith("   ") and line.strip():
            items[number] += "\n   " + line.strip()
        elif not line.strip() and number is not None:
            continue
        else:
            number = None
    return items


def criteria_items(text: str) -> list[tuple[bool, str, bool]]:
    """Галочки в тексте тикета: (отмечена, текст без жирного, под заголовком «После выката»)."""
    items, section = [], False
    for line in text.splitlines():
        if heading := HEADING.match(line):
            section = bool(AFTER_HEADING.fullmatch(MARKUP.sub("", heading.group(1)).strip()))
        elif box := CHECKBOX.match(line):
            items.append((box.group(1) != " ", MARKUP.sub("", box.group(2)).strip(), section))
    return items


def after_deploy(text: str) -> list[tuple[bool, str]]:
    """Пункты «после выката» (#301): (отмечен, текст) без повторов."""
    found: list[tuple[bool, str]] = []
    for done, item, section in criteria_items(text):
        if section or WHOLE.match(item):
            part = item
        elif inside := INSIDE.search(item):
            part = item[inside.start() :]
        else:
            continue
        part = part[:1].upper() + part[1:]
        if part and part not in (known for _, known in found):
            found.append((done, part))
    return found


def on_prod(text: str) -> bool:
    """В галочках есть «на проде» до реализации или на копии — не в пункте «после выката»."""
    return any(
        ON_PROD.search(INSIDE.split(item, 1)[0])
        for _, item, section in criteria_items(text)
        if not section and not WHOLE.match(item)
    )


def snapshot_time(copy: str) -> datetime | None:
    """Время снимка копии прода — как `snapshot_time` оркестратора: `Z` и пояс `+07`, `+0700` —
    как `+07:00`, доли секунды отбрасываются (Python до 3.11 их не разбирает), без пояса —
    местное; не разобрать — None."""
    text = re.sub(r"(:\d{2})\.\d+", r"\1", copy.strip().replace(" ", "T", 1))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif found := re.search(r"T.*?([+-]\d{2})(\d{2})?$", text):
        text = f"{text[: found.start(1)]}{found.group(1)}:{found.group(2) or '00'}"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when.astimezone() if when.tzinfo is None else when


def stale(copy: str | None) -> bool:
    """Снимок копии прода старше `COPY_AGE`; время не разобрать — не старше (не просим зря)."""
    when = snapshot_time(copy) if copy else None
    return when is not None and datetime.now(timezone.utc) - when > COPY_AGE


def comments_text(comments: list[dict], trusted, member=None) -> tuple[str, int]:
    """Комментарии тикета для критериев `/verify`: решения, вопросы и ответы — по порядку, с датой.

    Скрытые в GitHub (Hide — Outdated и т. п.) не идут: владелец их отозвал. Длинный комментарий
    — начало и конец, середина вырезана; сверх общего предела отбрасываются ранние: позднее
    решение владельца отменяет раннее, поэтому свежее важнее. Комментарии не участников
    репозитория не идут; второе в ответе — их число (без скрытых и пустых)."""
    kept, strangers = [], 0
    for c in comments:
        body = (c.get("body") or "").strip()
        if not body or c.get("isMinimized"):
            continue
        if not trusted(c):
            strangers += 1
            continue
        if member is not None and not member(c):  # App — сессия и оркестратор, не владелец
            continue
        if REPORT.match(body):
            continue
        body = ASK_FOOTER.sub("", OWN_MARK.sub("", body)).strip()
        if not body:
            continue
        if len(body) > COMMENT_LIMIT:
            tail_from = len(body) - (COMMENT_LIMIT - COMMENT_HEAD)
            body = f"{body[:COMMENT_HEAD].rstrip()}\n{CUT}\n{body[tail_from:].lstrip()}"
        when = (c.get("createdAt") or "")[:16].replace("T", " ")
        kept.append(f"### {when} UTC\n\n{body}" if when else body)
    total, start = 0, len(kept)
    while start and total + len(kept[start - 1]) + 2 <= COMMENTS_LIMIT:
        start -= 1
        total += len(kept[start]) + 2
    head = [f"{CUT}: ранние комментарии ({start}) не включены."] if start else []
    return "\n\n".join(head + kept[start:]), strangers


def triage_label(root: Path) -> str:
    """Метка роли `needs-triage` в трекере проекта (`docs/agents/triage-labels.md`)."""
    try:
        text = (root / TRIAGE).read_text(encoding="utf-8")
    except OSError:
        return "needs-triage"
    for line in text.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == "needs-triage" and cells[1]:
            return cells[1]
    return "needs-triage"


def last_decision(text: str) -> tuple[bool, str] | None:
    """Последнее решение о `/verify` в тексте: (снят ли, причина); строки с «?» — не решение."""
    found = [m for m in DECIDED.finditer(text) if "?" not in m.group(0)]
    if not found:
        return None
    return found[-1].group(1).lower() == "не нужен", found[-1].group(2).strip()


def rollup_cost(path: Path) -> float:
    try:
        cost = json.loads(path.read_text(encoding="utf-8")).get("total_cost_usd")
    except (OSError, ValueError, AttributeError):
        return 0.0
    return float(cost) if isinstance(cost, (int, float)) else 0.0


class Route:
    def __init__(self, number: int, core):
        self.n, self.core = number, core
        self.cmd = core.command("ship", number)
        self.state = core.State(number)

    # --- вывод ---

    def turn(self, title: str, body: str, *, long: str = ""):
        hint = f"Затем вызов ({self.core.LONG_CALL}"
        hint += f"; {long})" if long else ")"
        return (self.core.TURN, title, f"{body}\n\n{hint}:", (self.cmd,))

    # --- вход ---

    def run(self, again: bool) -> int:
        core, st = self.core, self.state
        here = core.branch()
        if core.ticket_of(here) != self.n:
            return self.elsewhere(here)
        self.root = core.toplevel()
        core.answers(self.root)
        st["branch"] = here
        if st.step is None:
            st.go("review")
        if again and st.step == "stopped":
            st["attempts"], st["wait"] = {}, None  # владелец ответил — круги и ожидания заново
            step = (st["stop"] or {}).get("step") or "review"
            # Отчёт об остановке после финиша перевёл открытый PR в черновик и выключил авто-мёрж
            # — снова финиш, как после остановки `ticket wait`: `ship-finish` вернёт то и другое
            # (#190, п. 1). Слит (владелец слил черновик сам) — шаг «готово» как есть: итог или,
            # если в ветке новые коммиты, маршрут с нуля. Только здесь: на шаге «готово»
            # черновик без «продолжай» — придержанный владельцем PR, финиш снял бы придержку.
            # Под оркестратором аналог «готово» — «код готов» после PR (#214): финиш `--pr-only`.
            after_pr = step == "ready" and (st["ready"] or {}).get("reviewed")
            if (step == "done" or after_pr) and not self.pr_merged():
                step = "finish"
            if self.stopped_on_verify() and (out := self.waive_verify()) is not None:
                return core.emit(*out)  # шаг «остановка» как был: повтор `--again` пойдёт дальше
            st.go(step)
            st["stop"] = None
            self.unmark()
        for _ in range(30):
            out = self.advance()
            st.save()
            if out is not None:
                return core.emit(*out)
        raise core.Failure("маршрут не сошёлся за 30 переходов")

    def stopped_on_verify(self) -> bool:
        """Остановка на `/verify`: на его шаге (BLOCKED, FAIL сверх предела, нет критериев) или
        сессией на починке после его FAIL («починить нельзя» — `stop.md` на шаге проверок)."""
        stop, decision = self.state["stop"] or {}, self.state["verify"] or {}
        failed = stop.get("step") == "checks" and decision.get("verdict") == "FAIL"
        return stop.get("step") == "verify" or failed

    def waive_verify(self):
        """Решение владельца «verify — не нужен» после остановки на `/verify` (#236): признак
        «нужен» из разбора снимается законным путём. Голос владельца — только его комментарий
        после отчёта об остановке в тикете или открытом PR ветки (участник, не App; не скрытый,
        не служебный маршрута). Ответ из чата в `.ticket/reply.md` — не голос владельца: файл
        пишет сама сессия, и headless-сессия после FAIL сняла бы себе `/verify` (#249). Последнее
        слово решает: позднее «verify — нужен» отменяет раннее «не нужен». Пересказ сессией (её
        файлы разбора, отчёта, «Что сделано») признак не снимает. Снят — до конца маршрута: и
        после новых коммитов, и при новых критериях. Кортеж — задание: в ручном режиме лежит
        ответ из чата, а решения в комментариях нет — молча гнать платный `/verify` нельзя,
        владелец мог думать, что снял проверку; файл уходит в состояние, повтор идёт дальше."""
        core, st = self.core, self.state
        said = sorted(self.after_report(), key=lambda c: c.get("createdAt") or "")
        found = None
        for c in said:
            body = (c.get("body") or "").strip()
            if c.get("isMinimized") or not core.member(c) or not core.is_answer(body):
                continue
            if m := last_decision(body):
                found = {"url": c.get("url"), "why": m[1]} if m[0] else None
        if found:
            found["url"] = found["url"] or f"тикет #{self.n}"
            st["verify_off"] = found
            st.save()
        path = self.root / core.ANSWERS / "reply.md"
        if core.orchestrated() or not core.read_answer(self.root, "reply.md"):
            return None
        core.keep(st, path, f"reply-{core.now()}.md")
        if found:
            return None
        body = (
            "В `.ticket/reply.md` ответ из чата: решение о /verify маршрут из чата не принимает — "
            "файл пишет сама сессия (#249); файл убран в состояние маршрута. Владелец снял "
            "проверку — попроси его написать строку «verify — не нужен: почему» комментарием в "
            "тикете; сама сессия эту строку в тикет не пишет. Велит проверять или комментарий "
            "уже есть — вызов:"
        )
        return (core.TURN, "Ответ из чата не принимается", body, (f"{self.cmd} --again",))

    def after_report(self) -> list[dict]:
        """Комментарии тикета и открытого PR ветки после последнего отчёта об остановке в каждом:
        граница — сам отчёт, не часы машины. Где отчёта нет — оттуда ничего. Отчёт — только от
        своих (`trusted`), как у Stop-хука и `session-start`: «Отчёт об остановке» постороннего
        сдвинул бы границу за решение владельца (#249). Скрытый свой отчёт — тоже граница:
        владелец прячет отработанный отчёт, а остановка от этого не раньше. Сбой сверки автора
        — `Failure`, код 1 без перехода: «не свой» молча пустил бы платный `/verify`."""
        core, n = self.core, str(self.n)
        fields = ["--state", "open", "--json", "number"]
        prs = core.gh_json("pr", "list", "--head", self.state["branch"], *fields) or []
        sources = [("issue", n)] + [("pr", str(pr["number"])) for pr in prs[:1]]
        found = []
        for kind, ref in sources:
            said = (core.gh_json(kind, "view", ref, "--json", "comments") or {}).get("comments")
            said = said or []
            at = next((i for i in reversed(range(len(said))) if self.own_report(said[i])), None)
            if at is not None:
                found += said[at + 1 :]
        return found

    def own_report(self, comment: dict) -> bool:
        """Комментарий — отчёт об остановке от своих: участника или App."""
        if not REPORT.match(comment.get("body") or ""):
            return False
        return self.core.trusted(comment, strict=True)

    def unmark(self) -> None:
        """Метка остановки — с тикета и открытого PR ветки: владелец ответил «продолжай», ждать
        больше некого, как после ответа на `ticket ask` (#190). На PR метка остаётся, если на нём
        и признак перерасхода ревью или `/verify` (комментарий `ready-for-human: …` финиша): это
        свой сигнал владельцу, а финиш второй раз его не ставит. Сигнал — только от своих
        (участник или App, `trusted`) и не скрытый, как фильтры #196: посторонний «ready-for-human:
        …» метку не удержит (#204, п. 7). Сбой `gh` — не стоп: строкой в выводе."""
        core, n = self.core, str(self.n)
        if core.v2_task():  # токен сессии v2 — только чтение: метки снимает оркестратор (#341)
            return

        def labelled(item: dict) -> bool:
            return any(label.get("name") == LABEL for label in item.get("labels") or [])

        try:
            if labelled(core.gh_json("issue", "view", n, "--json", "labels") or {}):
                core.gh("issue", "edit", n, "--remove-label", LABEL)
            fields = ["--state", "open", "--json", "number,labels"]
            prs = core.gh_json("pr", "list", "--head", self.state["branch"], *fields) or []
            for pr in [pr for pr in prs[:1] if labelled(pr)]:
                number = str(pr["number"])
                said = core.gh_json("pr", "view", number, "--json", "comments") or {}
                # Автор — после пометки (REST у App), сбой сверки — `Failure`: метка остаётся.
                signals = [
                    c
                    for c in said.get("comments") or []
                    if (c.get("body") or "").startswith(f"{LABEL}: ") and not c.get("isMinimized")
                ]
                if not any(core.trusted(c, strict=True) for c in signals):
                    core.gh("pr", "edit", number, "--remove-label", LABEL)
        except core.Failure as exc:
            core.say(f"ticket: метку {LABEL} снять не удалось — {exc}; сними руками")

    def elsewhere(self, here: str | None) -> int:
        path = self.core.worktree_of(self.n)
        where = self.core.toplevel()
        if path is None:
            body = (
                f"Скрипт запущен из {where} на ветке {here or '(detached)'}, а worktree тикета "
                f"#{self.n} нет: сначала старт сессии тикета (`session-start {self.n}`)."
            )
            return self.core.emit(self.core.TURN, "Нет worktree тикета", body)
        body = (
            f"Скрипт запущен из {where} на ветке {here or '(detached)'}: здесь правила и код "
            f"могут отставать от ветки тикета. Маршрут тикета #{self.n} идёт из его worktree — "
            f"войди в него (`EnterWorktree` с path {path}; в терминале — открой сессию там) и "
            "вызови оттуда:"
        )
        return self.core.emit(self.core.TURN, "Запуск не из worktree тикета", body, (self.cmd,))

    def advance(self):
        """Один переход: None — шаг пройден, дальше; кортеж — вывод и выход."""
        core, st = self.core, self.state
        if (question := st["question"]) and core.orchestrated():
            body = (
                f"Вопрос владельцу ждёт ответа: {question['url']}. Чата нет: ответ владельца в "
                "тикете забирает вызов; ответа нет — он велит закончить сессию:"
            )
            ask = core.command("ask", self.n)
            return (core.TURN, f"Тикет #{self.n}: вопрос владельцу", body, (ask,))
        if question := st["question"]:  # вопрос `ticket ask` ждёт ответа — маршрут стоит
            body = (
                f"{core.pending(question)}Маршрут не идёт до ответа. "
                "Владелец ответил и написал «продолжай» — ответ из чата дословно в "
                "`.ticket/reply.md`, затем вызов:"
            )
            st["waiting"] = core.OWNER
            ask = core.command("ask", self.n)
            return (core.STOPPED, f"Тикет #{self.n}: ждём ответа", body, (ask,))
        if st.step == "stopped":
            where = (st["stop"] or {}).get("where", "?")
            body = (
                f"Отчёт об остановке — {where}. Ждём владельца.{self.waive_task()} Владелец "
                "ответил «продолжай» — продолжить маршрут:"
            )
            return (core.STOPPED, f"Тикет #{self.n}: остановка", body, (f"{self.cmd} --again",))
        if st.step != "stop" and core.read_answer(self.root, "stop.md"):
            name = self.core.STEP_NAMES.get(st.step, st.step)
            return self.stop(f"сессия остановила маршрут на шаге «{name}»")
        if st.step == "done":
            if st["finished_head"] == core.head():
                return self.result()
            st["attempts"] = {}  # новый круг: красный CI или конфликт после финиша
            if self.pr_merged():
                # PR слит, а в ветке новые коммиты — новая работа: маршрут с нуля, а отчёт ревью
                # покрывает только код слитого PR (#190, п. 5).
                st.retire(self.root)
                st.go("review")
                return None
            st.go("checks")  # новые коммиты после финиша: проверки и отметка на новый head
            return None
        if st.step not in ("stop", "sync") and self.dirty():
            return self.turn(
                "Незакоммиченные правки",
                f"В worktree незакоммиченные правки отслеживаемых файлов:\n{self.dirty()}\n"
                "Закоммить (сообщение одной строкой `-m`) или откати.",
            )
        return getattr(self, f"step_{st.step}")()

    def dirty(self) -> str:
        return self.core.git("status", "--porcelain", "--untracked-files=no")

    # --- ревью ошибок ---

    def reviewed(self) -> dict | None:
        """`canon_review` отчёта, если он годен для этой ветки (иначе ревью заново)."""
        core = self.core
        try:
            data = json.loads((self.state.dir / "review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        verdict = data.get("canon_review") if isinstance(data, dict) else None
        if not isinstance(verdict, dict) or not all(
            isinstance(verdict.get(k), str) for k in ("base_sha", "head")
        ):
            return None
        fresh = core.git_ok("merge-base", "--is-ancestor", verdict["base_sha"], "origin/main")
        mine = core.git_ok("merge-base", "--is-ancestor", verdict["head"], "HEAD")
        if verdict.get("skipped") is True and fresh and mine:
            # Пропуск годен, пока в ветке только документы и чистая установка канона (#309) —
            # тем же правилом `needs_review`, что выдал пропуск и что перепроверит `ship-finish`.
            files = core.git("diff", "--name-only", "--no-renames", "origin/main...HEAD")
            base = core.git("merge-base", "origin/main", "HEAD").strip()
            code, _, _ = load_review().needs_review(files.splitlines(), base, core.head())
            mine = not code
        return verdict if fresh and mine else None

    def pr_state(self) -> str | None:
        if not self.state["pr"]:
            return None
        data = self.core.gh_json("pr", "view", self.state["pr"], "--json", "state") or {}
        return data.get("state")

    def pr_merged(self) -> bool:
        return self.pr_state() == "MERGED"

    def closed_pr(self) -> str | None:
        """PR маршрута закрыт без мёржа — причина остановки, иначе None (#204, п. 4). Финиш
        (`find_pr` ищет только открытые) создал бы новый PR с авто-мёржем вопреки владельцу —
        после «продолжай» и после новых коммитов. Отчёт — в тикет: `ship-finish --unconfirmed`
        без открытого PR пишет туда и PR не создаёт."""
        if self.pr_state() != "CLOSED":
            return None
        fresh = "`agent:retry`" if self.core.orchestrated() else f"`session-run {self.n} --discard`"
        return (
            f"PR {self.state['pr']} закрыт владельцем без слияния — продолжать нечего: новый PR "
            f"только по решению владельца — {fresh} (маршрут с чистого листа)"
        )

    def step_review(self):
        core, st = self.core, self.state
        verdict = self.reviewed()
        if verdict is None and core.orchestrated():
            core.profile_checks(self.root / PROFILE)  # профиль без проверок — до оплаты ревью
            return self.ready(reviewed=False)  # ревью ошибок гоняет оркестратор вне сессии
        if verdict is not None and core.orchestrated():
            if (st["review"] or {}).get("head") != verdict["head"]:  # новый отчёт оркестратора
                for old in ("findings.md", "answer.md"):
                    (self.root / core.ANSWERS / old).unlink(missing_ok=True)
                st["asked"] = st["decisions"] = None
                st["cost"]["review"] = verdict.get("cost_usd") or 0
        if verdict is None:
            core.profile_checks(self.root / PROFILE)  # профиль без проверок — до оплаты ревью
            st.dir.mkdir(parents=True, exist_ok=True)
            for old in ("findings.md", "answer.md"):  # ответы прошлого ревью к новому не относятся
                (self.root / core.ANSWERS / old).unlink(missing_ok=True)
            st["asked"] = st["decisions"] = None
            core.say("ticket: ревью ошибок — обычно 5–8 минут, ждите")
            res = core.canon("review", str(st.dir / "review.json"))
            core.say(res.stdout.rstrip())
            if res.returncode != 0:
                log = self.keep_output("review", res.stdout + res.stderr)
                said = failure(res.stderr + res.stdout, log)
                return self.stop(f"ревью ошибок не состоялось (код {res.returncode}): {said}")
            verdict = self.reviewed()
            if verdict is None:
                return self.stop("ревью ошибок не оставило годного отчёта")
            lines = res.stdout.splitlines()
            st["review_warnings"] = [x for x in lines if x.startswith("review: предупреждение")]
            spent = st["cost"].get("review") or 0  # прошлые прогоны оплачены — не теряются
            st["cost"]["review"] = spent + (verdict.get("cost_usd") or 0)
        st["review"] = {
            "head": verdict["head"],
            "skipped": verdict.get("skipped") is True,
            "install": (verdict.get("install") or {}).get("version"),
            "findings": verdict.get("findings") or 0,
            "weakening": verdict.get("weakening") or "",
        }
        if st["review"]["skipped"]:
            why = "чистая установка канона" if st["review"]["install"] else "только документы"
            st["verify"] = {"needed": False, "why": why}
            st.go("checks")
            return None
        st.go("findings")
        return self.findings_task()

    # --- разбор ---

    def findings_task(self, errors: list[str] | None = None):
        review = self.state["review"]
        md = (self.state.dir / "review.md").read_text(encoding="utf-8").strip()
        cost = self.state["cost"].get("review") or 0
        parts = []
        if errors:
            parts.append("Ответ не принят:\n" + "\n".join(f"- {e}" for e in errors))
        parts.append(f"Ревью ошибок ({cost:.2f} USD), находки — {self.state.dir / 'review.md'}:")
        parts.append(md)
        if review["weakening"]:
            parts.append(WEAKENING_TASK.format(text=review["weakening"]))
        parts += [RULE, FORMAT]
        return self.turn("Разбор находок ревью", "\n\n".join(parts), long="тесты — до 45 минут")

    def step_findings(self):
        core, st = self.core, self.state
        text = core.read_answer(self.root, "findings.md")
        if not text:
            return self.findings_task()
        review = st["review"]
        decisions, verify, weakening, errors = parse_decisions(
            text, review["findings"], bool(review["weakening"])
        )
        if errors:
            return self.findings_task(errors)
        questions = {n: said for n, (kind, said) in decisions.items() if kind == "вопрос"}
        if questions:
            asked = st["asked"] or {}
            asked.update({str(n): said for n, said in questions.items()})
            st["asked"] = asked
            st.go("findings", core.OWNER)
            listed = "\n".join(f"- находка {n}: {said}" for n, said in sorted(questions.items()))
            return self.turn("Вопрос владельцу до финиша", QUESTION_TASK.format(questions=listed))
        if st["asked"] and not core.read_answer(self.root, "answer.md"):
            st.go("findings", core.OWNER)
            listed = "\n".join(f"- находка {n}: {said}" for n, said in st["asked"].items())
            return self.turn(
                "Ответ владельца не записан",
                f"Был вопрос владельцу:\n{listed}\nЕго ответ дословно — в `.ticket/answer.md`.",
            )
        st["decisions"] = {str(n): list(d) for n, d in sorted(decisions.items())}
        st["verify"], st["weakening"] = verify, weakening
        try:
            self.file_issues(decisions)
        except core.Failure as exc:
            return self.stop(f"тикет по находкам не заведён: {exc}")
        st.go("checks")
        return None

    def file_issues(self, decisions: dict[int, tuple[str, str]]) -> None:
        items = review_items((self.state.dir / "review.md").read_text(encoding="utf-8"))
        debt = [n for n, (kind, _) in sorted(decisions.items()) if kind == "техдолг"]
        canon = [n for n, (kind, _) in sorted(decisions.items()) if kind == "канон"]
        if debt:
            label = triage_label(self.root)
            self.issue("techdebt", debt, decisions, items, f"Техдолг после #{self.n}", label)
        if canon:
            repo = self.core.gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
            title = f"Находки ревью {repo}#{self.n}"
            self.issue("canon_issue", canon, decisions, items, title, "needs-triage", CANON_REPO)

    def issue(self, key, numbers, decisions, items, title, label, repo=None) -> None:
        """Тикет по находкам: новый или комментарий в уже заведённый этой веткой."""
        core, st = self.core, self.state
        record = st[key] or {"url": None, "numbers": []}
        new = [n for n in numbers if n not in record["numbers"]]
        if not new:
            return
        lines = [
            f"Находки ревью ошибок ветки `{st['branch']}` (тикет #{self.n}) со сценарием вреда — "
            "в ветке не исправлялись:"
            if record["url"] is None
            else f"{title} — ещё:",
            "",
        ]
        for n in new:
            lines.append(f"{n}. {items.get(n, '(текст находки — в отчёте ревью)')}")
            lines.append(f"   Решение сессии: {decisions[n][1]}")
        body = "\n".join(lines)
        if core.v2_task():
            # #341: токен сессии v2 — только чтение: находки без блокировки не тикетом, а
            # строками карточки PR — их пишет оркестратор из состояния (`held`).
            record["held"] = [*(record.get("held") or []), body]
            record["numbers"] = record["numbers"] + new
            st[key] = record
            st.save()
            return
        # Строки владений (#289): по ним оркестратор пустит тикет параллельно; флаг — в триаже.
        # Тикет «канон» — в трекере канона: управляемые файлы там лежат под `project/`.
        paths = self.finding_paths(items, new, "project/" if repo else "")
        if record["url"]:
            # Второй пакет — и в строку владений тела (оркестратор читает тело), если она есть:
            # нет — первый пакет путей не дал, тикет идёт один. До комментария: правка
            # повторима, а сбой после комментария повторил бы его.
            if paths:
                found = core.gh_json("issue", "view", record["url"], "--json", "body") or {}
                old = found.get("body") or ""
                if core.OWNS_LINE.search(old) and (merged := core.add_owns(old, paths)) != old:
                    core.gh("issue", "edit", record["url"], "--body", merged)
            core.gh("issue", "comment", record["url"], "--body", body)
        else:
            if paths:
                body = core.add_owns(body, paths)
            area = re.search(r"`([^`:]+)", items.get(new[0], "")) or None
            full = f"{title}: {area.group(1)}" if area else title
            args = ["issue", "create", "--title", full, "--label", label, "--body", body]
            record["url"] = core.gh(*args, *(["--repo", repo] if repo else [])).splitlines()[-1]
        record["numbers"] = record["numbers"] + new
        st[key] = record
        st.save()

    def finding_paths(self, items: dict[int, str], numbers: list[int], prefix: str) -> list[str]:
        """Пути находок для строки «Владения»: находка начинается с `` `путь:строка` `` — номер
        строки отбрасывается, каталог — с `/`, `prefix` — перед путём. Путь хоть одной находки не
        разобрать (маска, `..`, нет пути) — пусто: тикет пойдёт один, а не уже, чем правит."""
        paths = []
        for n in numbers:
            found = re.match(r"`([^`]+)`", items.get(n, ""))
            raw = re.sub(r":[\d\-–,]*$", "", found.group(1)) if found else ""
            path = self.core.claim_path(raw) if raw else None
            if path is None:
                return []
            if not path.endswith("/") and (self.root / path).is_dir():
                path += "/"
            if prefix + path not in paths:
                paths.append(prefix + path)
        return paths

    # --- тесты и линтеры ---

    def step_checks(self):
        core, st = self.core, self.state
        if self.reviewed() is None:  # код после пропуска «только документы», ветка переписана
            st.go("review")
            return None
        current = core.head()
        green = st["checks"]
        if green and green["head"] == current:
            st.go("verify")
            return None
        docs_since = bool(green) and self.docs_only_since(green["head"])
        results = []
        for name, argv in core.profile_checks(self.root / PROFILE):
            if docs_since and name == "tests":
                note = f"не повторялся: после `{green['head'][:7]}` — только документы"
                results.append({"name": name, "code": 0, "note": note})
                continue
            core.say(f"ticket: {name} …")
            if name == "secrets":  # помощник — копия рядом, а не файл ветки (#283)
                argv = core.canon_secrets(argv, core.HERE)
            if Path(argv[0]).name != "run-log":
                argv = [sys.executable, str(core.HERE / "run-log"), "-l", name, "--", *argv]
            res = core.run(*argv, cwd=self.root)
            log = re.search(r"лог: (\S+)", res.stdout)
            output = res.stdout + res.stderr
            if res.returncode != 0 and log:  # строка ошибки — из полного лога, не из хвоста
                try:
                    output = Path(log.group(1)).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
            # Без значений секретов gitleaks (#258): строка ошибки уходит в отчёт на GitHub.
            hide = core.without_secret_fields
            results.append(
                {
                    "name": name,
                    "code": res.returncode,
                    "log": log.group(1) if log else None,
                    "tail": tail(hide(res.stdout + res.stderr), 30),
                    "error": first_error(hide(output)),
                }
            )
        st["checks_run"] = {"head": current, "results": [self.brief(r) for r in results]}
        red = [r for r in results if r["code"] != 0]
        if not red:
            st["checks"] = st["checks_run"]
            st["attempts"].pop("checks", None)
            st.go("verify")
            return None
        attempt = st.attempt("checks")
        names = ", ".join(f"`{r['name']}` — код {r['code']}" for r in red)
        if attempt > LIMIT:
            return self.stop(f"тесты и линтеры красные {attempt}-й раз: {names}")
        st.go("checks")
        shown = "\n\n".join(f"`{r['name']}` — код {r['code']}:\n{r['tail']}" for r in red)
        body = f"{shown}\n\n{CHECKS_TASK.format(attempt=attempt, limit=LIMIT)}"
        return self.turn("Красные тесты или линтеры", body, long="тесты — до 45 минут")

    @staticmethod
    def brief(result: dict) -> dict:
        """Запись проверки в состояние: без хвоста вывода (он — в задании сессии и в логе), у
        красной — первая строка ошибки и путь к логу для отчёта об остановке (#182, #275)."""
        kept = {k: v for k, v in result.items() if k != "tail"}
        if result["code"] == 0:
            kept.pop("error", None)
        return kept

    def keep_output(self, name: str, text: str) -> Path:
        """Полный вывод упавшей команды — в каталог состояния тикета: в отчёт об остановке
        на GitHub уходит только первая строка ошибки и этот путь (#275)."""
        st = self.state
        st.dir.mkdir(parents=True, exist_ok=True)
        path = st.dir / f"{name}-{self.core.now().replace(':', '')}.log"
        path.write_text(text, encoding="utf-8")
        return path

    def local_paths(self, text: str) -> str:
        """Текст для GitHub без путей машины сессии: worktree, главный чекаут, временный каталог,
        домашний — отчёт об остановке владелец читает на GitHub, там они ничего не дают (#182)."""
        names = {self.root: "", self.core.common_dir().parent: "<главный чекаут>"}
        names[Path(tempfile.gettempdir())] = "<tmp>"
        names[Path.home()] = "~"
        pairs = []
        for path, short in names.items():
            for form in {str(path), str(path.resolve())}:
                if form != "/":
                    pairs.append((form, short))
        for form, short in sorted(pairs, key=lambda pair: -len(pair[0])):
            text = text.replace(f"{form}/", f"{short}/" if short else "")
            text = text.replace(form, short or ".")
        return text

    def recipe_touched(self, before: str) -> bool:
        """`/verify` менял рецепт `.claude/skills/run-*` — в дереве или своими коммитами."""
        core = self.core
        moved = (
            core.git("diff", "--name-only", before, "HEAD")
            if core.git_ok("cat-file", "-e", f"{before}^{{commit}}")
            else ""
        )
        # Строки `git status` целиком: `core.git` срезает пробел первой (` M путь` → `M путь`), и
        # срез `[3:]` съедал у правки отслеживаемого рецепта начало пути (#190, п. 6).
        tree = core.git("status", "--porcelain", "--untracked-files=all")
        paths = moved.splitlines() + tree.splitlines()
        return any(".claude/skills/run-" in p for p in paths)

    def tree(self) -> set[str]:
        """Опись рабочего дерева, как у `leftovers()` в `verify`: правки и неотслеживаемые файлы.
        Строки целиком, без среза пробела первой (`core.git` его срезает); сбой — `Failure`."""
        res = self.core.run("git", "status", "--porcelain", "--untracked-files=all")
        if res.returncode != 0:
            raise self.core.Failure(f"git status: {(res.stderr or res.stdout).strip()}")
        return set(res.stdout.splitlines())

    def trace_left(self, trace: dict) -> bool:
        """След `/verify` не разобран: правка рецепта (файлом или коммитом), правки и новые файлы
        в дереве сверх описи до прогона (`tree`: `.claude/skills/verify/`, новые тесты — #204,
        п. 1) или коммиты поверх head до прогона (`head`), — после «продолжай» общее задание о
        незакоммиченном предложило бы закоммитить их в PR тикета (#190, п. 6). История
        переписана (`head` — не предок `HEAD`) — не след: по `TRACE_TASK` это вопрос в
        `.ticket/stop.md`, а он уже есть (#204, п. 2) — если переписал сам `/verify` (так и в
        строке следа); переписала сессия при разборе — след. Опись до прогона не записана (след
        до #204) — как раньше, только правки отслеживаемых."""
        core, before = self.core, trace["head"]
        if "tree" in trace:
            left = bool(self.tree() - set(trace["tree"]))
        else:
            left = bool(self.dirty())
        if left or self.recipe_touched(before):
            return True
        if not core.git_ok("merge-base", "--is-ancestor", before, "HEAD"):
            return not any(m in trace["line"] for m in REWRITTEN)
        return bool(core.git("diff", "--name-only", before, "HEAD"))

    def docs_only_since(self, sha: str | None) -> bool:
        if not sha:
            return False
        if not self.core.git_ok("cat-file", "-e", f"{sha}^{{commit}}"):
            return False
        files = self.core.git("diff", "--name-only", "--no-renames", sha, "HEAD").splitlines()
        review = load_review()
        if not all(review.is_docs(f) for f in files):
            return False
        if not files:
            return True
        # Пути блока ```verify-tool — код, как у `review` (#417): основы и ветки (блок, который
        # завела ветка, — тоже); не прочитаны — не документы.
        try:
            base = self.core.git("merge-base", "origin/main", "HEAD").strip()
            tool = review.tool_paths(base) + review.tool_paths("HEAD")
        except (review.UsageError, self.core.Failure):
            return False
        return all(review.is_docs(f, tool) for f in files)

    # --- /verify ---

    def verify_criteria(self, decision: dict) -> tuple[str, int, bool]:
        """Критерии `/verify`, число отброшенных комментариев и «критериев тикета нет»: тикет
        открыл не участник, а участники его не прокомментировали."""
        core = self.core
        head = core.issue_head(self.n)
        if head["trusted"]:
            text = f"Тикет #{self.n}: {head['title']}\n\n{head['body']}".rstrip()
        else:  # #185: посторонний автор перепишет тело и после взятия в работу
            text = f"Тикет #{self.n}\n\n{STRANGER_HEAD}"
        issue = core.gh_json("issue", "view", str(self.n), "--json", "comments") or {}
        said, strangers = comments_text(issue.get("comments") or [], core.trusted, core.member)
        if said:
            text += (
                f"\n\n## Комментарии тикета (по порядку; позднее решение отменяет раннее)\n\n{said}"
            )
        if decision.get("why"):
            text += f"\n\nЧто проверить (решение сессии): {decision['why']}"
        return text, strangers, not head["trusted"] and not said

    def criteria_changed(self) -> bool:
        """Засчитанный итог `/verify` — на других критериях: тикет дополнили после проверки."""
        decision = self.state["verify"] or {}
        if self.state["verify_off"]:
            return False
        if not decision.get("needed") or decision.get("verdict") not in ("PASS", "пропущено"):
            return False
        text, *_ = self.verify_criteria(decision)
        return decision.get("criteria") != hashlib.sha256(text.encode()).hexdigest()

    def step_verify(self):
        core, st = self.core, self.state
        decision = st["verify"] or {}
        if not decision.get("needed") or st["verify_off"]:
            st.go("sync")
            return None
        text, strangers, empty = self.verify_criteria(decision)
        if empty:  # без критериев тикета платный /verify проверил бы только решение сессии
            return self.stop(
                "тикет открыл не участник репозитория, его заголовок и тело не критерий, а "
                "комментариев участников нет: критерии /verify — комментарием владельца в тикете"
            )
        # Итог засчитан, пока после него менялись только документы и критерии те же (#181):
        # новый комментарий владельца после PASS — /verify заново. Пометка об отброшенных — вне
        # суммы: посторонний комментарием не перезапустит платный /verify.
        digest = hashlib.sha256(text.encode()).hexdigest()
        passed = decision.get("verdict") in ("PASS", "пропущено")
        if passed and decision.get("criteria") == digest and self.docs_only_since(decision["head"]):
            st.go("sync")
            return None
        copy = core.prod_copy(st)
        old = stale(copy) and on_prod(text)
        if old and not st["verify_fresh"]:
            st["verify_fresh"] = copy  # раз на маршрут: свежей не дали — /verify на этой
            fresh = core.command("fresh", self.n)
            body = STALE_COPY.format(copy=copy, long=core.LONG_CALL)
            return (core.TURN, f"Тикет #{self.n}: копия прода старше суток", body, (fresh,))
        criteria = st.dir / "verify-criteria.txt"
        if items := after_deploy(text):  # #301: вне суммы — производное от тех же критериев
            listed = "\n".join(f"- {item}" for _, item in items)
            text += f"\n\n{AFTER_DEPLOY_CRITERIA.format(items=listed)}"
        if strangers:
            text += f"\n\nКомментарии не участников репозитория ({strangers}) не включены."
        # Копия прода (#307) — тоже вне суммы: новый снимок не перезапускает платный /verify.
        if copy:
            text += f"\n\n{PROD_COPY_CRITERIA.format(copy=copy)}"
            if old:
                text += f" Этот {STALE_NOTE}: так и напиши в отчёте."
        criteria.write_text(text + "\n", encoding="utf-8")
        report = st.dir / "verify.json"
        before, tree = core.head(), sorted(self.tree())
        stamp = report.stat().st_mtime_ns if report.exists() else None
        core.say("ticket: /verify — до 7 минут, ждите")
        res = core.canon("verify", str(report), "--criteria-file", str(criteria))
        core.say(res.stdout.rstrip())
        if report.exists() and report.stat().st_mtime_ns != stamp:  # прогон дошёл до `claude`
            st["cost"]["verify"] = (st["cost"].get("verify") or 0) + rollup_cost(report)
        lines = (res.stdout + res.stderr).splitlines()
        mark = "verify: ready-for-human: "
        human = [x.split(mark, 1)[1] for x in lines if mark in x]
        if human:
            st["ready_for_human"] = human[0]
        left = [x for x in lines if x.startswith(TRACE)]
        trace = {"line": left[0], "head": before, "tree": tree} if left else None
        if res.returncode != 0:  # след разбирается и тут — до отчёта об остановке (#190, п. 6)
            errors = "\n".join(x for x in res.stderr.splitlines() if not x.startswith(TRACE))
            log = self.keep_output("verify", res.stdout + res.stderr)
            raw = st.dir / "verify.raw.md"
            reason = ""
            if BLOCKED_MARK in res.stderr and raw.is_file():
                reason = blocked_reason(raw.read_text(encoding="utf-8", errors="replace"))
            if reason:
                why = (
                    f"{BLOCKED_MARK}: {reason}; отчёт /verify — `{raw}`, полный вывод — лог "
                    f"`{log}` на машине сессии"
                )
            else:
                why = f"/verify не состоялся (код {res.returncode}): {failure(errors, log)}"
            return self.stop(why, trace)
        if "verify: пропущено" in res.stdout:
            verdict = "пропущено"
        else:
            m = re.search(r"— (PASS|FAIL) \(", res.stdout)
            verdict = m.group(1) if m else None
        if verdict is None:  # вывод /verify — в лог, не в отчёт на GitHub (#275)
            log = self.keep_output("verify", res.stdout + res.stderr)
            return self.stop(f"итог /verify не распознан: полный вывод — лог `{log}`", trace)
        if left and self.recipe_touched(before):
            verdict = None  # рецепт менял сам /verify (файлом или коммитом) — итог не засчитан
        decision.update(verdict=verdict, head=before, criteria=digest, copy=copy, stale=old)
        st["verify"] = decision
        if verdict == "PASS":
            st["attempts"].pop("verify", None)
        if left:
            if st.attempt("verify_trace") > LIMIT:
                return self.stop(f"/verify оставляет след в worktree: {left[0]}", trace)
            failed = ""
            if verdict == "FAIL":
                st.go("checks")  # после уборки и починки — тесты и /verify заново
                report_md = (st.dir / "verify.md").read_text(encoding="utf-8").strip()
                failed = (
                    f"/verify: FAIL — почини и это (коммит):\n{report_md}\n\nКритерий не "
                    f"подтверждается и починить нельзя — {self.cannot_fix()}.\n\n"
                )
            return self.turn(
                "След /verify в worktree",
                f"{failed}{left[0]}\n{TRACE_TASK}",
            )
        if verdict != "FAIL":
            st.go("sync")
            return None
        attempt = st.attempt("verify")
        if attempt > LIMIT:
            return self.stop(f"/verify: FAIL {attempt}-й раз")
        st.go("checks")  # после починки — тесты и /verify заново
        report_md = (st.dir / "verify.md").read_text(encoding="utf-8").strip()
        return self.turn(
            "/verify: FAIL",
            f"{report_md}\n\nПочини в ветке (коммит, сообщение одной строкой `-m`); тесты и "
            f"/verify пройдут заново. Попытка {attempt} из {LIMIT}; критерий не подтверждается "
            f"и починить нельзя — {self.cannot_fix()}; не просить одобрить непроверенное.",
            long="тесты и /verify — до 45 минут",
        )

    def cannot_fix(self) -> str:
        """Куда вопрос, если FAIL `/verify` починить нельзя: под оркестратором — `ticket ask`
        (ответ комментарием сам вернёт тикет в очередь, #289), иначе — отчёт об остановке."""
        if not self.core.orchestrated():
            return "вопрос в `.ticket/stop.md`"
        return (
            "вопрос владельцу о продукте — в `.ticket/ask.md`, затем вызов "
            f"`{self.core.command('ask', self.n)}`; отчёт об остановке (`.ticket/stop.md`) — "
            "только для тупика без вопроса"
        )

    # --- догон базы ---

    def merging(self) -> bool:
        return self.core.git_ok("rev-parse", "-q", "--verify", "MERGE_HEAD")

    def step_sync(self):
        core, st = self.core, self.state
        if self.merging():
            unresolved = core.git("diff", "--name-only", "--diff-filter=U").splitlines()
            if unresolved:
                return self.conflict_task(unresolved)
            return self.turn(
                "Слияние не доведено",
                "Конфликты разрешены, а коммита слияния нет: `git commit --no-edit`.",
            )
        if self.dirty():
            return self.turn("Незакоммиченные правки", f"{self.dirty()}\nЗакоммить или откати.")
        fetch = core.run("git", "fetch", "-q", "origin")
        if fetch.returncode != 0:
            return self.stop(f"git fetch origin: {tail(fetch.stderr, 3)}")
        if core.git_ok("merge-base", "--is-ancestor", "origin/main", "HEAD"):
            if (st["checks"] or {}).get("head") != core.head():
                st.go("checks")  # слитое из main — тесты на слитом коде
                return None
            st.go("body")
            return None
        (self.root / core.ANSWERS / "sync.md").unlink(missing_ok=True)  # объяснение прошлого догона
        base = core.git("rev-parse", "origin/main")
        install = ".canon.lock" in core.git("diff", "--name-only", "origin/main...HEAD").split()
        message = f"#{self.n}: догон origin/main"
        merge = core.run("git", "merge", "--no-edit", "-m", message, "origin/main")
        record = {"base": base, "conflict": merge.returncode != 0, "install": install}
        st["sync"] = record
        if merge.returncode == 0:
            st.go("checks")
            return None
        unresolved = core.git("diff", "--name-only", "--diff-filter=U").splitlines()
        if not unresolved:
            why = tail(merge.stderr or merge.stdout, 5)
            return self.stop(f"git merge origin/main отказал: {why}")
        record["files"] = unresolved
        attempt = st.attempt("sync")
        if attempt > LIMIT:
            core.run("git", "merge", "--abort")
            return self.stop(f"конфликт с origin/main {attempt}-й раз")
        st.go("sync")
        return self.conflict_task(unresolved)

    def conflict_task(self, files: list[str]):
        install = (self.state["sync"] or {}).get("install")
        body = CONFLICT_TASK.format(
            files=", ".join(f"`{f}`" for f in files),
            vendored=VENDORED_INSTALL if install else VENDORED,
        )
        return self.turn("Конфликт при догоне базы", body, long="тесты — до 45 минут")

    # --- тело PR и финиш ---

    def step_body(self):
        if self.state["heads"].get("body") != self.core.head():
            self.state.go("checks")  # коммит на шаге тела — проверки, /verify и догон заново
            return None
        summary = self.core.read_answer(self.root, "summary.md")
        if not summary:
            return self.turn("Что сделано", SUMMARY_TASK, long="финиш — push и PR, до минуты")
        (self.state.dir / "pr-body.md").write_text(self.pr_body(summary), encoding="utf-8")
        self.state.go("finish")
        return None

    def review_line(self) -> str:
        st = self.state
        review = st["review"] or {}
        if review.get("skipped") and review.get("install"):
            return f"Ревью ошибок: пропущено — чистая установка канона {review['install']}"
        if review.get("skipped"):
            return "Ревью ошибок: пропущено — только документы"
        decisions = st["decisions"] or {}
        count = {kind: 0 for kind in KINDS}
        for kind, _ in decisions.values():
            count[kind] += 1
        debt = (st["techdebt"] or {}).get("url")
        canon = (st["canon_issue"] or {}).get("url")
        parts = [
            f"исправлено {count['исправлено']}",
            f"отклонено {count['отклонено']}",
            f"техдолг {count['техдолг']}" + (f" ({debt})" if debt else ""),
            f"тикет канона {count['канон']}" + (f" ({canon})" if canon else ""),
            f"не записано {count['не записано']}",
        ]
        cost = st["cost"].get("review") or 0
        found = review.get("findings", 0)
        return f"Ревью ошибок: найдено {found} — {', '.join(parts)}; {cost:.2f} USD"

    def checks_line(self) -> str:
        checks = self.state["checks"] or {}
        results = "; ".join(
            f"`{r['name']}` — " + (r["note"] if r.get("note") else f"код {r['code']}")
            for r in checks.get("results", [])
        )
        return f"Тесты и линтеры на `{checks.get('head', '?')[:7]}`: {results}"

    def verify_line(self) -> str:
        if off := self.state["verify_off"]:
            why = f": {off['why']}" if off.get("why") else ""
            return f"/verify снят решением владельца ({off['url']}){why}"
        decision = self.state["verify"] or {}
        if not decision.get("needed"):
            return f"/verify не нужен: {decision.get('why', '')}"
        cost = self.state["cost"].get("verify") or 0
        line = f"/verify: {decision.get('verdict')} ({cost:.2f} USD)"
        if copy := self.verify_copy():
            line += f", проверено на копии прода от {copy}"
            if decision.get("stale"):
                line += f" ({STALE_NOTE})"
        return line

    def verify_copy(self) -> str | None:
        """Копия прода, на которой прошёл засчитанный `/verify` (#307); без копии — None."""
        decision = self.state["verify"] or {}
        if self.state["verify_off"] or not decision.get("needed"):
            return None
        return decision.get("copy") if decision.get("verdict") in ("PASS", "FAIL") else None

    def pr_body(self, summary: str) -> str:
        core, st = self.core, self.state
        lines = [summary, "", f"Closes #{self.n}", ""]
        text, *_ = self.verify_criteria({})
        if items := after_deploy(text):  # #301: проверяет и отмечает владелец после выката
            lines += ["## После выката", "", "Проверяет владелец после выката, отмечает здесь.", ""]
            lines += [f"- [{'x' if done else ' '}] {item}" for done, item in items] + [""]
        lines += ["## Evidence", ""]
        lines.append(f"- {self.checks_line()}")
        if note := core.read_answer(self.root, "checks.md"):
            lines.append(f"- Красное по пути: {note}")
        lines.append(f"- {self.verify_line()}")
        verify_md = st.dir / "verify.md"
        # Путь v2 (#352): доказательства хранит оркестратор, в тело — его сводка без данных.
        v2 = core.v2_task()
        if not v2 and (st["verify"] or {}).get("verdict") == "PASS" and verify_md.exists():
            report = verify_md.read_text(encoding="utf-8").strip()
            lines += ["", "<details><summary>Что проверил /verify</summary>", "", report]
            lines += ["", "</details>", ""]
        if not self.verify_copy() and (copy := core.prod_copy(st)):
            lines.append(f"- Данные прода — копия в песочнице, снимок от {copy}")  # #307
        lines.append(f"- {self.review_line()}")
        for n, (kind, said) in (st["decisions"] or {}).items():
            if kind == "отклонено":
                lines.append(f"  - отклонено {n}: {said}")
        lines += [f"  - {w}" for w in st["review_warnings"] or []]
        if answer := core.read_answer(self.root, "answer.md"):
            lines.append(f"- Ответ владельца на вопрос по находке: {answer}")
        if sync := st["sync"]:
            text = f"- Догон базы: origin/main на `{sync['base'][:7]}`"
            files = ", ".join(sync.get("files", []))
            text += f", конфликт в {files}" if sync["conflict"] else ", без конфликта"
            if how := core.read_answer(self.root, "sync.md"):
                text += f"; {how}"
            lines.append(text)
        return "\n".join(lines) + "\n"

    def step_finish(self):
        core, st = self.core, self.state
        if why := self.closed_pr():
            return self.stop(why)
        body = st.dir / "pr-body.md"
        if self.reviewed() is None:
            st.go("review")  # отчёт ревью устарел (ветка переписана) — ревью заново
            return None
        if not body.exists() or (st["heads"].get("body") != core.head()):
            st.go("checks")  # коммит после сборки тела — проверки, догон и тело заново
            return None
        if self.criteria_changed():
            st.go("verify")  # владелец дополнил тикет после PASS — /verify и тело заново
            return None
        title = core.issue_head(self.n)["title"]  # у тикета постороннего — только номер
        args = ["--title", f"#{self.n}: {title}".rstrip(": ")]
        args += ["--body-file", str(body), "--review-report", str(st.dir / "review.json")]
        if st["weakening"]:
            args += ["--weakening", st["weakening"]]
        if st["ready_for_human"]:
            args += ["--ready-for-human", st["ready_for_human"]]
        if core.v2_task():
            # #341: токен сессии v2 — только чтение: ветку отправляет, PR и его тело ведёт
            # оркестратор (тело — `pr-body.md` рядом с состоянием).
            st["finish_line"] = "ветку и PR ведёт оркестратор"
            st["finished_head"] = core.head()
            st["attempts"] = {}
            return self.ready(reviewed=True)
        if core.orchestrated():
            args.insert(0, "--pr-only")  # отметку и авто-мёрж ставит оркестратор токеном App
        res = core.canon("ship-finish", *args)
        core.say(res.stdout.rstrip())
        if res.returncode == GATES_RED:
            # Местные ворота воркфлоу без CI (#328): красный тест или линтер чинит сессия, как
            # красный шаг «тесты и линтеры», а не владелец.
            attempt = st.attempt("gates")
            if attempt > LIMIT:
                return self.stop(f"местные ворота красные {attempt}-й раз: {tail(res.stderr, 5)}")
            st.go("checks")
            body = f"{tail(res.stderr, 30)}\n\n{CHECKS_TASK.format(attempt=attempt, limit=LIMIT)}"
            return self.turn("Красные местные ворота", body, long="тесты — до 45 минут")
        if res.returncode != 0:
            return self.stop(f"ship-finish отказал (код {res.returncode}): {tail(res.stderr, 5)}")
        url = re.search(r"https://\S+/pull/\d+", res.stdout)
        st["pr"] = url.group(0) if url else st["pr"]
        st["finish_line"] = tail(res.stdout, 1)
        st["finished_head"] = core.head()
        st["attempts"] = {}
        if core.orchestrated():
            return self.ready(reviewed=True)
        st.go("done", core.CI)
        return self.result()

    # --- код готов (под оркестратором) ---

    def ready(self, reviewed: bool):
        """Конец сессии под оркестратором: `reviewed` False — ветка ждёт его ревью ошибок, True
        — разбор, проверки и PR сделаны, ждёт его отметки после CI."""
        st = self.state
        st["ready"] = {"head": self.core.head(), "reviewed": reviewed}
        st.go("ready", self.core.ORCHESTRATOR)
        return self.ready_result()

    def step_ready(self):
        core, st = self.core, self.state
        ready = st["ready"] or {}
        if not core.orchestrated():
            # Тикет оркестратора доводит ручная сессия: ревью сама (если его не было) и полный
            # финиш с отметкой — проверки на том же head не повторяются.
            st.go("checks" if ready.get("reviewed") else "review")
            return None
        if not ready.get("reviewed"):
            if self.reviewed() is not None:  # отчёт ревью оркестратора — разбор находок
                st.go("review")
                return None
            return self.ready(reviewed=False)  # head мог смениться — запомнить новый
        if ready.get("head") == core.head():
            return self.ready_result()
        st["attempts"] = {}  # новые коммиты после «код готов»: красный CI, догон базы
        if self.pr_merged():
            st.retire(self.root)
            st.go("review")
            return None
        st.go("checks")
        return None

    def ready_result(self):
        st, core = self.state, self.core
        ready = st["ready"]
        sha = ready["head"][:7]
        if ready["reviewed"]:
            body = (
                f"PR: {st['pr']} — {st['finish_line']}\nНаходки разобраны, тесты и догон базы "
                f"пройдены на `{sha}`; {self.verify_line()}. Отметку review и авто-мёрж "
                "поставит оркестратор после CI; красный CI или конфликт он вернёт этой сессии "
                "заданием."
            )
        else:
            body = (
                f"Ветка на `{sha}` готова к ревью ошибок. Его гоняет оркестратор вне сессии; "
                "находки вернутся этой сессии заданием «разбери находки»."
            )
        body += (
            "\nЗакончи сессию — ничего больше не делая, без итога владельцу и без `ticket wait`: "
            "сессию возобновит оркестратор."
        )
        return (core.READY, f"Тикет #{self.n}: код готов", body, ())

    def result(self):
        st = self.state
        lines = [
            f"- PR: {st['pr']} — {st['finish_line']}",
            f"- {self.review_line()}",
            f"- {self.checks_line()}",
            f"- {self.verify_line()}",
        ]
        if sync := st["sync"]:
            how = "конфликт разрешён" if sync["conflict"] else "без конфликта"
            lines.append(f"- Догон базы: {how}")
        if st["weakening"]:
            lines.append(f"- Ослабление защиты: {st['weakening']}")
        body = (
            "Итог для владельца — передай одним сообщением:\n\n" + "\n".join(lines) + "\n\n"
            f"{BIND}\n\n{WAIT.format(long=self.core.LONG_CALL)}"
        )
        wait = self.core.command("wait", self.n)
        return (self.core.DONE, f"Тикет #{self.n}: PR {st['pr']}", body, (wait,))

    # --- остановка ---

    def stop(self, reason: str, trace: dict | None = None):
        """Остановка; `trace` — след `/verify` в worktree (строка и head до прогона)."""
        st = self.state
        st["stop"] = {"reason": reason, "step": st.step, "trace": trace}
        st.go("stop")
        return self.step_stop()

    def step_stop(self):
        core, st = self.core, self.state
        reason, trace = st["stop"]["reason"], st["stop"].get("trace")
        question = core.read_answer(self.root, "stop.md")
        if not question:
            first = (
                f"Сначала след /verify в worktree — {trace['line']}\n{TRACE_TASK}\n"
                if trace
                else ""
            )
            return self.turn(
                "Отчёт об остановке",
                f"Довести нельзя: {reason}.\n{first}Допиши для владельца в `.ticket/stop.md`: "
                "вопрос на языке поведения продукта, варианты и рекомендацию — без имён функций и "
                "файлов. Данные маршрута (шаг, тесты, ревью, /verify) соберёт и опубликует скрипт.",
            )
        # След /verify — до отчёта (#190, п. 6): его правки дожили бы до «продолжай», а там общее
        # задание о незакоммиченном предлагает закоммитить их в PR тикета. Не разобран за два
        # круга — отчёт уходит и так, со строкой о нём: владельцу важнее узнать об остановке.
        held = bool(trace) and self.trace_left(trace)
        if held and st.attempt("stop_trace") <= LIMIT:
            body = (
                f"Отчёт об остановке не опубликован: след /verify не разобран — {trace['line']}\n"
                f"{TRACE_TASK}\n`.ticket/stop.md` уже есть."
            )
            return self.turn("Отчёт об остановке", body)
        text = self.stop_report(reason, question, held)
        saved = st.dir / "stop-report.md"
        st.dir.mkdir(parents=True, exist_ok=True)
        saved.write_text(text, encoding="utf-8")
        where = self.publish_stop(text, saved)
        (self.root / core.ANSWERS / "stop.md").replace(st.dir / f"stop-{core.now()}.md")
        st["stop"]["where"] = where
        st["stop"]["at"] = core.now()
        st.go("stopped", core.OWNER)
        body = (
            f"Отчёт об остановке — {where}, метка {LABEL}. Передай владельцу одним сообщением: "
            f"{reason}; вопрос — в отчёте.{self.waive_task()} Ответил «продолжай» — продолжить "
            "маршрут:"
        )
        return (core.STOPPED, f"Тикет #{self.n}: остановка", body, (f"{self.cmd} --again",))

    def waive_task(self) -> str:
        """Строка задания при остановке на `/verify`: решение владельца — только комментарием."""
        if not self.stopped_on_verify():
            return ""
        if self.core.orchestrated():
            return " " + waive_hint(self.core)
        return (
            f" {waive_hint(self.core)} Владелец снял проверку в чате — попроси его написать эту "
            "строку комментарием: из чата маршрут её не принимает, сама сессия её в тикет не пишет."
        )

    def stop_report(self, reason: str, question: str, held: bool = False) -> str:
        """Текст отчёта; `held` — след `/verify` так и не разобран."""
        st = self.state
        lines = [f"Отчёт об остановке: {reason}", "", question, ""]
        step = (st["stop"] or {}).get("step")
        if self.stopped_on_verify():
            lines += [waive_hint(self.core), ""]
        lines += ["## Данные маршрута", ""]
        name = self.core.STEP_NAMES.get(step, step)
        lines.append(f"- Ветка `{st['branch']}`, head `{self.core.head()[:7]}`, шаг «{name}»")
        if st["review"]:
            lines.append(f"- {self.review_line()}")
        if run := st["checks_run"]:
            results = "; ".join(f"`{r['name']}` — код {r['code']}" for r in run["results"])
            lines.append(f"- Тесты и линтеры на `{run['head'][:7]}`: {results}")
        if st["verify"]:
            lines.append(f"- {self.verify_line()}")
        if trace := (st["stop"] or {}).get("trace"):
            left = trace["line"].removeprefix(f"{TRACE}:").strip()
            lines.append(f"- След /verify в worktree: {left}" + ("; не разобран" if held else ""))
        for r in (run or {}).get("results", []):
            if r["code"] != 0:  # хвост вывода не уходит на GitHub: в нём бывают секреты (#275)
                said = failure(r.get("error") or "", r.get("log"))
                lines.append(f"- `{r['name']}` — код {r['code']}, {said}")
        total = sum(v for v in st["cost"].values() if isinstance(v, (int, float)))
        lines.append(f"- Расход: {total:.2f} USD")
        return self.local_paths("\n".join(lines) + "\n")

    def publish_stop(self, text: str, saved: Path) -> str:
        """`ship-finish --unconfirmed`; не прошёл — то же кодом, без `gh pr merge` (#152):
        PR в черновик (черновик не сливается и с включённым авто-мёржем), метка, комментарий; с
        упоминанием владельца и ссылкой в тикете, как у `ship-finish` (#295)."""
        core = self.core
        if core.v2_task():
            # #341: токен сессии v2 — только чтение: отчёт (`stop-report.md` рядом с состоянием)
            # публикует оркестратор, когда сессия кончится, — с меткой и упоминанием владельца.
            return "у оркестратора: он опубликует его в тикете"
        res = core.canon("ship-finish", "--unconfirmed", text)
        if res.returncode == 0:
            m = re.search(r"отчёт об остановке — (.*?), ждёт", res.stdout)
            return m.group(1) if m else "опубликован"
        # Упоминание и ссылка в тикете — как у `ship-finish` (#295): без них нет пуша. Владелец
        # не узнан — отчёт без упоминания: отчёт важнее пуша.
        try:
            owner = core.gh("repo", "view", "--json", "owner", "-q", ".owner.login")
        except core.Failure:
            owner = ""
        text = with_mention(text, owner)
        try:
            fields = ["--state", "open", "--json", "number,url,isDraft"]
            prs = core.gh_json("pr", "list", "--head", self.state["branch"], *fields)
            if prs:
                pr = prs[0]
                number = str(pr["number"])
                if not pr.get("isDraft"):
                    core.gh("pr", "ready", number, "--undo")
                core.gh("pr", "edit", number, "--add-label", LABEL)
                posted = core.gh("pr", "comment", number, "--body", text)
                url = posted if posted.startswith("https://") else pr["url"]
                link = f"{core.REPORT_MARK} — на PR: {url}"
                link = with_mention(link, owner, f"отчёт и метка `{LABEL}` — на PR")
                try:  # отчёт уже на PR: сбой ссылки — не «отчёт не опубликован»
                    core.gh("issue", "comment", str(self.n), "--body", link)
                except core.Failure:
                    pass
                return f"{pr['url']} (черновик)"
            core.gh("issue", "edit", str(self.n), "--add-label", LABEL)
            core.gh("issue", "comment", str(self.n), "--body", text)
            return f"тикет #{self.n}"
        except core.Failure as exc:
            raise core.Failure(
                f"отчёт об остановке не опубликован (ship-finish: {tail(res.stderr, 2)}; {exc}); "
                f"текст — {saved}: передай владельцу в чате"
            ) from None


def main(argv: list[str], core) -> int:
    parser = argparse.ArgumentParser(prog="ticket ship", description=__doc__.split("\n\n")[0])
    parser.add_argument("ticket", type=int, help="номер тикета")
    parser.add_argument(
        "--again", action="store_true", help="продолжить после остановки (владелец ответил)"
    )
    args = parser.parse_args(argv)
    return Route(args.ticket, core).run(args.again)
