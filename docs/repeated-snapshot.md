# Повторный intake одного snapshot

`snapshot_id` — это UUID5 от immutable manifest содержимого. Два intake одного
и того же проекта дают один `snapshot_id`, но разные `artifactId` (и иногда
разные ZIP bytes/artifactHash при том же extracted manifest). Это не конфликт
содержимого.

## Модель

- `data_contract.snapshots` — одна immutable content snapshot; первая
  artifact identity остаётся для обратной совместимости.
- `snapshot_content_hashes` — все artifact hashes, которыми приходил этот
  snapshot; на неё ссылаются `runs`.
- `snapshot_origins` — все intake `(artifact_id, artifact_hash)`; append-only.
- `snapshot_files` — по-прежнему единственный trusted manifest; файлы и
  `snapshot_manifest_hash` не переписываются.

Hash без происхождения не является фактом: deferred constraint trigger
`check_snapshot_content_origin` проверяет на commit, что у
`snapshot_content_hashes` есть хотя бы один `snapshot_origins` с тем же
`artifact_hash`. `CanonicalPublisher` вставляет оба факта в одной транзакции;
обычный `INSERT` произвольного hash без origin откатывается. Origin с
несуществующим artifact отклоняет FK.

`CanonicalPublisher` больше не сравнивает новый intake artifact с
`snapshots.artifact_id`; он добавляет origin/content hash и проверяет, что
`artifact_id` не связан с другим hash. Несовпадающие файлы, manifest hash или
повреждённые bytes по-прежнему отклоняются.

## Инварианты

- workspace/snapshot/run остаются scoped; чужой workspace не читается;
- evidence/findings и bundle hash остаются run-scoped;
- один content snapshot может иметь много intake artifacts и runs;
- нельзя переписать существующий snapshot или его files;
- нельзя удалить/обновить origin/content hash; `TRUNCATE` новых таблиц тоже
  заблокирован statement-триггерами;
- `expectedScopesVerified` и `globalComparable` не меняются этим изменением.

Это защита обычного SQL path. DB owner/superuser может отключить триггеры или
изменить схему; такие действия не считаются гарантией приложения.

## Миграция и adoption

`db/migrations/006_snapshot_origins.up.sql` создаёт таблицы, FK `runs` и
guard-триггеры. `backend/app/storage/migrate.py::MARKERS` проверяет полный
набор: обе таблицы с PK/FK, `runs_snapshot_content_hash_fkey`, deferred origin
guard, row-guards и TRUNCATE-guards. Поэтому ручной `psql` с полным 006 без ledger
подхватывается при старте, а частичная схема не помечается применённой.
`snapshot_content_hashes`/`snapshot_origins` backfill-ятся из существующих
`snapshots` с сохранением данных.
