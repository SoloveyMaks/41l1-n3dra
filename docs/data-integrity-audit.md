# Аудит целостности данных и честности результатов

Ветка: `SoloveyMaks/data-integrity-audit`, run `run_bbf876ca1e6c`.
Локальная `dev` обновлена координатором до `9083f04` (включает admin/roles `4012fb5`, snapshot fix `113bfeff`, verdict guard `9083f04`); она смержена в ветку аудита merge-коммитами `6fe2aa3`, `63fe303`, `9a194d6`. Базовая точка `9862e19` была предком HEAD ещё до этих merge.

## 1. Итог

| ID | Влияние | Статус | Дефект | Где |
|---|---|---|---|---|
| P1-1 | высокое (исправлен) | исправлено `113bfeff`; S-1/S-2/S-3 открыты | До `113bfeff` повторная загрузка того же архива давала тот же `snapshotId`, но новый `artifactId`, и canonical publish падал с конфликтом. После `006_snapshot_origins` второй artifact/run принимается; остаточные DB-gaps S-1/S-2 ниже | `backend/app/storage/canonical.py` (fix `113bfeff`); `db/migrations/006_snapshot_origins.up.sql`; старый конфликт — pre-fix `canonical.py:1125` |
| S-1 | среднее | открыто | `snapshot_content_hashes` принимает произвольный hash без artifact; `runs` затем ссылается на него | `db/migrations/006_snapshot_origins.up.sql:7-14`; проверено temp DB |
| S-2 | низкое | открыто | `TRUNCATE snapshot_origins` проходит при TRUNCATE-праве/владельце: provenance intake можно стереть | `db/migrations/006_snapshot_origins.up.sql:62-69`; проверено temp DB |
| S-3 | среднее | открыто | В `migrate.py MARKERS` нет 006: ручное применение через psql + старт backend = повторный `CREATE TABLE` и падение | `backend/app/storage/migrate.py:23-32`; воспроизведено на temp DB |
| P1-2 | высокое | исправлено `9083f04` (code review); live e2e не прогнан | Runtime показывает PASS как итог, хотя canonical bundle остаётся `partial` и может быть не опубликован; API/отчёт не отдают `publication`/coverage рядом с вердиктом. `expectedScopesVerified=false` — принятое честное ограничение, его нельзя поднимать в `true` без доказанных scopes | `backend/app/engine/executor.py:300-320`, `593-606`; `backend/app/storage/database.py:468-500`; `backend/app/api/router.py:420-433`, `601-609`; `backend/app/reports/builder.py:44-46` |
| P2-0 | среднее (продукт) | принятое ограничение | Сравнения и `vetted` недостижимы, пока `expectedScopesVerified=false`: comparison всегда `comparable=false`, `RemediationStore` требует `expected_scopes_verified=true`. Это известная незавершённость, не подделка данных; UI/API должны называть причину явно | `backend/app/codefacts/publish.py:96`; `packages/comparison/index.mjs:369-374`; `backend/app/storage/remediation.py:215-223` |
| P1-3 | высокое | открыто | `/security-map` и `/evidence` не различают published canonical envelope и локальный mutable fallback; `publication.reason` теряется | `backend/app/api/router.py:456-476`, `480-511`, `512-548` |
| P2-1 | среднее | открыто | Runtime-слой не связан FK с artifact/revision и не проверяет evidence/coverage: принимает `verdict=pass` с несуществующими artifact/revision, findings и report можно переписать после завершения | `backend/app/storage/runtime_schema.sql:52-60`, `90-111`, `113-126`; проверено живыми SQL probes |
| P2-2 | среднее | открыто (латентно) | Comparison engine вычисляет `comparable` до добавления reason про `contract_scenario`; результат — `comparable=true` с причиной «синтетика» | `packages/comparison/index.mjs:376-377`; подтверждено live node probe |
| P2-3 | среднее | частично: canonical guard `9083f04`; scan outcomes ещё не проверяются | До `9083f04` `strictCoverageRequired=false` позволял PASS по неполному runtime coverage; теперь не-`fail` требует canonical check. Остаточные: `Coverage` без scopes, team_b теряет skipped/limits, policy gate не проверяет три scan outcome | `backend/app/engine/executor.py:593-606`; `backend/app/integrations/team_b.py:188-199` |
| P2-4 | среднее | открыто | `save_findings` не удаляет устаревшие findings; `started_at` node_run равен времени завершения; `official.json` всегда пишет `executionStatus: succeeded` | `backend/app/storage/database.py:607-647`, `677-702`; `backend/app/engine/executor.py:925-936`; `backend/app/reports/builder.py:168` |
| P2-5 | среднее | открыто | Domain policy никогда не попадает в canonical signature/policyInputs; `hmacKeyVersion` захардкожен, ротация ключа не видна сравнению | `backend/app/codefacts/canonical.py:731-735`, `620-706` |
| P3-1 | низкое | открыто | Маскирование source best-effort: JWT маскируется частично, короткие значения не маскируются; `redacted_snippet` в БД не проверяется на содержание | `backend/app/codefacts/source.py:66-88`; проверено pure-функцией |
| P3-2 | низкое (до split) | открыто (до split) | Runtime-таблицы читаются из одного env-workspace, session workspace учитывается только для canonical/local файлов | `backend/app/api/session.py:227-229`; `backend/app/storage/database.py:292-296` |
| P3-3 | низкое | открыто | `raw_secrets_checked=True` — константа; `ai_explanations` и `remediation_examples` нигде не пишутся приложением | `backend/app/ai/service.py:62`, `227`, `385`; `backend/app/contracts/schemas.py:240` |
| A-1 | среднее | открыто | Нет server-side отзыва сессии: logout удаляет cookie, admin-токен жив до TTL | `backend/app/api/router.py:168-170`; `backend/app/api/session.py:155-188`; `docs/admin-access.md` |
| A-2 | низкое | открыто | `POST /api/webhook-test` не admin-гард; в local mode вообще без сессии | `backend/app/main.py:31`; `backend/app/api/router.py:688-715` |

Полного «safe/fixed» нет: часть инвариантов SQL сильна, но прикладной слой обходит их или не публикует факты.

## 2. Методика и границы

Проверки выполнены в отдельных временных PostgreSQL-контейнерах (`pgvector/pgvector:pg16`, tmpfs, порт только `127.0.0.1`): один для базовых инвариантов, второй — pre-006 база для проверки backfill миграции `006_snapshot_origins` (в него сначала применены 001/002/004/005 и legacy fixture, затем 006). Production и общая БД не трогались, секреты не читались и не печатались. В контейнер применены:

```text
db/migrations/001_core.up.sql
db/migrations/002_integrity_publish.up.sql
db/migrations/004_bundle_envelopes.up.sql
db/migrations/005_canonical_envelope.up.sql
db/migrations/006_snapshot_origins.up.sql
db/optional/003_exact_vector.up.sql
backend/app/storage/runtime_schema.sql
backend/app/storage/runtime_guards.sql
```

SQL probes выполнялись с `ON_ERROR_STOP=1`, каждый негативный probe — в `BEGIN … ROLLBACK`. Живые проверки comparison engine — `node v22.23.3` на `docs/data-contracts/illustrative-bundle.json`. Чистые функции identity/masking проверены Python-вызовом; ядро сессий/ролей — Python-вызовом с заглушкой `fastapi` (без запуска сервера). Вердикт-guard `9083f04` проверен чтением кода/диффа и сверкой с `docs/verdict-consistency.md`; полный executor run не запускался.

Не проверено: полный backend e2e (в системном Python нет `psycopg`), реальные сканеры на чужом коде, сетевой AI/embedding provider, production RLS/роли, поведение под нагрузкой. Выводы P1-1/P1-2 опираются на код и воспроизведение чистых функций/движка, а не на полный API-прогон.

### Граница доверия: БД-владелец vs приложение

Все «immutable»-гарантии ниже действуют для обычных разрешённых SQL-операций приложения и роли с DML/DDL-правами на таблицы, но **не являются защитой от DB owner/superuser**. Владелец БД может:

- `ALTER TABLE ... DISABLE TRIGGER` / `DROP TRIGGER`, `DROP CONSTRAINT`, `DROP FUNCTION`;
- `SET session_replication_role = replica` и обойти row-триггеры;
- переписать `data_contract`/`runtime` строки, `TRUNCATE` таблицы, изменить `public.schema_migrations`;
- прочитать raw rows и отключить любые проверки.

Это не дефект миграций и не обещание аудита: криптографической защиты от суперпользователя нет. S-1 — обычный `INSERT` приложения/роли (guard отсутствует), поэтому это реальный gap. S-2 — `TRUNCATE` (нужно TRUNCATE-право, обычно у владельца), row-триггеры на него не срабатывают, поэтому provenance теряется. S-3 — ошибка прикладного migration-пути, не суперпользователя. Минимальный фикс — добавить constraint/trigger/marker, **не ослабляя** существующие manifest/hash/immutability guards; для реального admin отдельно нужен least-privilege runtime role и запрет DDL на прикладной роли.

## 3. Что подтверждено как корректное

Живые SQL probes приняли/отклонили ровно то, что заявлено в миграциях:

| Проверка | Результат |
|---|---|
| `pass` при published bundle, 3 `complete` outcomes и checked coverage по каждой family | принят |
| `pass` при отсутствии checked coverage для SAST | отклонён: `pass requires published complete outcomes and nonempty checked coverage for every scanner family` |
| evidence с `byteEnd > byte_length` или чужим `source_hash` | отклонён: `evidence range/hash is outside snapshot file` |
| source-edge без `edge_evidence` | отклонён: `source edge needs evidence` |
| не-actor node без `node_evidence` | отклонён: `source node needs evidence` |
| `policy_assumption` edge с policy, отличной от run policy | отклонён: `edge policy differs from run policy` |
| SECRET finding без `secret_hmac` | отклонён CHECK-констрейнтом |
| добавление файла в sealed snapshot; повторный seal | отклонены |
| публикация с неверными `expected_counts` | отклонена |
| повторный `publish_bundle` с тем же hash | идемпотентен; с другим hash — `idempotency conflict` |
| UPDATE/DELETE опубликованного bundle, INSERT member после publish, DELETE/TRUNCATE run | отклонены |
| AI explanation на draft bundle | отклонён: `AI explanation requires published immutable source bundle` |
| `vetted` remediation без verification basis / published source | отклонён |
| comparison `comparable=true` при разных signature | отклонён: `analysis signatures incompatible` |
| comparison `resolved` без checked scope в current | отклонён: `resolved requires checked file/rule in both runs` |
| `resolved` при checked scope в обоих runs и отсутствии fingerprint | принят |
| `file_deleted`, когда файл ещё есть в current snapshot | отклонён: `file still exists` |
| draft finding через `published_findings` | не виден (0 строк), прямой `findings` — 1 строка |

Это проверка SQL-контракта на ручном fixture, а не поведение приложения: приложение само не публикует `complete`/`pass` из-за `expectedScopesVerified=false` (P2-0). Инварианты закрывают ссылки node/edge на evidence, диапазоны evidence, неизменяемость опубликованных фактов, подделку `resolved/file_deleted` и публикацию без трёх family outcomes.

## 4. Дефекты по влиянию

### P1-1. Повторный publish одинакового snapshot (исправлен `113bfeff`)

До фикса `113bfeff`: `snapshotId` — `uuid5` от хэша manifest (`identity.py:98`), а `artifactId` — `uuid5` от случайного runtime id (`identity.py:113`; id создаётся в `intake.py:118`, `303`). Повторная загрузка того же архива давала тот же `snapshotId` и новый `artifactId`. `CanonicalPublisher._ensure_snapshot` сравнивал новый artifact со старой строкой snapshot:

```python
# pre-fix backend/app/storage/canonical.py:1119-1129
if str(row["artifact_id"]) != artifact_id or row["artifact_hash"] != identity["artifactHash"]:
    raise CanonicalConflictError("snapshot: exists with a different artifact identity")
```

`publish_run_artifact` ловит исключение и возвращает `{"published": False, "reason": "publish failed: ..."}` (`publish.py:153-154`). Следствия:

- второй прогон неизменённого проекта не имеет canonical bundle;
- comparison отдаёт `NOT_PUBLISHED` (`api/comparison.py:62-69`);
- `security-map` живёт на локальном fallback, при этом API не сообщает, что публикации нет (см. P1-3);
- `vetted` remediation и embeddings для такого run недостижимы.

Проверено pure-функциями: одинаковый manifest hash → одинаковый `snapshot_id`; два случайных artifact id → разные `artifact_scope_id`. Полный API-прогон не выполнялся (нет `psycopg`), но конфликт — прямое следствие сравнения в коде.

Точный reproduction для pre-fix состояния (без правки исходников):

```text
1. POST /api/artifacts с одним и тем же zip дважды.
   -> два разных artifactId (art_<random>), один и тот же snapshotId.
2. Первый run: publish_run_artifact -> published=true.
3. Второй run с новым artifactId и тем же manifest:
   canonical.py:1125 -> CanonicalConflictError("snapshot: exists with a different artifact identity")
   publish.py:153 -> {"published": false, "reason": "publish failed: ..."}
```

Ожидаемый контракт: одинаковые bytes проекта должны либо переиспользовать одну artifact identity (content-addressed по archive hash), либо давать детерминированный конфликт до запуска с явным `publication.status=conflict`; молчаливый `published=false` при успешном run недопустим. Snapshot с тем же manifest не должен становиться непубликуемым из-за случайного id загрузки.

**Статус после `113bfeff`:** фикс принят в локальный dev и смержен в ветку аудита (`63fe303`). `snapshots` остаётся immutable content snapshot; `snapshot_content_hashes` хранит все artifact hashes, `snapshot_origins` — все intake `(artifact_id, artifact_hash)`, `runs` ссылается на content hash. Независимая проверка на temp PostgreSQL:

- migration 006 на pre-006 данных: backfill 1 run → 1 content hash + 1 origin, старый run сохранён, FK `runs` заменён на `runs_snapshot_content_hash_fkey`;
- второй artifact на тот же snapshot + новый run с тем же content hash — принят;
- run с неизвестным content hash — отклонён FK;
- origin с artifact hash, не совпадающим с `artifacts`, — отклонён FK;
- sealed snapshot/files по-прежнему нельзя менять; UPDATE/DELETE origin/content hash — отклонены.

**Остаточные DB-gaps (S-1/S-2):**

- **S-1 (среднее):** `INSERT INTO snapshot_content_hashes` принимает произвольный `artifact_hash` без artifact/origin; после этого `runs` с этим hash принимается FK. Приложение так не пишет, но DB-инвариант «hash принадлежит реальному artifact» ослаблен. Нужен trigger/CHECK, требующий существующий `snapshot_origins` (или artifact) для content hash.
- **S-2 (низкое):** `TRUNCATE snapshot_origins` проходит при наличии TRUNCATE-права (обычно владелец таблицы): нет `forbid_truncate` для новых таблиц, provenance intake можно стереть; `TRUNCATE snapshot_content_hashes` без CASCADE блокируется FK, с CASCADE — `runs is immutable (TRUNCATE forbidden)`. Добавить `forbid_truncate` на `snapshot_origins` (и content hashes для симметрии).
- **S-3 (среднее, миграционный путь):** `backend/app/storage/migrate.py:23-32` не содержит marker для `006_snapshot_origins.up.sql`. `db/README.md` разрешает manual `psql`; после такого применения ledger пуст, marker не найден, следующий старт backend выполняет 006 повторно и падает на `relation "snapshot_content_hashes" already exists` (воспроизведено на temp DB). Нужен marker `to_regclass('data_contract.snapshot_content_hashes') IS NOT NULL` и обновление списка миграций в `db/README.md`.

Направление фикса (оставшееся): content-addressed artifact identity уже есть; закрыть S-1/S-2 и добавить regression «тот же zip дважды» в прикладной путь.

### P1-2. PASS в runtime при partial/unpublished canonical

**Статус:** исправлено `9083f04`, принято в local dev и смержено (`9a194d6`). Проверка — code review + сверка с `docs/verdict-consistency.md`; live e2e не прогонялся (нет `psycopg`).

`expectedScopesVerified=false` в `publish.py:96` остаётся принятым честным ограничением: сканеры не доказали expected scopes, поднимать флаг в `true` нельзя. Из-за него canonical coverage понижается до `partial` (`_downgrade_unverified`, `canonical.py:691-704`), SQL `guard_run` не пропускает `pass` (`002_integrity_publish.up.sql:71-77`) — это ожидаемо.

До `9083f04` runtime вердикт считался отдельно в `POLICY_GATE` по `FindingsBundle.coverage`, и пользователь видел PASS, хотя canonical bundle оставался `partial`/неопубликованным. После фикса policy gate вызывает `_canonical_coverage_facts` **до** condition/AI/report/notify: любой не-`fail` при `coverage.status != complete` или `expectedScopesVerified != true` становится `inconclusive`, включая `strictCoverageRequired=false`. `fail` по findings не меняется. `PolicyResult.details.canonicalCoverage` хранит `coverageStatus`, `expectedScopesVerified`, `skippedScopes`, `bundleHash`, `parserHash`, `rulesHash`, `artifactHash`; финальный guard оставлен только как защита от обхода. Это закрывает исходный разрыв: clean relaxed run больше не даёт PASS, webhook/report/condition получают `inconclusive`.

Остаточные ограничения, которые важно не выдать за fixed:

- в `analysisBundle` обоих producers (`codefacts/canonical.py`, `analyzers/cg26/contract.py`) нет поля `provenance`; `_canonical_coverage_facts` читает `analysisBundle.provenance.expectedScopesVerified`, поэтому фактически всегда видит `false`. Ветка `verified=true` недостижима, и все чистые не-`fail` run'ы честно уходят в `inconclusive`. Это согласуется с принятым ограничением, но означает, что «verified path» пока мёртв.
- `_canonical_coverage_facts` проверяет только `codeFacts.coverage` и `expectedScopesVerified`, но не три `scanResults[].status/coverage`, которые требует SQL `guard_run`. Если будущий producer выставит `expectedScopesVerified=true` при partial scan outcome, runtime PASS снова станет возможен до SQL-отказа. Нужно зеркалить полный SQL-набор (3 complete outcome + checked scopes).
- API всё ещё не отдаёт `publication`/`coverage.status` рядом с `security_verdict` (P1-3), поэтому пользователь видит `inconclusive` без причины, кроме `reasons`.

Направление: оставить `expectedScopesVerified=false`; добавить provenance в bundle только вместе с реальным доказательством scopes; зеркалить scan-outcome guard; отдавать `publication`/`coverage` в API/UI.

### P1-3. Published и локальный fallback неразличимы в API

`get_security_map` сначала пробует canonical, затем молча отдаёт локальный run-файл:

```python
# api/router.py:480-490
payload = _load_published_map(run_id, workspace)
...
if not payload:
    payload = _load_run_artifact(run_id, workspace)
```

Локальный файл (`CodeFactsStore.save_run`) перезаписывается на каждом этапе и содержит `publication` с `published=false`, но ответ API собирается только из `identity/map/findings`; поле `publication` не возвращается. То же для `/evidence` (`router.py:512-548`). Клиент не может отличить immutable published bundle от mutable локальной карты; `map.bundleHash` — внутренний content hash, не SQL `bundleHash`.

Направление фикса: добавить в ответ `publication: {status, bundleHash, publishedAt, reason, expectedScopesVerified}` и не выдавать fallback за published.

### P2-0. Сравнения и vetted недостижимы по принятой причине

Пока `expectedScopesVerified=false`, comparison engine всегда ставит `comparable=false` (`packages/comparison/index.mjs:369-374`), а `RemediationStore._require_vetted_evidence` требует `expected_scopes_verified=true` (`storage/remediation.py:215-223`). Это продуктовая незавершённость, а не подделка данных: превращать её в `comparable=true` нельзя. Проблема в том, что API/UI не всегда называют причину явно: `/comparisons` отдаёт `comparable=false` с текстом, а `security-map`/отчёт вообще не показывают `expectedScopesVerified`. До появления доказанных scopes сравнение должно быть явно помечено «недоступно: expected scopes не подтверждены», а не выглядеть как пустой/сломанный результат.

### P2-1. Runtime-слой не связан с фактами

`runtime_schema.sql`:

- `runtime.runs.revision_id` и `artifact_id` — просто `text`, без FK (`:52-60`);
- `runtime.findings` хранит `primary_location_json`/`redacted_evidence` без ссылок на snapshot/evidence (`:90-111`);
- `runtime.reports` и `runtime.findings` не имеют immutability-триггеров (`:90-126`).

Живые probes подтвердили:

- `INSERT runtime.runs (..., artifact_id='art_missing', revision_id='rev_missing', execution_status='succeeded', security_verdict='pass')` — принят;
- upsert finding с изменением fingerprint/severity/evidence на завершённом run — принят;
- UPDATE `runtime.reports.official_json` после завершения — принят;
- DELETE run и изменение identity — отклонены (`runtime_guards.sql`).

Влияние: UI и отчёты читают именно runtime. Если до split user/admin появится запись в runtime из другого контекста, можно получить PASS без canonical-фактов, «исправленный» отчёт или findings, не привязанные к snapshot. Для аудита это хуже, чем строгий `data_contract`.

Направление фикса: либо перенести пользовательские чтения на `published_*`, либо добавить в runtime FK/проверки (`artifact_id`, `revision_id`, snapshot id, evidence refs) и immutability отчёта после финализации.

### P2-2. `comparable=true` для синтетического contract_scenario

```javascript
// packages/comparison/index.mjs:376-377
const comparable = reasons.length === 0;
if (before.envelope?.provenance.origin === 'contract_scenario' || ...) reasons.push('contract scenario, ...');
```

Reason добавляется после вычисления `comparable`. Live probe:

| label | comparable | reasons |
|---|---|---|
| scanner + unverified | false | expected scopes не подтверждены |
| scanner + verified | true | — |
| contract_scenario + verified | **true** | «contract scenario, not scanner output…» |
| contract_scenario + unverified | false | обе причины |

SQL `guard_comparison` не проверяет provenance origin и допускает `comparable=true` с непустыми `reasons`. Если в bundle есть findings, engine получит `comparable=true` и сможет выдать `persistent/changed/resolved/introduced` по синтетическим DTO. Через текущий API это не воспроизводится, потому что `publish.py:92` всегда пишет `origin="scanner"`; дефект латентный, но станет реальным, как только появится producer с `contract_scenario`/`manual_fixture`.

Направление фикса: вычислять `comparable` после всех `reasons.push`, либо явно запрещать `comparable=true` при любом synthetic origin.

### P2-3. Policy Gate и покрытие

**Статус:** частично исправлено `9083f04`. До фикса `strictCoverageRequired=false` (`executor.py:528`, `569`) отключал проверку coverage gaps, и при отсутствии findings выставлялся PASS по агрегату «сколько файлов просканировал сам сканер». Теперь для любого не-`fail` обязателен canonical check (`_canonical_coverage_facts`), поэтому текущие producers с `expectedScopesVerified=false` дают `inconclusive` даже при relaxed policy.

Остаточные ограничения:

- `Coverage` (`contracts/schemas.py`) не содержит per-file/per-rule scopes, skipped scopes и limits; `team_b.py:188-199` теряет `skippedScopes`/`limitsReached`/статусы scopes при преобразовании canonical coverage. Runtime-диагностика остаётся беднее canonical.
- `_canonical_coverage_facts` проверяет только `codeFacts.coverage` + `expectedScopesVerified`, но не три `scanResults[].status/coverage`, которые требует SQL `guard_run`. Если будущий producer выставит expected=true при partial scan outcome, runtime PASS снова станет возможен до SQL-отказа.
- Ветка `verified=true` сейчас недостижима: ни `codefacts/canonical.py`, ни `analyzers/cg26/contract.py` не кладут `provenance` в `analysisBundle`; `expectedScopesVerified` всегда false. Это честно, но означает, что PASS по этому guard'у пока невозможен в принципе.

Направление: зеркалить полный SQL-набор (3 complete outcome + checked scopes) в policy gate; не терять skipped/limits в team_b; добавлять provenance только вместе с реальным доказательством scopes.

### P2-4. Устаревшие findings, время node_run, статус в official.json

- `save_findings` делает только INSERT/UPDATE по `(workspace, run, id)` (`database.py:607-647`), не удаляя findings, которых больше нет. При повторном merge в том же run (например, два merge-узла) таблица содержит union, а `runs.total_findings` — счётчики последнего policy gate. `get_findings` вернёт больше строк, чем заявлено в run.
- `_record_node_run` пишет `started_at = now` и `finished_at = now` (`executor.py:925-936`), поэтому время старта узла фактически равно времени завершения, а сортировка `ORDER BY started_at` теряет порядок.
- `build_official_json` жёстко пишет `"executionStatus": "succeeded"` (`reports/builder.py:168`), независимо от фактического статуса run; отчёт может быть сформирован до позднего fail/notify.

Направление фикса: reconcile findings по run (delete stale в транзакции merge), передавать реальные start/finish, брать executionStatus из run.

### P2-5. Domain policy и версия HMAC-ключа

`_build_signature` всегда возвращает `domainPolicyRevisionId/Version/Hash = None` (`canonical.py:733-735`), а `codeFacts.policyInputs` в `_build_analysis_bundle` жёстко `[]` (`canonical.py:702`). `publish.py:98-102` из-за этого всегда пишет `domainPolicyApplicability="not_applicable"`. Domain policy edges/сравнение policy drift в текущем пайплайне не работают, хотя SQL и comparison engine их поддерживают.

`hmacKeyVersion` захардкожен `"cg-anchor-hmac/v1"` (`canonical.py:731`), хотя фактический ключ берётся из `CG_ANCHOR_HMAC_KEY`/`CG_SESSION_SECRET`. Ротация ключа меняет `secretHmac` и fingerprint SECRET-находок, но signature утверждает ту же версию ключа.

Направление фикса: версионировать ключ явно, включать policyInputs в bundle, либо честно объявлять policy `not_applicable` на уровне продукта и не хранить мёртвые поля.

### P3-1. Маскирование

`mask_text` (`source.py:66-88`) — best-effort. Live probe:

```text
password = "hunter2"      -> password = "hu****r2"
pwd=x                     -> pwd=x            # короче 4 символов не маскируется
JWT                       -> eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do****8U  # только длинный сегмент
```

`redacted_snippet` в `data_contract.evidence` принимает любую строку (`canonical.py:259`, `allow_empty=True`), проверки на отсутствие секретов нет. Runtime findings берут `redactedEvidence` из анализаторов (например, `sca_analyzer.py:254` — `line.strip()`), а не из единого masker. Для отчётов/UI это остаточный риск утечки.

### P3-2. Workspace/session до split

`request_workspace` берёт workspace из подписанной cookie (`session.py:227-229`), но `DatabaseRepository` инициализируется одним env-workspace (`database.py:292-296`), и все runtime-чтения (`/runs`, `/findings`, `/report`, `/schedules`) этот workspace игнорируют. Сейчас все сессии получают один и тот же default workspace, поэтому утечки между пользователями нет. После введения разных user/admin workspace чтения runtime нужно скоупить по `request_workspace`, иначе оба увидят одни и те же runs/reports.

### P3-3. AI и vetted memory

- `ExplainResult.raw_secrets_checked` всегда `True` (`service.py:62`, `227`, `385`; default в `schemas.py:240`), независимой проверки нет. Поле не выводится в `official.json`, но как контракт оно ложно.
- `data_contract.ai_explanations` и `RemediationStore` не используются API/executor: grep по `backend/app` не находит INSERT в `ai_explanations` и вызовов `RemediationStore` вне storage-модуля. AI-текст лежит в `runtime.reports` рядом с фактами (в отдельных полях), а «vetted vs draft» существует только как SQL/библиотека. Для split это значит: immutability AI-объяснений и vetted-примеров пока не является свойством работающего продукта.

## 5. Admin/roles после merge dev (`8f5d523`)

Контракт: `docs/admin-access.md`. Модель — один общий demo-workspace, роли `user`/`admin` только закрывают операторские действия; ACL и отдельных аккаунтов нет.

### Что подтверждено кодом

| Проверка | Где | Результат |
|---|---|---|
| admin-код отдельный и не равен user-коду | `session.py:79-92` | `admin_available()` false при пустом/совпадающем коде; `verify_admin_code()` сравнивает UTF-8 bytes через `hmac.compare_digest` |
| роль подписана в токене, не берётся из body без кода | `session.py:145-188`; `router.py:174-205` | `v2.<workspace>.<role>...`; `v1` принимается только как `user`; `role` в body валидируется как `user`/`admin`, иначе 400 |
| anon → 401, user → 403 на admin-маршрутах | `session.py:237-243` | `require_admin`: нет сессии — 401, роль не admin — 403 |
| user не меняет draft/schedules | `router.py:323`, `339`, `649`, `655`, `676` | `create_workflow`, `PUT draft`, `GET/POST/PATCH schedules` вызывают `require_admin` |
| training/AI/logs закрыты | `router.py:255`, `715`, `722` | `require_admin` на training artifact, AI synthetic-test, webhook logs |
| overview только admin | `admin.py:106-110` | `require_admin` первой строкой |
| overview без DSN/keys/codes | `admin.py:32-104`, `112-168` | counts, latency, `error` только `type(exc).__name__`, migrations, provider/model/configured, vector status/version/active specs, upload limits, booleans. Нет `DATABASE_URL`, `CG_*`, base URL, API key |
| session response без секретов | `router.py:134-210` | `role`, `adminAvailable`, `workspaceId`, TTL; коды не возвращаются |
| local mode не даёт auto-admin | `main.py:141-150` | авто-сессия на safe GET всегда `issue_session()` → `user`; admin только через `POST /api/session` с admin-кодом |
| token/role core, live (stub FastAPI) | `session.py:145-188`, `237-243` | admin token → `role=admin`; подмена role/workspace/срока → `None`; v1 token → `user`; неверный role в issue → `ValueError`; `require_admin`: anon 401, user 403, admin OK |

### Остаточные риски admin-слоя

- **A-1 (среднее): нет server-side отзыва сессии.** Logout удаляет cookie, но подписанный admin-токен жив до `SESSION_TTL_SECONDS` (по умолчанию 12 ч). `docs/admin-access.md` это признаёт. Для реального admin до split нужен session store/rotation/revocation, иначе утёкшая cookie = admin до истечения TTL.
- **A-2 (низкое): `POST /api/webhook-test` не закрыт admin-гардом.** В local mode путь входит в `_LOCAL_SESSION_EXEMPT` (`main.py:31`, `125-150`), в public mode доступен любому залогиненному user. Список ограничен 200 записями и 64 KiB (`router.py:688-715`), но это log injection/спам, а не admin-действие. Логи читает только admin.
- **A-3 (информация): роль не изолирует данные.** user видит все runs/findings/map/report общего workspace — это заявленный контракт, не баг. Но до user/admin split нельзя трактовать `role` как tenant boundary: данные общие, роль закрывает только мутации и overview.
- **A-4 (информация): overview шире примера в docs.** Отдаёт `vector.embeddingModel/dimension/metric` и `migrations.latestAppliedAt`; это не секреты, но при ужесточении контракта их стоит либо оставить явно разрешёнными, либо убрать.
- **A-5 (низкое): local mode с эфемерным `CG_SESSION_SECRET`.** Это допустимо для локального demo (`main.py:57-61`), но при `CG_PUBLIC_DEMO=1` `public_config_error` требует секрет и код (`session.py:99-109`).
- **A-6 (низкое): Secure-cookie зависит от `CG_TRUST_PROXY`.** `set_session_cookie` ставит `secure=True` только если видит `https` в `request.url.scheme` или доверенном `x-forwarded-proto` (`session.py:246-257`). За TLS-прокси без `CG_TRUST_PROXY=1` cookie уйдёт без `Secure`; для admin-панели это надо закрыть на deploy.

`docs/admin-access.md` заявляет live HTTP-матрицу 401/403/200. Полный HTTP-прогон в этом аудите не выполнялся (нет `psycopg`, backend не поднимался). Ядро токенов/кодов и `require_admin` проверено live с заглушкой `fastapi`; маршрутные гарды проверены статически — расхождений с матрицей по перечисленным маршрутам не найдено.

## 6. Матрица сравнения

| Измерение | Где проверяется | Статус |
|---|---|---|
| parser version/hash | `SIGNATURE_KEYS` + SQL `static_signature` | работает; SQL probe `comparable` при разных signature отклонён |
| rules version/hash + per-rule `ruleVersion/ruleFamily` | `signatureDiff`, `beforeRules/afterRules` | работает |
| advisory version/hash | `SIGNATURE_KEYS` | работает |
| config version/scanConfigHash | `SIGNATURE_KEYS` | работает, но `configVersion` константа, hash покрывает не все env |
| domain policy | `SIGNATURE_KEYS`, `static_signature`, `guard_edge_policy` | SQL готов; приложение всегда `None`/`not_applicable` |
| удалённый файл | manifest diff в engine; `file_deleted` + SQL «file still exists» | работает; удаление не считается исправлением |
| потеря coverage | `completeOutcome`, `checkedScope`, `graphComplete`; SQL checked scope | работает; `resolved/introduced` без checked отклоняются |
| несовместимость AI/advisory | вне сравнения; AI validator только CVE/версии | не смешивается с фактами, но grounding ограничен |
| synthetic origin | reason добавляется, но `comparable` уже вычислен | дефект P2-2 |

## 7. Рекомендации перед разделением user/admin

1. P1-1 закрыт в `113bfeff`; осталось закрыть S-1/S-2/S-3 (content hash без artifact, TRUNCATE origins, marker 006) и добавить regression «тот же архив дважды» в прикладной путь.
2. `9083f04` закрыл PASS-разрыв; не поднимать `expectedScopesVerified` вручную. Осталось отдавать рядом с вердиктом `publication.status`/`coverage.status`/`expectedScopesVerified` (P1-3, P2-0).
3. Возвращать `publication` в `/security-map` и `/evidence`, не маскировать fallback под published (P1-3).
4. Закрыть runtime FK/immutability или перевести пользовательские чтения на `published_*` (P2-1).
5. Исправить порядок вычисления `comparable` (P2-2).
6. `9083f04` подключил canonical check к Policy Gate; осталось зеркалить три scan outcome (не только graph coverage) и не терять skipped/limits в team_b (P2-3).
7. Reconcile findings по run, реальные timestamp, executionStatus из run (P2-4).
8. Версионировать HMAC-ключ, подключить или явно убрать domain policy (P2-5).
9. Усилить masking и добавить проверку `redacted_snippet` на секреты (P3-1).
10. Скоупить runtime-чтения по `request_workspace` до появления разных workspace (P3-2).
11. Подключить `ai_explanations`/`RemediationStore` к API или убрать их из заявлений о продукте (P3-3).
12. До реального admin добавить server-side session store/revocation: logout сейчас не отзывает подписанный admin-токен до TTL (A-1).
13. Закрыть `POST /api/webhook-test` admin-гардом или явно описать его как публичный тестовый приёмник; не считать `role` tenant-изоляцией (A-2, A-3).
14. Запускать backend под least-privilege ролью без DDL/TRUNCATE и явно документировать, что DB owner/superuser может обойти guards; аудит это не покрывает.

## 8. Что не подтверждено

- Нет полного API e2e: в системном Python нет `psycopg`/`fastapi`, backend не поднимался; P1-1 проверен кодом + миграцией 006 на temp PostgreSQL, admin/roles — ядро live с заглушкой, HTTP-матрица 401/403/200 не прогонялась.
- Нет реального scanner run на Kotlin-проекте и реального AI/embedding provider; выводы о PASS/partial — по коду и SQL guard, не по живому прогону с секретами. Live-таблица `docs/verdict-consistency.md` независимо не воспроизводилась: executor требует `psycopg`/runtime.
- Не проверялись production RLS/роли, удаление артефактов по TTL, вебхуки наружу, производительность и конкурентные гонки под нагрузкой; live auth не прогонялся.
- SQL-инварианты проверены на синтетическом fixture; часть негативных сценариев (например, `introduced` при partial baseline) выведена из кода guard, а не воспроизведена отдельным run.
