# Vetted vector retrieval — exact pgvector path

Статус: код и SQL-контракт готовы, **реальные embeddings недоступны**, реальных
`vetted` примеров **0**. Поэтому честный статус runtime — `unavailable`; ничего
из этого не доказывает, что retrieval улучшает анализ.

Что действительно проверено живьём:

- PostgreSQL 17.11 + pgvector 0.8.2, одноразовая БД;
- пять synthetic demo/fixture примеров в отдельных таблицах, `draft`-only;
- exact cosine search по 16-мерным synthetic-векторам: self-query distance `0`;
- workspace / language / framework / rule-family / status фильтры;
- отказ при несовпадении model/version/metric/preparation versions и размерности;
- отказ demo-таблицы принять `vetted`;
- `VectorRuntime` + `AiService` в demo-режиме: retrieval `ok`, чат-провайдер в
  fallback, анализ не падает;
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
не пересекается с `db/migrations/013_custom_checks.up.sql`: контрактные миграции
и optional идут разными ledger'ами в разных каталогах. Обе миграции идемпотентны
(`CREATE OR REPLACE`, `IF NOT EXISTS`), старую 7-аргументную
`exact_similar_fixes` заменяет 15-аргументная.

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
- ошибка retrieval — typed `unavailable`, а не пустая «успешная» выдача.

Runtime по умолчанию передаёт `status=vetted`; `draft` возможен только вместе с
`VECTOR_RETRIEVAL_DEMO_FIXTURE=1` и demo-функцией, чтобы черновик нельзя было
случайно выдать за проверенную правку.

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

## Ограничение shared DTO

`SimilarFix` в `backend/app/contracts/schemas.py` — общий контракт C; в этой
ветке он не менялся. `retrieve_examples_for_findings()` уже отдаёт полный
`VectorExample` (`redactedBefore`, `ruleVersion`, `status`). Legacy
`retrieve_for_findings()` передаёт эти поля в `SimilarFix` как kwargs; текущая
схема их игнорирует. Готовый root-сниппет для владельца контракта лежит
отдельным файлом: [vetted-vector-similar-fix-snippet.md](vetted-vector-similar-fix-snippet.md).
После него `retrieve_for_findings()` начнёт сохранять поля без второго запроса.
Если контракт расширять не хотят, использовать `retrieve_examples_for_findings()`
в новом узле/эндпоинте, не меняя `SimilarFix`.

## Blocker

`POST /v1/embeddings` у ReformBoss вернул 403 `token_model_not_allowed`;
подтверждённого embedding provider/model/version/dimension нет,
`embedding_specs` остаётся пустым/pending, реальных `vetted` примеров 0. Поэтому:

- `VECTOR_RETRIEVAL_ENABLED=0` по умолчанию, статус `disabled`;
- при включении без полного spec — `unavailable` с перечнем полей;
- demo-режим явно помечен `demo fixture (synthetic vectors, draft-only, not
  vetted)`, его нельзя включать как production retrieval;
- ни один synthetic пример не публикуется как `vetted`, и AI-совет статус
  `vetted` не получает.

## Проверка (disposable PostgreSQL, 21.11.2026)

```text
PostgreSQL 17.11, pgvector 0.8.2, Unix/TCP только внутри одноразового контейнера.
Применены: 001, 002, 003, 014, 015 (и повторно 014/015 — идемпотентно).
С нуля через backend.app.storage.migrate + DB_ENABLE_VECTOR=1: ledger применил
        003/014/015 после контрактных 001/002/004..012.
exact_demo_fixes: 5/5 self-query distance = 0, status = draft, limitations на месте.
Фильтры: framework jvm → 0 при spring-boot; чужой rule-family → 0.
Отказы demo: status=vetted; source без demo/fixture:; vetted-вставка; model/
        dimension/limit=0/zero-vector mismatch.
Отказы 014 (active spec, строк example_embeddings нет): второй active spec;
        status=retired; metric mismatch; dimension mismatch; zero vector;
        пустой language filter; валидный вызов → 0 строк без ошибки.
VectorRuntime demo (с VECTOR_RULE_FAMILY_MAP): status=ok, 1 demo fix, поля source/
        redactedBefore/redactedFix/ruleVersion/verificationBasis/limitations/
        status/distance/metric; без map — честные 0 строк.
AiService без REFORMBOSS_API_KEY: status=fallback, retrieval=ok рядом с demo.
Real-режим без base URL/key: status=unavailable, fallback работает, findings не меняются.
```

Значения `distance` проверены на exact-математике, а не на «похожести»: demo
self-query `0`, DB-free harness — `|d| < 1e-15`. Это не оценка применимости и не
доказательство пользы retrieval.