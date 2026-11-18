"""ticket ship — конец работы над тикетом: от проверки ветки до PR с отметкой ревью и авто-мёржем.

    scripts/canon/ticket ship <N> [--again]

Из worktree тикета (ветка `ticket-N` или `ticket-N-…`), когда работа закоммичена; главная папка
на ветке тикета, без рабочей копии, — остановка с путём рабочей копии (#458; не под
оркестратором). Шаги по
порядку; механику делает скрипт, на шаге с суждением печатает задание и выходит («ход агента»):

| Шаг | Скрипт | Агент (файл в `.ticket/`) |
|---|---|---|
| документы | `affected-tests --docs`, файлы ветки | обновить или «не задет» (`docs.md`) |
| допуск | `fast-checks` и `checks` (`run-log`), `{files}` — файлы ветки | починка (`checks.md`) |
| ревью ошибок | `review`, отчёт рядом с состоянием; «только документы» — без разбора | — |
| разбор | находки и правило разбора | решения, исправления (`findings.md`) |
| проверка разбора | все разобраны; тикеты техдолга и канона — сам | ответ владельца (`answer.md`) |
| тесты исправлений | `tests` по файлам починки — только перед `/verify` | починка (`checks.md`) |
| `/verify` | критерии из тикета и его комментариев, `verify`, итог | FAIL — починить |
| догон базы | `git merge origin/main` | конфликт — разрешить (`sync.md`) |
| тело PR | факты: Closes, тесты, ревью, `/verify`, догон | «Что сделано» (`summary.md`) |
| финиш | `ship-finish`: push, PR, авто-мёрж, отметка ревью | привязка PR, Auto-fix, `ticket wait` |
| остановка | отчёт, `ready-for-human` без `ready-for-agent` | вопрос и варианты (`stop.md`) |

Задетые документы (#512) — первым делом допуска, один раз на маршрут: документы из блока
```affected-docs профиля, которые называют изменённые файлы ветки; по каждому сессия отвечает
«обновлено» (документ изменён в ветке) или «не задет: почему», итог — в теле PR. Блока нет —
шага нет.
Проверки — по решению владельца 30.10 (#465, `docs/decisions/2026-10-30-checks-on-start-path.md`):
допуск — один раз до платного ревью: строки ```fast-checks профиля (список хука; одноимённая
строка ```checks заменяет строку хука) и строки ```checks (задетые тесты, миграции, документация
API); упало — после починки перегоняются только упавшие, а у красной `tests` с общим отбором
`affected-tests {files}` — только файлы, не названные зелёными, и тесты, задетые починкой (#510;
так же в тестах исправлений; зелёные не узнаны или своя команда проекта — весь отбор). Починка
после допуска (находки, FAIL `/verify`, красный CI, красные ворота, поздний коммит) — шаг «тесты
исправлений»: строка `tests` по файлам своих коммитов починки (без коммитов слияния), и только
если дальше `/verify`; без `/verify` их проверяют CI или ворота. `/verify` после PASS
повторяется, только если починка задела код приложения — не документы и не тесты (шаблоны `tests`
блока ```affected-tests). После догона базы и тела PR проверки не повторяются — стык проверяет CI.
Полного набора тестов в маршруте нет.

Пределы — два круга исправлений каждого вида (красные проверки, FAIL `/verify`, догон), третий —
остановка. Новые коммиты после финиша (красный CI, конфликт) — тот же вызов: тесты исправлений
перед `/verify`, догон и отметка на новый head без второго ревью; PR уже слит — маршрут с нуля
(новая работа, новый PR).
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
import os
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
# отчёта на GitHub (#283): схема, логин и хост остаются, пароль — `***`. Копия
# `ticket_core.URL_PASSWORD` (#446: любой символ перед схемой, `@` в логине) — функции модуля
# зовут и без `core`; весь отчёт об остановке ещё раз проходит `core.mask`.
URL_PASSWORD = re.compile(r"(?<![A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]*://[^\s/:]*:)\S+(@)")
TRACE = "verify: /verify оставил в рабочем дереве"  # след /verify; при коде 1 — в stderr
# `/verify` вернул BLOCKED (#292): строка `verify` в stderr, отчёт — `verify.raw.md`. Причина —
# из строки вердикта отчёта («**Verdict:** BLOCKED. …»; после BLOCKED пусто — следующая строка),
# иначе первая строка не-заголовок. Значения присваиваний (`ИМЯ=значение`) — маской, как
# пароль в URL: строка уходит на GitHub (#275).
BLOCKED_MARK = "/verify не смог проверить (BLOCKED)"
BLOCKED_REASON = re.compile(r"\bBLOCKED\b[\s*_.:;,—–-]*(.*)")
MARKDOWN = re.compile(r"^(?:[#>\s]+|[-*+]\s+|\d+[.)]\s+)+|\*\*")
# Значение в кавычках — целиком, с пробелами (#294 п. 3: `PGPASSWORD="my secret pass"`).
ASSIGNMENT = re.compile(r"""\b([A-Za-z_][A-Za-z0-9_]*=)(?:"[^"\n]*"|'[^'\n]*'|\S+)""")
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
Документ механизма или учебник (`docs/` — кроме `docs/agents/`, решений и ADR; README, глоссарий),
устаревший из-за правки ветки, — исправлено в этой ветке, даже если файл параллельно правит другой
тикет: конфликт сведёт догон базы. Это не правка сверх тикета, вопрос владельцу не нужен. Решение
или ADR не переписываются — новый файл со ссылкой на старый, по вопросу владельцу. Техдолг по
документу — только расхождение, которое было до ветки.
Инструкция агенту (AGENTS.md, CLAUDE.md, скиллы, профиль, подсказки скриптов) — код: устаревшая,
по которой агент выполнит неверную команду, — дефект. Правку сверх тикета молча не делать: вопрос
владельцу или отступление в «Что сделано»."""

FORMAT = """Ответ запиши в файл `.ticket/findings.md` (Write): строка на находку, строка verify:
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
Попытка {attempt} из {limit}; дальше — отчёт об остановке. {rerun}
Полный вывод — в логе из строки `run-log: завершено …`; длинный лог читать через docs-scout, не
целиком."""

# Строки `secrets` нет ни в профиле основной ветки, ни в ветке (#459): ветку не отправить.
NO_SECRETS = (
    "error: проверки `secrets` нет ни в {profile} основной ветки, ни в ветке — допиши строку "
    "`secrets` в блок ```checks профиля ветки (образец — шаблон профиля воркфлоу)"
)
# Красный `secrets` с находкой (#459): не «почини», а вопрос владельцу — задание отчёта об
# остановке. Сбой самой проверки (нет gitleaks, нет `origin/main`) — обычная починка.
SECRETS_STOP = """Секрет в ветке — не чинить и не глушить: ни коммитом поверх (ключ остаётся в
истории), ни `.gitleaksignore`, `gitleaks:allow`, правкой `.gitleaks.toml` или профиля; ветку не
отправлять. Вопрос владельцу: ключ настоящий (отозвать его — дело владельца; неотправленные
коммиты ветки переписать без ключа) или ложная тревога (правило в `.gitleaks.toml` основной ветки
правит владелец) — с рекомендацией (`docs/secrets.md` воркфлоу)."""

# Задетые документы до ревью (#512): отбор `affected-tests --docs`, правило «документ, устаревший
# из-за ветки, — в ветке» (#511, решение `2026-11-18-stale-docs-fixed-in-branch.md`).
DOCS_TASK = """Документы, которые называют изменённые файлы ветки ({count}; блок ```affected-docs
профиля, #512):
{listed}
По каждому одно решение — сверить, что документ говорит об изменённом файле, с кодом ветки:
- описание расходится с веткой — обнови документ в этой ветке (даже если файл параллельно правит
  другой тикет: конфликт сведёт догон базы), коммит сообщением одной строкой `-m`; ответ
  `<путь> — обновлено`;
- не расходится — `<путь> — не задет: почему` одной строкой: что документ говорит о файле и почему
  это осталось верным. Уйдёт в тело PR.
Ответ — в файл `.ticket/docs.md` (Write), строка на каждый документ из списка."""
DOCS_ANSWER = re.compile(
    rf"^[-*\s]*`?([^`\s:—–]+?)`?{DASH}(обновлено|не задет)\b[\s:—–-]*(.*)$", re.I
)

RERUN_ADMISSION = (
    "Следующий вызов перегонит только упавшие проверки допуска; у `tests` с общим отбором и "
    "выводом `tests/run.py` — только не зелёные файлы и задетые починкой, иначе весь отбор."
)
RERUN_RETEST = (
    "Следующий вызов перегонит тесты перед /verify: при общем отборе и выводе `tests/run.py` — "
    "не зелёные файлы и задетые починкой, иначе — задетые всеми коммитами починки."
)
RERUN_GATES = (
    "Следующий вызов — тесты, задетые починкой, только если дальше /verify; ворота на финише "
    "перегонят всё сами."
)
# Проверка хука по индексу коммита (`git diff --cached`, `gitleaks --staged`): в допуске индекс
# пуст — она ничего не проверяет, и зелёный итог был бы ложным (#465).
INDEX_ARGS = ("--cached", "--staged")

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
Ветка меняет проверки (тесты, CI, ревью, хуки), права агента, правила (AGENTS.md, CLAUDE.md,
скиллы, профиль) или обезличивание — первая строка «Меняются правила процесса: <что и зачем>».
Closes, тесты, ревью, `/verify` и догон базы скрипт допишет сам."""

# Строки следа `verify` (`moved`): историю ветки сменил сам `/verify` — по `TRACE_TASK` вопрос.
REWRITTEN = ("история ветки переписана", "ветка сменилась")

TRACE_TASK = """Разобрать каждый пункт: рецепт `.claude/skills/run-*` — откатить (отслеживаемое —
`git checkout -- <путь>`, новое — удалить): итог проверки не засчитан, /verify пройдёт заново;
`.claude/skills/verify/` — удалить; тестовые файлы — откатить; коммиты /verify поверх ветки —
разобрать так же, лишнее откатить коммитом; «ветка сменилась» или «история переписана» — ничего
не откатывать, вопрос в `.ticket/stop.md`."""

BIND = """Привязка PR к сессии — только в приложении Claude; в терминале и в другой среде — пропуск.
Проверь, привязан ли PR к сессии (инструменты ccd_pr, отложенные — загрузить поиском
инструментов: get_status); не привязан — привяжи его ссылкой выше (bind_pr), без вопроса
владельцу. Привязка не удалась — не стоп: в итоге «PR не привязан».
Затем включи Auto-fix (решение владельца 13.10, прогон B0; set_monitor с url PR, auto_fix и
address_comments true) — без вопроса; событие CI разбудит сессию на красном CI или конфликте. PR не
привязан, в терминале или в другой среде — пропуск: красный CI и конфликт найдёт ожидание мёржа."""

WAIT = """Затем ожидание мёржа и уборка — сессия ждёт сама, до 20 минут ({long}); итог ожидания
передать владельцу вместе с итогом выше:"""


def selected(path: Path) -> dict | None:
    """Отбор задетых тестов, записанный командой проверки (`files`, `tests`); нет или испорчен —
    None: промахи такой проверки не ловятся."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    files, tests = data.get("files"), data.get("tests")
    if not all(isinstance(x, list) and all(isinstance(i, str) for i in x) for x in (files, tests)):
        return None
    return {"files": files, "tests": tests}


# Зелёный файл в выводе `tests/run.py`: `ok   test_x.py — тестов: 3, 0.4 с` (имя — от каталога
# тестов). Перегон после починки (#510) пропускает только файлы, названные так: упавший, не
# узнанный или не дошедший до конца (прерванный прогон, `limit`) файл идёт снова.
PASSED_LINE = re.compile(r"^ok\s+(\S+)", re.M)
# Перегон после починки (#510): команда `run` блока ```affected-tests с заданным списком —
# через сам `affected-tests` (его настройки, предел `limit` и остановка группы процессов), тем же
# интерпретатором строки `tests`. Отдельным процессом, а не `load_script`: прогон идёт через
# `run-log` (лог, остановка), как любая проверка; `-I` — модули проекта в корне (`types.py`) не
# подменяют стандартные. Защиты — как у `main()` скрипта: ошибка настроек и пустой `run` — код 2.
RERUN_CODE = (
    "import pathlib, sys, types\n"
    "path = pathlib.Path(sys.argv[1])\n"
    "mod = types.ModuleType('canon_affected_tests_rerun')\n"
    "mod.__file__ = str(path)\n"
    "exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), mod.__dict__)\n"
    "try:\n"
    "    conf = mod.read_settings(pathlib.Path.cwd())\n"
    "except mod.SettingsError as exc:\n"
    "    sys.exit(f'affected-tests: {mod.PROFILE}: {exc}')\n"
    "if not conf['run']:\n"
    "    sys.exit('affected-tests: в блоке ```affected-tests нет `run` — нечем гонять')\n"
    "sys.exit(mod.run_tests(conf['run'], sys.argv[2:], conf['limit']))\n"
)


def passed_tests(output: str, tests: list[str]) -> list[str]:
    """Файлы отбора `tests`, которые вывод прогона называет зелёными (#510) — строкой `ok <файл>`
    `tests/run.py`, имя совпадает с концом пути ровно у одного файла отбора. Прочие форматы не
    узнаются — пустой список: перегон тогда весь отбор. Ошибка узнавания лишь расширяет перегон:
    файл, не названный зелёным, идёт снова."""
    found = set()
    for name in PASSED_LINE.findall(output):
        same = [t for t in tests if t == name or t.endswith(f"/{name}")]
        if len(same) == 1:
            found.add(same[0])
    return [t for t in tests if t in found]


def canon_selector(argv: list[str], files_arg: re.Pattern) -> list[str] | None:
    """Строка `tests` — общий отбор канона `<python> … affected-tests {files}` (#510): команда до
    `{files}` (обёртка с интерпретатором — `python3`, `uv run python` — и путь скрипта); иначе
    (своя команда проекта, скрипт без интерпретатора, `{files:шаблоны}`, лишние аргументы) —
    None."""
    for i, arg in enumerate(argv):
        if Path(arg).name == "affected-tests":
            rest = argv[i + 1 :]
            python = i > 0 and Path(argv[i - 1]).name.startswith("python")
            if python and len(rest) == 1 and (m := files_arg.match(rest[0])) and m[1] is None:
                return argv[: i + 1]
            return None
    return None


def load_script(name: str) -> types.ModuleType:
    """Скрипт канона рядом (файл без `.py`) модулем."""
    path = Path(__file__).resolve().parent / name
    module = types.ModuleType(f"canon_{name.replace('-', '_')}_for_ticket")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def load_review() -> types.ModuleType:
    """`review` рядом со скриптом — его `is_docs` и `needs_review`: пропуск тем же правилом."""
    return load_script("review")


def admission_checks(core, root: Path) -> list[tuple[str, list[str]]]:
    """Список допуска (#465): строки ```fast-checks профиля — список хука, — затем строки
    ```checks; одноимённая строка ```checks заменяет строку хука: она для ветки целиком (секреты
    по коммитам ветки, формат проверкой, а не правкой). Блока ```checks нет — `Failure`, как у
    `profile_checks`. Строка `secrets` — из ```checks профиля основной ветки (#459): ветка не
    уберёт и не подменит её своим профилем; там её нет — строка ветки (`trusted_secrets`, одна
    политика с хуком и `ship-finish`); нет нигде, а профиль в основной ветке есть, — красная
    проверка с заданием дописать строку в профиль ветки: `ship-finish` без неё ветку не отправит,
    и ревью не оплачивается зря."""
    profile = root / PROFILE
    checks = core.profile_checks(profile)
    try:
        fast = core.fast_checks(profile.read_text(encoding="utf-8"), profile) or []
    except OSError:
        fast = []
    names = {name for name, _ in checks}
    listed = [(name, argv) for name, argv in fast if name not in names] + checks
    listed = [(name, argv) for name, argv in listed if name != core.SECRETS]
    secrets, where = core.trusted_secrets(root, PROFILE)
    if secrets is None and where:
        said = NO_SECRETS.format(profile=PROFILE)
        secrets = [sys.executable, "-c", f"import sys; print({said!r}); sys.exit(1)"]
    if secrets is not None:
        listed.append((core.SECRETS, secrets))
    return listed


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


def parse_docs_answers(
    text: str, listed: list[str], changed: set[str]
) -> tuple[dict[str, list[str]], list[str]]:
    """Ответ сессии о задетых документах (#512): строка на документ `путь — обновлено` или
    `путь — не задет: почему`. Ошибки: документ без ответа, путь не из списка, «не задет» без
    причины, «обновлено» без правки документа в ветке (`changed` — файлы ветки)."""
    answers: dict[str, list[str]] = {}
    errors, seen = [], set()
    for line in text.splitlines():
        if not line.strip():
            continue
        m = DOCS_ANSWER.match(line.strip())
        if not m:
            errors.append(
                f"строка «{line.strip()}» — нужна `путь — обновлено` или `путь — не задет: почему`"
            )
            continue
        doc, kind, said = m[1], m[2].lower(), m[3].strip()
        seen.add(doc)
        if doc not in listed:
            errors.append(f"`{doc}` — не из списка задетых документов")
        elif kind == "не задет" and not said:
            errors.append(f"`{doc}` — «не задет» без причины")
        elif kind == "обновлено" and doc not in changed:
            errors.append(
                f"`{doc}` — «обновлено», а в ветке документ не изменён: закоммить правку или "
                "«не задет: почему»"
            )
        else:
            answers[doc] = [kind, said]
    missing = [doc for doc in listed if doc not in seen]
    if missing:
        errors.append("нет ответа: " + ", ".join(f"`{doc}`" for doc in missing))
    return answers, errors


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
        if not core.orchestrated() and (main := self.main_folder()) is not None:
            return self.in_main(main, here)
        self.root = core.toplevel()
        core.answers(self.root)
        st["branch"] = here
        if st.step is None:
            st.go("checks")  # допуск — до платного ревью (#465)
        if again and st.step == "stopped":
            st["attempts"], st["wait"] = {}, None  # владелец ответил — круги и ожидания заново
            step = (st["stop"] or {}).get("step") or "checks"
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
            self.relay_stop_reply()
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
        сессией на починке после его FAIL («починить нельзя» — `stop.md` на шаге тестов
        исправлений)."""
        stop, decision = self.state["stop"] or {}, self.state["verify"] or {}
        repairing = stop.get("step") == "retest"
        failed = repairing and decision.get("verdict") == "FAIL"
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

    def relay_stop_reply(self) -> None:
        """Ответ владельца на отчёт из чата (`.ticket/reply.md`) — комментарием в тикет, после
        отчёта (#459): следующий старт найдёт его там же, где ответ из GitHub. Под оркестратором
        чата нет — файл не ответ; решение о `/verify` разбирает `waive_verify` раньше. Сбой `gh`
        — строкой в выводе: маршрут идёт, ответ остаётся в состоянии."""
        core = self.core
        reply = core.read_answer(self.root, "reply.md")
        if not reply or core.orchestrated():
            return
        try:
            core.relay_reply(self.n, reply)
        except core.Failure as exc:
            core.say(f"ticket: ответ из чата в тикет не передан — {exc}")
        core.keep(self.state, self.root / core.ANSWERS / "reply.md", f"reply-{core.now()}.md")

    def after_report(self) -> list[dict]:
        """Комментарии тикета и открытого PR ветки после последнего отчёта об остановке
        (`ticket_core.after_report`); сбой сверки автора — `Failure`, код 1 без перехода: «не
        свой» молча пустил бы платный `/verify`."""
        return self.core.after_report(self.n, self.state["branch"])

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

    def main_folder(self) -> Path | None:
        """Главная папка проекта, если скрипт запущен из неё (первый worktree списка git)."""
        listing = self.core.git("worktree", "list", "--porcelain").splitlines()
        first = next((x[9:] for x in listing if x.startswith("worktree ")), None)
        if first is None:
            return None
        main = Path(first).resolve()
        return main if self.core.toplevel().resolve() == main else None

    def in_main(self, main: Path, here: str) -> int:
        """Главная папка на ветке тикета, без рабочей копии (#458, слабое место 6): маршрут не
        идёт — в главной папке работают все сессии проекта, и правка тикета смешалась бы с ними."""
        path = main / ".claude" / "worktrees" / f"ticket-{self.n}"
        body = (
            f"Главная папка {main} на ветке тикета {here}, рабочей копии у тикета нет: маршрут "
            "из главной папки не идёт. Незакоммиченное — коммитом в ветку тикета; затем "
            f"переключи главную папку на main (`git -C {main} switch main`) и старт тикета — "
            f"рабочая копия будет {path}, маршрут — из неё. Вызов:"
        )
        start = self.core.command("start", self.n)
        return self.core.emit(self.core.STOPPED, "Главная папка на ветке тикета", body, (start,))

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
            f"перейди в эту рабочую копию по пути {path} (Claude Code — `EnterWorktree` с path "
            f"{path}; в терминале — открой сессию там) и "
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
                st.go("checks")
                return None
            # Новые коммиты после финиша (красный CI, ворота): тесты исправлений перед /verify,
            # догон и отметка на новый head (#465).
            st.go("retest")
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
            return self.ready(reviewed=False)  # ревью ошибок гоняет оркестратор вне сессии
        if verdict is not None and core.orchestrated():
            if (st["review"] or {}).get("head") != verdict["head"]:  # новый отчёт оркестратора
                for old in ("findings.md", "answer.md"):
                    (self.root / core.ANSWERS / old).unlink(missing_ok=True)
                st["asked"] = st["decisions"] = None
                st["cost"]["review"] = verdict.get("cost_usd") or 0
        if verdict is None:
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
            st.go("retest")
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
        st.go("retest")  # исправления по находкам — их тесты, только перед /verify (#465)
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

    # --- допуск ---

    def step_checks(self):
        """Допуск до ревью (#465): один раз на маршрут, список — `admission_checks`. Упало —
        после починки перегоняются только упавшие: прошедшие лежат в `admission.passed`."""
        core, st = self.core, self.state
        if st["checks"]:  # допуск пройден — один раз на маршрут
            st.go(self.after_admission())
            return None
        if (task := self.step_docs()) is not None:
            return task
        current = core.head()
        passed = {r["name"]: r for r in (st["admission"] or {}).get("passed", [])}
        listed = admission_checks(core, self.root)
        fresh, files = [], None
        for name, argv in listed:
            if name in passed:
                continue
            if any(a in INDEX_ARGS for a in argv):
                note = "пропущена: проверяет индекс коммита, в допуске он пуст"
                fresh.append({"name": name, "code": 0, "note": note})
                continue
            if any(core.FILES_ARG.match(a) for a in argv):
                files = core.branch_files() if files is None else files
            if name == "tests":
                fresh.append(self.check_tests(name, argv, files, current))
                continue
            fresh.append(self.run_check(name, argv, files))
        for r in fresh:
            r["head"] = current  # круги допуска — на разных head: тело PR называет каждый
            if r["code"] == 0:
                passed[r["name"]] = self.brief(r)
        order = [name for name, _ in listed]
        known = {**passed, **{r["name"]: self.brief(r) for r in fresh}}
        results = [known[name] for name in order if name in known]
        st["admission"] = {"passed": [passed[n] for n in order if n in passed]}
        st["checks_run"] = {"head": current, "results": results}
        red = [r for r in fresh if r["code"] != 0]
        if not red:
            st["checks"] = {**st["checks_run"], "admitted": current}
            st["tested"] = current
            st["attempts"].pop("checks", None)
            st.go(self.after_admission())
            return None
        return self.red_checks(red, "Допуск до ревью: красные проверки", RERUN_ADMISSION)

    def step_docs(self):
        """Задетые документы (#512) — первым делом допуска, до прогона проверок: отбор
        `affected-tests --docs` по файлам ветки (с удалёнными) и блоку ```affected-docs профиля —
        один раз на маршрут, список в состоянии (`docs.listed`). По каждому документу сессия
        отвечает в `.ticket/docs.md`: «обновлено» (документ изменён в ветке) или «не задет:
        почему». Блока нет — шага нет. None — пройдено, дальше проверки допуска."""
        core, st = self.core, self.state
        docs = st["docs"]
        if docs is None:
            selector = load_script("affected-tests")
            try:
                conf = selector.read_docs_settings(self.root)
                if conf is None:
                    st["docs"] = {"configured": False, "listed": [], "answers": {}}
                    return None
                changed = self.fix_files(None, deleted=True)  # файлы ветки с удалёнными
                listed = selector.select_docs(self.root, changed, conf)
            except selector.SettingsError as exc:
                return self.stop(f"отбор задетых документов не сделан: {exc}")
            docs = st["docs"] = {"configured": True, "listed": listed, "answers": None}
        if docs["answers"] is not None or not docs["listed"]:
            docs["answers"] = docs["answers"] or {}
            return None
        text = core.read_answer(self.root, "docs.md")
        if not text:
            return self.docs_task(docs["listed"])
        changed = set(self.fix_files(None, deleted=True))  # с удалёнными: документ убран
        answers, errors = parse_docs_answers(text, docs["listed"], changed)
        if errors:
            return self.docs_task(docs["listed"], errors)
        docs["answers"] = answers
        return None

    def docs_task(self, listed: list[str], errors: list[str] | None = None):
        parts = []
        if errors:
            parts.append("Ответ не принят:\n" + "\n".join(f"- {e}" for e in errors))
        shown = "\n".join(f"- `{doc}`" for doc in listed)
        parts.append(DOCS_TASK.format(count=len(listed), listed=shown))
        return self.turn("Задетые документы до ревью", "\n\n".join(parts))

    def docs_lines(self) -> list[str]:
        """Итог задетых документов (#512) для тела PR: сколько обновлено, сколько «не задет» и
        почему. Блока ```affected-docs нет — строки нет."""
        docs = self.state["docs"] or {}
        if not docs.get("configured"):
            return []
        answers = docs.get("answers") or {}
        if not docs.get("listed"):
            return ["- Задетые документы: нет — изменённые файлы ветки не названы ни в одном"]
        kept = [(doc, said) for doc, (kind, said) in answers.items() if kind == "не задет"]
        updated = len(answers) - len(kept)
        lines = [f"- Задетые документы: обновлено {updated}, не задет {len(kept)}"]
        lines += [f"  - не задет `{doc}`: {said}" for doc, said in kept]
        return lines

    def after_admission(self) -> str:
        """Шаг после допуска: ревью; отчёт ревью этого маршрута уже есть и годен — тесты
        исправлений. Так идёт состояние до #465, где шаг `checks` стоял после разбора: повторный
        разбор того же отчёта затёр бы решение о /verify и его PASS."""
        if self.state["review"] and self.reviewed() is not None:
            return "retest"
        return "review"

    def red_checks(self, red: list[dict], title: str, rerun: str):
        """Красные проверки: задание починить; третий круг — остановка. Красный `secrets` —
        сразу остановка с вопросом владельцу, без «почини» (#459, слабое место 7): коммит поверх
        ключ не убирает, а `.gitleaksignore`, `gitleaks:allow` и правка профиля глушат защиту."""
        st = self.state
        if leaked := [r for r in red if r["name"] == self.core.SECRETS and r.get("leaked")]:
            said = leaked[0].get("error") or f"код {leaked[0]['code']}"
            return self.stop(f"красная проверка `secrets`: {said}", hint=SECRETS_STOP)
        attempt = st.attempt("checks")
        names = ", ".join(f"`{r['name']}` — код {r['code']}" for r in red)
        if attempt > LIMIT:
            return self.stop(f"проверки красные {attempt}-й раз: {names}")
        st.go(st.step)
        shown = "\n\n".join(f"`{r['name']}` — код {r['code']}:\n{r['tail']}" for r in red)
        task = CHECKS_TASK.format(attempt=attempt, limit=LIMIT, rerun=rerun)
        return self.turn(title, f"{shown}\n\n{task}", long="тесты — до 45 минут")

    def check_tests(
        self, name: str, argv: list[str], files: list[str] | None, current: str
    ) -> dict:
        """Строка `tests` допуска и тестов исправлений: после красного прогона — только не
        зелёные файлы и задетые починкой (`rerun_tests`, #510), иначе — как прочие проверки.
        Красный прогон, где зелёные файлы узнаны, запоминается (`tests_red`: head, файлы, что идут
        снова, полный отбор); зелёный или без узнанных — забывается: следующий — весь отбор."""
        result = self.rerun_tests(name, argv) or self.run_check(name, argv, files)
        again = result.pop("again", None)
        red = result["code"] != 0 and again and result.get("selected")
        self.state["tests_red"] = (
            {"head": current, "again": again, "selected": result["selected"]} if red else None
        )
        return result

    def rerun_tests(self, name: str, argv: list[str]) -> dict | None:
        """Перегон после красной `tests` (#510; решение 27.10 `2026-10-27-affected-tests-once.md`,
        п. 3): файлы прошлого прогона, которые он не назвал зелёными (упавшие, не дошедшие до
        конца), — что остались в дереве, — и тесты, которые общий отбор `affected-tests` даёт по
        файлам починки: своих коммитов после красного прогона (без коммитов слияния). Зелёные
        файлы, не задетые починкой, не повторяются. В итоге `selected` — прошлый отбор вместе с
        перегоном: по нему `ticket wait` ловит промахи. None — перегон весь, как раньше: нет
        красного прогона с узнанными зелёными, история переписана, починка удалила файл, строка
        `tests` — не общий отбор `<python> affected-tests {files}` или он не отобрал."""
        core, red = self.core, self.state["tests_red"]
        if not red or not red.get("again") or not red.get("selected"):
            return None
        if not core.git_ok("merge-base", "--is-ancestor", red["head"], "HEAD"):
            return None
        script = canon_selector(argv, core.FILES_ARG)
        if script is None:
            return None
        touched = self.fix_files(red["head"], deleted=True)
        fixed = [f for f in touched if (self.root / f).is_file()]
        if len(fixed) != len(touched):
            return None  # починка удалила файл: отбор по удалённому тестов не найдёт
        listed: list[str] = []
        if fixed:
            # Без `CANON_AFFECTED_OUT`: отбор для перегона не пишется поверх чужого файла отбора
            # (внешний прогон, в котором идёт маршрут, — например, сцены тестов).
            env = {k: v for k, v in os.environ.items() if k != core.AFFECTED_OUT}
            res = core.run(*script, "--list", *fixed, cwd=self.root, env=env)
            if res.returncode != 0:
                return None
            listed = [line for line in res.stdout.splitlines() if line.strip()]
        again = [t for t in red["again"] if (self.root / t).is_file()]
        chosen = sorted({*again, *listed})
        extra = len(set(listed) - set(again))
        said = f"перегон после починки: не зелёные — {len(again)}, задетые починкой — {extra}"
        before = red["selected"]
        kept = {
            "files": sorted({*before["files"], *fixed}),
            "tests": sorted({*before["tests"], *chosen}),
        }
        if not chosen:
            return {"name": name, "code": 0, "note": f"{said}; гнать нечего", "selected": kept}
        python = [*script[:-1], "-I", "-c", RERUN_CODE, str(self.root / script[-1])]
        result = self.run_check(name, [*python, *chosen], None, {"files": fixed, "tests": chosen})
        return {**result, "selected": kept, "note": f"код {result['code']}, {said}"}

    def run_check(
        self, name: str, argv: list[str], files: list[str] | None, chosen: dict | None = None
    ) -> dict:
        """Одна проверка через `run-log`: `{files}` — `files`; итог с хвостом вывода, строкой
        ошибки и отбором задетых тестов (#464). Проверка, изменившая отслеживаемые файлы рабочей
        копии (форматер в режиме правки), — красная: правка не уходит в ветку молча, как у хука."""
        core, st = self.core, self.state
        if any(core.FILES_ARG.match(a) for a in argv):  # задетые тесты и т.п. (#464)
            argv = core.expand_files(argv, files or [])
            if argv is None:
                return {"name": name, "code": 0, "note": "пропущена: подходящих файлов нет"}
        core.say(f"ticket: {name} …")
        if name == "secrets":  # помощник — копия рядом, а не файл ветки (#283)
            argv = core.canon_secrets(argv, core.HERE)
        if Path(argv[0]).name != "run-log":
            argv = [sys.executable, str(core.HERE / "run-log"), "-l", name, "--", *argv]
        # Отбор задетых тестов команда пишет сюда: по нему `ticket wait` ловит промах (#464).
        st.dir.mkdir(parents=True, exist_ok=True)
        picked = st.dir / f"affected-{re.sub(r'[^A-Za-z0-9_-]+', '-', name)}.json"
        picked.unlink(missing_ok=True)
        env = {**os.environ, core.AFFECTED_OUT: str(picked)}
        before = self.dirty()
        res = core.run(*argv, cwd=self.root, env=env)
        selection = selected(picked) if chosen is None else chosen
        log = re.search(r"лог: (\S+)", res.stdout)
        shown = output = res.stdout + res.stderr
        code = res.returncode
        if code == 0 and (after := self.dirty()) != before:
            code = 1
            shown = output = f"{output}\nпроверка изменила файлы рабочей копии:\n{after}\n"
        elif code != 0 and log:  # строка ошибки — из полного лога, не из хвоста
            try:
                output = Path(log.group(1)).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        # Без значений секретов gitleaks (#258): строка ошибки уходит в отчёт на GitHub.
        hide = core.without_secret_fields
        again = None  # #510: файлы отбора, что идут в перегон, — не названные зелёными
        if code != 0 and selection and (passed := passed_tests(output, selection["tests"])):
            again = [t for t in selection["tests"] if t not in passed] or None
        error, leaked = first_error(hide(output)), False
        if name == core.SECRETS and code != 0 and (found := core.secret_findings(output)):
            error, leaked = core.leaks_line(found), True  # файл и строка — без значения
        return {
            "name": name,
            "code": code,
            "log": log.group(1) if log else None,
            "tail": tail(hide(shown), 30),
            "error": error,
            **({"leaked": True} if leaked else {}),
            **({"selected": selection} if selection is not None else {}),
            **({"again": again} if again else {}),
        }

    # --- тесты исправлений ---

    def step_retest(self):
        """Тесты исправлений (#465): после допуска — только строка `tests` по файлам своих
        коммитов починки и только если дальше `/verify`; без `/verify` починку проверяют CI или
        ворота. Не годен отчёт ревью (код после пропуска «только документы», ветка переписана)
        — сначала ревью."""
        core, st = self.core, self.state
        if self.reviewed() is None:
            return self.readmit()
        current = core.head()
        base = st["tested"] or (st["checks"] or {}).get("head")
        files = self.fix_files(base)
        if not files or not self.verify_ahead():
            st.go("verify")
            return None
        tests = [(n, a) for n, a in core.profile_checks(self.root / PROFILE) if n == "tests"]
        if not tests:
            note = "строки `tests` в профиле нет"
            st["retest"] = {
                "head": current,
                "results": [{"name": "tests", "code": 0, "note": note}],
            }
            st["tested"] = current
            st.go("verify")
            return None
        name, argv = tests[0]
        result = self.check_tests(name, argv, files, current)
        brief = self.brief(result)
        st["checks_run"] = {"head": current, "results": [brief]}
        if result["code"] == 0:
            st["retest"] = {"head": current, "results": [brief]}
            st["tested"] = current
            st["attempts"].pop("checks", None)
            st.go("verify")
            return None
        return self.red_checks([result], "Тесты исправлений красные", RERUN_RETEST)

    def readmit(self):
        """Отчёт ревью не годен (код после пропуска «только документы», ветка переписана) —
        ревью заново, и до него — допуск заново: новый код не идёт на платное ревью без него."""
        st = self.state
        st["checks"] = st["admission"] = st["tested"] = st["retest"] = st["tests_red"] = None
        st.go("checks")
        return None

    def verify_ahead(self) -> bool:
        """Дальше пойдёт `/verify`: он нужен и не снят, а итога нет, итог FAIL или после PASS
        починка задела код приложения."""
        decision = self.state["verify"] or {}
        if not decision.get("needed") or self.state["verify_off"]:
            return False
        if decision.get("verdict") not in ("PASS", "пропущено"):
            return True
        if self.app_code(self.fix_files(decision.get("head"), deleted=True)):
            return True
        return self.criteria_changed()  # /verify пойдёт заново — его тесты перед ним

    def fix_files(self, sha: str | None, deleted: bool = False) -> list[str]:
        """Файлы своих коммитов ветки после `sha`: первая линия истории без коммитов слияния —
        правки main, принесённые догоном, сюда не входят (стык проверяет CI). Без `deleted` —
        только файлы, что есть в дереве (аргументы `{files}`); с ним — и удалённые (удаление
        кода — тоже правка кода). `sha` нет или не предок `HEAD` (история переписана) — файлы
        всей ветки."""
        core = self.core
        if not sha or not core.git_ok("merge-base", "--is-ancestor", sha, "HEAD"):
            if deleted:
                diff = ["diff", "--name-only", "--no-renames", "-z", "origin/main...HEAD"]
                return [x for x in core.git(*diff).split("\0") if x]
            return core.branch_files()
        out = core.git(
            "-c",
            "core.quotepath=off",  # не-ASCII пути — как есть, не в кавычках с escape
            "log",
            "--first-parent",
            "--no-merges",
            "--name-only",
            "--format=",
            "--no-renames",
            f"{sha}..HEAD",
        )
        found = list(dict.fromkeys(x for x in out.splitlines() if x.strip()))
        return found if deleted else [f for f in found if (self.root / f).is_file()]

    def app_code(self, files: list[str]) -> bool:
        """Среди файлов есть код приложения — не документ (правило `review`, с картой блока
        ```verify-tool, #417) и не тест (шаблоны `tests` блока ```affected-tests профиля)."""
        if not files:
            return False
        selector = load_script("affected-tests")
        try:
            patterns = selector.read_settings(self.root)["tests"]
        except (selector.SettingsError, OSError):  # испорченный блок — шаблон по умолчанию
            patterns = list(selector.DEFAULT_TESTS)
        code = [f for f in files if not selector.matches(f, patterns)]
        if not code:
            return False
        review = load_review()
        try:
            base = self.core.git("merge-base", "origin/main", "HEAD").strip()
            tool = review.tool_paths(base) + review.tool_paths("HEAD")
        except (review.UsageError, self.core.Failure):
            return True
        return not all(review.is_docs(f, tool) for f in code)

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
        # Итог засчитан, пока починка после него не задела код приложения (#465: документы и
        # тесты — не код) и критерии те же (#181): новый комментарий владельца после PASS —
        # /verify заново. Пометка об отброшенных — вне суммы: посторонний комментарием не
        # перезапустит платный /verify.
        digest = hashlib.sha256(text.encode()).hexdigest()
        passed = decision.get("verdict") in ("PASS", "пропущено")
        same = passed and decision.get("criteria") == digest
        if same and not self.app_code(self.fix_files(decision.get("head"), deleted=True)):
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
                st.go("retest")  # после уборки и починки — тесты исправлений и /verify
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
        st.go("retest")  # после починки — тесты исправлений и /verify заново
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
            st.go("body")  # после догона проверки не повторяются — стык проверяет CI (#465)
            return None
        (self.root / core.ANSWERS / "sync.md").unlink(missing_ok=True)  # объяснение прошлого догона
        base = core.git("rev-parse", "origin/main")
        install = ".canon.lock" in core.git("diff", "--name-only", "origin/main...HEAD").split()
        message = f"#{self.n}: догон origin/main"
        merge = core.run("git", "merge", "--no-edit", "-m", message, "origin/main")
        record = {"base": base, "conflict": merge.returncode != 0, "install": install}
        st["sync"] = record
        if merge.returncode == 0:
            st.go("body")
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
            # Коммит на шаге тела — починка: тесты исправлений перед /verify, догон, тело заново.
            self.state.go("retest")
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
        """Допуск и, если были, тесты исправлений — для тела PR и итога."""

        def listed(run: dict) -> str:
            shown = []
            for r in run.get("results", []):
                said = r["note"] if r.get("note") else f"код {r['code']}"
                if r.get("head") and r["head"] != (run.get("admitted") or run.get("head")):
                    said += f" на `{r['head'][:7]}`"  # прошла в прошлом круге допуска
                shown.append(f"`{r['name']}` — {said}")
            return "; ".join(shown)

        checks = self.state["checks"] or {}
        admitted = checks.get("admitted") or checks.get("head") or "?"
        line = f"Допуск до ревью на `{admitted[:7]}`: {listed(checks)}"
        if retest := self.state["retest"]:
            line += f"; тесты исправлений на `{retest['head'][:7]}`: {listed(retest)}"
        return line

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
        lines += self.docs_lines()
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
            return self.readmit()  # отчёт ревью устарел (ветка переписана) — допуск и ревью
        if not body.exists() or (st["heads"].get("body") != core.head()):
            st.go("retest")  # коммит после сборки тела — как починка, тело заново
            return None
        if self.criteria_changed():
            st.go("verify")  # владелец дополнил тикет после PASS — /verify и тело заново
            return None
        # Проверки маршрута по правилам #465 пройдены на этом head: допуск, тесты исправлений
        # перед /verify; после догона и тела PR не повторяются. `checks.head` — head, на котором
        # их засчитал маршрут: по нему оркестратор ставит отметку (`unmarkable`), `admitted` —
        # head допуска для тела PR.
        st["checks"] = {**(st["checks"] or {}), "head": core.head()}
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
            # красные проверки маршрута, а не владелец.
            attempt = st.attempt("gates")
            if attempt > LIMIT:
                return self.stop(f"местные ворота красные {attempt}-й раз: {tail(res.stderr, 5)}")
            st.go("retest")  # ворота после починки перегонят всё сами
            task = CHECKS_TASK.format(attempt=attempt, limit=LIMIT, rerun=RERUN_GATES)
            body = f"{tail(res.stderr, 30)}\n\n{task}"
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
            st.go("retest" if ready.get("reviewed") else "checks")
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
            st.go("checks")
            return None
        st.go("retest")
        return None

    def ready_result(self):
        st, core = self.state, self.core
        ready = st["ready"]
        sha = ready["head"][:7]
        if ready["reviewed"]:
            body = (
                f"PR: {st['pr']} — {st['finish_line']}\nНаходки разобраны, проверки маршрута "
                f"по его правилам и догон базы пройдены на `{sha}`; {self.verify_line()}. "
                "Отметку review и авто-мёрж "
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

    def stop(self, reason: str, trace: dict | None = None, hint: str = ""):
        """Остановка; `trace` — след `/verify` в worktree (строка и head до прогона), `hint` —
        о чём вопрос владельцу (красный `secrets`)."""
        st = self.state
        st["stop"] = {"reason": reason, "step": st.step, "trace": trace}
        if hint:
            st["stop"]["hint"] = hint
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
            hint = st["stop"].get("hint")
            first += f"{hint}\n" if hint else ""
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
        # Тикет ждёт владельца — метка триажа «можно брать» неверна (#500); у сессии v2 токен на
        # чтение: отчёт и метки — у оркестратора.
        untriaged = "" if core.v2_task() else core.untriage(self.n)
        (self.root / core.ANSWERS / "stop.md").replace(st.dir / f"stop-{core.now()}.md")
        st["stop"]["where"] = where
        st["stop"]["at"] = core.now()
        st.go("stopped", core.OWNER)
        body = (
            f"Отчёт об остановке — {where}, метка {LABEL}.{untriaged} Передай владельцу одним "
            f"сообщением: {reason}; вопрос — в отчёте.{self.waive_task()} Ответил «продолжай» — "
            "продолжить маршрут:"
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
            lines.append(f"- Проверки на `{run['head'][:7]}`: {results}")
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
        # Маска — на весь текст (#446): вопрос сессии и строки ошибок уходят на GitHub, а
        # секрет в комментарии остаётся в истории его правок.
        return self.core.mask(self.local_paths("\n".join(lines) + "\n"))

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
