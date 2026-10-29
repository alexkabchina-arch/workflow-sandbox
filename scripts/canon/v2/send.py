"""send — отправка ветки оркестратором, черновой PR, карточка PR и вопросы ворот пути v2 (#341;
спека #329, «Оркестратор: очередь, отправка ветки, проверка в песочнице» и «Лицо агента»).

На GitHub на пути v2 действует только оркестратор — **лицо агента**; у сессии токен на чтение
(`app-token --kind reader`). После «код готов» (до ревью и после разбора) ветку отправляет он:

1. бандл коммитов ветки изнутри песочницы — в свой клон оркестратора (`check_secrets` с `keep`);
2. проверка секретов профиля на Mac (`secrets`, конфиг — из `main`);
3. **только вперёд**: ветка на сервере — ровно то, что он отправил последним (`sent` в своём
   состоянии тикета; первая отправка — ветки нет), новый head — её потомок. Чужой коммит на
   сервере — остановка с кодом `чужой-коммит`; сессия переписала отправленное — `не-вперёд`;
4. коммиты, меняющие файлы CI (`CI_PATHS`), без разрешения владельца не уходят: файл CI из
   ветки исполняется на GitHub раньше допуска. Вопрос владельцу; «разрешаю» — на этот head;
5. `git push` из своего клона токеном оркестратора (помощник git берёт его из окружения, в
   аргументах токена нет; адрес ssh — заменяется на https: ключ владельца не участвует);
6. сразу после первой отправки — черновой PR (CI на черновике не идёт, #336) и карточка PR
   (`v2/card.py`); после разбора находок — доказательства `/verify` в своё хранилище, тело PR из
   `pr-body.md` маршрута с отчётом `/verify`, заменённым сводкой без данных (`v2/evidence.py`,
   #352), и находки без блокировки (решение «техдолг» сессии v2 — строкой карточки, а не
   тикетом).

Красные секреты — вопрос владельцу, не чинить и не глушить: «удалить» — свежая сессия
переписывает неотправленные коммиты (после последнего отправленного), затем проверка заново;
«ложное срабатывание» — исключение в `main` вносит владелец, затем проверка заново. Круг один:
снова красная — отчёт об остановке. Вопросы ворот — комментарием оркестратора с той же меткой,
что вопрос сессии (`ASK_MARK`), ответ — комментарием владельца в тикете (`answered`). После
разбора находок, до отправки, — ещё вопрос до слияния (#343): ревью ошибок нашло у тикета с
флагом «миграция» серьёзную находку без доказательства; «разрешаю» — на этот отчёт ревью, «нет» —
отчёт об остановке. После отметки у тикета с флагом «миграция» — вопрос «да» на слияние (#346,
`v2/gates.py`): «да» — авто-мёрж на этот head, «нет» — отчёт об остановке.

Перед каждой новой сессией тикета рабочая копия в песочнице приводится к ветке на сервере
(`align`): сервер — ровно последнее отправленное; копия впереди (неотправленные коммиты) — как
есть, позади — перемотка, разошлась — остановка с кодом `копия-расходится`. Незавершённая работа
до «код готов» на GitHub не уходит: её коммитит только локально `session-run --handover` (#339).

Удалить: выключить переключатель, затем этот файл и `v2/card.py`; вызовы `orch.sender()` в
`orchestrator` (отправка в `review` и перед CI, вопросы ворот в `answered` и `begin`, `align` в
`launch`), вид `reader` в `app-token` и ветки `core.v2_task()` в `ticket_ship.py`,
`ticket_ask.py` и `session-start` — старый путь своё не менял.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

SEND_REF = "refs/canon-send"  # коммиты бандла в своём клоне — до конца отправки
CI_PATHS = (".github/workflows/", ".github/actions/")  # файлы CI GitHub Actions
CODES = {
    "foreign": "чужой-коммит",
    "backward": "не-вперёд",
    "diverged": "копия-расходится",
}
SECRETS, CI = "secrets", "ci"  # вопросы ворот
MIGRATION = "migration"  # вопрос до слияния: серьёзная находка ревью без доказательства (#343)
MERGE = "merge"  # «да» на слияние тикета с флагом «миграция» после отметки (#346, `v2/gates.py`)
ASKED = {
    SECRETS: "секрет в коммитах",
    CI: "коммиты меняют файлы CI",
    MIGRATION: "находка ревью без доказательства до слияния",
    MERGE: "«да» на слияние тикета с флагом «миграция»",
}
# Ответы владельца: «ложное» — раньше «удалить» («не удалять — ложное срабатывание»), отрицание
# перед словом его снимает.
DELETE = re.compile(r"(?<!не )\bудал", re.I)
FALSE = re.compile(r"ложн", re.I)
ALLOW = re.compile(r"(?<!не )\bразреш|^\s*да\b", re.I)
DENY = re.compile(r"\bне\s+разреш|^\s*нет\b", re.I)
PUSH_TIMEOUT = 600  # отправка дольше — сбой, а не зависший такт
SSH = re.compile(r"^(?:git@github\.com:|ssh://git@github\.com/)")
# Помощник git: токен — из окружения процесса `git push`, не из аргументов и не из файла.
HELPER = (
    '!f() { test "$1" = get || exit 0; echo username=x-access-token; '
    'echo "password=$CANON_PUSH_TOKEN"; }; f'
)
ALIGN = r"""
set -u
sent="$1"
git cat-file -e "$sent^{commit}" 2>/dev/null || git fetch -q origin "$2" || exit 3
git cat-file -e "$sent^{commit}" 2>/dev/null || exit 3
git merge-base --is-ancestor "$sent" HEAD && exit 0
git merge-base --is-ancestor HEAD "$sent" || exit 5
git merge -q --ff-only "$sent" || exit 4
"""

PR_BODY = """Closes #{n}

Черновой PR: ветку отправил оркестратор после «код готов» на `{sha}`. Ход тикета и то, что
требует внимания владельца, — в карточке PR (комментарий оркестратора ниже). CI на черновике
не идёт; полный прогон — при переводе из черновика.
"""
SECRETS_ASK = """**Причина:** проверка секретов нашла в неотправленных коммитах ветки `{branch}`
похожее на ключ или пароль — {where}. Ветку на GitHub я не отправил: ревью и CI ждут.

**Сделано:** ничего не чинил и не глушил; значение секрета нигде не показываю. Настоящий ключ
отзовите там, где он выдан: он уже лежит в копиях ветки.

**Доступно** (ответ — комментарием в этом тикете):
- «удалить» — новая сессия перепишет неотправленные коммиты без секрета, затем проверка заново;
- «ложное срабатывание» — сначала внесите исключение в `main` (`.gitleaksignore` или конфиг
  gitleaks), затем ответьте: проверка заново.

Круг один: снова красная проверка — отчёт об остановке. Не уверены — рекомендую «удалить»."""
CI_ASK = """**Причина:** коммиты сессии меняют файлы CI: {paths}. Файл CI из ветки исполняется на
GitHub раньше допуска, поэтому без вашего разрешения я такую ветку не отправляю.

**Сделано:** ветка на `{sha}` не отправлена; проверка секретов пройдена.

**Доступно** (ответ — комментарием в этом тикете):
- «разрешаю» — отправлю ветку как есть (разрешение — на этот коммит; новый коммит в файлы CI —
  снова вопрос);
- «нет» — отчёт об остановке: правку файлов CI уберёте или поручите отдельным тикетом.

Рекомендую «нет», если правка CI не названа в тикете."""
MIGRATION_ASK = """**Причина:** у тикета флаг «{flag}», а ревью ошибок нашло серьёзную находку без
доказательства — без теста и без сценария `/verify`:
{items}
Ошибку здесь дорого откатить, поэтому до слияния нужен ваш ответ.

**Сделано:** находки разобраны сессией тикета{decided}; ветка на `{sha}`. PR остаётся
черновиком: CI и слияние ждут вашего ответа.

**Доступно** (ответ — комментарием в этом тикете):
- «разрешаю» — дальше CI и слияние;
- «нет» — отчёт об остановке: доказательство или правку поручите сессии.

Рекомендую «нет», если находка про данные миграции и разбор её не опроверг."""
SECRETS_TASK = """Оркестратор: проверка секретов нашла секрет в неотправленных коммитах ветки тикета
#{n} — {where}; владелец ответил «удалить». Перепиши неотправленные коммиты — всё после
`{base}` — так, чтобы секрета не было ни в одном из них: например `git reset --soft {base}`,
убери значение из файлов (оно приходит из окружения или настроек, не из кода) и сделай один
новый коммит (сообщение одной строкой `-m`). Отправленное (`{base}` и раньше) не трогай: такую
ветку оркестратор не отправит. Затем вызов `{ship}` ({long}) и его задания — до {until};
секреты оркестратор проверит заново. Без владельца не обойтись — вопрос только `{ask}`."""


class Send:
    """Ворота отправки пути v2 для одного такта. `tools` — общее оркестратора: `core`, `asks`,
    `card`, `run`, `say`, `stamp`, `tail`, `without_tokens`, `SHA`, `HUMAN`, `STOPPED`, `QUEUED`."""

    def __init__(self, orch, tools, source: str | None = None):
        self.orch, self.t = orch, tools
        self.source = source  # кто отправляет: None — «код готов», `rtest` — тест ревьюера (#344)

    # --- отправка ---

    def ready(self, n: int, t: dict, st) -> bool:
        """Head «код готов» — на GitHub по воротам; True — отправлено (или уже там), шаг идёт
        дальше; False — остановка или вопрос владельцу, шаг такта сделан."""
        head = (st["ready"] or {}).get("head")
        if not isinstance(head, str) or not self.t.SHA.fullmatch(head):
            why = f"head «код готов» в состоянии маршрута — не коммит: {head!r}"
            return self.halt(n, t, None, why)
        return self.send(n, t, st, head) and self.pr(n, t, st, head)

    def send(self, n: int, t: dict, st, head: str) -> bool:
        """Коммит `head` рабочей копии тикета в песочнице — на GitHub по воротам (шаги 1–5),
        без PR: так уходит и коммит «только тест» оркестратора (#344). Итог — как у `ready`."""
        o, branch = self.orch, st.branch()
        worktree = o.worktree(n)
        if worktree is None:
            return self.halt(n, t, None, "worktree тикета в клоне песочницы нет")
        o.own_repo(refetch=True)
        tip, sent = o.remote_tip(branch), t.get("sent") or ""
        if tip != sent:
            was = f"последним оркестратор отправил `{sent[:7]}`" if sent else "он её не отправлял"
            now = f"`{tip[:7]}`" if tip else "её нет"
            return self.halt(n, t, "foreign", f"ветка `{branch}` на сервере — {now}, а {was}")
        ref = f"{SEND_REF}/ticket-{n}"
        try:
            o.leaks = None
            if problem := o.check_secrets(n, st, head, worktree, keep=ref):
                if o.leaks:
                    return self.secrets(n, t, st, head, o.leaks)
                return self.halt(n, t, None, problem)
            if tip and not o.ancestor(tip, head):
                why = f"head `{head[:7]}` — не потомок отправленного `{tip[:7]}`: переписан"
                return self.halt(n, t, "backward", why)
            if (ci := self.ci_files(tip, head)) and t.get("ci_allowed") != head:
                return self.ask_ci(n, t, head, ci)
            if tip != head:
                if problem := self.push(head, branch):
                    return self.halt(n, t, None, problem)
                t["sent"] = head  # сразу: сбой сверки ниже — не «чужой коммит» на повторе
                o.own_repo(refetch=True)
                if (now := o.remote_tip(branch)) != head:
                    return self.halt(n, t, None, f"после отправки ветка — {now[:7] or 'нет'}")
        finally:
            o.git("update-ref", "-d", ref)
        t["sent"] = head
        self.t.say(f"#{n}: ветка `{branch}` отправлена на {head[:7]}")
        return True

    def halt(self, n: int, t: dict, code: str | None, why: str) -> bool:
        """Остановка до отправки: с кодом причины (`CODES`), если он есть; строка — в карточку."""
        said = f"код `{CODES[code]}`: {why}" if code else why
        self.mark(n, t, self.t.card.ATTENTION, f"Остановка — {said}; отчёт в тикете", key="stop")
        self.orch.stop(n, t, f"ветка не отправлена — {said}")
        return False

    def ci_files(self, sent: str, head: str) -> list[str]:
        """Файлы CI, которые неотправленные коммиты сессии меняют: дифф от отправленного (или
        основы) до head и при этом дифф ветки от основы — правка CI самого `main`, пришедшая
        догоном, не в счёт. Пути — через `-z`: имя не в ASCII git иначе берёт в кавычки."""
        o = self.orch
        base = o.git("merge-base", "origin/main", head).stdout.strip()

        def changed(start: str) -> set[str]:
            res = o.git("diff", "-z", "--name-only", "--no-renames", start, head)
            if res.returncode != 0 or not start:
                raise self.t.core.Failure(f"дифф ветки не прочитан: {self.t.tail(res.stderr)}")
            return {f for f in res.stdout.split("\0") if f.startswith(CI_PATHS)}

        found = changed(base)
        if sent and found:
            found &= changed(sent)
        return sorted(found)

    def push_url(self) -> str:
        """Адрес отправки: `origin` своего клона; ssh GitHub — https того же репозитория (ключ
        владельца не участвует, только токен лица агента)."""
        url = self.orch.git("remote", "get-url", "origin").stdout.strip()
        return f"https://github.com/{self.orch.repo}.git" if SSH.match(url) else url

    def push(self, head: str, branch: str) -> str | None:
        """`git push` head из своего клона токеном оркестратора, без force. Итог — причина
        сбоя или None."""
        env = self.t.without_tokens()
        env["CANON_PUSH_TOKEN"] = os.environ.get("GH_TOKEN", "")
        # Без настроек git владельца: `url.….insteadOf` вернул бы https в ssh — его ключом.
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        own = self.orch.dir / "repo"
        try:
            res = self.t.run(
                "git", "-C", str(own), "-c", "credential.helper=",
                "-c", f"credential.helper={HELPER}", "push", "-q", self.push_url(),
                f"{head}:refs/heads/{branch}", env=env, timeout=PUSH_TIMEOUT,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            return f"`git push` из своего клона дольше {PUSH_TIMEOUT // 60} мин"
        if res.returncode != 0:
            return f"`git push` из своего клона: {self.t.tail(res.stderr or res.stdout)}"
        return None

    # --- черновой PR и карточка ---

    def pr(self, n: int, t: dict, st, head: str) -> bool:
        """Черновой PR сразу после первой отправки и карточка в нём; дальше — строки карточки и
        тело PR после разбора. PR не открылся до ревью — строка, снова на следующей отправке;
        после разбора без PR CI не начать — остановка."""
        core, card = self.t.core, self.t.card
        reviewed = bool((st["ready"] or {}).get("reviewed"))
        if not t.get("pr"):
            try:
                url = self.open_pr(n, st, head)
            except core.Failure as exc:
                if reviewed:
                    return self.halt(n, t, None, f"черновой PR не открыт: {exc}")
                self.t.say(f"#{n}: черновой PR не открыт — {exc}; снова на следующей отправке")
                return True
            t["pr"] = url
            t["undraft"] = True  # перевод из черновика — на шаге CI (`own_draft`)
            t.setdefault("card", card.blank())
            self.t.say(f"#{n}: черновой PR {t['pr']}")
            text = f"«Код готов» на `{head[:7]}`: ветку отправил оркестратор, открыт черновой PR"
        else:
            text = f"Ветка отправлена на `{head[:7]}`"
        if reviewed:
            evidence = self.orch.evidence()  # доказательства `/verify` — к себе (#352)
            evidence.keep(n, t, st, head)
            evidence.mark(n, t, head)
            self.body(n, t, st, head)
            text = f"Находки разобраны, проверки и /verify пройдены — ветка на `{head[:7]}`"
        self.mark(n, t, card.DONE, text, key=f"sent-{head}")
        for key in ("stop", "gate"):
            self.mark(n, t, card.ATTENTION, None, key=key, publish=False)
        for name in ("techdebt", "canon_issue"):
            record = st.data.get(name)
            held = record.get("held") if isinstance(record, dict) else None
            for i, said in enumerate(held if isinstance(held, list) else []):
                if isinstance(said, str):
                    if name == "canon_issue":  # тикет в чужом репозитории токеном App не завести
                        said = f"Для трекера воркфлоу (решение «канон»): {said}"
                    self.mark(n, t, card.FINDINGS, said, key=f"{name}-{i}", publish=False)
        self.publish(n, t)
        return True

    def open_pr(self, n: int, st, head: str) -> str:
        """Черновой PR ветки: уже открытый (прошлая попытка не дошла до записи) — он; иначе
        новый. Ссылки в выводе `gh` нет — `Failure`."""
        core, branch = self.t.core, st.branch()
        fields = ("--head", branch, "--state", "open", "--json", "url")
        if found := core.gh_json("pr", "list", *fields) or []:
            return found[0]["url"]
        issue = core.gh_json("issue", "view", str(n), "--json", "title") or {}
        title = f"#{n}: {issue.get('title') or ''}".rstrip(": ")
        body = PR_BODY.format(n=n, sha=head[:7])
        args = ("--draft", "--head", branch, "--base", "main", "--title", title)
        out = core.gh("pr", "create", *args, "--body", body)
        if not (m := re.search(r"https://\S+/pull/\d+", out)):
            raise core.Failure(f"ссылки на PR нет в выводе «{out.strip()[:80]}»")
        return m.group(0)

    def body(self, n: int, t: dict, st, head: str) -> None:
        """Тело PR — из `pr-body.md` маршрута (его собрала сессия после разбора и проверок) и
        разделы, которые на старом пути дописывал `ship-finish`: решение об ослаблении защиты и
        коммиты после ревью ошибок. Нет файла или сбой — строкой, тело остаётся прежним."""
        core = self.t.core
        try:
            data = self.t.read_inside(st.clone, st.rel / "pr-body.md").decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return self.t.say(f"#{n}: тело PR не обновлено — {exc}")
        data = self.orch.evidence().scrub(n, t, data)  # отчёт `/verify` — сводкой без данных
        if weakening := st["weakening"]:
            data = f"{data.rstrip()}\n\n## Ослабление защиты\n\n{weakening}\n"
        if after := self.after_review(n, head):
            data = f"{data.rstrip()}\n\n## Коммиты после ревью\n\n{after}\n"
        fd, tmp = tempfile.mkstemp(dir=self.orch.dir, prefix=".pr-body-", suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(data)
        try:
            core.gh("pr", "edit", t["pr"], "--body-file", tmp)
        except core.Failure as exc:
            self.t.say(f"#{n}: тело PR не обновлено — {exc}")
        finally:
            os.unlink(tmp)

    def after_review(self, n: int, head: str) -> str:
        """Коммиты после head отчёта ревью ошибок до `head` — строками; отчёта нет или head
        отчёта не предок — пусто."""
        o = self.orch
        try:
            reviewed = json.loads(o.own_report(n).read_text(encoding="utf-8"))
            reviewed = reviewed["canon_review"]["head"]
        except (OSError, ValueError, KeyError, TypeError):
            return ""
        if not isinstance(reviewed, str) or not o.ancestor(reviewed, head):
            return ""
        res = o.git("log", "--format=- `%h` %s", f"{reviewed}..{head}")
        return res.stdout.strip() if res.returncode == 0 else ""

    def mark(
        self, n: int, t: dict, section: str, text: str | None, key: str, publish: bool = True
    ) -> None:
        """Строка карточки PR (`v2/card.py`); PR ещё нет — только в состояние, карточка покажет
        её с первой публикацией."""
        card = t.setdefault("card", self.t.card.blank())
        if self.t.card.line(card, section, text, key=key) and publish and t.get("pr"):
            self.publish(n, t)

    def publish(self, n: int, t: dict) -> None:
        """Карточка — комментарием оркестратора в PR: первый раз — новый, дальше — правка того
        же (id из ссылки `gh pr comment`). Сбой — строкой: ворота важнее карточки."""
        core, card = self.t.core, t.setdefault("card", self.t.card.blank())
        text = self.t.card.render(card, n)
        try:
            if card.get("id"):
                path = f"repos/{self.orch.repo}/issues/comments/{card['id']}"
                core.gh("api", "-X", "PATCH", path, "-f", f"body={text}")
                return
            out = core.gh("pr", "comment", t["pr"], "--body", text)
        except core.Failure as exc:
            return self.t.say(f"#{n}: карточка PR не обновлена — {exc}")
        if m := re.search(r"#issuecomment-(\d+)", out):
            card["id"] = m.group(1)
        else:
            self.t.say(f"#{n}: id карточки PR не прочитан из «{out.strip()[:80]}»")

    # --- вопросы ворот ---

    def before_merge(self, n: int, t: dict, st) -> bool:
        """Вопрос владельцу до слияния (#343): ревью ошибок нашло у тикета с флагом из
        `SERIOUS_FLAGS` скрипта `review` серьёзную находку без доказательства
        (`canon_review.v2.owner_question` своего отчёта). Нет таких или владелец уже разрешил
        этот отчёт (его head) — True, шаг идёт дальше; иначе вопрос — False."""
        try:
            review = json.loads(self.orch.own_report(n).read_text(encoding="utf-8"))
            review = review["canon_review"]
            asked, head = review["v2"]["owner_question"], review["head"]
        except (OSError, ValueError, KeyError, TypeError):
            return True
        if not asked or t.get("merge_allowed") == head:
            return True
        decisions = st.data.get("decisions") if isinstance(st.data.get("decisions"), dict) else {}
        rows = []
        for item in asked:
            row = f"- находка {item['n']} `{item['where']}` — {item['summary']}"
            said = decisions.get(str(item["n"]))
            if isinstance(said, list) and len(said) == 2:
                row += f"; решение сессии: {said[0]} — {said[1]}".rstrip(" —")
            rows.append(row)
        flag = review["v2"].get("serious_flag") or "миграция"  # флаг, ради которого вопрос
        decided = ", её решения — в строках находок" if decisions else ""
        sha = ((st["ready"] or {}).get("head") or head)[:7]
        text = MIGRATION_ASK.format(flag=flag, items="\n".join(rows), decided=decided, sha=sha)
        return self.ask(n, t, MIGRATION, head, text)

    def secrets(self, n: int, t: dict, st, head: str, found: list[str]) -> bool:
        where = self.t.core.leaks_line(found)
        if t.get("secret_rounds", 0) >= 1:
            why = f"{where} — снова после ответа владельца, а круг один; ключ отзывает владелец"
            return self.halt(n, t, None, why)
        text = SECRETS_ASK.format(branch=st.branch(), where=where)
        return self.ask(n, t, SECRETS, head, text, where=where)

    def ask_ci(self, n: int, t: dict, head: str, files: list[str]) -> bool:
        paths = ", ".join(f"`{f}`" for f in files[:20]) + (" и др." if len(files) > 20 else "")
        return self.ask(n, t, CI, head, CI_ASK.format(paths=paths, sha=head[:7]))

    def ask(self, n: int, t: dict, kind: str, head: str, text: str, **extra) -> bool:
        """Вопрос ворот владельцу: метка `agent:stopped` (#347), комментарий с меткой вопроса и
        упоминанием; место свободно до ответа (`answered` → `answer`)."""
        o, core = self.orch, self.t.core
        stamp = self.t.stamp()
        owner = o.repo.split("/")[0]
        body = (
            f"{core.ASK_MARK.format(stamp=stamp)}\n**Вопрос оркестратора по тикету #{n}**\n\n"
            f"{text}\n\n---\n@{owner} Ответ — комментарием в этом тикете (можно с телефона): "
            "работа продолжится сама."
        )
        o.labels(n, add=(self.t.STOPPED,))
        o.comment(n, body)
        t["gate"] = {"kind": kind, "stamp": stamp, "head": head, **extra}
        if self.source:
            t["gate"]["source"] = self.source  # ответ вернёт отправку туда (`resume`)
        t.update(phase="question", asked=t.get("asked", 0) + 1)
        o.free(n)
        what = ASKED[kind]
        self.mark(n, t, self.t.card.ATTENTION, f"Ждёт вашего ответа в тикете: {what}", key="gate")
        self.t.say(f"#{n}: вопрос владельцу ({what}) — ветка не отправлена, место свободно")
        return False

    def reply(self, n: int, t: dict, comments: list[dict]) -> list[str] | None:
        """Ответы владельца на вопрос ворот среди комментариев тикета по порядку; нет — None.
        Вопроса нет — `asks.NoQuestion`."""
        gate = t["gate"]
        question = {"stamp": gate["stamp"], "url": f"вопрос ворот в тикете #{n}"}
        replies, _ = self.t.asks.answers(
            self.t.core, question, comments, strict=True, strangers=False
        )
        return replies or None

    @staticmethod
    def decide(kind: str, said: str) -> str | None:
        """Решение по одному ответу: `delete`, `false`, `allow`, `deny`; не распознан — None.
        «Ложное» — раньше «удалить»: «не удалять — ложное срабатывание» — не удаление."""
        if kind == SECRETS:
            if FALSE.search(said):
                return "false"
            return "delete" if DELETE.search(said) else None
        if DENY.search(said):
            return "deny"
        return "allow" if ALLOW.search(said) else None

    def answer(self, n: int, t: dict) -> None:
        """Ответ владельца на вопрос ворот (тикет занял место) — последний распознанный из его
        ответов («разрешаю», затем «спасибо» — разрешение): «удалить» — свежая сессия с
        заданием переписать неотправленное; «ложное срабатывание» и «разрешаю» — отправка
        заново; «нет» и нераспознанное — отчёт об остановке."""
        o = self.orch
        gate = t.pop("gate")
        answers = gate.get("answer") or []
        answers = [answers] if isinstance(answers, str) else answers
        decided = [d for d in (self.decide(gate["kind"], a) for a in answers) if d]
        what = decided[-1] if decided else None
        st = o.route(n)
        if gate["kind"] == SECRETS:
            t["secret_rounds"] = t.get("secret_rounds", 0) + 1
        if gate["kind"] == MERGE:  # код готов и отмечен: остановка — на шаге CI, не сессии
            t["phase"] = "ci"
        if gate.get("source") == "rtest" and what not in ("false", "allow"):
            # Секрет или файл CI — в тесте, который писал ревьюер, а не сессия: переписывать
            # ветку некому; тест ревьюера не отправлен.
            said = " / ".join(a.strip() for a in answers)[:200]
            why = f"тест ревьюера не отправлен — {ASKED[gate['kind']]}, ответ владельца «{said}»"
            return o.stop(n, t, f"{why}; `agent:retry` — тикет заново")
        if what == "delete":
            base = t.get("sent")
            base = base or o.git("merge-base", "origin/main", gate["head"]).stdout.strip()
            words = o.task_words(n)
            task = SECRETS_TASK.format(n=n, where=gate.get("where", ""), base=base, **words)
            return o.launch(n, task=task)
        if what == "false":
            return self.resume(n, t, st, gate)
        if what == "allow" and gate["kind"] == MERGE:
            # «Да» на слияние (#346) — на этот head: шаг CI снова, отметка с авто-мёржем.
            t["merge_yes"] = gate["head"]
            self.mark(n, t, self.t.card.ATTENTION, None, key="gate", publish=False)
            said = f"Владелец дал «да» на слияние `{gate['head'][:7]}` (флаг «миграция»)"
            self.mark(n, t, self.t.card.DONE, said, key="merge-yes")
            t["phase"] = "ci"
            return o.steps(t)["ci"](n, t)
        if what == "deny" and gate["kind"] == MERGE:
            return o.stop(n, t, "владелец не дал «да» на слияние тикета с флагом «миграция»")
        if what == "allow" and gate["kind"] == MIGRATION:
            t["merge_allowed"] = gate["head"]
            # Вопрос решён: его строки «Требует внимания» карточки — в «Что сделано».
            rows = (t.get("card") or {}).get("sections", {}).get(self.t.card.ATTENTION) or []
            asked = [r["key"] for r in rows if str(r.get("key", "")).startswith("unproved-")]
            for key in asked:
                self.mark(n, t, self.t.card.ATTENTION, None, key=key, publish=False)
            if asked:
                said = "Владелец разрешил слияние с находкой ревью без доказательства"
                self.mark(n, t, self.t.card.DONE, said, key="merge-allowed", publish=False)
            return o.ready_step(n, t, st)
        if what == "allow":
            t["ci_allowed"] = gate["head"]
            return self.resume(n, t, st, gate)
        if what == "deny" and gate["kind"] == MIGRATION:
            return o.stop(n, t, "владелец не разрешил слияние: находка ревью без доказательства")
        if what == "deny":
            return o.stop(n, t, "владелец не разрешил отправку ветки с правкой файлов CI")
        said = " / ".join(a.strip() for a in answers)
        short = said if len(said) <= 200 else said[:200] + "…"
        o.stop(n, t, f"ответ владельца на вопрос ворот не распознан: «{short}»")

    def resume(self, n: int, t: dict, st, gate: dict) -> None:
        """Отправка после ответа на вопрос ворот — с того шага, который её начал (`source`
        вопроса): коммит «только тест» оркестратора (#344) или «код готов»."""
        if gate.get("source") == "rtest":
            t["phase"] = "rtest"
            return self.orch.rtest().step(n, t)
        return self.orch.ready_step(n, t, st)

    # --- рабочая копия ---

    def align(self, n: int, t: dict) -> str | None:
        """Перед новой сессией: ветка на сервере — ровно последнее отправленное, рабочая копия в
        песочнице его содержит (позади — перемотка). Итог — причина остановки или None."""
        o = self.orch
        sent, worktree = t.get("sent"), o.worktree(n)
        if not sent or worktree is None:
            return None
        st = o.route(n)
        branch = st.branch()
        o.own_repo(refetch=True)
        if (tip := o.remote_tip(branch)) != sent:
            now = f"`{tip[:7]}`" if tip else "её нет"
            return (
                f"код `{CODES['foreign']}`: ветка `{branch}` на сервере — {now}, а последним "
                f"оркестратор отправил `{sent[:7]}` — новая сессия не начата"
            )
        res = o.inside(
            "sh", "-c", ALIGN, "sh", sent, branch, workdir=worktree, env=o.session_env(reader=True)
        )
        if res.returncode == 0:
            return None
        if res.returncode == 5:
            return (
                f"код `{CODES['diverged']}`: рабочая копия в песочнице не содержит отправленного "
                f"`{sent[:7]}` — новая сессия не начата"
            )
        said = self.t.tail(res.stderr or res.stdout)
        return f"рабочая копия не приведена к ветке на сервере: {said}"
