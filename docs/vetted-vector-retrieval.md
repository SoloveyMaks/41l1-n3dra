# Vetted vector retrieval — exact pgvector path

Статус: код и SQL-контракт готовы, **реальные embeddings недоступны**, реальных
`vetted` примеров **0**. Поэтому честный статус runtime — `unavailable`; ничего
из этого не доказывает, что retrieval улучшает анализ.

Что действительно проверено живьём:

- PostgreSQL 16.16 + pgvector 0.8.7, одноразовый локальный контейнер;
- пять synthetic demo/fixture примеров в отдельных таблицах, `draft`-only;
- exact cosine search по 16-мерным synthetic-векторам: self-query distance `0`;
- workspace / language / framework / rule-family / status фильтры;
- отказ при несовпадении model/version/metric/preparation versions и размерности;
- отказ demo-таблицы принять `vetted`;
- запрос без authorized workspace scope и с чужим scope → `unavailable` без SQL;
- прямой synthetic `vetted`-INSERT в обход writer больше не попадает в выдачу;
- `VectorRuntime` + `AiService` в demo-режиме: retrieval `ok`, чат-провайдер в
  fallback, полный ряд с `status=draft` сохраняется в `ExplainResult`;
- real-режим без embeddings config: typed `unavailable`, fallback работает.

Не проверено и не заявляется: реальный embedding provider/model/dimension, выдача
по реальным `vetted` примерам (их нет), польза поиска, применимость найденной
правки, ANN-индекс.

## Что где лежит

| Часть | Файл |
| --- | --- |
| Базовый optional-контракт | `db/optional/003_exact_vector.up.sql` |
| Exact retrieval: один spec, все фильтры, полный ряд | `db/optional/014_exact_vector_contract.up.sql` |
| Demo/fixture-хранилище и функция | `db/optional/015_vector_demo_fixture.up.sql` |
| Synthetic fixture (5 примеров) | `db/optional/fixtures/vector_demo_examples.json` |
| Backend runtime | `backend/app/ai/vector_runtime.py` |
| Полный typed row | `backend/app/ai/vector_result.py` |
| Synthetic embedder + seed/verify CLI | `backend/app/ai/vector_demo.py` |
| DB-free harness | `packages/vector/` |

`db/optional/*` применяется только при `DB_ENABLE_VECTOR=1` и доступном pgvector.
Мигратор берёт все `*.up.sql` из `db/optional`, поэтому 014/015 подхватываются
автоматически после 003; `backend/app/storage/migrate.py` не менялся. Нумерация
не пересекается с `db/migrations/013_custom_checks.up.sql`. Ledger **один** —
`public.schema_migrations` с ключом по имени файла; optional-прогон отличается
advisory lock (`LOCK_NAME:optional`) и списком файлов, а не отдельной таблицей.
Обе миграции идемпотентны (`CREATE OR REPLACE`, `IF NOT EXISTS`), старую
7-аргументную `exact_similar_fixes` заменяет 15-аргументная.

## Контракт exact retrieval

`data_contract.exact_similar_fixes(w, spec, q, provider, model, model_version,
metric, query_prep, document_prep, redaction, language, framework, rule_family,
status, limit)` возвращает:

`example_id, source_ref, redacted_before, redacted_fix, rule_version,
verification_basis, limitations, example_status, distance, metric`.

Правила:

- ровно один `active` spec на workspace; provider/model/model_version/metric/
  query_preparation/document_preparation/redaction должны совпасть с переданными;
- dimension запроса обязан совпасть с `embedding_specs.dimension`;
- cosine/inner_product отвергают нулевой query-вектор;
- `language`, `framework`, `rule_family`, `status`, `limit` — обязательны;
  `status` только `vetted` или `draft`, `limit` 1..20;
- `distance` — exact distance без ANN-индекса: cosine `1-cos`, l2 — евклидово,
  inner_product — отрицательное скалярное произведение; меньше — ближе;
- ряд не возвращается без `verification_basis` и `limitations`;
- `vetted`-ряд дополнительно обязан иметь stored provenance: fixed run/snapshot,
  опубликованные source/fixed bundles, `origin='scanner'` и
  `expected_scopes_verified` в обоих envelope, совпадающую `static_signature`,
  опубликованный `comparable` comparison с `status='resolved'` для source finding,
  непустые `reviewed_by`/`reviewed_at` и `cardinality(limitations) > 0`;
- ошибка retrieval — typed `unavailable`, а не пустая «успешная» выдача.

Runtime по умолчанию передаёт `status=vetted`; `draft` возможен только вместе с
`VECTOR_RETRIEVAL_DEMO_FIXTURE=1` и demo-функцией, чтобы черновик нельзя было
случайно выдать за проверенную правку.

## Привязка к workspace запроса

Retrieval не авторизует сам себя. Runtime читает `current_workspace_scope()`
(request/background ContextVar, который ставит middleware или scheduler) и
требует, чтобы `VECTOR_WORKSPACE_ID` совпал с этим scope по UUID. Тогда:

- нет scope (анонимный вызов, прямой вызов вне request/scheduler) — `unavailable`;
- scope другого workspace — `unavailable`, SQL не вызывается;
- process-wide `VECTOR_WORKSPACE_ID` — только конфигурация корпуса, не
  доказательство владения сессией; ambient env не подставляется вместо scope.

`exact_similar_fixes` фильтрует по переданному `w`, но сам по себе не является
проверкой владения. Общий корпус для нескольких workspace требует отдельной
явной authorization-политики, которой в этой ветке нет.

## Env

Real-режим (`VECTOR_RETRIEVAL_ENABLED=1`, `status=vetted`):

`VECTOR_RETRIEVAL_DSN` или `DATABASE_URL`, `VECTOR_WORKSPACE_ID`,
`VECTOR_EMBEDDING_SPEC_ID`, `VECTOR_EMBEDDING_PROVIDER`,
`VECTOR_EMBEDDING_MODEL`, `VECTOR_EMBEDDING_VERSION`,
`VECTOR_EMBEDDING_DIMENSION`, `VECTOR_EMBEDDING_METRIC`,
`VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION`,
`VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION`,
`VECTOR_EMBEDDING_REDACTION_VERSION`,
`VECTOR_EMBEDDING_BASE_URL`, `VECTOR_EMBEDDING_API_KEY`.

Demo-режим — только для проверки механики; сеть и ключ не нужны:

```text
VECTOR_RETRIEVAL_ENABLED=1
VECTOR_RETRIEVAL_DEMO_FIXTURE=1
VECTOR_RETRIEVAL_STATUS=draft
VECTOR_WORKSPACE_ID=887e5059-1fbe-5571-a037-601a3cd67744
VECTOR_EMBEDDING_SPEC_ID=84983e62-ec64-5d00-8636-688ab339da82
VECTOR_EMBEDDING_PROVIDER=demo-fixture
VECTOR_EMBEDDING_MODEL=synthetic-hashed-tokens
VECTOR_EMBEDDING_VERSION=demo/1
VECTOR_EMBEDDING_DIMENSION=16
VECTOR_EMBEDDING_METRIC=cosine
VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION=demo/query/1
VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION=demo/document/1
VECTOR_EMBEDDING_REDACTION_VERSION=demo/redaction/1
VECTOR_RULE_FAMILY_MAP={"KOTLIN-SQL-INJECTION":"sql-injection"}
```

Без `VECTOR_RULE_FAMILY_MAP` `ruleId` уходит в SQL как есть, и пример с
`rule_family=sql-injection` не найдётся: это честный пустой результат, а не
ошибка. Для остальных demo-семейств маппинг добавляется так же
(`tls-verification`, `credentialed-cors`, `route-authorization`,
`object-authorization`). Любое расхождение с demo-spec даёт `unavailable` с
перечнем полей. DSN читается
из окружения или stdin (`--dsn-stdin`), не из argv; CLI не печатает DSN и ключи.

## Demo/fixture

`db/optional/015` создаёт отдельные `vector_demo_specs` и `vector_demo_examples`.
Это не `remediation_examples` и не `example_embeddings`: demo-строки не могут
попасть в реальную выдачу. Схема запрещает:

- `status <> 'draft'`;
- `source_ref` без префикса `demo/fixture:`;
- `rule_version` без префикса `demo/`.

Fixture — пять примеров (`sql-injection`, `tls-verification`, `credentialed-cors`,
`route-authorization`, `object-authorization`). Векторы — 16-мерный
token-hash, cosine, генерируются `backend.app.ai.vector_demo.embed_text`; это
лексический bucket sketch, не embedding-модель. Векторы проверяются на
воспроизводимость при загрузке fixture.

```bash
# в контейнере/окружении с psycopg; DSN только в env или stdin
python -m backend.app.ai.vector_demo --seed --verify
python -m backend.app.ai.vector_demo --verify --dsn-stdin

# без БД: точная математика и фильтры на том же fixture
node packages/vector/verify-cli.mjs
```

## Shared DTO

`SimilarFix` в `backend/app/contracts/schemas.py` теперь несёт полный ряд:
`redactedBefore`, `ruleVersion` и **обязательный** `status: Literal["draft",
"vetted"]`. Поля не имеют vetted-дефолта: строка без статуса или с неизвестным
статусом отбрасывается как `unavailable`, а не превращается в проверенную.
`VectorRuntime.retrieve_for_findings()` и `AiService` передают эти поля в
`ExplainResult.similar_fixes` без второго запроса; `retrieve_examples_for_findings()`
остаётся полным `VectorExample` для новых узлов.

JSON-схема/TS-контракт `docs/data-contracts/*` принадлежат владельцу контракта:
в этой ветке они не менялись, сниппет для них — в
[vetted-vector-similar-fix-snippet.md](vetted-vector-similar-fix-snippet.md).
Пока контрактные файлы не обновлены, их `SimilarFix` не описывает Python DTO;
это не повод ослаблять runtime-статус.

## Admission boundary

Доверенный путь записи `vetted` — `RemediationStore.save()`: он требует
published scanner source/fixed bundles, `expected_scopes_verified`, совпадающую
подпись, checked coverage, исчезнувший fingerprint и опубликованный
`comparison_item.status='resolved'`, плюс review-поля и limitations. `014`
повторяет те проверки, которые можно вывести из уже сохранённых строк, на
чтении (см. контракт выше), чтобы прямой INSERT не появился в выдаче только
потому, что у него `status='vetted'` и непустой `verification_basis`.

Это defense in depth, а не замена admission. Оставшиеся пределы, которые нельзя
закрыть в этой ветке без решения владельца БД:

- нет RLS и production grants: кто имеет table-owner/write на
  `data_contract.*`, может подделать bundles/envelopes/comparison и пройти
  проверки; запись обязана идти только через writer под ограниченной ролью;
- runtime-предикат проверяет stored fields, но не восстанавливает человеческое
  review или применимость правки;
- никакие fake/synthetic строки не публикуются как реальный `vetted`-корпус.

## Blocker

`POST /v1/embeddings` у ReformBoss вернул 403 `token_model_not_allowed`;
подтверждённого embedding provider/model/version/dimension нет,
`embedding_specs` остаётся пустым/pending, реальных `vetted` примеров 0. Поэтому:

- `VECTOR_RETRIEVAL_ENABLED=0` по умолчанию, статус `disabled`;
- при включении без полного spec — `unavailable` с перечнем полей;
- без authorized workspace scope — `unavailable` до любого SQL;
- demo-режим явно помечен `demo fixture (synthetic vectors, draft-only, not
  vetted)`, его нельзя включать как production retrieval;
- ни один synthetic пример не публикуется как `vetted`, и AI-совет статус
  `vetted` не получает.

## Проверка (disposable PostgreSQL, 21.11.2026)

```text
PostgreSQL 16.16, pgvector 0.8.7, TCP только 127.0.0.1 на одноразовый контейнер.
backend.app.storage.migrate + DB_ENABLE_VECTOR=1: применил 001,002,004..012, затем
        optional 003/014/015; повторный запуск ничего не применил.
Ledger: одна таблица public.schema_migrations, 14 строк; exact_similar_fixes —
        ровно одна 15-аргументная функция, старой 7-аргументной нет.
Demo seed+verify: 5/5 self-query distance = 0, status = draft, limitations на месте;
        vetted-строк в реальных таблицах 0.
Direct synthetic vetted INSERT в обход RemediationStore (published manual_fixture,
        review-поля, пустые limitations): store отверг manual_fixture;
        exact_similar_fixes вернул 0 строк, хотя old 014 вернул бы строку.
        Позитивный writer-admitted vetted-ряд намеренно не фабриковался.
VectorRuntime без scope: unavailable, SQL не вызывался; чужой scope: unavailable,
        SQL не вызывался; demo scope: status=ok, полный ряд.
AiService fallback + demo: ExplainResult.similar_fixes содержит redactedBefore,
        ruleVersion, status=draft; retrieval reason помечен draft-only.
Другой вызов exact_similar_fixes без active spec: typed "inactive or unknown
        embedding spec", не пустая успешная выдача.
DB-free harness: cosine для scaled-вектора -2.220446e-16, pgvector <=> дал 0;
        чужой workspace и смена регистра model → unavailable; status обязателен.
Сеть/провайдер: запросов не было; embedding base URL/key не использовались.
```

Важно про checksum: 014 в этой доработке изменён относительно кандидата
`5c18403`. Одноразовая БД для проверки создана с нуля. Если какой-то
нерелизный стенд уже применил старые байты 014, его ledger отвергнет новый
checksum — стенд нужно пересоздать или вручную переприменить функцию; для
production-БД этот optional-путь ещё не включался.

Значения `distance` проверены на exact-математике, а не на «похожести»: demo
self-query `0`, DB-free harness — `|d| < 1e-15` (машинный zero). Это не оценка
применимости и не доказательство пользы retrieval.