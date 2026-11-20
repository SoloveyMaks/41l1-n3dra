# AI и optional vector runtime

Коротко: `backend/app/ai/service.py` вызывает настоящий OpenAI-compatible
`/chat/completions`, проверяет JSON и grounding, пишет provenance модели. Любая
ошибка провайдера, ключа или валидатора даёт детерминированный fallback и не
ломает отчёт. Exact pgvector retrieval — отдельный optional шаг с typed
`unavailable`; он не откатывает scan/report.

## Chat provider

Ключи только из env, в код/логи/provenance не попадают.

| Env | Значение |
| --- | --- |
| `AI_PROVIDER` | `reformboss` или `wikivibe`; пусто — выбрать по доступному ключу |
| `REFORMBOSS_API_KEY` | ключ ReformBoss, base URL `https://api.reformboss.com/v1` |
| `WIKIVIBE_GPT_KEY` | ключ WikiVibe GPT, base URL `https://api.wikivibe.dev/v1` |
| `AI_BASE_URL` | override base URL |
| `AI_MODEL` / `LLM_MODEL` | override модели; defaults: `deepseek-v4.1-flash-1017` / `deepseek-v4.1-flash` |
| `AI_TIMEOUT_SECONDS` | таймаут запроса, default 30 |
| `AI_MAX_TOKENS` | default 2048 (reasoning-моделям нужно ≥2048) |
| `AI_TEMPERATURE` | default 0 |
| `AI_JSON_MODE` | `1` только если шлюз реально поддерживает `response_format=json_object` |

Порядок выбора: явный `AI_PROVIDER` → `REFORMBOSS_API_KEY` → `WIKIVIBE_GPT_KEY`.
Нет ключа → `status=fallback`, `provenance.fallback_reason=provider_not_configured`.

## Что проверяется

1. HTTP-ответ провайдера разобран как JSON-объект (markdown-фенсы снимаются).
2. `validate_json_shape`: `summary`, непустые `items`, строковые
   `findingId/explanation/remediation`, `suggestedFixedVersion: string|null`.
3. `AiResponseValidator.validate`: только реальные `findingId`, только известные
   CVE и `suggestedFixedVersion` из эталонного справочника; не-SCA версии
   отбрасываются.
4. `ExplainResult.provenance` фиксирует provider/model/endpoint/request id,
   latency, usage, `finish_reason`, SHA-256 контента и причину fallback.
5. Запрос к модели собирается из `redactedEvidence` и дополнительно маскируется
   (`sk-...`, Bearer, private keys, `password=...`).

## Optional exact retrieval

SQL-контракт: `db/optional/003_exact_vector.up.sql` (`exact_similar_fixes`).
Код: `backend/app/ai/vector_runtime.py`. Включён только при
`VECTOR_RETRIEVAL_ENABLED=true`; иначе `retrieval.status=disabled`.

Нужны: `VECTOR_RETRIEVAL_DSN` (или `DATABASE_URL`), `VECTOR_WORKSPACE_ID`,
`VECTOR_EMBEDDING_SPEC_ID`, `VECTOR_EMBEDDING_BASE_URL`,
`VECTOR_EMBEDDING_API_KEY`. Драйвер PostgreSQL (`psycopg` или `psycopg2`) —
optional dependency; без него typed `unavailable`.

Chat-провайдер не даёт подтверждённой embedding-модели в `/v1/models`, поэтому
endpoint эмбеддингов задаётся отдельно через `VECTOR_EMBEDDING_*`.

Зафиксированный embedding spec по умолчанию (менять только вместе с active row
`embedding_specs` и переиндексацией):

| Параметр | Значение |
| --- | --- |
| model | `text-embedding-3-small` |
| version | `2024-02` |
| dimension | `1536` |
| metric | `cosine` |
| query preparation | `1` |
| document preparation | `1` |
| redaction | `1` |

Фактические значения также в `.env.example`; модель и размерность не
подставляются молча в БД. `VECTOR_RULE_FAMILY_MAP` переводит `ruleId` в
`rule_family`; без маппинга используется сам `ruleId`. Значения language,
framework и rule family должны совпадать с `remediation_examples`, иначе exact
retrieval честно вернёт пустую выдачу. Запросы фильтруются по workspace, language, framework,
rule family и spec. Меньше distance — лучше; distance не выдаётся за
человеческую оценку применимости.

Статусы: `disabled`, `unavailable`, `ready`, `ok`, `partial`.
Ошибка retrieval никогда не меняет findings, вердикт или отчёт.