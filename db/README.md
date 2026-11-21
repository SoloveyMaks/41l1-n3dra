# PostgreSQL — proposal v0.1

> Retrieval-хранилище элементов `rag/` файловое и эти таблицы не заменяет:
> PostgreSQL остаётся источником канонических фактов, `rag/` отвечает только за
> переиспользование элементов между запусками (см. [rag/README.md](../rag/README.md)).

Миграции рассчитаны на PostgreSQL 16+, отдельную schema `data_contract`, без новых runtime dependencies. Это проверяемое предложение C, не подтверждение A/B/D. Не применять к чужой рабочей БД без разбора.

1. `psql -X -v ON_ERROR_STOP=1 -d <empty-db> -f db/migrations/001_core.up.sql`
2. `psql -X -v ON_ERROR_STOP=1 -d <empty-db> -f db/migrations/002_integrity_publish.up.sql`
3. Только при установленном pgvector, по порядку: `db/optional/003_exact_vector.up.sql`, `db/optional/014_exact_vector_contract.up.sql`, `db/optional/015_vector_demo_fixture.up.sql`.

`003` создаёт базовые таблицы; `014` заменяет `exact_similar_fixes` на 15-аргументную версию с точной сверкой одного active spec, обязательными фильтрами и provenance-полом для `vetted`-строк (fixed bundle, scanner-envelope, совпадающая подпись, resolved comparison, review-поля, непустые limitations); `015` создаёт отдельные demo/fixture-таблицы (`draft`-only, `public.vector`, synthetic-векторы) и `exact_demo_fixes`. Demo-строки не связаны с `remediation_examples`, не могут быть `vetted` и не входят в реальную выдачу. Заполнение demo: `python -m backend.app.ai.vector_demo --seed`; проверка: `--verify` или `node packages/vector/verify-cli.mjs`.

Каждый файл транзакционный, один раз; повторное применение миграций не является publish idempotence. `db/migrations/013_custom_checks.up.sql` живёт в обязательном namespace; `014`/`015` — только в `db/optional`. Ledger **один**: `public.schema_migrations` с ключом по имени файла; optional-прогон отличается своим advisory lock (`LOCK_NAME:optional`) и своим списком файлов, а не отдельной таблицей. Имена файлов не пересекаются, поэтому номер 013 не занят и коллизии нет. Этот же прогон выполняет backend: ledger `public.schema_migrations` (filename + checksum), advisory lock, автоматически при старте или `python -m backend.app.storage.migrate`. Ручной psql без ledger тоже подхватывается: миграция считается применённой по маркеру схемы. Для будущего migrator A нужен обычный migration ledger. Down/destructive SQL не включён: история запусков и immutable facts не должны исчезать с…

## Таблицы и владельцы

| Данные | Кто формирует / кто пишет |
|---|---|
| workspaces/workflows/workflow_revisions | A; revision append-only, draft отдельно |
| artifacts/snapshots/snapshot_files | A intake; C schema; ZIP в закрытом внешнем store, в БД только opaque ref/hash/TTL/manifest |
| domain_policy_revisions | пользователь + B описывают policy, A/C фиксируют revision; approvedBy — не AI |
| runs/node_runs/run_events/schedules/schedule_occurrences/deliveries | A executor; scanner B не пишет напрямую |
| analysis_bundles/scan_outcomes/coverage/findings/diagnostics/evidence | B typed envelopes → C/A persistence transaction |
| code_nodes/code_edges/*_evidence/finding_nodes/graph_coverage | source facts B → C persistence, без усиления выводов |
| ai_explanations | D typed response + grounding status → A persistence, отдельно от статических bundles |
| comparisons/comparison_items | C по двум опубликованным bundles |
| remediation_examples | C по redacted материалам; человек переводит в vetted с основанием |
| embedding_specs/example_embeddings | D даёт проверенный spec/вектор; C сохраняет совместимое и ищет |

Coverage PK включает `(workspace_id,run_id,snapshot_id,category,path,rule_id)`: одна пара path/rule может быть у нескольких семей, comparison predicates всегда включают category. Все ключи scoped workspace; граф и evidence дополнительно scoped run/snapshot. Составные FK на оба endpoint ребра, finding↔evidence/node, primary evidence, snapshot path/source hash, comparison baseline/current и provenance примеров. FK не заменяют auth. Нет RLS/production role grants: модель auth ещё у A на согласовании; не выдавать browser/worker arbitrary SQL или table-owner role. Владелец PostgreSQL/superuser может отключить triggers; это не граница от администратора БД.

## Snapshot и run

A создаёт open snapshot, вставляет manifest, затем `UPDATE snapshots SET manifest_hash=data_contract.snapshot_manifest_hash(workspace_id,snapshot_id),sealed_at=clock_timestamp() WHERE ...`. `manifest_hash` = SHA-256 от UTF-8 PostgreSQL `jsonb::text` массива manifest, сортировка path `COLLATE "C"`. Формат SQL-defined, **не RFC8785**, вычислять серверной функцией. Никакого raw source в manifest. File hash по оригинальным bytes проверяется A при intake.

После seal файлы нельзя добавлять/править/удалять, snapshot тоже. Run можно создать только по sealed snapshot; identity/static signature не меняются. Artifact metadata immutable; единственная правка — one-way `purged_at` после фактического удаления A из store. Истечение TTL не удаляет manifest/hash, они не восстанавливают источник. Revision и domain policy append-only. Никаких cascade delete исторических фактов.

`one_active_workflow` запрещает два queued/running run одного workflow. Глобальное ограничение одного scanner worker, обработка expired artifact и cron-parser — ответственность A, не второй scheduler в SQL. Schedule pinned на revision/artifact, occurrence unique `(workspace_id,schedule_id,scheduled_for)`. Изменить pinned revision/artifact у расписания с историей не получится: создать новое расписание. Проверяются IANA timezone и соответствие occurrence→run→revision/artifact. Exactly-once delivery БД не обещает; primary key `(workspace_id,run_id,node_id,message_version)` — idempotency identity, attempts не меняют её. `delivery_unknown` требует явного решения A/получателя. Event seq выделять под `SELECT ... runs FOR UPDATE`, изменение состояния и INSERT события одной транзакцией.

## Atomic/idempotent publish

Не держать транзакцию на время сканирования. B отдаёт готовый redacted bundle; A/C предварительно валидирует Schema + semantic links/coords/redaction и ожидаемые counts. Короткая транзакция:

```sql
BEGIN;
-- Serialize creation/retries for the same run before checking the existing bundle.
SELECT 1 FROM data_contract.runs WHERE workspace_id=:workspace AND run_id=:run FOR UPDATE;
-- If published already: compare supplied source identities + expected counts/hash;
-- call publish_bundle with the saved expected hash, do not re-insert members.
-- Otherwise insert draft analysis_bundles + all normalized members and joins.
-- Include all three outcomes, failures included, never silently empty results.
SELECT data_contract.bundle_document_hash(:workspace,:run,:snapshot);
SELECT data_contract.publish_bundle(:workspace,:run,:snapshot,:expected_hash,:expected_counts);
COMMIT;
```

`expected_counts` — object **со всеми** keys `scan_outcomes,coverage,graph_coverage,evidence,findings,code_nodes,code_edges,finding_evidence,node_evidence,edge_evidence,finding_nodes,diagnostics`; значения nonnegative integers; `graph_coverage` всегда 1 (даже для unavailable графа). Counts приходят из проверенного входного bundle до INSERT, не вычисляются из уже потерянных строк. `expected_hash` в v0.1 SQL-defined SHA-256 от `bundle_document(...)::text` UTF-8; эта серверная canonical storage representation включает identity/signature/manifest и все нормализованные members, с массивами, отсортированными по row JSON `COLLATE "C"`. Это **не hash DTO JSON**. Для повторного запроса A сохраняет expected hash исходного publish; если повторные данные отличаются, явный conflict. Финальный дизайн external idempotency request hash нужно подтвердить A — нельзя считать повторный клиентский payload одинаковым только по одному runId.

`publish_bundle` блокирует header, проверяет counts, все три family outcomes, source-edge/node evidence и фактический hash; повтор того же hash возвращает его, другой hash — error. Mutation triggers блокируют header при любой вставке/правке members; полный `graph_coverage.coverage` сохраняет именно CodeFactsBundle.coverage со всеми graph-only gaps; publication и members одной транзакцией либо целиком видны, либо rollback. Черновик можно восстановить вручную до publish; у публичного reader только `published_bundles`/`published_findings` и JOIN остальных таблиц через published header. Чтение сырых member tables выдаёт draft — A обязан закрепить reader grants/queries. SecurityMapDto не отдаётся из draft. SQL не доказывает, что B не забыл запрошенное правило: expected scope manifest/config и completeness проверяются в ingestion.

После publish bundle/facts immutable, включая `TRUNCATE`; AI приходит отдельно и не меняет hash. Любой ai_explanations INSERT (accepted/rejected/unavailable) требует published source header под FOR SHARE lock; на draft Finding нельзя привязать immutable explanation. После publish facts неизменяемы, поэтому grounding не устареет при правке draft. Фактический provider failure до publish A держит вне этой таблицы до frozen source, не сохраняет как grounded explanation. Run execution/delivery lifecycle может завершаться позже. `pass` требует опубликованный bundle, все complete scanner outcomes и **непустое checked coverage каждой scanner family**; пустой или полностью нерелевантный input консервативно остаётся inconclusive до отдельного решения A/B; policy verdict/threshold вычисляет A, SQL не запускает анализатор или policy engine. Domain policy JSONB проходит `valid_domain_policy_document`: непустые resources, ровно один kind `role_access|ownership`, проверяемые обязательные поля, никакого выдуманного owner у role policy.

## Embeddings — pending D

Основная миграция создаёт registry без модели и размерности. До ответа D записывать только `pending` со всеми model fields null. Один `active` spec на workspace; перевод в active требует provider/model/version/dimension/metric/queryPreparation/documentPreparation/redaction versions. Active spec immutable кроме retire; смена — новая registry row и отдельные vectors. Нет автоматического DEFAULT dimension/model.

Optional миграция хранит `vector` без фиксированной размерности, FK к spec.dimension + `vector_dims` check и проверка ненулевого вектора. Ingestion требует active spec и vetted example неизменного content hash. Retrieval `exact_similar_fixes` принимает точный specId, проверяет provider/model/model_version/metric и версии подготовки, dimension и обязательные workspace/language/framework/ruleFamily/status фильтры, сортирует exact distance без ANN index. Ряд содержит `source_ref`, `redacted_before`, `redacted_fix`, `rule_version`, `verification_basis`, `limitations`, `example_status`, `distance`, `metric`. Значения distance: cosine = `1-cos`, l2 = евклидово расстояние, inner_product = **отрицательное** скалярное произведение; меньше лучше, это не вероятность. Spec/model/preparation должен совпадать не только по dimension: endpoint принимает vector **от адаптера D**, который возвращает проверенный specId, клиенту запрещено подменять provenance.

`015_vector_demo_fixture` — отдельный demo/fixture: 5 synthetic примеров `draft`-only, 16-мерные token-hash векторы и своя таблица spec. Он проверяет storage и exact-механику, но не является выбором embedding-модели и не доказывает пользу поиска; demo-строки не могут стать `vetted`.

Любое отсутствие active spec/несовместимость/provider failure → typed retrieval `unavailable`, не пустая «успешная» выдача. Вызывать в отдельной транзакции/узле после immutable static publish: ошибка retrieval никогда не откатывает scan/report. Redaction до embedding request; vector не является способом обезличить raw secret. Человеческая verified/vetted оценка и применимость не выводятся из distance.
