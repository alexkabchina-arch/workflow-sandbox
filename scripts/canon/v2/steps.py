"""steps — шаги тикета пути v2 по фазе оркестратора (#333; спека #329, порядок перестройки).

Граница: общее — такт, очередь, места, метки-команды, обёртки `gh` и `sbx`, песочница — в
`orchestrator` и служит обоим путям; здесь — только шаги, которые v2 меняет. Каркас: путь v2
зовёт нынешние шаги оркестратора, копии старого кода нет; следующие тикеты перестройки заменяют
их здесь по одному. После переключения (#361) шаги, оставшиеся только у старого пути, удаляются
из `orchestrator`.

Файл вендорится из канона `alexkabchina-arch/workflow`: руками не править.
Только стандартная библиотека Python 3.
"""

from __future__ import annotations


def steps(orch) -> dict:
    """Шаг тикета пути v2 по фазе: `(номер, тикет) -> None`. CI — нынешний шаг с разбором причин
    красного по отчёту о каждом тесте (`v2/ci.py`, #335) и допуском к слиянию (`v2/gates.py`,
    #346); тест ревьюера между ревью и разбором (`v2/rtest.py`, #344). CI и мёрж начинаются с
    висящего догона на сервере и долга `/verify` после него (`v2/catchup.py`, #348)."""

    def settled(n: int, t: dict) -> bool:
        catchup = orch.catchup()
        return catchup.settle(n, t, "ci") and catchup.verify_again(n, t)

    def ci(n: int, t: dict) -> None:
        if settled(n, t):
            orch.ci(n, t, cause=orch.cause(), gate=orch.gates())

    def merge(n: int, t: dict) -> None:
        if settled(n, t) and t["phase"] == "merge":  # догон на мёрже — снова шаг CI
            orch.merge(n, t)

    def rtest(n: int, t: dict) -> None:
        orch.rtest().step(n, t)

    return {"session": orch.session_over, "rtest": rtest, "ci": ci, "merge": merge}
