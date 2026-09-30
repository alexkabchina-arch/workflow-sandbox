#!/usr/bin/env python3
"""Проверка `.canon.lock`: вендоренные файлы канона совпадают с версией из lock.

Файл вендорится из канона `alexkabchina-arch/workflow` и сам лежит под `.canon.lock`:
руками не править, обновлять через `canon-sync`. Вызывается из CI проекта
(обязательная проверка) из корня проекта:

    python3 scripts/canon/check_lock.py

Код выхода 0 — всё совпадает; 1 — расхождения (список в stderr); 2 — нет или битый lock.

Здесь же живёт склейка `settings.json` (`merge_settings`): `canon-sync` берёт её из той же
версии канона, поэтому проверка и установка склеивают одинаково.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

LOCK_NAME = ".canon.lock"
SETTINGS = ".claude/settings.json"
SETTINGS_BASE = ".claude/settings.base.json"
SETTINGS_PROJECT = ".claude/settings.project.json"


class MergeError(Exception):
    """Проектные добавки противоречат базе канона."""


def file_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def merge_settings(base, project, path: str = ""):
    """База канона + проектные добавки.

    Словари склеиваются рекурсивно, списки — база и затем новые элементы добавок
    (без повторов), скаляры должны совпадать: переопределить значение базы добавки
    не могут, только дополнить её.
    """
    if isinstance(base, dict) and isinstance(project, dict):
        merged = dict(base)
        for key, value in project.items():
            sub = f"{path}.{key}" if path else key
            merged[key] = merge_settings(base[key], value, sub) if key in base else value
        return merged
    if isinstance(base, list) and isinstance(project, list):
        return base + [item for item in project if item not in base]
    if type(base) is type(project) and base == project:
        return base
    raise MergeError(
        f"{path}: добавки проекта ({json.dumps(project, ensure_ascii=False)}) "
        f"противоречат базе канона ({json.dumps(base, ensure_ascii=False)}); "
        "добавки могут только дополнять базу"
    )


def render_json(data) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def build_settings(base, project) -> bytes:
    return render_json(merge_settings(base, project or {}))


def check(root: Path) -> list[str]:
    """Список расхождений проекта с его `.canon.lock` (пустой — всё совпадает)."""
    lock = json.loads((root / LOCK_NAME).read_text(encoding="utf-8"))
    problems = []
    for rel, expected in sorted(lock["files"].items()):
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: файла нет")
        elif file_hash(path.read_bytes()) != expected:
            problems.append(f"{rel}: изменён вручную (сумма не совпадает с {LOCK_NAME})")

    base_path = root / SETTINGS_BASE
    if SETTINGS in lock["files"] and base_path.is_file():
        try:
            fresh = build_settings(read_json(base_path), read_json(root / SETTINGS_PROJECT))
        except (MergeError, json.JSONDecodeError) as exc:
            problems.append(f"{SETTINGS_PROJECT}: {exc}")
        else:
            settings = root / SETTINGS
            if settings.is_file() and settings.read_bytes() != fresh:
                problems.append(
                    f"{SETTINGS}: не совпадает со склейкой {SETTINGS_BASE} + {SETTINGS_PROJECT}"
                )
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    try:
        problems = check(root)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"check_lock: не прочитать {LOCK_NAME}: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(f"check_lock: вендоренные файлы канона расходятся с {LOCK_NAME}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Файлы канона руками не правят: верните их или обновите канон через canon-sync "
            f"(добавки к настройкам — в {SETTINGS_PROJECT}, затем canon-sync той же версии).",
            file=sys.stderr,
        )
        return 1
    print(f"check_lock: {LOCK_NAME} совпадает")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
