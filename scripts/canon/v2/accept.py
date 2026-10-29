"""accept — «принято» владельца до этапа выката (#347; спека #329, «Выкат»).

Пока карточки выката нет (#195), «принято» владелец даёт по карточке PR слитого тикета перед
ручным выкатом. Решение тикета #347 — как «нет» становится тикетом отката:

- слит тикет пути v2 — оркестратор пишет в тикет вопрос «принято?» с меткой вопроса маршрута
  (`ASK`) и помнит его в своём состоянии (`data["accept"]`);
- каждый такт (`poll`) — ответы владельца после вопроса: «принято» — запись снимается; «нет» с
  причиной — оркестратор заводит **тикет отката** (`ROLLBACK`: что откатить — PR и коммит
  слияния, причина словами владельца, ссылка на слитый тикет — не разделом `## Parent`: иначе
  он стал бы PRD) и ссылку на него пишет в тикет. Тикет отката — без `ready-for-agent`: в
  работу его пускает владелец, как любой другой;
- ответа нет `WAIT_DAYS` дней — запись снимается молча: молчание — не «нет», а выкат за это время
  уже прошёл. Слова владельца после этого — обычные комментарии, тикета отката они не заводят.

С этапом выката (#195) вопрос переходит в карточку выката, а этот файл удаляется.

Удалить: выключить переключатель, затем этот файл; вызовы `ask` в `merged` и `poll` в `tick`
оркестратора.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import re

WAIT_DAYS = 14
# «Нет» — отказ, только когда за ним причина через двоеточие или тире или он один («нет
# замечаний, принято» — не отказ); «не принято» — отказ; «принято» — где угодно, кроме «не …».
NO = re.compile(r"^\W*(?:не\s+принят\w*|нет)\s*(?:[:—–-]|$)", re.I)
YES = re.compile(r"(?<!не )\bпринят", re.I)
ASK = """{mark}
**Принято? (тикет #{n} слит — PR {pr})**

Перед ручным выкатом посмотрите карточку PR {pr}: что сделано, доказательства, что требует
внимания.

@{owner} Ответ — комментарием в этом тикете:
- «принято» — замысел выполнен, вопрос снят;
- «нет: <что не так>» — заведу тикет отката с вашей причиной.

Ответа нет {days} дней — вопрос снимается сам."""
# Без раздела `## Parent`: по нему оркестратор счёл бы слитый тикет PRD (`children`).
ROLLBACK = """## Откуда

Тикет #{n} слит, владелец ответил «нет» на вопрос «принято?» перед выкатом.

## What to build

Откатить изменения тикета #{n}: PR {pr}, {how} — или правкой, если откат по частям.

Причина владельца:

> {why}

## Acceptance criteria

- [ ] Изменения PR {pr} в `main` отменены или исправлены так, что причина владельца снята.
"""


def ask(orch, tools, n: int, pr: str, merge: str | None) -> None:
    """Вопрос «принято?» в слитый тикет и запись в состоянии оркестратора."""
    stamp = tools.stamp()
    owner = orch.repo.split("/")[0]
    mark = tools.core.ASK_MARK.format(stamp=stamp)
    orch.comment(n, ASK.format(mark=mark, n=n, pr=pr, owner=owner, days=WAIT_DAYS))
    orch.data.setdefault("accept", {})[str(n)] = {"stamp": stamp, "pr": pr, "merge": merge}
    tools.say(f"#{n}: вопрос «принято?» перед выкатом — в тикете")


def decide(replies: list[str]) -> tuple[str, str] | None:
    """Последний распознанный ответ: ("yes", текст) или ("no", причина); нет — None."""
    found = None
    for said in replies:
        text = said.strip()
        if NO.search(text):
            found = ("no", NO.sub("", text, count=1).strip(" :—-–.") or text)
        elif YES.search(text):
            found = ("yes", text)
    return found


def poll(orch, tools) -> list[str]:
    """Ответы владельца на «принято?»: «принято» — снять, «нет» — тикет отката, ответа нет к
    сроку — снять (ответы читаются и после срока: «нет», данный вовремя, не теряется, пока такты
    стояли). Тикет отката заводится один раз: его ссылка — в записи до комментария. Сбой — по
    тикету (повтор на следующем такте); итог — сбои."""
    errors, held = [], orch.data.get("accept") or {}
    core, asks = tools.core, tools.asks
    for key, item in list(held.items()):
        n = int(key)
        try:
            found = core.gh_json("issue", "view", key, "--json", "comments") or {}
            question = {"stamp": item["stamp"], "url": f"вопрос «принято?» в тикете #{n}"}
            try:
                replies, _ = asks.answers(
                    core, question, found.get("comments") or [], strict=True, strangers=False
                )
            except asks.NoQuestion:
                del held[key]
                tools.say(f"#{n}: вопроса «принято?» в тикете нет — снят")
                continue
            said = decide(replies or [])
            if said is None:
                if tools.seconds_since(item["stamp"]) > WAIT_DAYS * 86400:
                    del held[key]
                    tools.say(f"#{n}: «принято?» — ответа нет {WAIT_DAYS} дней, вопрос снят")
                continue
            if said[0] == "no":
                if not item.get("rollback"):
                    item["rollback"] = rollback(orch, tools, n, item, said[1])
                orch.comment(n, f"Оркестратор: «нет» записано — тикет отката {item['rollback']}.")
            del held[key]
            tools.say(f"#{n}: ответ на «принято?» — {'принято' if said[0] == 'yes' else 'нет'}")
        except (core.Failure, OSError, ValueError, KeyError) as exc:
            errors.append(f"#{n} «принято?»: {exc}")
            tools.say(f"#{n}: ответ на «принято?» не разобран — {exc}")
    return errors


def rollback(orch, tools, n: int, item: dict, why: str) -> str:
    """Тикет отката; итог — его ссылка."""
    quoted = "\n> ".join(why.splitlines())
    merge = item.get("merge")
    # Авто-мёрж — squash: коммит слияния с одним родителем, откат — без `-m`.
    how = (
        f"коммит слияния `{merge}` (`git revert {merge}`)"
        if merge
        else "коммит слияния не прочитан — он на странице PR"
    )
    body = ROLLBACK.format(n=n, pr=item["pr"], how=how, why=quoted)
    out = tools.core.gh(
        "issue", "create", "--title", f"Откат #{n}: владелец не принял", "--body", body
    )
    found = re.search(r"https://\S+/issues/\d+", out)
    return found.group(0) if found else out.strip()[:200]
