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

`CanonicalPublisher` больше не сравнивает новый intake artifact с
`snapshots.artifact_id`; он добавляет origin/content hash и проверяет, что
`artifact_id` не связан с другим hash. Несовпадающие файлы, manifest hash или
повреждённые bytes по-прежнему отклоняются.

## Инварианты

- workspace/snapshot/run остаются scoped; чужой workspace не читается;
- evidence/findings и bundle hash остаются run-scoped;
- один content snapshot может иметь много intake artifacts и runs;
- нельзя переписать существующий snapshot или его files;
- `expectedScopesVerified` и `globalComparable` не меняются этим изменением.

Миграция: `db/migrations/006_snapshot_origins.up.sql`.
