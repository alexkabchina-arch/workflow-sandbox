"""minutes — учёт оплаченных минут Actions и их бюджет (#358; спека #329, «Минуты CI»).

Минуты — общие на аккаунт: 3000 в месяц на все приватные репозитории владельца (публичные
бесплатны). Оркестратор раз в сутки считает прошедшие сутки (UTC, как счёт GitHub) по прогонам
всех приватных репозиториев аккаунта `canon.agentMinutes` и складывает сутки месяца:

- каждая задача прогона (все попытки) округляется вверх до целой минуты, с множителем ОС
  раннера (`MULTIPLIERS`); пропущенная задача и свой раннер (`self-hosted`) — 0;
- платны и прогоны самих обновлений Dependabot (`dynamic/dependabot/…`): по сверке с оплатой
  GitHub (`docs/control-runs/2026-10-26-ci-minutes.md`) они в счёте, вопреки посылке тикета;
- статья — по репозиторию (`ARTICLES`), прогон по расписанию — «ночи», прочее — «запас».

Пороги — от месячного итога (`TOTAL`): `WARN` — сообщение владельцу, `HOLD` — новые тикеты не
берутся. Бюджет статей — на время исключения 3б (#328); после него — воркфлоу 1000, запас 500.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import math
from datetime import datetime

TOTAL = 3000
BUDGET = {"ozon": 1200, "workflow": 0, "nights": 300, "reserve": 1500}
NAMES = {"ozon": "ozon", "workflow": "воркфлоу", "nights": "ночи", "reserve": "запас"}
ARTICLES = {"ozon-analytics": "ozon", "workflow": "workflow"}  # имя репозитория → статья
WARN, HOLD = 0.8, 0.95
MULTIPLIERS = (("macos", 10), ("windows", 2))  # метка раннера → множитель; иначе Linux, 1
# Только нужные поля: прогоны и задачи целиком — мегабайты JSON в сутки.
RUN_FIELDS = ".workflow_runs[] | {id, event}"
JOB_FIELDS = ".jobs[] | {started_at, completed_at, conclusion, labels}"

if sum(BUDGET.values()) != TOTAL:
    raise ValueError(f"бюджет статей {sum(BUDGET.values())} ≠ {TOTAL}")


def parse(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def article(repo: str, run: dict) -> str:
    if run.get("event") == "schedule":
        return "nights"
    return ARTICLES.get(repo.rsplit("/", 1)[-1], "reserve")


def job_minutes(job: dict) -> int:
    """Оплаченные минуты задачи: вверх до целой минуты, с множителем ОС; не шла — 0."""
    labels = [str(x).lower() for x in job.get("labels") or []]
    if job.get("conclusion") == "skipped" or "self-hosted" in labels:
        return 0
    began, ended = parse(job.get("started_at")), parse(job.get("completed_at"))
    if began is None or ended is None:
        return 0
    minutes = math.ceil(max((ended - began).total_seconds(), 0) / 60)
    factor = next((m for key, m in MULTIPLIERS if any(key in x for x in labels)), 1)
    return minutes * factor


def day(gh, account: str, date: str) -> dict[str, int]:
    """Минуты суток `date` (UTC, `ГГГГ-ММ-ДД`) по статьям. `gh(*args)` — объекты JSON вывода
    `gh` (по строке на объект); сбой — исключение вызывающего."""
    spent = dict.fromkeys(BUDGET, 0)
    for repo in gh(
        "repo", "list", account, "--visibility", "private", "--limit", "1000",
        "--json", "nameWithOwner", "--jq", ".[]",
    ):  # fmt: skip
        name = repo["nameWithOwner"]
        span = f"{date}T00:00:00Z..{date}T23:59:59Z"
        url = f"repos/{name}/actions/runs?created={span}&per_page=100"
        for run in gh("api", "--paginate", "--jq", RUN_FIELDS, url):
            of_run = f"repos/{name}/actions/runs/{run['id']}/jobs?filter=all&per_page=100"
            jobs = gh("api", "--paginate", "--jq", JOB_FIELDS, of_run)
            spent[article(name, run)] += sum(job_minutes(j) for j in jobs)
    return spent


def total(days: dict) -> dict[str, int]:
    """Сумма суток месяца по статьям."""
    spent = dict.fromkeys(BUDGET, 0)
    for counted in days.values():
        for key, value in counted.items():
            spent[key] = spent.get(key, 0) + value
    return spent


def summary(spent: dict[str, int]) -> str:
    """«2412 из 3000 (80 %): ozon 1500 из 1200, …»; доля — вниз до целого: 2849 — «94 %», а
    не «95 %» без паузы."""
    used = sum(spent.values())
    parts = ", ".join(f"{NAMES.get(k, k)} {spent.get(k, 0)} из {v}" for k, v in BUDGET.items())
    return f"{used} из {TOTAL} ({used * 100 // TOTAL} %): {parts}"
