# Runtime на PostgreSQL

Слой A: `DatabaseRepository` и таблицы API. Контрактная схема C (`data_contract`)
не меняется и не ослабляется — она приходит из `db/migrations/001..002`.

## Настройки

| Переменная | Обязательна | Смысл |
|---|---|---|
| `DATABASE_URL` | да | `postgresql://user:password@host:5432/db` |
| `RUNTIME_WORKSPACE_ID` | нет | UUID workspace; по умолчанию `93486ede-5bf9-58ff-87fe-062e7b47d748` (`uuid5(NAMESPACE_URL, "41l1-n3dra/default-workspace")`) |

API пока не принимает workspace в запросах: процесс работает в одном workspace,
и чтение по id из чужого workspace возвращает `None`, а не чужие данные.
Секреты — только в локальном окружении, не в репозитории.

## Что делает старт

1. Применяет миграции C через ledger `public.schema_migrations` (filename + sha256,
   advisory lock); повторный запуск их не повторяет, изменённый файл — ошибка.
2. Заводит identity-строку workspace в `data_contract.workspaces`
   (`runtime:<uuid>`), если её нет. Других записей в C-схему runtime не делает.
3. Проверяет `runtime.schema_meta` и приводит runtime schema к версии 3:
   старая схема без workspace-колонок обновляется с сохранением строк,
   версия 2 дополняется immutability-триггерами.
4. Создаёт свежую schema `runtime` версии 3, если база пустая.

## Границы runtime-слоя

- Все таблицы лежат в schema `runtime`, не в `data_contract`.
- PK всегда начинается с `workspace_id`, поэтому одинаковые текстовые id в разных
  workspace не конфликтуют.
- `findings`, `node_runs`, `reports`, `run_events` scoped по
  `(workspace_id, run_id)`. Старый scanner finding id может повториться в
  следующем прогоне: это отдельная строка, первый прогон не перетирается.
- Ни одного `ON DELETE CASCADE`: удалить run/workspace/workflow с историей нельзя,
  FK не уносит факты прогонов.
- Никакого `INSERT OR REPLACE`: только точечный `ON CONFLICT ... DO UPDATE` в
  пределах своего ключа (workspace + run), а артефакт при конфликте только
  сверяется и не перезаписывается (`ArtifactImmutableError`).
- Версия 3 запрещает триггерами менять/удалять `workflow_revisions` и
  `artifacts`; у run-а можно менять только статус/вердикт/счётчики/время,
  identity-поля и удаление неизменны.
- Интерфейс `DatabaseRepository` и ключи возвращаемых словарей прежние;
  `workspace_id` наружу не отдаётся.

## Чего здесь нет

`DatabaseRepository` — compatibility layer, а не canonical `data_contract`.
Runtime сам C facts не пишет: отчёт строится по своим findings и не считается
подтверждением published/coverage C. Канонические факты появляются только через
явный `CanonicalPublisher` (ниже). `ai_explanations`, `comparisons`,
`remediation_examples` не затрагиваются. `croniter` заявлен в зависимостях для
расписаний A; текущий scheduler пока разбирает `interval:`/cron вручную и этот
слой не меняет.

## Canonical bridge

`backend/app/storage/canonical.py` — единственный путь от `AnalysisBundle 0.1`
B к published C facts. API/executor пока его не вызывают: это storage-контракт
для интеграции A/B.

```python
from backend.app.storage.canonical import CanonicalPublisher, compute_document_hash

publisher = CanonicalPublisher(workspace_id)
result = publisher.publish(
    envelope,                       # AnalysisBundle 0.1 как отдал B
    manifest_files=files,           # [{path, sourceHash, byteLength, mediaType}]
    document_hash=compute_document_hash(files, envelope),
    provenance={...},               # origin, normalizationVersion, ...
    artifact={...}, workflow={...}, run={...},
)
```

Что проверяется до SQL и чем:

- ровно три distinct scanner outcomes SECRET/SCA/SAST; identity и
  `AnalysisSignature` совпадают во всех envelopes;
- scope `workspaceId/runId/snapshotId` у каждого объекта; уникальные id;
- evidence path/sourceHash/byteLength внутри доверенного manifest, диапазон
  bytes не выходит за файл; raw source не принимается и не хранится;
- coverage: `complete` только когда все scopes `checked` и нет
  skipped/parserErrors/unresolvedSymbols/limitsReached; иначе покрытие честно
  понижается до `partial` с причиной в `limitsReached` (анализ не отклоняется);
  пустой `complete` на непустом input тоже даёт `partial`; scanner `complete`
  понижается вместе с покрытием;
- `fp/v1` пересчитывается, SECRET требует `secretHmac`, остальные его не несут;
- graph endpoints и evidence, policy approval, policyId/version/hash из
  signature;
- `snapshotManifestHash` равен `data_contract.snapshot_manifest_hash`;
  `document_hash` — sha256 канонического JSON `{schemaVersion, manifest,
  envelope}` (транспортная целостность; HMAC-подписи в контракте C нет).

Публикация одной транзакцией: artifact (только opaque `storage_ref`, без исходников)
→ open+seal snapshot → run в `queued/pending` → draft `analysis_bundles` → все
`scan_outcomes/coverage/graph_coverage/evidence/findings/code_nodes/code_edges/
*_evidence/finding_nodes/diagnostics/domain_policy_revisions` → expected counts из проверенного входа (до INSERT) → `bundle_document_hash` → `publish_bundle(...)` → immutable
raw envelope в `data_contract.bundle_envelopes` (ledger migration `004`) → run
status. `published_at`/`bundle_hash` выставляет только SQL.

Идемпотентность: тот же identity + `document_hash` возвращает существующий
`bundleHash` (`idempotent=true`); другой payload для уже published identity —
`CanonicalConflictError`, перезаписи нет.

Чтение для comparison package:

```python
publisher.get_published_envelope(run_id, snapshot_id)
# {bundle, manifest: {complete, workspaceId, snapshotId, hash, files},
#  provenance: {published, bundleHash, normalizationVersion, origin,
#               expectedScopesVerified, domainPolicyApplicability}}
publisher.list_published_runs(limit=50)
```

`expectedScopesVerified=false`: все `complete` coverage/outcomes понижаются до
`partial` с причиной, bridge не подменяет aggregate на checked; comparison
получает флаг в provenance и возвращает inconclusive.

`artifact.expiresAt` обязателен (C schema: NOT NULL, expires_at > created_at),
`workflow.draftVersion` — целое > 0.

Stable UUID mapping: `canonical_uuid(kind, local_id, workspace_id)` =
`uuid5(NAMESPACE_URL, f"cg-{kind}:{workspace}:{local_id}")`; identity из envelope
не переписывается. Runtime text id не превращается в UUID молча: новый run id
должен быть UUID, legacy — только явный mapping.

## Локальная проверка

```bash
# временная БД (пример; креды — локальные, в репозиторий не попадают)
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/runtime_check
python -m backend.app.storage.migrate     # применить миграции C + 004
python -c "from backend.app.storage.database import DatabaseRepository as R; R()"
```

Прогон без временной БД: `backend/test_e2e.py` проверяет полный путь
artifact → run → findings → report. Проверка изоляции: заведите строку в другом
workspace и убедитесь, что `get_run`/`get_artifact` по её id возвращают `None`.

## Обновление схемы и dev-баз

Версия runtime-слоя — `runtime.schema_meta` (сейчас 3). Обновление идёт
транзакционно при старте, C-миграции 001/002 не редактируются:

| Было | Что делает старт |
|---|---|
| пустая БД | создаёт schema `runtime` v3 |
| v1 (819f4de, без workspace-колонок) | переименовывает старую schema в `runtime_legacy_v1`, создаёт v3, копирует все строки в workspace процесса, ставит версию 3 |
| v2 (workspace isolation без триггеров) | добавляет immutability-триггеры, поднимает версию до 3 |
| другая версия | понятная ошибка, молча работать нельзя |

`runtime_legacy_v1` после обновления остаётся бэкапом; удалить вручную, когда
убедились, что данные читаются. Данные в нём не теряются и не переписываются.

## Секреты в ошибках

Публичный отказ подключения содержит только тип причины
(`OperationalError` и т.п.) — текст драйвера с DSN наружу не выводится.
Поэтому `DATABASE_URL` не попадает в exception, ответ API или лог. Остальные
ошибки runtime называют объект/таблицу, но не строку подключения.