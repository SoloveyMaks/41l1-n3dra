# Аудит целостности данных и честности результатов

Ветка: `SoloveyMaks/data-integrity-audit`, run `run_bbf876ca1e6c`.
Локальная `dev` (`9862e19`) уже является предком HEAD (`git merge-base --is-ancestor 9862e19 HEAD`), отдельный merge-коммит не создавался.

## 1. Итог

| ID | Влияние | Дефект | Где |
|---|---|---|---|
| P1-1 | высокое | Повторная загрузка того же архива даёт тот же `snapshotId`, но новый `artifactId`; canonical publish падает с конфликтом и остаётся `published=false` | `backend/app/storage/canonical.py:1125`, `1129`; `backend/app/codefacts/identity.py:98`, `113`; `backend/app/artifacts/intake.py:118`, `303` |
| P1-2 | высокое | Опубликованный canonical bundle всегда `partial`: backend-сканеры захардкожены как partial, graph coverage всегда содержит skipped scopes, а `expectedScopesVerified=false` понижает и team_b. SQL `pass`, сравнения и `vetted` недостижимы, при этом runtime может показать PASS | `backend/app/codefacts/canonical.py:674`, `493-506`, `620-706`; `backend/app/codefacts/publish.py:92-102`; `db/migrations/002_integrity_publish.up.sql:71-77` |
| P1-3 | высокое | `/security-map` и `/evidence` не различают published canonical envelope и локальный mutable fallback; `publication.reason` теряется | `backend/app/api/router.py:421-465`, `451-458`, `477-500` |
| P2-1 | среднее | Runtime-слой не связан FK с artifact/revision и не проверяет evidence/coverage: принимает `verdict=pass` с несуществующими artifact/revision, findings и report можно переписать после завершения | `backend/app/storage/runtime_schema.sql:52-60`, `90-111`, `113-126`; проверено живыми SQL probes |
| P2-2 | среднее | Comparison engine вычисляет `comparable` до добавления reason про `contract_scenario`; результат — `comparable=true` с причиной «синтетика» | `packages/comparison/index.mjs:376-377`; подтверждено live node probe |
| P2-3 | среднее | Policy Gate может выдать PASS по неполному покрытию: `strictCoverageRequired=false` отключает проверку gaps, а `Coverage` не содержит scopes/skipped/limits; team_b теряет эти поля | `backend/app/engine/executor.py:528`, `560-575`; `backend/app/integrations/team_b.py:188-199` |
| P2-4 | среднее | `save_findings` не удаляет устаревшие findings; `started_at` node_run равен времени завершения; `official.json` всегда пишет `executionStatus: succeeded` | `backend/app/storage/database.py:607-647`, `677-702`; `backend/app/engine/executor.py:925-936`; `backend/app/reports/builder.py:168` |
| P2-5 | среднее | Domain policy никогда не попадает в canonical signature/policyInputs; `hmacKeyVersion` захардкожен, ротация ключа не видна сравнению | `backend/app/codefacts/canonical.py:731-735`, `620-706` |
| P3-1 | низкое | Маскирование source best-effort: JWT маскируется частично, короткие значения не маскируются; `redacted_snippet` в БД не проверяется на содержание | `backend/app/codefacts/source.py:66-88`; проверено pure-функцией |
| P3-2 | низкое (до split) | Runtime-таблицы читаются из одного env-workspace, session workspace учитывается только для canonical/local файлов | `backend/app/api/session.py:185-187`; `backend/app/storage/database.py:292-296` |
| P3-3 | низкое | `raw_secrets_checked=True` — константа; `ai_explanations` и `remediation_examples` нигде не пишутся приложением | `backend/app/ai/service.py:62`, `227`, `385`; `backend/app/contracts/schemas.py:240` |

Полного «safe/fixed» нет: часть инвариантов SQL сильна, но прикладной слой обходит их или не публикует факты.

## 2. Методика и границы

Проверки выполнены в отдельном временном PostgreSQL-контейнере (`pgvector/pgvector:pg16`, tmpfs, порт только `127.0.0.1`). Production и общая БД не трогались, секреты не читались и не печатались. В контейнер применены:

```text
db/migrations/001_core.up.sql
db/migrations/002_integrity_publish.up.sql
db/migrations/004_bundle_envelopes.up.sql
db/migrations/005_canonical_envelope.up.sql
db/optional/003_exact_vector.up.sql
backend/app/storage/runtime_schema.sql
backend/app/storage/runtime_guards.sql
```

SQL probes выполнялись с `ON_ERROR_STOP=1`, каждый негативный probe — в `BEGIN … ROLLBACK`. Живые проверки comparison engine — `node v22.23.3` на `docs/data-contracts/illustrative-bundle.json`. Чистые функции identity/masking проверены Python-вызовом.

Не проверено: полный backend e2e (в системном Python нет `psycopg`), реальные сканеры на чужом коде, сетевой AI/embedding provider, production RLS/роли, поведение под нагрузкой. Выводы P1-1/P1-2 опираются на код и воспроизведение чистых функций/движка, а не на полный API-прогон.

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

Эти инварианты закрывают ссылки node/edge на evidence, диапазоны evidence, неизменяемость опубликованных фактов, подделку `resolved/file_deleted` и публикацию без трёх family outcomes.

## 4. Дефекты по влиянию

### P1-1. Повторный publish одинакового snapshot невозможен

`snapshotId` — `uuid5` от хэша manifest (`identity.py:98`), а `artifactId` — `uuid5` от случайного runtime id (`identity.py:113`; id создаётся в `intake.py:118`, `303`). Повторная загрузка того же архива даёт тот же `snapshotId` и новый `artifactId`. `CanonicalPublisher._ensure_snapshot` находит существующий sealed snapshot и сравнивает `artifact_id`/`artifact_hash`:

```python
# backend/app/storage/canonical.py:1119-1129
if str(row["artifact_id"]) != artifact_id or row["artifact_hash"] != identity["artifactHash"]:
    raise CanonicalConflictError("snapshot: exists with a different artifact identity")
```

`publish_run_artifact` ловит исключение и возвращает `{"published": False, "reason": "publish failed: ..."}` (`publish.py:153-154`). Следствия:

- второй прогон неизменённого проекта не имеет canonical bundle;
- comparison отдаёт `NOT_PUBLISHED` (`api/comparison.py:62-69`);
- `security-map` живёт на локальном fallback, при этом API не сообщает, что публикации нет (см. P1-3);
- `vetted` remediation и embeddings для такого run недостижимы.

Проверено pure-функциями: одинаковый manifest hash → одинаковый `snapshot_id`; два случайных artifact id → разные `artifact_scope_id`. Полный API-прогон не выполнялся (нет `psycopg`), но конфликт — прямое следствие сравнения в коде.

Направление фикса: сделать artifact identity content-addressed (по archive hash) либо разрешить переиздание snapshot с тем же manifest и новым artifact, сохраняя immutable привязку явно; добавить regression на «загрузить тот же zip дважды».

### P1-2. Canonical всегда partial, runtime может показать PASS

В `_build_analysis_bundle` статус каждого сканера захардкожен:

```python
# canonical.py:674
"status": "partial" if visited > 0 else "unsupported",
```

`_build_coverage` всегда кладёт в `skippedScopes` три значения и требует `not skipped` для `complete` (`canonical.py:495-517`), поэтому graph coverage тоже всегда `partial`. Плюс `publish.py:92-102` всегда ставит `origin="scanner"`, `expectedScopesVerified=False`, `domainPolicyApplicability="not_applicable"`. Для engine `team_b` bundle приходит из `analyzers.cg26.contract.build_bundle` и может содержать `complete`, но `_downgrade_unverified` (`canonical.py:691-704`) понижает его до `partial` из-за `expectedScopesVerified=false`.

SQL `guard_run` требует для `pass` три `complete` outcome и checked coverage по каждой family (`002_integrity_publish.up.sql:71-77`). Следовательно:

- published bundle с `security_verdict='pass'` из приложения невозможен;
- comparison engine получает `expectedScopesVerified=false` и всегда ставит `comparable=false` (live probe: `scanner+unverified` → `false`, reason «A/B не подтвердили полноту ожидаемых scopes»);
- `RemediationStore._require_vetted_evidence` требует `expected_scopes_verified=true`, значит `vetted` из этого пайплайна невозможен.

При этом runtime verdict считается независимо в `POLICY_GATE` (`executor.py:528-575`) по `FindingsBundle.coverage`, а UI читает runtime (`/api/runs/{id}`, `/report`). Поэтому сценарий «runtime PASS + canonical partial/не опубликован» достижим: canonical publish вернёт `published=false`, но пользователь увидит PASS. Это расхождение фактов и вердикта, а не только косметика.

Направление фикса: либо публиковать `complete` только при реально доказанных scopes и не показывать PASS без этого, либо явно хранить/показывать `publication.status` и `coverage.status` рядом с вердиктом и запретить PASS при partial canonical coverage. Решать вместе с владельцами A/B: docs/data-contracts/verification.md уже относит «scanner полнота expected scopes» к непроверенному.

### P1-3. Published и локальный fallback неразличимы в API

`get_security_map` сначала пробует canonical, затем молча отдаёт локальный run-файл:

```python
# api/router.py:451-458
payload = _load_published_map(run_id, workspace)
...
if not payload:
    payload = _load_run_artifact(run_id, workspace)
```

Локальный файл (`CodeFactsStore.save_run`) перезаписывается на каждом этапе и содержит `publication` с `published=false`, но ответ API собирается только из `identity/map/findings`; поле `publication` не возвращается. То же для `/evidence` (`router.py:477-500`). Клиент не может отличить immutable published bundle от mutable локальной карты; `map.bundleHash` — внутренний content hash, не SQL `bundleHash`.

Направление фикса: добавить в ответ `publication: {status, bundleHash, publishedAt, reason, expectedScopesVerified}` и не выдавать fallback за published.

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

`POLICY_GATE` проверяет `files_scanned`, `rules_executed`, наличие трёх сканеров, parser errors, unresolved deps и diagnostics. Но:

- `Coverage` (`contracts/schemas.py`) не содержит per-file/per-rule scopes, skipped scopes и limits;
- `team_b.py:188-199` преобразует canonical coverage в `Coverage`, теряя `skippedScopes`, `limitsReached` и статусы scopes;
- при `strictCoverageRequired=false` (`executor.py:528`, `569`) gaps игнорируются, и при отсутствии findings выставляется PASS с reason «Все проверки пройдены».

То есть PASS может быть выставлен по агрегату «сколько файлов просканировал сам сканер», без доказательства, что нужные path/rule проверены. Canonical coverage (которая как раз содержит scopes) в runtime-вердикте не участвует. Дефолтный workflow ставит `strictCoverageRequired=true` (`demo/default_workflow.py:53`), но draft API позволяет это изменить.

Направление фикса: передавать в Policy Gate canonical coverage/scopes; запретить PASS при partial/unverified coverage независимо от флага; для team_b не терять skipped/limits.

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

`request_workspace` берёт workspace из подписанной cookie (`session.py:185-187`), но `DatabaseRepository` инициализируется одним env-workspace (`database.py:292-296`), и все runtime-чтения (`/runs`, `/findings`, `/report`, `/schedules`) этот workspace игнорируют. Сейчас все сессии получают один и тот же default workspace, поэтому утечки между пользователями нет. После введения разных user/admin workspace чтения runtime нужно скоупить по `request_workspace`, иначе оба увидят одни и те же runs/reports.

### P3-3. AI и vetted memory

- `ExplainResult.raw_secrets_checked` всегда `True` (`service.py:62`, `227`, `385`; default в `schemas.py:240`), независимой проверки нет. Поле не выводится в `official.json`, но как контракт оно ложно.
- `data_contract.ai_explanations` и `RemediationStore` не используются API/executor: grep по `backend/app` не находит INSERT в `ai_explanations` и вызовов `RemediationStore` вне storage-модуля. AI-текст лежит в `runtime.reports` рядом с фактами (в отдельных полях), а «vetted vs draft» существует только как SQL/библиотека. Для split это значит: immutability AI-объяснений и vetted-примеров пока не является свойством работающего продукта.

## 5. Матрица сравнения

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

## 6. Рекомендации перед разделением user/admin

1. Починить identity artifact/snapshot (P1-1) и добавить regression «тот же архив дважды».
2. Определить один источник истины для вердикта: либо canonical `complete` с доказанными scopes, либо явный `partial` в API и запрет PASS без него (P1-2).
3. Возвращать `publication` в `/security-map` и `/evidence`, не маскировать fallback под published (P1-3).
4. Закрыть runtime FK/immutability или перевести пользовательские чтения на `published_*` (P2-1).
5. Исправить порядок вычисления `comparable` (P2-2).
6. Подключить canonical coverage/scopes к Policy Gate, не терять skipped/limits в team_b (P2-3).
7. Reconcile findings по run, реальные timestamp, executionStatus из run (P2-4).
8. Версионировать HMAC-ключ, подключить или явно убрать domain policy (P2-5).
9. Усилить masking и добавить проверку `redacted_snippet` на секреты (P3-1).
10. Скоупить runtime-чтения по `request_workspace` до появления разных workspace (P3-2).
11. Подключить `ai_explanations`/`RemediationStore` к API или убрать их из заявлений о продукте (P3-3).

## 7. Что не подтверждено

- Нет полного API e2e: в системном Python нет `psycopg`, backend не поднимался; P1-1 проверен кодом и чистыми функциями, не HTTP-запросом.
- Нет реального scanner run на Kotlin-проекте и реального AI/embedding provider; выводы о PASS/partial — по коду и SQL guard, не по живому прогону с секретами.
- Не проверялись production RLS/роли, удаление артефактов по TTL, вебхуки наружу, производительность и конкурентные гонки под нагрузкой.
- SQL-инварианты проверены на синтетическом fixture; часть негативных сценариев (например, `introduced` при partial baseline) выведена из кода guard, а не воспроизведена отдельным run.