#!/usr/bin/env python3
"""Мягкое предупреждение о длине `CLAUDE.md` для CI проекта.

Файл вендорится из канона `alexkabchina-arch/workflow` и лежит под `.canon.lock`: руками не
править, обновлять через `canon-sync`. Вызывается шагом CI проекта из корня проекта:

    python3 scripts/canon/claude_md_length.py

Пишет «`CLAUDE.md` — N строк, ориентир 200» (из корня, только корневой `CLAUDE.md`).
Длиннее ориентира — аннотация GitHub Actions `::warning` (жёлтое предупреждение в PR).
Код выхода всегда 0: длина коммит и мёрж не блокирует.
"""

from __future__ import annotations

import sys
from pathlib import Path

NAME = "CLAUDE.md"
TARGET = 200


def plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "строка"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "строки"
    return "строк"


def report(root: Path) -> str:
    path = root / NAME
    if not path.is_file():
        return f"{NAME} нет — проверять нечего"
    data = path.read_bytes()
    # Как `wc -l` и редактор: только \n, плюс последняя строка без перевода строки.
    lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    message = f"{NAME} — {lines} {plural(lines)}, ориентир {TARGET}"
    if lines > TARGET:
        return f"::warning file={NAME},title=Длина {NAME}::{message}"
    return message


def main() -> int:
    print(report(Path.cwd()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
