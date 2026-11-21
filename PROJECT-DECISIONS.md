# 41l1-n3dra — решения проекта

Короткий канон для команды и жюри. Операционные команды — в
[README](README.md) и [release handoff](docs/RELEASE-HANDOFF.md); детали
контрактов — в [docs/data-contracts](docs/data-contracts/README.md).

## Статус

- `dev` — собранная интеграция: backend, frontend, карта, comparison, AI,
  PostgreSQL publish и локальный деплой.
- `main` — только после приёмки; эта ветка не делает merge/push в `main`.
- Публикация `nedra.soloveyka.bond` — staged/pending. Хост — локальный ПК +
  Cloudflare Tunnel, не независимый VPS; DNS ещё не опубликован.
- Production readiness, внешняя auth-модель и преимущество retrieval не
  заявляются.

## Продукт и команда

Собственный визуальный конструктор проверок Kotlin/Spring Boot: ручной запуск и
расписание, условия/сравнения/regex, собственные SECRET/SCA/SAST, живые статусы,
карта доказательств, AI-объяснение, два отчёта и один канал уведомлений. Готовые
сканеры/workflow-платформы не подменяют собственную реализацию.

Роли: A — backend/workflow/UI, B — анализаторы и code facts, C — схема/DTO/карта/
сравнение, D — AI/validation/embedding adapter. Владельцы файлов не правят чужие
контракты параллельно.

## Зафиксированные технические решения

### Данные и runtime

- Runtime работает на PostgreSQL 16+; SQLite в runtime нет. Канонические факты —
  schema `data_contract`; API-состояние — отдельная schema `runtime`.
- Миграции применяются ledger-ом `public.schema_migrations` (filename + checksum).
  Обязательные: `001_core`, `002_integrity_publish`, `004_bundle_envelopes`,
  `005_canonical_envelope`. `003_exact_vector` — optional, только при явном
  `DB_ENABLE_VECTOR=1` и доступном pgvector.
- Snapshot, run и published bundle неизменяемы: SQL guards запрещают
  переписывать identity, coverage/facts после publish и удалять историю.
- UUID identity: `workspaceId` — UUID; новый `runId` создаётся как UUID;
  `snapshotId` выводится из workspace + sealed manifest. Идентичность в envelope
  не переписывается; runtime text id не превращается в UUID молча.

### Факты, AI и policy

- Findings/evidence/coverage/code facts — отдельный слой. AI-объяснения и
  пользовательская domain policy хранятся отдельно и не меняют факты, coverage
  или вердикт.
- Карта безопасности (code facts/evidence) — не workflow-граф исполнения. Это
  разные DTO, экраны и идентификаторы; evidence map нельзя выдавать за execution
  graph.
- Canonical publish сохраняет две явные копии: immutable `source_envelope` (что
  отдал B) и `canonical_envelope` с coverage/status, реально сохранёнными в SQL.
  `get_published_envelope` отдаёт canonical; `get_source_envelope` — исходник.
  Republish того же документа ничего не меняет.
- Три независимых hash не смешиваются: SQL `provenance.bundleHash` — authority
  published facts; `canonicalHash`/`envelopeHash` — jsonb-копии;
  `SecurityMapDto.bundleHash` — внутренний backend hash, пересчитываемый по
  recipe codefacts.

### Сравнение

- Comparison работает только по published immutable bundles одного workspace.
  Совместимость — точные версии/hash parser/rules/advisory/config/domain policy/
  HMAC key.
- Статусы опираются на устойчивый fingerprint/anchor, checked coverage и
  неоднозначность. `changed`, потеря покрытия и неизвестный anchor не становятся
  `resolved`; удалённые файлы — отдельный `deletedFiles`.
- SQL proof в [docs/sql-before-after.md](docs/sql-before-after.md) узкий:
  per-file `source_pattern` `KOTLIN-SQL-INJECTION` на demo fixture (before
  finding, after 0 при checked scope). Это не global comparison и не
  production publication. Global per-rule observations + comparison persistence
  сейчас доводятся в `dev`.

### AI и retrieval

- Единственный chat-провайдер — ReformBoss; default model
  `deepseek-v4.1-flash-1017`. Ответы валидируются по advisory/finding, ошибка
  провайдера даёт честный fallback и не ломает отчёт. Ключ не попадает в argv,
  логи, provenance или отчёт.
- Embeddings сейчас `unavailable`: реальный `POST /v1/embeddings` вернул 403
  `token_model_not_allowed`, размерность не получена. `embedding_specs` остаётся
  pending, fake vectors/модели не создаются, retrieval по умолчанию выключен и
  не влияет на scan/report.
- `vetted` remediation example требует реального scanner provenance, published
  fixed bundle, checked coverage выбранного правила и published comparison item
  `resolved` для exact source finding. Synthetic `contract_scenario` остаётся
  draft; AI-совет не становится vetted.
- 3–5 подготовленных примеров проверяют механику хранения/сравнения; они не
  доказывают преимущество retrieval.

## Деплой и доступ

- `dev` собирается локально через `scripts/nedra-*.sh`: PostgreSQL + pgvector и
  backend в rootless Podman; БД без host-портов; backend только на
  `127.0.0.1:18080`.
- Наружу планируется только HTTPS Cloudflare Tunnel на
  `nedra.soloveyka.bond`. DNS/route/run не выполнялись — это отдельный шаг после
  review public auth; домен пока не published.
- Shared demo access code (`CG_ACCESS_CODE`) и остальные секреты живут только в
  `~/.config/nedra/runtime.env` (mode 600). Они не коммитятся, не печатаются
  скриптами и не передаются в argv.
- После reboot локальный хост должен быть включён; rootless-контейнеры
  поднимаются systemd user units (при `enable-linger`) или после логина.

## Ветки и приёмка

`feature branch → review + live check → dev → сквозная приёмка → main → отдельная
публикация`. Merge/push в `main` выполняет координатор после приёмки. В
репозиторий не попадают ключи, исходные ZIP, личные переписки и память машины.

## Что ещё не доказано

- Семантика parser/anchor (overloads, duplicate anchors, нормализация) и полнота
  runtime graph (`runtime-configuration`, `runtime-guards`,
  `binaries-and-archives`) неизвестны. Такие gaps остаются видимыми; graph
  comparison может быть `inconclusive`.
- Global comparison и production per-rule observation ещё доводятся; demo SQL
  proof нельзя переносить на весь проект.
- Реальный vetted example и embedding provider/model/dimension появятся только
  после подтверждённого adapter/D; сейчас их нет.
- Auth/roles, TTL artifact store и публикация домена — предмет отдельной
  приёмки; текущий loopback demo не является production-границей.