#!/usr/bin/env python3
"""Мягкое предупреждение о длине `CLAUDE.md` для CI проекта.

Файл вендорится из канона `alexkabchina-arch/workflow` и лежит под `.canon.lock`: руками не
править, обновлять через `canon-sync`. Вызывается шагом CI проекта из корня проекта:

    python3 scripts/canon/claude_md_length.py

Пишет «`CLAUDE.md` — N строк, ориентир 200» (из корня, только корневой `CLAUDE.md`). Импорт —
строка `@<путь>` целиком вне блока ```, путь от корня проекта, файл есть — идёт в счёт один раз:
Claude Code грузит его в контекст вместе с `CLAUDE.md`; тогда «`CLAUDE.md` с импортами — N строк».
Импорт внутри строки и вложенный не считаются — счёт приблизительный, как и сам ориентир.
Нет строки `@.claude/canon/process.md` или файла — `::warning`: без них обязательный раздел
канона «Процесс — канон» молча выпадает из контекста сессии (#144).
Длиннее ориентира — аннотация GitHub Actions `::warning` (жёлтое предупреждение в PR).
Код выхода всегда 0: длина коммит и мёрж не блокирует.
"""

from __future__ import annotations

import sys
from pathlib import Path

NAME = "CLAUDE.md"
TARGET = 200
CANON_SECTION = ".claude/canon/process.md"


def plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "строка"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "строки"
    return "строк"


def count(data: bytes) -> int:
    # Как `wc -l` и редактор: только \n, плюс последняя строка без перевода строки.
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:  # слишком длинное имя, нет доступа — не импорт, шаг не падает
        return False


def imports(root: Path, data: bytes) -> list[str]:
    found, fence = [], False
    for line in data.decode(errors="replace").split("\n"):
        line = line.strip()
        if line.startswith("```"):
            fence = not fence
        elif not fence and line.startswith("@") and " " not in line:
            rel = line[1:]
            if rel != NAME and rel not in found and is_file(root / rel):
                found.append(rel)
    return found


def report(root: Path) -> str:
    path = root / NAME
    if not path.is_file():
        return f"{NAME} нет — проверять нечего"
    data = path.read_bytes()
    extra = imports(root, data)
    lines = count(data)
    for rel in extra:
        try:
            lines += count((root / rel).read_bytes())
        except OSError:
            pass
    name = f"{NAME} с импортами" if extra else NAME
    message = f"{name} — {lines} {plural(lines)}, ориентир {TARGET}"
    if lines > TARGET:
        message = f"::warning file={NAME},title=Длина {NAME}::{message}"
    if CANON_SECTION not in extra:
        section = (
            f"::warning file={NAME},title=Нет раздела канона::в {NAME} нет строки "
            f"@{CANON_SECTION} или файла — обязательный раздел «Процесс — канон» не грузится"
        )
        message = f"{section}\n{message}"
    return message


def main() -> int:
    print(report(Path.cwd()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
