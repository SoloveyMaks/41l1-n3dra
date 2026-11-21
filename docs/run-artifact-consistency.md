# Run artifact consistency

Дата: 2026-11-21. Ветка `task/postgres-runtime`, local dev `95da50b`.

## Проблема

При `POST /api/workflows/{id}/runs` с `artifactId=B` run создавался с
`artifact_id=B`, но `Project Input` предпочитал `config.artifactId=A` из
сохранённого draft. В результате scanners читали A, а map/publication/report
использовали B из run row. Это не просто отчётная нестыковка: findings,
manifest, snapshot и artifact hash могли относиться к разным архивам.

## Правило

1. Явно выбранный в запуске `artifactId` — единственный авторитетный источник
   для всех `Project Input`, scanners, codefacts map, report и publication.
2. Draft `config.artifactId` используется только как fallback, если в запуске
   artifact не выбран.
3. Если artifact не выбран и несколько `Project Input` ссылаются на разные
   `artifactId`, запуск отклоняется `400`; смешивания и выбора первого
   попавшегося нет.
4. Manifest/hash/snapshot не пересчитываются и не подменяются; downstream
   получает тот же artifact, что записан в run row.

Изменения: `backend/app/engine/executor.py` (Project Input precedence) и
`backend/app/api/router.py` (fallback/conflict проверка). Storage, SQL и
frontend не менялись.

## Живой reproduce

Temp PostgreSQL/runtime, два ZIP:

| Архив | Файл | Hash (начало) |
|---|---|---|
| A `art_658f761ebb7c` | `src/main/kotlin/Alpha.kt` | `b4d6b25448c1…` |
| B `art_0d8eff238bd2` | `src/main/kotlin/Beta.kt` | `ae530fe64ab3…` |

Workflow draft указывает `Project Input.artifactId=A`; запуск:

```text
POST /api/workflows/<wf>/runs {"artifactId":"art_0d8eff238bd2"}
```

Фактический результат:

```text
run.artifact_id = art_0d8eff238bd2 (B)
findings = 1, path = src/main/kotlin/Beta.kt
map.identity.artifactHash = ae530fe64ab3e8f6352f5d2cbf84ff97c525cb431a61ed6144856b83afacf03a (B)
map.identity.snapshotId = 80a7fb6a-dd57-5d73-861e-5ae209d92a10
map.coverage.scopes = [src/main/kotlin/Beta.kt]
report.readable_markdown содержит Beta.kt и не содержит Alpha.kt
codefacts identity.artifactHash = B; publication.published = true
```

Fallback без `artifactId` на том же workflow дал `run.artifact_id=A` и finding
`Alpha.kt` — draft-артефакт работает только когда запуск artifact не выбрал.

Schedule с `artifact_id=B` при draft A дал schedule-run `artifact_id=B` и
finding `Beta.kt`; admin/manual trigger через тот же API — тот же B. Два
`Project Input` с разными artifactId без выбранного artifact дают:

```text
400 Project Input nodes ссылаются на разные artifactId:
    выберите artifactId в запуске
```

Это проверяет именно выбранный artifact, а не доверие к draft. Map coverage
остаётся честно `partial` по canonical ограничениям; это не влияет на
artifact provenance.