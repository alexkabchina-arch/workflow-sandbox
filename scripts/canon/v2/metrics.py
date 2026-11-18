"""metrics — метрики тикета пути v2 при слиянии (#356; спека #329, «Метрики и журнал событий»).

Набор — общий с партнёром (`ITEMS`), чтобы сравнивать процессы по одним данным: время полное
и машинное; попытки и запуски сессий; ходы; USD по ценам API по ролям; команды владельца;
вопросы и остановки; замечания проверки постановки; дефекты после слияния; минуты CI по
задачам; возвраты по видам; сбои среды — отдельной строкой. Пишутся комментарием в тикет при
слиянии (`lines`) и строкой в `metrics.jsonl` рядом с журналом событий (хранилище оркестратора).

Источники: журнал событий (`v2/journal.py`) попыток тикета — время запусков, ходы, USD по
ролям (дочерние прогоны маршрута — `/verify` — из его состояния); состояние тикета с прошлыми
попытками (`before`) — попытки, запуски, команды, вопросы, остановки, замечания, сбои среды;
состояние последней попытки — возвраты по видам; прогоны Actions ветки тикета — минуты CI по
задачам (правило счёта — `v2/minutes.py`). Дефекты после слияния при слиянии ещё не известны:
их считает сводка PRD по тикетам `escaped-defect`.

Удалить — по списку раздела «Пределы на роль и метрики тикета» в `docs/orchestrator.md` канона.
Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import json
from pathlib import Path

METRICS = "metrics.jsonl"
# Пункт набора → его имя в комментарии.
ITEMS = (
    ("time", "время полное и машинное"),
    ("attempts", "попытки и запуски сессий"),
    ("turns", "ходы"),
    ("usd", "USD по ценам API по ролям"),
    ("commands", "команды владельца"),
    ("stops", "вопросы и остановки"),
    ("intake", "замечания проверки постановки"),
    ("defects", "дефекты после слияния"),
    ("ci", "минуты CI по задачам"),
    ("returns", "возвраты по видам"),
    ("environment", "сбои среды"),
)
# Возвраты последней попытки по видам: имя → как прочитать из состояния тикета.
RETURNS = (
    ("красные проверки и исправления", lambda t: t.get("returns", 0)),
    ("конфликт догона", lambda t: (t.get("catchup") or {}).get("conflicts", 0)),
    ("короткое ревью", lambda t: (t.get("fixes") or {}).get("short", 0)),
    ("проверка посылки", lambda t: len((t.get("limits") or {}).get("parcel") or [])),
    ("повтор после сбоя роли", lambda t: (t.get("limits") or {}).get("retried", 0)),
)
DEFECTS = "при слиянии не известны — сводка PRD считает тикеты `escaped-defect`"


def total(t: dict, key: str) -> int:
    """Счётчик тикета с прошлыми попытками (`before`)."""
    return int(t.get(key) or 0) + int((t.get("before") or {}).get(key) or 0)


def merged_dicts(*found: dict | None) -> dict:
    out: dict = {}
    for d in found:
        for k, v in (d or {}).items():
            out[k] = out.get(k, 0) + v
    return out


def ci_minutes(gh, minutes, repo: str, branch: str) -> dict[str, int]:
    """Оплаченные минуты прогонов Actions ветки `branch` по имени задачи (правило счёта —
    `minutes.job_minutes`, те же запросы, что у суточного счёта). `gh(*args)` — объекты JSON
    вывода `gh`; сбой — исключение вызывающего."""
    found: dict[str, int] = {}
    url = f"repos/{repo}/actions/runs?branch={branch}&per_page=100"
    for run in gh("api", "--paginate", "--jq", minutes.RUN_FIELDS, url):
        of_run = f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=all&per_page=100"
        for job in gh("api", "--paginate", "--jq", minutes.JOB_FIELDS, of_run):
            name = str(job.get("name") or "?")
            found[name] = found.get(name, 0) + minutes.job_minutes(job)
    return found


def collect(
    t: dict, events: list[dict], elapsed: float | None, ci, route: dict | None = None
) -> dict:
    """Метрики тикета: `events` — журнал его попыток, `elapsed` — секунды от первой очереди до
    слияния, `ci` — минуты по задачам или строка сбоя их счёта, `route` — расход дочерних
    прогонов маршрута по ролям (`/verify` идёт внутри сессии, в журнал не пишется)."""
    before = t.get("before") or {}
    roles: dict[str, dict] = {}
    for e in events:
        role = roles.setdefault(str(e.get("role")), {"usd": 0.0, "estimate": 0.0, "runs": 0})
        role["runs"] += 1
        if isinstance(e.get("usd"), (int, float)):
            role["estimate" if e.get("estimated") else "usd"] += e["usd"]
    for name, cost in (route or {}).items():
        role = roles.setdefault(name, {"usd": 0.0, "estimate": 0.0, "runs": 0})
        role["usd"] += cost
    for role in roles.values():
        role["usd"], role["estimate"] = round(role["usd"], 4), round(role["estimate"], 4)
    seconds = [e["seconds"] for e in events if isinstance(e.get("seconds"), (int, float))]
    turns = [e["turns"] for e in events if isinstance(e.get("turns"), int)]
    return {
        "time": {"total": elapsed, "machine": round(sum(seconds), 3)},
        "attempts": {
            "attempts": int(before.get("attempts") or 0) + 1,
            "runs": int(before.get("runs") or 0) + len(t.get("runs") or []),
        },
        "turns": {"turns": sum(turns), "unknown": len(events) - len(turns)},
        "usd": roles,
        "commands": merged_dicts(before.get("commands"), t.get("commands")),
        "stops": {k: total(t, k) for k in ("asked", "reports", "owner_stops")},
        "intake": total(t, "intake_remarks"),
        "defects": None,
        "ci": ci,
        "returns": {name: int(read(t) or 0) for name, read in RETURNS},
        "environment": total(t, "environment_stops"),
    }


def clock(value: float | None) -> str:
    if value is None:
        return "не измерено"
    minutes = round(value / 60)
    return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"


def shown(found: dict, empty: str = "нет") -> str:
    return ", ".join(f"`{k}` {v}" for k, v in found.items() if v) or empty


def lines(record: dict) -> list[str]:
    """Строки комментария при слиянии — по пункту набора на строку, в порядке `ITEMS`."""
    names = dict(ITEMS)
    m = record
    usd = m["usd"]
    spent = sum(r["usd"] for r in usd.values())
    roles = ", ".join(
        f"{role} {r['usd']:.2f}"
        + (f" (+ оценка ≤ {r['estimate']:.2f})" if r["estimate"] else "")
        + (f" за {r['runs']}" if r["runs"] else "")
        for role, r in usd.items()
    )
    turns = f"{m['turns']['turns']}" + (
        f" (у {m['turns']['unknown']} запусков ходы не известны)" if m["turns"]["unknown"] else ""
    )
    stops = m["stops"]
    ci = m["ci"]
    if isinstance(ci, dict):
        ci = f"{sum(ci.values())}: " + shown(ci, "прогонов нет") if ci else "0, прогонов нет"
    else:
        ci = f"не посчитаны — {ci}"
    said = {
        "time": f"полное {clock(m['time']['total'])}, машинное {clock(m['time']['machine'])} "
        "(сумма запусков ролей)",
        "attempts": f"попыток {m['attempts']['attempts']}, запусков сессий {m['attempts']['runs']}",
        "turns": turns,
        "usd": f"{spent:.2f}: {roles}" if roles else "запусков нет",
        "commands": shown(m["commands"]),
        "stops": f"вопросов {stops['asked']}, остановок {stops['reports']} (из них по команде "
        f"владельца {stops['owner_stops']})",
        "intake": str(m["intake"]),
        "defects": DEFECTS,
        "ci": ci,
        "returns": ", ".join(f"{k} {v}" for k, v in m["returns"].items()),
        "environment": str(m["environment"]),
    }
    return [f"- {names[key]}: {said[key]}" for key, _ in ITEMS]


def write(directory: Path, record: dict) -> None:
    """Метрики — строкой в конец `metrics.jsonl` в `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / METRICS, "a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
