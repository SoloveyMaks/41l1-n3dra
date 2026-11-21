# 41l1-n3dra

AI Security Workflow для Cyber Garden 2026: собственные SECRET/SCA/SAST проверки
Kotlin/Spring Boot, визуальный workflow, карта доказательств и сравнение
immutable bundles.

**Статус:** `dev` собран и прошёл живой PostgreSQL publish (13 findings, AI
succeeded, 26 evidence, map hash self-check). `main` — после приёмки.
Публикация `nedra.soloveyka.bond` — staged/pending: приложение живёт на локальном
ПК и будет доступно через Cloudflare Tunnel, а не на отдельном VPS. Домен ещё не
опубликован; production readiness не заявляется.

## Что внутри

- `backend/` — FastAPI, executor workflow, собственные анализаторы, code facts,
  canonical publish в PostgreSQL, сравнение, AI-объяснения и отчёты.
- `frontend/` — React/Vite: workflow-конструктор, запуски, отчёты, карта.
- `ui/security-map` — React Flow карта; `packages/comparison` — сравнение
  published bundles.
- `db/` — PostgreSQL-миграции; `data_contract` хранит канонические факты,
  `runtime` — API-состояние. SQLite в runtime нет.
- `deploy/`, `scripts/nedra-*.sh` — локальный контейнерный запуск.

## Начать здесь

- [Решения проекта](PROJECT-DECISIONS.md)
- [Release handoff](docs/RELEASE-HANDOFF.md)
- [Контракты данных](docs/data-contracts/README.md), [SQL ↔ DTO](docs/data-contracts/mapping.md)
- [Миграции и публикация](db/README.md), [runtime PostgreSQL](docs/postgres-runtime.md)
- [Локальный деплой](docs/DEPLOYMENT.md), [AI/retrieval](docs/ai-runtime.md)
- [SQL before/after proof](docs/sql-before-after.md), [что проверено](docs/data-contracts/verification.md)

## Локальный запуск (PostgreSQL)

PostgreSQL 16+; pgvector не обязателен. Python 3.11+ (в контейнере 3.12).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env                         # впиши свой DATABASE_URL
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/41l1_n3dra
python -m backend.app.storage.migrate        # 001/002/004/005 (+003 при DB_ENABLE_VECTOR=1)
uvicorn backend.app.main:app --env-file .env --port 8000
```

Backend сам применяет ledger-миграции при старте. `DB_ENABLE_VECTOR=1` включает
optional `db/optional/003` только при доступном pgvector; fake vectors не
создаются. Без `DATABASE_URL`/драйвера/доступной БД старт падает с понятной
ошибкой, без DSN в тексте.

Frontend: собранная статика отдаётся backend-ом, если есть `frontend/dist`.

```bash
cd frontend && npm ci && npm run build       # production build
# отдельный dev-сервер: npm run dev          # /api проксируется на :8000
```

Локальная сессия: `POST /api/session` (или GET выдаёт cookie в local-режиме).
Режим `CG_PUBLIC_DEMO=1` требует `CG_ACCESS_CODE` и не выдаёт cookie сам.

Минимальный smoke:

```bash
curl -s -c /tmp/nedra.jar http://127.0.0.1:8000/api/health
curl -s -b /tmp/nedra.jar -c /tmp/nedra.jar -X POST http://127.0.0.1:8000/api/session
curl -s -b /tmp/nedra.jar http://127.0.0.1:8000/api/workflows
curl -s -b /tmp/nedra.jar -X POST http://127.0.0.1:8000/api/artifacts/sample
```

## Контейнерный запуск (staged)

Локальный ПК — единственный хост. Публикация DNS/туннеля — отдельный шаг.

```bash
scripts/nedra-bootstrap.sh --check     # read-only проверка
scripts/nedra-bootstrap.sh --write     # ~/.config/nedra/runtime.env (600)
scripts/nedra-build.sh
scripts/nedra-up.sh                    # db + app; идемпотентно
scripts/nedra-status.sh
scripts/nedra-stop.sh                  # или nedra-down.sh
scripts/nedra-systemd-install.sh       # user units; tunnel не включается
scripts/nedra-tunnel.sh status         # read-only; route/run не выполнялись
```

Секреты и shared demo access code — только в `~/.config/nedra/runtime.env`
(`CG_ACCESS_CODE`); они не коммитятся, не печатаются скриптами и не передаются
в argv. Подробности, owner labels и health — в [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Инварианты

- `workspaceId`, `runId`, `snapshotId` — UUID и не переписываются; snapshot
  immutable после seal. Canonical envelope сохраняет identity.
- Факты анализа, AI-объяснения и domain policy — разные слои; AI не меняет
  findings/coverage.
- Карта фактов (code facts, evidence) — не workflow-граф исполнения.
- `provenance.bundleHash` — SQL facts; `canonicalHash`/`envelopeHash` — jsonb;
  `SecurityMapDto.bundleHash` — отдельный internal map hash, пересчитывается по
  своему recipe, не подменяется SQL.
- Comparison: fingerprint/anchor + signature + coverage. Потеря покрытия,
  неоднозначный anchor и `changed` не выдаются за fix; удалённые файлы — отдельный
  `deletedFiles`.
- SQL proof в `docs/sql-before-after.md` — узкий per-file `source_pattern` для
  `KOTLIN-SQL-INJECTION`, не global comparison и не production publication.
- AI: только ReformBoss, default `deepseek-v4.1-flash-1017`. Embeddings сейчас
  `unavailable`: `POST /v1/embeddings` вернул 403 `token_model_not_allowed`;
  registry pending, fake vectors запрещены, vetted требует реального scanner
  provenance и resolved comparison.

## Проверка

- HTTP smoke выше проверяет health/session/workflows/artifact.
- `node packages/comparison/compare-cli.mjs <baseline.json> <current.json>` —
  сравнение published envelopes.
- `examples/demo/sql-before-after/` — реальный SAST/codefacts прогон и
  negative probes; команды и границы — в [docs/sql-before-after.md](docs/sql-before-after.md).

## Ограничения

Локальный хост должен быть включён; домен пока не опубликован. Семантика
parser/anchor и полнота runtime graph (`runtime-configuration`,
`runtime-guards`, `binaries-and-archives`) не доказаны — такие gaps остаются
видимыми, а comparison может быть `inconclusive`. 3–5 подготовленных примеров
проверяют механику хранения/сравнения, но не преимущество retrieval. Полные
ограничения и acceptance — в [docs/RELEASE-HANDOFF.md](docs/RELEASE-HANDOFF.md).