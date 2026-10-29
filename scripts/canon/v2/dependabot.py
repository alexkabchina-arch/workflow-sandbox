"""dependabot — своя дорожка PR Dependabot в воротах пути v2 (#350; спека #329, «Dependabot»).

PR Dependabot — не тикет: у него нет сессии, проверки в песочнице, теста ревьюера и «Владений».
Каждый такт (`poll`, после шагов мест: тикеты берут место слияния первыми) оркестратор проекта на
пути v2 читает открытые PR Dependabot в `main` и ведёт их своим **набором условий допуска** — без
условий 4 (приёмка) и 6 (тикет v2) спеки:

1. полный CI на head PR зелёный (PR Dependabot — не черновик: CI идёт полным прогоном);
2. ревью: patch — без ревью; minor и major — ревью ошибок обновления (`review`, роль `review`
   таблицы; вход — заголовок PR и набор обновлений, без тела PR: заметки выпуска пишет автор
   пакета, это чужой текст) и **«да» владельца** комментарием в PR. Ревью и «да» — на набор
   обновлений (`updates`: имя, версия, вид), а не на head: догон `@dependabot rebase` их не
   повторяет, новая версия того же пакета в том же PR — повторяет. Ревью — не больше одного за
   такт;
5. ветка свежая — в ней последняя основа;
7. ослабление защиты, найденное ревью, — в вопросе владельцу: «да» — его решение.

Вид обновления — из блока `updated-dependencies` сообщений коммитов (как
`dependabot/fetch-metadata`): patch — только если у каждой зависимости `update-type:
version-update:semver-patch`; вида нет — как major. **PR целиком от Dependabot**: ветка
`dependabot/…` и каждый коммит у GitHub — автор `dependabot[bot]` с проверенной подписью
(`verification.verified`): имя и почту автора git задаёт любой, кто пушит, подпись GitHub — нет.
Иначе (правка поверх) — дорожка PR не ведёт.

**Слияние по одному — вместе с тикетами** (`v2/catchup.py`, место `merging`): условия выполнены, а
сливается другой — PR ждёт. Очередь дошла — PR занимает место (`queued`); отстала ветка —
**догон командой `@dependabot rebase`** (PR не перестраиваются сами: `rebase-strategy: disabled`):
команду Dependabot принимает только от пользователя с правом записи, от App и
`github-actions[bot]` — нет («only users with push access», dependabot-core#9147), поэтому её
пишет `gh` учёткой владельца на Mac (без токена оркестратора). Новый head — полный CI заново.
Свежая и зелёная — отметка `review` **токеном оркестратора** (лицо агента, правило сервера
`protect-main --v2`) и авто-мёрж `--match-head-commit`; слияние делает GitHub.

**PR встал на этом head** (`parked`) — место отпускается, дорожка ждёт нового head от Dependabot
(или владельца): догон не пришёл за `REBASE_WAIT`, авто-мёрж не слил за `MERGE_WAIT` или PR в
конфликте — тикет на владельца; владелец выключил авто-мёрж — придержан, без тикета. Смена набора
обновлений у PR на месте — место отпускается до нового ревью и «да».

**Красный CI** — тикет на владельца (`issue create`, раз на head; новый красный head — комментарий
в том же тикете), авто-мёрж выключен, место свободно; зелёный после починки — PR идёт дальше сам.
«Нет» владельца — дорожка PR больше не трогает (закрывает его владелец).

Удалить: выключить переключатель, затем этот файл; `dependabot()` и его вызов в `tick`
оркестратора, PR Dependabot в `active` у `v2/catchup.py`; `canon-sync` снова ставит
`dependabot-review` проектам на пути v2 (`V2_SKIP`).
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time

AUTHOR = "app/dependabot"
BOT = "dependabot[bot]"
BOT_MAIL = "+dependabot[bot]@users.noreply.github.com"
BASE = "main"
LIST_LIMIT = "200"  # `gh pr list` по умолчанию — 30: не попавший PR счёлся бы ушедшим
FIELDS = (
    "number,url,state,isDraft,title,baseRefName,headRefName,headRefOid,mergeable,"
    "mergeStateStatus,statusCheckRollup,autoMergeRequest,commits,comments"
)
PATCH, MINOR, MAJOR = "patch", "minor", "major"
# Значение — скаляр YAML в строке ключа: Dependabot берёт его в кавычки (`dependency-version:
# '26.3'` — прогон #350, `dependency-name: "@types/node"`); пустое значение — пусто, не следующая
# строка.
VALUE = r"[ \t]*['\"]?([^\s'\"]*)"
NAME = re.compile(rf"^\s*-?[ \t]*dependency-name:{VALUE}", re.M)
FIELD = re.compile(rf"^[ \t]*(dependency-version|update-type):{VALUE}", re.M)
SEMVER = re.compile(r"version-update:semver-(patch|minor|major)")
REBASE = "@dependabot rebase"
REBASE_WAIT = 1800  # догон Dependabot не пришёл — PR встал: место отпускается, тикет
MERGE_WAIT = 3600  # авто-мёрж не слил после отметки — PR встал: место отпускается, тикет
HOLDING = ("queued", "merge")  # PR держит место слияния (`BOT_ACTIVE` у `v2/catchup.py`)
DESCRIPTION = {
    PATCH: "Dependabot patch: полный CI зелёный, ревью ошибок не нужно",
    MINOR: "Dependabot minor: полный CI зелёный, ревью и «да» владельца",
    MAJOR: "Dependabot major: полный CI зелёный, ревью и «да» владельца",
}
ASK = """{mark}
**Слить обновление? (PR Dependabot, {kind})**

{updates}

Полный CI на `{sha}` зелёный. {review}

@{owner} Ответ — комментарием в этом PR:
- «да» — сольётся по очереди слияния: догон — `@dependabot rebase`, ревью и вопрос не
  повторяются (повторятся, если Dependabot сменит версию в этом PR);
- «нет» — PR останется как есть: оркестратор его не трогает, закройте его сами.
{report}"""
RED_TICKET = """## Откуда

PR Dependabot {url} ({kind}) — красный CI на `{sha}`:

{red}

Обновление: {updates}

## Что сделать

Само обновление не сольётся. Посмотрите упавшие проверки:
- поломка от обновления — закройте PR или поручите совместимость отдельным тикетом;
- сбой CI — перезапустите проверки: зелёный CI — PR пойдёт дальше сам.

@{owner}"""
STUCK_TICKET = """## Откуда

PR Dependabot {url} ({kind}) встал на `{sha}`: {why}.

## Что сделать

{what} Место слияния отпущено: тикеты сливаются дальше. Новый коммит Dependabot в PR — дорожка
поведёт его снова.

@{owner}"""
STUCK = {
    "late": (
        "очередь слияния дошла, ветка отстала от `main`, а `@dependabot rebase` не догнал её за "
        f"{REBASE_WAIT // 60} мин",
        "Посмотрите ответ Dependabot в PR: `@dependabot recreate` — пересоздать PR; не нужен — "
        "закройте.",
    ),
    "unmerged": (
        f"отметка `review` и авто-мёрж стоят, а GitHub не слил PR за {MERGE_WAIT // 60} мин",
        "Посмотрите, чего ждёт GitHub на странице PR: одобрения CODEOWNERS (обновление "
        "`.github/`), обязательной проверки. Одобрите или закройте PR.",
    ),
    "conflict": (
        "PR в конфликте с `main`",
        "`@dependabot recreate` — пересоздать PR на новой основе; не нужен — закройте.",
    ),
}


def updates(pr: dict) -> list[list[str]]:
    """Набор обновлений PR из блоков `updated-dependencies` сообщений коммитов: [имя, версия,
    вид]; поля нет — пусто."""
    found = []
    for commit in pr.get("commits") or []:
        text = f"{commit.get('messageHeadline') or ''}\n{commit.get('messageBody') or ''}"
        names = list(NAME.finditer(text))
        for i, name in enumerate(names):
            end = names[i + 1].start() if i + 1 < len(names) else len(text)
            fields = dict(m.groups() for m in FIELD.finditer(text, name.end(), end))
            item = [
                name.group(1),
                fields.get("dependency-version", ""),
                fields.get("update-type", ""),
            ]
            if item not in found:
                found.append(item)
    return found


def level(found: list[list[str]]) -> str:
    """Вид PR: patch — только если каждая зависимость patch; вида нет — major."""
    kinds = [(SEMVER.fullmatch(kind) or [None, MAJOR])[1] for _, _, kind in found]
    if kinds and all(k == PATCH for k in kinds):
        return PATCH
    return MAJOR if not kinds or MAJOR in kinds else MINOR


def by_dependabot(pr: dict) -> bool:
    """Дешёвая сверка до запросов: ветка Dependabot, и каждый коммит назван его. Подделку имени
    закрывает `Dependabot.signed`: подпись GitHub."""
    if not str(pr.get("headRefName") or "").startswith("dependabot/"):
        return False
    commits = pr.get("commits") or []
    return bool(commits) and all(
        c.get("authors")
        and all(
            BOT in (a.get("login"), a.get("name")) or str(a.get("email") or "").endswith(BOT_MAIL)
            for a in c["authors"]
        )
        for c in commits
    )


def shown(found: list[list[str]]) -> str:
    if not found:
        return "блока `updated-dependencies` в коммитах нет — вид неизвестен, как major"
    return "; ".join(
        f"`{name}` {version or '?'} ({kind.removeprefix('version-update:') or 'вид неизвестен'})"
        for name, version, kind in found
    )


class Dependabot:
    """Дорожка PR Dependabot для оркестратора `orch`; `tools` — его помощники: `core`, `asks`,
    `send`, `start`, `journal`, `roles`, `ci_green`, `run`, `without_tokens`, `say`, `stamp`,
    `seconds_since`, `tail`, `HERE`, `REVIEW_LIMIT`, `REVIEW_TIMEOUT`."""

    def __init__(self, orch, tools):
        self.orch, self.t = orch, tools
        self.reviewed = False  # ревью в этом такте уже было

    @property
    def held(self) -> dict:
        return self.orch.data.setdefault("dependabot", {})

    def owner(self) -> str:
        return self.orch.repo.split("/")[0]

    # --- такт ---

    def poll(self) -> list[str]:
        """Открытые PR Dependabot — по шагу каждому; ушедшие из открытых — итог (слит: основа
        ушла у тикетов). Любой сбой — по PR (места и прочий такт идут дальше), повтор на
        следующем такте; итог — сбои."""
        core, errors = self.t.core, []
        try:
            listed = core.gh_json(
                "pr", "list", "--author", AUTHOR, "--state", "open", "--limit", LIST_LIMIT,
                "--json", "number,url",
            )  # fmt: skip
        except core.Failure as exc:
            self.t.say(f"PR Dependabot не прочитаны — {exc}")
            return [f"PR Dependabot: {exc}"]
        opened = {str(p["number"]): p["url"] for p in listed or []}
        steps = [(k, self.gone, (k,)) for k in self.held if k not in opened]
        steps += [(k, self.step, (k, url)) for k, url in opened.items()]
        for key, do, args in steps:
            try:
                do(*args)
            except Exception as exc:  # noqa: BLE001 — любой сбой одного PR (как у мест, #275)
                errors.append(f"PR Dependabot #{key}: {exc}")
                self.t.say(f"PR Dependabot #{key}: шаг не выполнен — {exc}")
        return errors

    def gone(self, key: str) -> None:
        """PR нет среди открытых: слит — у тикетов основа ушла; закрыт — забыт; всё ещё открыт
        (список неполон) — ничего."""
        e = self.held[key]
        pr = self.t.core.gh_json("pr", "view", e["url"], "--json", "state") or {}
        if pr.get("state") == "OPEN":
            return
        self.release(key)
        del self.held[key]
        if pr.get("state") == "MERGED":
            for other in self.orch.data["tickets"].values():
                other["behind"] = True
            self.t.say(f"PR Dependabot #{key}: слит")
        else:
            self.t.say(f"PR Dependabot #{key}: закрыт без слияния — забыт")

    def step(self, key: str, url: str) -> None:
        """Условия допуска PR по порядку; первое невыполненное — его действие и конец шага."""
        pr = self.t.core.gh_json("pr", "view", url, "--json", FIELDS) or {}
        e = self.held.setdefault(key, {"url": url})
        head = pr["headRefOid"]
        if pr.get("baseRefName", BASE) != BASE or not by_dependabot(pr) or not self.signed(e, pr):
            if e.get("foreign") != head:
                e["foreign"] = head
                why = f"база `{pr.get('baseRefName')}`, а не `{BASE}`, или коммит не от Dependabot"
                self.t.say(f"PR Dependabot #{key}: {why} — дорожка не ведёт")
            return self.release(key)
        e.pop("foreign", None)
        found = updates(pr)
        if e.get("updates") != found:  # новый набор: ревью и «да» — заново, место — свободно
            self.release(key)
            for stale in ("reviewed", "asked", "yes", "declined"):
                e.pop(stale, None)
            e.update(updates=found, kind=level(found))
        if e.get("declined"):
            return self.release(key)
        if (e.get("parked") or {}).get("head") == head:
            return self.release(key)  # встал на этом head: ждём нового коммита Dependabot
        e.pop("parked", None)
        if red := self.t.start.failed_checks(pr):
            return self.red(key, e, pr, red)
        if not self.t.ci_green(pr):
            return  # CI идёт (или идёт на новом head после догона)
        if e["kind"] != PATCH:
            if not e.get("reviewed") and not self.review(key, e, pr):
                return
            if not e.get("yes"):
                return self.answer(key, e, pr)
        if why := self.orch.catchup().waits(int(key)):
            if e.get("held_by") != why:
                self.t.say(f"PR Dependabot #{key}: слияние ждёт — {why}")
            e["held_by"] = why
            return
        e.pop("held_by", None)
        if self.t.start.is_conflicting(pr):
            return self.park(key, e, head, "conflict")
        if not self.fresh(head):
            return self.rebase(key, e, head)
        if e.get("phase") == "merge" and e.get("marked") == head:
            return self.merging(key, e, pr)
        self.mark(key, e, pr)

    # --- условия ---

    def signed(self, e: dict, pr: dict) -> bool:
        """Каждый коммит ветки у GitHub — автор `dependabot[bot]` с проверенной подписью;
        проверенные помнятся (`signed`)."""
        known, core = e.setdefault("signed", []), self.t.core
        for c in pr.get("commits") or []:
            oid = c.get("oid")
            if oid in known:
                continue
            data = core.gh_json("api", f"repos/{self.orch.repo}/commits/{oid}") or {}
            verified = ((data.get("commit") or {}).get("verification") or {}).get("verified")
            if not oid or verified is not True or (data.get("author") or {}).get("login") != BOT:
                return False
            known.append(oid)
        del known[:-50]
        return True

    def fresh(self, head: str) -> bool:
        """В ветке последняя основа (`origin/main` своего клона — с GitHub, раз за такт)."""
        self.orch.own_repo()
        return self.orch.ancestor("origin/main", head)

    def red(self, key: str, e: dict, pr: dict, red: list[str]) -> None:
        """Красный CI: авто-мёрж выключен (перезапуск CI не сольёт PR мимо очереди), место
        слияния — свободно; тикет на владельца — раз на head."""
        core, owner, head = self.t.core, self.owner(), pr["headRefOid"]
        if pr.get("autoMergeRequest"):
            core.gh("pr", "merge", e["url"], "--disable-auto")
        self.release(key)
        if e.get("red") == head:
            return
        listed = "\n".join(f"- {r}" for r in red)
        if e.get("ticket"):
            number = e["ticket"].rstrip("/").rsplit("/", 1)[-1]
            text = f"Оркестратор: PR Dependabot {e['url']} снова красный на `{head[:7]}`:\n\n"
            core.gh("issue", "comment", number, "--body", f"{text}{listed}\n\n@{owner}")
        else:
            body = RED_TICKET.format(
                url=e["url"], kind=e["kind"], sha=head[:7], red=listed,
                updates=shown(e["updates"]), owner=owner,
            )  # fmt: skip
            title = f"Dependabot: красный CI на PR #{key} — {pr.get('title') or ''}".rstrip(" —")
            e["ticket"] = self.issue(title, body)
        e["red"] = head
        self.t.say(f"PR Dependabot #{key}: красный CI на {head[:7]} — тикет на владельца")

    def issue(self, title: str, body: str) -> str:
        out = self.t.core.gh("issue", "create", "--title", title, "--body", body)
        found = re.search(r"https://\S+/issues/\d+", out)
        return found.group(0) if found else out.strip()[:200]

    def review(self, key: str, e: dict, pr: dict) -> bool:
        """Ревью ошибок обновления minor и major — раз на набор обновлений, одно за такт, в
        пределе `REVIEW_LIMIT`; паузы его держат. Сбой ревью — в вопросе владельцу, а не повтор
        за деньги. True — ревью есть (или его сбой записан)."""
        o, t = self.orch, self.t
        if self.reviewed or o.hold(int(key), "ревью ошибок PR Dependabot"):
            return False
        self.reviewed = True
        head = pr["headRefOid"]
        reviews = o.dir / "reviews"
        reviews.mkdir(parents=True, exist_ok=True)
        ticket = reviews / f"dependabot-{key}.issue.md"
        text = (
            f"# PR Dependabot #{key}: {pr.get('title') or ''}\n\n"
            f"Обновление зависимостей ({e['kind']}): {shown(e['updates'])}.\n"
        )
        ticket.write_text(text, encoding="utf-8")
        report = reviews / f"dependabot-{key}.json"
        report.unlink(missing_ok=True)
        o.own_repo(refetch=True)
        checkout = o.checkout("dependabot", int(key), head)
        env = t.without_tokens()
        env["PATH"] = f"{o.claude_shim()}:{env.get('PATH', '')}"
        env[t.roles.V2_ENV] = "1"
        budget = f"{t.REVIEW_LIMIT:.2f}"
        argv = [sys.executable, str(t.HERE / "review"), str(report), "--max-budget-usd", budget]
        t.say(f"PR Dependabot #{key}: ревью ошибок head {head[:7]}")
        result, started, clock = {"head": head}, t.stamp(), time.monotonic()
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
        ok = res is not None and res.returncode == 0 and isinstance(data, dict)
        if not ok:
            said = "не кончилось вовремя" if res is None else t.tail(res.stderr or res.stdout)
            result["failed"] = said or "отчёта нет"
        else:
            cost = data.get("total_cost_usd")
            result["cost"] = cost if isinstance(cost, (int, float)) else 0
            canon = data.get("canon_review") if isinstance(data.get("canon_review"), dict) else {}
            result["weakening"] = canon.get("weakening") or None
            md = report.with_suffix(".md")
            result["text"] = md.read_text(encoding="utf-8") if md.exists() else ""
        e["reviewed"] = result
        self.log(key, data if ok else None, time.monotonic() - clock, started, result, budget)
        return True

    def log(self, key, data, measured, started, result, budget) -> None:
        """Ревью — событием в журнал пути v2 (`dependabot` — номер PR, а не тикета); без отчёта
        расход — оценка бюджетом. Сбой записи — строкой."""
        t = self.t
        usd = result.get("cost")
        try:
            t.journal.write(
                self.orch.dir,
                {
                    "at": t.stamp(),
                    **t.journal.event(
                        t.roles, int(key), "review", data, measured, dependabot=int(key),
                        started=started, ended=t.stamp(),
                        usd=float(budget) if usd is None else usd, estimated=usd is None,
                        outcome="error" if result.get("failed") else "ok",
                    ),
                },
            )  # fmt: skip
        except OSError as exc:
            t.say(f"PR Dependabot #{key}: журнал v2 не записан — {exc}")

    def answer(self, key: str, e: dict, pr: dict) -> None:
        """«Да» владельца на слияние minor и major: вопрос — раз; ответ — комментарием в PR."""
        core, asks, send = self.t.core, self.t.asks, self.t.send
        if e.get("asked"):
            question = {"stamp": e["asked"], "url": f"вопрос в PR Dependabot #{key}"}
            try:
                replies, _ = asks.answers(
                    core, question, pr.get("comments") or [], strict=True, strangers=False
                )
            except asks.NoQuestion:
                e.pop("asked")  # вопрос удалён — задать снова
            else:
                decided = [d for d in (send.Send.decide(send.MERGE, r) for r in replies) if d]
                if decided and decided[-1] == "allow":
                    e["yes"] = True
                    self.t.say(f"PR Dependabot #{key}: «да» владельца — в очередь слияния")
                    return self.step(key, e["url"])
                if decided:
                    e["declined"] = True
                    text = "Оркестратор: «нет» записано — PR не трогаю, закройте его сами."
                    core.gh("pr", "comment", e["url"], "--body", text)
                    self.t.say(f"PR Dependabot #{key}: «нет» владельца")
                return
        stamp = self.t.stamp()
        r = e["reviewed"]
        if r.get("failed"):
            review = f"Ревью ошибок не состоялось: {r['failed'][:300]}."
            report = ""
        else:
            review = f"Ревью ошибок ({r.get('cost', 0):.2f} USD) — отчёт ниже."
            if r.get("weakening"):
                review += f" **Ревью нашло ослабление защиты:** {str(r['weakening'])[:300]}."
            report = (
                f"\n<details><summary>Отчёт ревью</summary>\n\n{r.get('text', '')[:50000]}\n\n"
                "</details>\n"
            )
        body = ASK.format(
            mark=core.ASK_MARK.format(stamp=stamp), kind=e["kind"], updates=shown(e["updates"]),
            sha=pr["headRefOid"][:7], review=review, owner=self.owner(), report=report,
        )  # fmt: skip
        core.gh("pr", "comment", e["url"], "--body", body)
        e["asked"] = stamp
        self.t.say(f"PR Dependabot #{key} ({e['kind']}): вопрос «слить?» владельцу — в PR")

    # --- очередь слияния ---

    def rebase(self, key: str, e: dict, head: str) -> None:
        """Очередь дошла, ветка отстала: место — за PR, `@dependabot rebase` учёткой владельца —
        раз на основу; догон не пришёл за `REBASE_WAIT` — PR встал на этом head."""
        base = self.orch.git("rev-parse", "origin/main").stdout.strip()
        rebased = e.get("rebased") or {}
        if rebased.get("base") == base and rebased.get("head") == head:
            self.hold(key, e)
            if self.t.seconds_since(rebased["at"]) > REBASE_WAIT:
                self.park(key, e, head, "late")
            return
        self.hold(key, e)
        res = self.t.run(
            "gh", "pr", "comment", e["url"], "--body", REBASE,
            cwd=self.orch.main, env=self.t.without_tokens(),
        )  # fmt: skip
        if res.returncode != 0:  # учётка владельца на Mac: место отпускаем, повтор — такт
            self.release(key)
            raise self.t.core.Failure(f"`{REBASE}` не отправлен: {self.t.tail(res.stderr)}")
        e["rebased"] = {"base": base, "head": head, "at": self.t.stamp()}
        self.t.say(f"PR Dependabot #{key}: очередь дошла — `{REBASE}` (основа {base[:7]})")

    def merging(self, key: str, e: dict, pr: dict) -> None:
        """Отметка и авто-мёрж стоят: ждём слияния GitHub — до `MERGE_WAIT`; авто-мёрж выключил
        владелец — PR придержан."""
        head = pr["headRefOid"]
        if not pr.get("autoMergeRequest"):
            e["parked"] = {"head": head, "why": "owner"}
            self.release(key)
            return self.t.say(f"PR Dependabot #{key}: авто-мёрж выключен — PR придержан")
        if self.t.seconds_since(e.get("marked_at") or self.t.stamp()) > MERGE_WAIT:
            self.park(key, e, head, "unmerged")

    def park(self, key: str, e: dict, head: str, why: str) -> None:
        """PR встал на этом head: тикет на владельца (до записи — сбой его повторит), место —
        свободно."""
        said, what = STUCK[why]
        if pr_auto := e.get("phase") == "merge":
            self.t.core.gh("pr", "merge", e["url"], "--disable-auto")
        body = STUCK_TICKET.format(
            url=e["url"], kind=e["kind"], sha=head[:7], why=said, what=what, owner=self.owner()
        )
        self.issue(f"Dependabot: PR #{key} встал — {said.split(',')[0]}", body)
        e["parked"] = {"head": head, "why": why}
        self.release(key)
        auto = ", авто-мёрж выключен" if pr_auto else ""
        self.t.say(f"PR Dependabot #{key}: встал — {said}{auto}; тикет на владельца")

    def hold(self, key: str, e: dict) -> None:
        """PR занимает место слияния (до слияния, красного CI или остановки)."""
        if e.get("phase") not in HOLDING:
            e["phase"] = "queued"
        self.orch.catchup().marked(int(key))

    def mark(self, key: str, e: dict, pr: dict) -> None:
        """Отметка `review` токеном оркестратора (лицо агента) на head и авто-мёрж этого
        head."""
        core, head = self.t.core, pr["headRefOid"]
        self.hold(key, e)
        core.gh(
            "api", "-X", "POST", f"repos/{self.orch.repo}/statuses/{head}",
            "-f", "state=success", "-f", f"context={self.t.start.REVIEW}",
            "-f", f"description={DESCRIPTION[e['kind']]}",
        )  # fmt: skip
        core.gh("pr", "merge", e["url"], "--auto", "--squash", "--match-head-commit", head)
        e.update(phase="merge", marked=head, marked_at=self.t.stamp())
        self.t.say(f"PR Dependabot #{key} ({e['kind']}): отметка review и авто-мёрж на {head[:7]}")

    def release(self, key: str) -> None:
        """Место слияния — свободно."""
        e = self.held.get(key) or {}
        if e.get("phase") in HOLDING:
            e["phase"] = None
            for stale in ("marked", "marked_at"):
                e.pop(stale, None)
        self.orch.catchup().release(int(key))
