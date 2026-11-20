# AI и optional vector runtime

Коротко: `backend/app/ai/service.py` вызывает ReformBoss через
OpenAI-compatible `/chat/completions`, проверяет JSON и grounding, пишет
provenance модели. Любая ошибка провайдера, ключа или валидатора даёт
детерминированный fallback и не ломает отчёт. Exact pgvector retrieval —
отдельный optional шаг с typed `unavailable`; он не откатывает scan/report.

## Chat provider

Провайдер один — ReformBoss. Другие ключи и провайдеры не используются.

| Env | Значение |
| --- | --- |
| `REFORMBOSS_API_KEY` | ключ ReformBoss из окружения сервера |
| `AI_MODEL` / `LLM_MODEL` | override модели; default `deepseek-v4.1-flash-1017` |
| `AI_BASE_URL` | override base URL; default `https://api.reformboss.com/v1` |
| `AI_TIMEOUT_SECONDS` | таймаут запроса, default 30 |
| `AI_MAX_TOKENS` | default 2048 (reasoning-моделям нужно ≥2048) |
| `AI_TEMPERATURE` | default 0 |
| `AI_JSON_MODE` | `1` только если шлюз реально поддерживает `response_format=json_object` |

Если `REFORMBOSS_API_KEY` не задан в процессе, ключ читается внутри процесса
из `~/.omp/agent/.env`. В argv, логи, provenance и отчёт ключ не попадает. Нет
ключа → `status=fallback`, `provenance.fallback_reason=provider_not_configured`.

## Что проверяется

1. HTTP-ответ провайдера разобран как JSON-объект (markdown-фенсы снимаются).
2. `validate_json_shape`: `summary`, непустые `items`, строковые
   `findingId/explanation/remediation`, `suggestedFixedVersion: string|null`.
3. `AiResponseValidator.validate`: CVE допустимы только из `advisoryIds`
   конкретной находки; `suggestedFixedVersion` — только версия из advisory
   этой же находки. Не-SCA версии, чужие версии и CVE в `summary` вне
   `advisoryIds` находок отклоняются. Duplicate, missing и вымышленные
   `findingId` не дают статус `succeeded`.
4. `ExplainResult.provenance` фиксирует provider/model/endpoint/request id,
   latency, usage, `finish_reason`, SHA-256 контента и причину fallback.
5. Запрос собирается из `redactedEvidence` и дополнительно маскируется
   (`sk-...`, Bearer, private keys, `password=...`). Ответ тоже маскируется.

## Optional exact retrieval

SQL-контракт: `db/optional/003_exact_vector.up.sql` (`exact_similar_fixes`).
Код: `backend/app/ai/vector_runtime.py`. Включён только при
`VECTOR_RETRIEVAL_ENABLED=true`; иначе `retrieval.status=disabled`.

Нужны явные значения: `VECTOR_RETRIEVAL_DSN` (или `DATABASE_URL`),
`VECTOR_WORKSPACE_ID`, `VECTOR_EMBEDDING_SPEC_ID`, `VECTOR_EMBEDDING_MODEL`,
`VECTOR_EMBEDDING_VERSION`, `VECTOR_EMBEDDING_DIMENSION`,
`VECTOR_EMBEDDING_METRIC`, `VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION`,
`VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION`,
`VECTOR_EMBEDDING_REDACTION_VERSION`, `VECTOR_EMBEDDING_BASE_URL`,
`VECTOR_EMBEDDING_API_KEY`. Драйвер PostgreSQL (`psycopg` или `psycopg2`) —
optional dependency.

В коде нет default embedding model/version/dimension/metric: размерность и
модель нельзя выдумывать. Значения заполняются только после реального
`POST /embeddings` и сверки с active row `embedding_specs`. Проверка каталога
ReformBoss `/v1/models` не показывает embedding-модель, поэтому рабочие
embeddings сейчас не заявляются, а retrieval остаётся `unavailable`, пока
интегратор/D не даст подтверждённый endpoint и spec. Реальный POST
`/v1/embeddings` с `text-embedding-3-small` вернул 403
`token_model_not_allowed`; размерность из ответа не получена и не выдумывается.

`VECTOR_RULE_FAMILY_MAP` переводит `ruleId` в `rule_family`; без маппинга
используется сам `ruleId`. Значения language, framework и rule family должны
совпадать с `remediation_examples`, иначе exact retrieval честно вернёт пустую
выдачу. Запросы фильтруются по workspace, language, framework, rule family и
spec. Меньше distance — лучше; distance не выдаётся за человеческую оценку
применимости.

Статусы: `disabled`, `unavailable`, `ready`, `ok`, `partial`. Ошибка retrieval
никогда не меняет findings, вердикт или отчёт.