# 41l1-n3dra — решения проекта

Короткий канон для команды и жюри. Операционные команды — в
[README](README.md) и [release handoff](docs/RELEASE-HANDOFF.md); детали
контрактов — в [docs/data-contracts](docs/data-contracts/README.md).

Независимые проверки: [Sol logic](docs/reviews/sol-logic.md),
[Sol visual](docs/reviews/sol-visual.md),
[Sol structure](docs/reviews/sol-structure.md),
[report coverage](docs/report-coverage.md).

## Статус

- Публичный стенд `https://nedra.soloveyka.bond` собран из `9862e19`: старый
  интерфейс, один общий код, без user/admin-разделения. Это не текущий `dev`.
- Локальная `dev` (`b6abde4`) содержит user/admin, `GET /api/admin/overview` и
  принятые исправления: `006`/происхождение снимков, один артефакт на проверку,
  защита канонического вердикта, фактический `executionStatus` и каноническая
  полнота отчёта.
- Три независимых Sol-отчёта имеют AFTER85: logic закрыл artifact A/B и
  failed-report, visual — основные мобильные блокеры, structure — 4 из 6.
  Два остатка structure закрыты в `b6abde4`: Markdown-полнота `9bcb5fa`,
  свежесть/авторизация админки `6a9dd99`.
- Review стенда 18082 на точном `b6abde4` — READY; structure выполняет узкий
  независимый FINAL этих двух. Root build PASS.
- Финальная приёмка, публичный релиз и «все проверки пройдены» не заявляются.
  Production пока `9862e19`, новый UI ещё не public.
- `main` — после приёмки. Merge/push делает координатор, не worker.

## Продукт и команда

Проверка безопасности Kotlin/Spring Boot: пользователь выбирает ZIP, запускает
готовый сценарий и получает проблемы, карту доступа, сравнение и отчёт.
Конструктор, расписания и системные настройки — в отдельной админке.

Роли: A — backend/workflow/UI, B — анализаторы и code facts, C — схема/DTO/карта/
сравнение, D — AI/validation/embedding adapter. Владельцы файлов не правят чужие
контракты параллельно.

## Зафиксированные технические решения

### Данные и runtime

- Runtime работает на PostgreSQL 16+; SQLite в runtime нет. Канонические факты —
  schema `data_contract`; API-состояние — отдельная schema `runtime`.
- Миграции применяются ledger-ом `public.schema_migrations` (filename + checksum).
  Обязательные: `001_core`, `002_integrity_publish`, `004_bundle_envelopes`,
  `005_canonical_envelope`, `006_snapshot_origins`. `003_exact_vector` — optional,
  только при явном `DB_ENABLE_VECTOR=1` и доступном pgvector.
- `006_snapshot_origins`: один снимок проекта может иметь несколько загрузок и
  проверок. Новые `(artifact_id, artifact_hash)` — append-only
  origins; `runs` ссылается на content hash, а не на случайный артефакт. Manifest
  и files снимка не переписываются. Это исправляет повторную загрузку того же ZIP,
  которая раньше давала конфликт publish.
- Одна проверка — один явно выбранный артефакт. Он авторитетен для scanners,
  code facts, карты, публикации и отчёта. Сохранённый draft — только fallback,
  если артефакт не выбран; разные `artifactId` без выбора отклоняются `400`.
- Snapshot, run и published bundle неизменяемы: SQL guards запрещают
  переписывать identity, покрытие/факты после publish и удалять историю.
- UUID identity: `workspaceId` — UUID; новый `runId` создаётся как UUID;
  `snapshotId` выводится из workspace + sealed manifest.

### Вердикт и отчёт

- `pass` — отсутствие блокирующих проблем по политике при данном покрытии, а не
  ноль проблем и не полная проверка. Проблемы ниже порога могут остаться.
- Policy Gate не выдаёт `pass` при частичном/неподтверждённом каноническом покрытии. Пока
  `expectedScopesVerified=false`, честный не-`fail` вердикт — `inconclusive`.
  `strictCoverageRequired=false` не отменяет эту проверку.
- `executionStatus` отчёта — фактическое выполнение (`succeeded`/`failed`/
  `running`), отдельно от вердикта безопасности. Позднее падение condition даёт
  `failed`; падение notify не делает анализ failed.
- Полнота проверки берётся из канонического покрытия и expected scopes. `100%`
  возможны только при `complete` и подтверждённых scopes; `partial`,
  `unavailable` и `unknown` дают «не установлена/недоступна/неизвестна», а не
  процент. Parser ratio остаётся отдельным legacy-полем и не выдаётся за общую
  полноту.

### Факты, AI и политика

- Проблемы/доказательства/покрытие/code facts — отдельный слой. AI-объяснения не
  меняют факты, покрытие или вердикт.
- Политика предметной области не подключена к канонической подписи и
  `policyInputs`. Нельзя выдавать допущения за работающее ограничение доступа.
- Карта доступа и данных (code facts/evidence) — не workflow-граф исполнения.
  Это разные DTO, экраны и идентификаторы.
- Каноническая публикация сохраняет `source_envelope` и `canonical_envelope`.
  `get_published_envelope` отдаёт canonical; republish того же документа ничего
  не меняет.
- Три независимых hash не смешиваются: SQL `provenance.bundleHash`,
  `canonicalHash`/`envelopeHash` и внутренний `SecurityMapDto.bundleHash`.

### Сравнение

- Comparison работает только по published immutable bundles одного workspace.
  Совместимость — точные версии/hash parser/rules/advisory/config/domain policy/
  HMAC key.
- `comparable` вычисляется после всех причин отказа. Synthetic
  `contract_scenario`/`manual_fixture` никогда не даёт `comparable=true`, даже
  если expected scopes совпали. Это исправление `df33cee`.
- Общее сравнение остаётся `inconclusive`, пока expected scopes не подтверждены.
  SQL-проверка узкая: per-file `source_pattern` `KOTLIN-SQL-INJECTION` на demo
  fixture (before finding, after 0 при checked scope). `pattern_disappeared` —
  не общий вердикт и не vetted.
- `changed`, потеря покрытия и неизвестный anchor не становятся `resolved`;
  удалённые файлы — отдельный `deletedFiles`.

### AI и retrieval

- Единственный chat-провайдер — ReformBoss; default model
  `deepseek-v4.1-flash-1017`. Ответы валидируются; ошибка провайдера или отказ
  валидатора даёт детерминированный fallback и не ломает отчёт.
- Embeddings optional, retrieval выключен и сейчас `unavailable`: реальный `POST /v1/embeddings`
  вернул 403 `token_model_not_allowed`. Это векторные представления текста для
  точного поиска, без ANN-индекса. `embedding_specs` pending, fake vectors нет.
- `vetted` remediation example требует реального scanner provenance, published
  fixed bundle, checked coverage и published comparison `resolved` для exact
  finding. Synthetic `contract_scenario` остаётся draft; AI-совет не становится
  vetted. Проверенных примеров сейчас 0.
- 3–5 подготовленных примеров проверяют механику хранения/сравнения; они не
  доказывают преимущество retrieval.

## Доступ и деплой

- В локальном `dev` роли `user`/`admin` реализованы: отдельные
  `CG_ACCESS_CODE` и `CG_ADMIN_ACCESS_CODE`, подписанная роль в сессии,
  `GET /api/admin/overview` только для admin.
- `adminAvailable=false`, если admin-код не задан или совпадает с
  пользовательским; admin-вход возвращает 503, user-вход работает.
- Роль — операторское ограничение поверх одного общего workspace. Аккаунтов,
  списков доступа, приватных кабинетов и multi-tenant-изоляции нет.
  `user` не меняет сценарий, расписания, AI-тест и training.
- Публичный стенд `9862e19` пока без этого разделения: там старый интерфейс и
  общий код. Не выдавать локальный `dev` за опубликованный стенд.
- `dev` собирается локально через `scripts/nedra-*.sh`: PostgreSQL + pgvector и
  backend в rootless Podman; backend только на `127.0.0.1:18080`.
- Секреты и коды живут только в `~/.config/nedra/runtime.env` (mode 600). Они не
  коммитятся, не печатаются скриптами и не передаются в argv.
- После reboot локальный хост должен быть включён; rootless-контейнеры
  поднимаются systemd user units при linger или после логина.

## Ветки и приёмка

`feature branch → review + live check → dev → focused retest/FINAL → приёмка → main →
отдельная публикация`. Merge/push в `main` выполняет координатор после приёмки.
В репозиторий не попадают ключи, исходные ZIP, личные переписки и память машины.

## Что ещё не доказано

- AFTER85: logic закрыл artifact A/B и failed-report живой независимой
  проверкой; visual закрыл основные мобильные блокеры; structure закрыл 4 из 6.
  Два остатка structure исправлены в `b6abde4` (`9bcb5fa` — полнота Markdown,
  `6a9dd99` — свежесть/авторизация админки), review стенда 18082 на exact `b6abde4`
  — READY, но узкий FINAL этих двух ещё не заявлен. Это не финальная приёмка.
- После AFTER85 остаются неблокирующие визуальные остатки: подписи коротких
  связей и часть comparison-текста на 390. Конструктор на 390 повторно не
  проверялся. Полного нового аудита всех сценариев не было.
- Семантика parser/anchor и полнота runtime graph (`runtime-configuration`,
  `runtime-guards`, `binaries-and-archives`) не доказаны. Такие gaps остаются
  видимыми; graph comparison может быть `inconclusive`.
- Общий comparison остаётся `inconclusive`, пока expected scopes не подтверждены.
  Узкую SQL-проверку нельзя переносить на весь проект.
- Нет проверенного embedding provider/model/dimension и реального vetted
  example. Это optional часть; основной анализ работает без неё.
- Публичный стенд отстаёт от `dev`; после пересборки нужен отдельный публичный
  smoke, а не перенос локальных результатов.
- Production readiness и полное покрытие не заявляются.

## Подключение командных анализаторов

`analyzers/` сохранён отдельным пакетом; runtime adapter включается явным
`config.engine=team_b` на scanner nodes. Смешение engines в одном run запрещено.
Training проверен с PostgreSQL: 15 findings (5/5/5), 43 nodes, 32 edges,
67 evidence, каноническая публикация успешна. Default engine остаётся `backend`.

Adapter использует полный frozen manifest и ZIP hash из intake, доверенный
серверный advisory и серверный HMAC. Известный demo key запрещён. Без advisory
SCA сохраняет error, zero findings дают `inconclusive`, а не `pass`. Team B
`CG26-SAST-001` не переименовывается под SQL-проверку другого правила.