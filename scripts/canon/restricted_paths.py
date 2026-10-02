#!/usr/bin/env python3
"""restricted_paths — задевает ли PR пути-ограничители (обязательная проверка `restricted-paths`).

    restricted_paths.py <CODEOWNERS> <файл со списком путей PR>

Пути-ограничители — строки `CODEOWNERS` с владельцами (последнее совпадение побеждает, строка
без владельцев снимает защиту). `CODEOWNERS` один: он же просит ревью владельца на PR партнёра.
Нужна отдельная проверка, потому что одобрение код-оунера не требуется, если автор PR сам
код-оунер, — а агенты работают под учётной записью владельца. Задет хоть один путь — выход 1:
авто-мёрж не сработает, сливает владелец кнопкой с обходом правил.

Запускается workflow `restricted-paths` на `pull_request_target`: скрипт и `CODEOWNERS` — из
ветки по умолчанию, код PR не исполняется. Только стандартная библиотека Python 3.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def pattern_regex(pattern: str) -> re.Pattern:
    """Шаблон CODEOWNERS (синтаксис .gitignore без `!` и `[]`) → регулярное выражение пути."""
    dir_only = pattern.endswith("/")
    body = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in body
    out = []
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    prefix = "" if anchored else "(?:.*/)?"
    if dir_only:
        suffix = "/.*"
    elif body.endswith("*") and not body.endswith("**"):
        suffix = ""  # `docs/*` — только файлы прямо в `docs/`, не глубже (в отличие от .gitignore)
    else:
        suffix = "(?:/.*)?"  # шаблон без `/` в конце совпадает и с каталогом
    return re.compile(prefix + "".join(out) + suffix)


def parse_codeowners(text: str) -> list[tuple[re.Pattern, re.Pattern, bool]]:
    """Правила по порядку: (шаблон, он же без учёта регистра, есть ли владельцы)."""
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        owners = []
        for token in tokens[1:]:
            if token.startswith("#"):
                break
            owners.append(token)
        regex = pattern_regex(tokens[0])
        rules.append((regex, re.compile(regex.pattern, re.IGNORECASE), bool(owners)))
    return rules


def is_restricted(path: str, rules: list[tuple[re.Pattern, re.Pattern, bool]]) -> bool:
    """Задет ли путь-ограничитель; последнее совпавшее правило побеждает.

    На macOS (APFS) `claude.md` — тот же файл `CLAUDE.md`, и Claude Code его прочитает: строка с
    владельцами защищает без учёта регистра. Строка без владельцев снимает защиту, только если
    совпадает с точным регистром и с путём, и с ним же в нижнем регистре (`Docs/public/` не снимает
    защиту с `Docs/public/x`, который на APFS может лечь в `docs/public/x`). Ошибка — в сторону
    лишнего ручного мёржа.
    """
    owned = False
    lower = path.lower()
    for exact, icase, has_owners in rules:
        if has_owners and icase.fullmatch(path):
            owned = True
        elif not has_owners and exact.fullmatch(path) and exact.fullmatch(lower):
            owned = False
    return owned


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    codeowners, files = map(Path, argv)
    if not codeowners.is_file():
        print(
            f"restricted_paths: нет файла CODEOWNERS ({codeowners}) — пути-ограничители не заданы",
            file=sys.stderr,
        )
        return 2
    rules = parse_codeowners(codeowners.read_text())
    paths = sorted({p.strip() for p in files.read_text().splitlines() if p.strip()})
    hit = [p for p in paths if is_restricted(p, rules)]
    if not hit:
        print(f"PR не задевает пути-ограничители ({len(paths)} файлов)")
        return 0
    print(
        f"PR задевает пути-ограничители: {', '.join(hit)} — "
        "сливает владелец кнопкой с обходом правил"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
