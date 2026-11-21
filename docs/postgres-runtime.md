# Runtime на PostgreSQL

Слой A: `DatabaseRepository` и таблицы API. Контрактная схема C (`data_contract`)
не меняется и не ослабляется — она приходит из `db/migrations/001..002`.

## Настройки

| Переменная | Обязательна | Смысл |
|---|---|---|
| `DATABASE_URL` | да | `postgresql://user:password@host:5432/db` |
| `CG_WORKSPACE_ID` | нет | UUID или seed (`uuid5(NAMESPACE_URL, "cg-workspace:<seed>")`); раньше использовался слоем codefacts |
| `RUNTIME_WORKSPACE_ID` | нет | только UUID, совместимость storage-контракта |

API пока не принимает workspace в запросах: процесс работает в одном workspace,
и чтение по id из чужого workspace возвращает `None`/404, а не чужие данные.
Оба имени переменных читаются одним resolver-ом в
`backend/app/codefacts/identity.py`; по умолчанию оба дают
`93486ede-5bf9-58ff-87fe-062e7b47d748`
(`uuid5(NAMESPACE_URL, "41l1-n3dra/default-workspace")`). Если `CG_WORKSPACE_ID` и
`RUNTIME_WORKSPACE_ID` заданы и разрешаются в разные UUID, старт падает с
понятной ошибкой вместо silent split; задайте один источник или одинаковое
значение. Секреты — только в локальном окружении, не в репозитории.

## Что делает старт

1. Применяет миграции C через ledger `public.schema_migrations` (filename + sha256,
   advisory lock); повторный запуск их не повторяет, изменённый файл — ошибка.
2. Если `DB_ENABLE_VECTOR=1` и на сервере есть pgvector, применяет все
   `db/optional/*.up.sql` (003, 014, 015) через **тот же** ledger
   `public.schema_migrations` и проверяет `pg_available_extensions`. Отдельной
   ledger-таблицы нет: optional-прогон отличается advisory lock и списком файлов.
   Без флага optional не применяется; повторный старт SQL не повторяет. Registry
   остаётся `pending`, fake vectors не создаются.
3. Под `pg_advisory_xact_lock` заводит identity-строку workspace в
   `data_contract.workspaces` (`runtime:<uuid>`), если её нет, и создаёт/обновляет
   runtime DDL. Lock сериализует параллельные старты на пустой БД; других записей
   в C-схему runtime не делает.
4. Проверяет `runtime.schema_meta` и приводит runtime schema к версии 3:
   старая схема без workspace-колонок обновляется с сохранением строк,
   версия 2 дополняется immutability-триггерами.
5. Создаёт свежую schema `runtime` версии 3, если база пустая.

## Optional pgvector

`DB_ENABLE_VECTOR=1` — единственный способ включить `db/optional/*.up.sql`.
Без флага файлы не применяются и в ledger не попадают. Если флаг включён, а
`pg_available_extensions` не содержит `vector`, старт падает с понятной ошибкой.
Мигратор берёт все optional-файлы по порядку: `003` создаёт
`example_embeddings` и базовую функцию, `014` заменяет `exact_similar_fixes`
на контракт с точной сверкой одного active spec, обязательными фильтрами и
provenance-полом для `vetted`-строк, `015` создаёт отдельные demo/fixture-таблицы
и `exact_demo_fixes`. Схема не
выбирает provider/model/dimension: `embedding_specs` остаётся пустым/pending,
fake vectors в реальные таблицы не пишутся. Demo/fixture заполняется только
явной командой `python -m backend.app.ai.vector_demo --seed`, `draft`-only и не
входит в реальную выдачу. API embeddings не появляется.

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
*_evidence/finding_nodes/diagnostics/domain_policy_revisions` → expected counts из проверенного входа (до INSERT) → `bundle_document_hash` → `publish_bundle(...)` → immutable envelopes в `data_contract.bundle_envelopes` (ledger migrations `004`+`005`) → run status. `published_at`/`bundle_hash` выставляет только SQL.

Две явные копии, без расхождения правды:

- `source_envelope` — ровно то, что отдал B; immutable для аудита. `document_hash`
  считается по нему + manifest (`compute_document_hash`), `envelope_hash` — SQL
  hash его jsonb.
- `canonical_envelope` — тот же envelope с coverage/status, которые реально
  сохранены в SQL после честного downgrade (expectedScopesVerified=false или
  complete-with-holes). `canonical_hash` — SQL hash этой копии. `get_published_envelope`
  отдаёт именно canonical, поэтому UI/comparison не видят «complete», которого нет
  в facts. `get_source_envelope` отдаёт исходник и оба hash отдельно.

Идемпотентность: тот же identity + `document_hash` возвращает существующий
`bundleHash` (`idempotent=true`); другой payload для уже published identity —
`CanonicalConflictError`, перезаписи нет.

Чтение для comparison package:

```python
publisher.get_published_envelope(run_id, snapshot_id)
# {bundle: <canonical AnalysisBundle>, manifest: {...},
#  provenance: {...}, envelopeHash, canonicalHash, documentHash, publishedAt}
publisher.get_source_envelope(run_id, snapshot_id)
# {bundle: <source AnalysisBundle>, documentHash, envelopeHash, canonicalHash,
#  manifestHash, bundleHash, publishedAt}
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

Concurrency: publish берёт транзакционный advisory lock на identity
`workspace/run/snapshot` (и отдельные lock-и на artifact/snapshot/workflow/policy).
Два параллельных publish одного identity дают один `idempotent=false` и один
`idempotent=true` с тем же `bundleHash`, без дублей строк. Один `snapshotId` с
другим artifact archive (даже при том же manifest) и один `artifactId` с другими
bytes отклоняются как immutable identity conflict.

## Comparison storage

`backend/app/storage/comparisons.py` — сохранение результата comparison engine:
`ComparisonStore.save(dto)` берёт advisory lock на comparison id, вставляет draft
`comparisons` + `comparison_items`, затем публикует header; `guard_comparison`
проверяет published bundles, совместимость signature, fingerprints и coverage.
Повторный save сверяет **всю header identity** (baseline/current run+snapshot,
comparable, reasons) и items: совпадение идемпотентно, любое отличие — conflict;
параллельные save одного id сериализуются. `get(comparison_id)` возвращает DTO с
`items` и вычисленными `deletedFiles` по двум sealed manifest. Pseudo-publish нет.

## Remediation examples

`backend/app/storage/remediation.py` — `RemediationStore.save/get/list` для
`data_contract.remediation_examples`. `vetted` — узкий автоматический допуск, не
произвольный текст: published source и **обязательный** published fixed bundle,
оба с `origin='scanner'` и `expected_scopes_verified=true`, совместимые
`static_signature`, checked coverage выбранного `category/path/rule` в обоих
run-ах и отсутствие того же fingerprint в fixed run. Дополнительно нужен
published `comparison_items.status='resolved'` именно для этого
`sourceFindingId` против fixed run/snapshot: `changed` не считается resolved, и
исчезновение одного fingerprint без этого не доказательство. Только тогда плюс
`verificationBasis`, `reviewedBy`, `reviewedAt`, `limitations`. Synthetic
`contract_scenario`/`manual_fixture` не может стать vetted; 0 findings после regex
без checked coverage — тоже. Content hash, source/verification identity и
`limitations` фиксируются; повтор exampleId с другой identity — conflict.
`reviewedAt` нормализуется в UTC до сравнения, поэтому `Z` и `+00:00` дают один
момент и не ломают идемпотентный save.

Подготовлены 3 remediation examples, все **draft**: SQL before/after с живым
`SastAnalyzer` (before `KOTLIN-SQL-INJECTION`, after 0 findings) и два варианта
фикса без перепроверки. Envelope-ы — synthetic adapter probe
(`contract_scenario`), поэтому автоматический vetted-допуск их отклоняет; vetted
появится только после настоящего scanner publish от adapter. Fake vetted с
synthetic provenance проверен отрицательным probe: `RemediationValidationError`,
строка не создаётся.

## Локальная проверка

```bash
# временная БД (пример; креды — локальные, в репозиторий не попадают)
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/runtime_check
python -m backend.app.storage.migrate     # применить миграции C + 004/005 (+003 при DB_ENABLE_VECTOR=1)
python -c "from backend.app.storage.database import DatabaseRepository as R; R()"
```

Проверочные canonical bundles в этой ветке — **synthetic handcrafted envelopes с
`origin=contract_scenario`**, а не настоящий scanner run: два настоящих scanner
run появятся только после adapter от backend author. Comparison по ним честно
несёт reason «contract scenario, not scanner output». Отдельно live `SastAnalyzer`
прогонялся на before/after Kotlin fixtures: before даёт `KOTLIN-SQL-INJECTION`,
after — 0 findings; это основание только для одного vetted remediation example
(автоматическая проверка, не human review — записано в limitations).

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

`005_canonical_envelope.up.sql` отделяет source envelope от canonical и для
старых pre-005 строк **восстанавливает** `canonical_envelope` из уже сохранённых
`scan_outcomes.coverage_summary` и `graph_coverage.coverage`: те же status/scopes/
limits, что в SQL. Source, `document_hash` и `envelope_hash` не меняются, republish
не нужен. Если fact-строка отсутствует, соответствующее coverage консервативно
помечается `partial` с reason `reconstructed: missing ...`, а не выдаётся за
complete. Новые публикации сразу пишут обе копии и оба hash.

`provenance.bundleHash` — авторитетный SQL `analysis_bundles.bundle_hash`; его
нельзя пересчитать из canonical/source envelope и нельзя подменять
`canonicalHash` (это hash jsonb-копии). UI/validator должны валидировать только
форму hash и брать значение из storage, а не recompute из coverage.

Не путать три независимых hash:

- `provenance.bundleHash` — SQL `analysis_bundles.bundle_hash` (published facts;
  storage authority).
- `canonicalHash` / `envelopeHash` — jsonb-hash canonical/source envelope.
- `SecurityMapDto.bundleHash` — внутренний hash backend codefacts:
  `sha256(canonical_json(map_dto без поля bundleHash, sort_keys=True))`. Storage
  его не производит и не подменяет SQL hash. Если API заменил coverage на
  canonical после построения map, внутренний hash нужно пересчитать этим
  recipe, иначе map вернёт старый hash.

## Секреты в ошибках

Публичный отказ подключения содержит только тип причины
(`OperationalError` и т.п.) — текст драйвера с DSN наружу не выводится.
Поэтому `DATABASE_URL` не попадает в exception, ответ API или лог. Остальные
ошибки runtime называют объект/таблицу, но не строку подключения.