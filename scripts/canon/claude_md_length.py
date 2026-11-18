#!/usr/bin/env python3
"""Мягкое предупреждение о длине `AGENTS.md` для CI проекта и итога `canon-sync`.

Файл вендорится из канона `alexkabchina-arch/workflow` и лежит под `.canon.lock`: руками не
править, обновлять через `canon-sync`. Вызывается шагом CI проекта из корня проекта:

    python3 scripts/canon/claude_md_length.py

Имя — прежнее (шаг CI проектов его зовёт), проверяет файл правил `AGENTS.md` (#454): пишет
«`AGENTS.md` — N строк, ориентир 200» (из корня, только корневой). Часть воркфлоу стоит в нём
прямо, не импортом, — счёт по одному файлу: `@`-ссылку раскрывает только Claude, другие агенты
читают файл как есть. Предупреждения — аннотации GitHub Actions `::warning` (жёлтые в PR):
- длиннее ориентира;
- больше 32 КБ — предел Codex (`project_doc_max_bytes`): хвост сверх него агент не прочтёт;
- нет метки части воркфлоу `<!-- canon:process` — обязательный раздел «Процесс — канон» выпал;
- `CLAUDE.md` — не одна строка `@AGENTS.md`: Claude Code читает `AGENTS.md` только по ней.
Код выхода всегда 0: длина коммит и мёрж не блокирует.
"""

from __future__ import annotations

import sys
from pathlib import Path

NAME = "AGENTS.md"
TARGET = 200
MAX_BYTES = 32 * 1024
CANON_BEGIN = "<!-- canon:process"
POINTER, POINTER_LINE = "CLAUDE.md", "@AGENTS.md"


def plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "строка"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "строки"
    return "строк"


def count(data: bytes) -> int:
    # Как `wc -l` и редактор: только \n, плюс последняя строка без перевода строки.
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def text_of(path: Path) -> str:
    """Текст файла без BOM — как его читают `canon-sync` и `check_lock`."""
    return path.read_bytes().decode(errors="replace").lstrip("﻿")


def check(root: Path) -> tuple[str, list[tuple[str, str]]]:
    """Строка о длине и предупреждения (заголовок, текст); нет `AGENTS.md` — без предупреждений."""
    path = root / NAME
    pointer = root / POINTER
    if pointer.is_symlink():  # символьная ссылка на `AGENTS.md` — Claude читает его же
        linked = pointer.resolve() == path.resolve()
    else:
        linked = pointer.is_file() and text_of(pointer).strip() == POINTER_LINE
    if not path.is_file():
        if linked:  # ссылка на пропавший файл: у сессий нет ни правил процесса, ни проекта
            return f"{NAME} нет", [
                (f"Нет {NAME}", f"{POINTER} ссылается на {NAME}, а его нет — правил у сессий нет")
            ]
        return f"{NAME} нет — проверять нечего", []
    data = path.read_bytes()
    lines = count(data)
    summary = f"{NAME} — {lines} {plural(lines)}, ориентир {TARGET}"
    warnings = []
    if lines > TARGET:
        warnings.append((f"Длина {NAME}", summary))
    if len(data) > MAX_BYTES:
        warnings.append(
            (
                f"Размер {NAME}",
                f"{NAME} — {len(data)} байт, больше 32 КБ: Codex прочтёт только первые 32 КБ",
            )
        )
    text = data.decode(errors="replace")
    if not any(line.lstrip("﻿").strip().startswith(CANON_BEGIN) for line in text.splitlines()):
        warnings.append(
            (
                "Нет раздела воркфлоу",
                f"в {NAME} нет части воркфлоу (метки {CANON_BEGIN} …) — обязательный раздел "
                "«Процесс — канон» не читается; верните её canon-sync той же версии",
            )
        )
    if not linked:
        warnings.append(
            (
                f"{POINTER} не ссылка",
                f"{POINTER} — не одна строка {POINTER_LINE}: Claude Code читает {NAME} только по "
                "ней, а правила только для Claude разошлись бы с общими",
            )
        )
    return summary, warnings


def report(root: Path) -> str:
    summary, warnings = check(root)
    lines = [f"::warning file={NAME},title={title}::{text}" for title, text in warnings]
    if not any(text == summary for _, text in warnings):
        lines.append(summary)
    return "\n".join(lines)


def main() -> int:
    print(report(Path.cwd()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
