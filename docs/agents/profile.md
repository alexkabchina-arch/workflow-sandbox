# Профиль проекта

Проектная часть процесса: то, что канон `alexkabchina-arch/workflow` берёт из проекта. Весь профиль —
путь-ограничитель (`.github/CODEOWNERS` не различает разделы): ревью ошибок проверяет его изменение на
ослабление защиты, PR других людей ждёт одобрения владельца.

## Окружение

Вариант — по признаку: `python3.12` на `PATH` есть (Mac владельца) — первый блок; нет (песочница
оркестратора, Docker Sandboxes: внутри Python 3.14 без `ensurepip` и `uv`) — второй. Python в
окружении — 3.12, как в CI.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

```bash
uv venv --clear --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

Во втором варианте pip в окружении нет: пакеты — только `uv pip install --python .venv/bin/python …`.
`--clear` пересоздаёт `.venv`: окружение привязано к платформе, созданное на Mac внутри песочницы
не запустится. Проверки ниже зовут `.venv/bin/…`: worktree без окружения оркестратор с
«доктором» среды (воркфлоу #342) не проверит — остановка с кодом «среда».

## Команды

| Что             | Команда                        |
| --------------- | ------------------------------ |
| Тесты           | `.venv/bin/pytest -q`          |
| Линтер          | `.venv/bin/ruff check .`       |
| Формат (проверка) | `.venv/bin/ruff format --check .` |
| Формат (правка) | `.venv/bin/ruff format .`      |

Все три проверки быстрые (секунды): вывод можно читать напрямую.

## Ожидаемые job CI

| Workflow | Job    | Что делает                            |
| -------- | ------ | ------------------------------------- |
| `CI`     | `test` | `check_lock` (`.canon.lock`), длина `CLAUDE.md` (предупреждение, не падает), gitleaks (секреты в коммитах), `ruff check`, `ruff format --check`, `pytest` на Python 3.12 с отчётом JUnit — артефакт `test-report-test` |

Запускается на `pull_request`, на push в `main` и раз в сутки на `main`. Обязательная проверка для
мёржа — `test`. Контракт CI оркестратора (шаблон профиля канона): отчёт по тестам артефактом
`test-report-test` (на `main` хранится 90 дней), предел задачи 10 минут, новый push в PR отменяет
прогон старого head, прогоны `main` не отменяются. На черновом PR задача `test` пропускается, полный
прогон — при переводе из черновика (`ready_for_review`) и на каждый push в PR не черновиком.

Точечный прогон тестов ревьюера — `.github/workflows/reviewer-tests.yml`: событие
`pull_request_target` на метку `ci:reviewer-tests` (ставит оркестратор; файл workflow — из `main`),
только тесты папки `tests/reviewer/` на head PR и на основе; отчёты — артефакты
`test-report-reviewer-head` и `test-report-reviewer-base`. Обязательной проверкой не является.
PR Dependabot сами не перестраиваются (`rebase-strategy: disabled`): догон — `@dependabot rebase`.

## Канон

Файлы канона `alexkabchina-arch/workflow` (`scripts/canon/`, `.claude/settings.base.json`,
`.claude/settings.json`) вендорятся через `canon-sync` и записаны в `.canon.lock`: руками не правятся,
шаг `check_lock` в CI это ловит. Линтер проекта их не проверяет (`extend-exclude` и
`force-exclude` в `pyproject.toml`): качество кода канона — забота CI канона, у которого свои
настройки. Проектные добавки к настройкам Claude Code — в `.claude/settings.project.json` (могут
только дополнять базу канона); после их правки — `canon-sync` той же версии.

## Критичные зоны

| Зона   | Пути                | Почему                                   |
| ------ | ------------------- | ---------------------------------------- |
| Деньги | `sandbox/money/**`  | Сумма заказа и скидка: ошибка тихая, итог просто неверный |

Зона условная — она нужна контрольным сценариям канона, а не продукту.

## Рецепт запуска для `/verify`

Приложение — CLI без сервера и базы. Живая проверка — вызов с известным ответом:

```bash
.venv/bin/python -m sandbox 3x100 1x49.90 --discount 10
```

Ожидается `314.91 ₽`: 349,90 ₽ минус 10 %, скидка округляется вниз в пользу покупателя.
Перед «₽» — неразрывный пробел U+00A0, на глаз он не отличается от обычного; проверка побайтово:

```bash
.venv/bin/python -m sandbox 3x100 1x49.90 --discount 10 | LC_ALL=C od -c
```

Ожидается `3   1   4   .   9   1 302 240 342 202 275  \n`: `302 240` — U+00A0, `342 202 275` — «₽».
Ошибочный ввод (`0x100`, `3`, `--discount 101`) — код 2 и ровно одна строка в stderr
`sandbox: error: <причина>`, stdout пуст; traceback или usage — провал:

```bash
.venv/bin/python -m sandbox 0x100; echo "exit $?"
```

Ожидается `sandbox: error: количество должно быть положительным` и `exit 2`.

## Инструмент проверки для `/verify` пути v2

`/verify` пути v2 (воркфлоу #351) проверяет критерии тикета командами инструмента проверки по
карте функций, оба — из `main` (ветка их не подменит). Инструмент — `tools/verify/tool.py`
(вывод — JSON, пробный режим `--dry-run`), карта — `tools/verify/map.md`; «доктор» — его команда
`doctor`: приложение в проверяемой копии запускается (`.venv` копии; без окружения — красный).

```verify-tool
tool tools/verify/tool.py
map tools/verify/map.md
doctor python3 tools/verify/tool.py doctor
```
