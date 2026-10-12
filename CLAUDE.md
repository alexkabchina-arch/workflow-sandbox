# CLAUDE.md

Песочница процесса агентной разработки: крошечное приложение, на котором гоняются контрольные сценарии
канона `alexkabchina-arch/workflow`. Секретов нет, репозиторий публичный.

@.claude/canon/process.md

## Проект

- Профиль проекта (команды, CI, критичные зоны, рецепт `/verify`) — [docs/agents/profile.md](docs/agents/profile.md).
- Трекер и метки — [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md), [docs/agents/triage-labels.md](docs/agents/triage-labels.md).

## Прогон B0 — временно (alexkabchina-arch/workflow#153)

Маршрут тикета ведёт одноразовая заглушка `scripts/b0/ticket`; после прогона блок, скилл `start` и
`scripts/b0/` удаляются. Вызовы заглушки — с Bash timeout 1800000 (внутри ревью и ожидание мёржа).

- `/implement N`: старт — как в каноне (`session-start`, вход в worktree). После коммита вместо
  `/ship` — `scripts/b0/ticket ship N`, дальше — ровно то, что он печатает (задание и строку
  «Дальше:»), до строки «Конец маршрута».
- `/start N` — скилл `start`: то же без вопросов в чат.
- На время прогона задания заглушки главнее `/ship` и `.claude/canon/process.md`, где они
  расходятся: включение Auto-fix по её заданию, вопросы в тикет в режиме `start`.
