# AI и optional vector runtime

Коротко: `backend/app/ai/service.py` вызывает выбранный OpenAI-compatible
провайдер через `/chat/completions`, проверяет JSON и grounding, пишет
provenance модели. Любая ошибка провайдера, ключа или валидатора даёт
детерминированный fallback и не ломает отчёт. Exact pgvector retrieval —
отдельный optional шаг с typed `unavailable`; он не откатывает scan/report.

## Chat provider

Общий resolver `backend/app/ai/provider.py` используют объяснение находок,
AI-скан, scenario builder и другие серверные AI-вызовы. По умолчанию
выбирается OpenCode Go, если задан Go credential; legacy ReformBoss остаётся
только для совместимости и не подхватывает Go-ключ.

| Env | Значение |
| --- | --- |
| `AI_PROVIDER` | `opencode-go` (default), `reformboss` (legacy), `openai-compatible` (local) |
| `OPENCODE_API_KEY` | ключ OpenCode Go |
| `AI_API_KEY` | общий override ключа; для Go/legacy имеет приоритет |
| `REFORMBOSS_API_KEY` | legacy-ключ ReformBoss |
| `AI_BASE_URL` | override base URL; для Go default `https://opencode.ai/inference/go/v1`, для legacy `https://api.reformboss.com/v1` |
| `AI_MODEL` / `LLM_MODEL` | override модели; Go default `deepseek-v4.1-flash`, legacy `deepseek-v4.1-flash-1017` |
| `AI_TIMEOUT_SECONDS` | таймаут запроса, default 30 |
| `AI_MAX_TOKENS` | верхний предел на батч, default 4096 |
| `AI_BATCH_SIZE` | findings на запрос, default 4 (допустимо 1..8) |
| `AI_TEMPERATURE` | default 0 |
| `AI_JSON_MODE` | `1` только если шлюз реально поддерживает `response_format=json_object` |

Правила выбора: явный `AI_PROVIDER=opencode-go` берёт только
`AI_API_KEY`/`OPENCODE_API_KEY` и Go-endpoint; legacy-ключ и legacy-endpoint
при этом не используются. Если `AI_BASE_URL` указывает на
`api.reformboss.com`, Go-провайдер заменяет его на Go endpoint; другие
local/proxy override сохраняются. Явный `AI_PROVIDER=reformboss` берёт
`AI_API_KEY`/`REFORMBOSS_API_KEY` и legacy-endpoint; Go-ключ сам по себе его
не включает. Без `AI_PROVIDER` сначала выбирается Go при наличии
`OPENCODE_API_KEY`, затем generic `AI_API_KEY` (Go, либо local при заданном
`AI_BASE_URL`), затем legacy. Для `openai-compatible` нужны `AI_API_KEY`,
`AI_BASE_URL` и явный `AI_MODEL`/`LLM_MODEL`; без модели конфиг не считается
настроенным, чтобы не угадывать чужую модель.

Legacy-совместимость: если `REFORMBOSS_API_KEY` нет в процессе, старое
значение всё ещё читается из `~/.omp/agent/.env`; Go и local ключи читаются
только из окружения. Продукт не открывает внешнюю credential DB. Ключ не
попадает в argv, логи, provenance или отчёт.

Go-запросы используют собственный `User-Agent` продукта
`NEDRA/0.1 (+https://nedra.soloveyka.bond)` и штатный
`x-opencode-session` — непрозрачный стабильный id логической AI-операции
(одинаковый для батчей/повторных попыток, без user identity и секретов).
Legacy и local запросы получают только продуктовый `User-Agent`.

Нет выбранного ключа → `status=fallback`,
`provenance.fallback_reason=provider_not_configured`.

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

## Bounded batching

Полный sample объясняется батчами по `AI_BATCH_SIZE` findings (default 4).
Бюджет ответа на батч: `max(2048, 1024 + 640 * findings)` токенов, но не выше
`AI_MAX_TOKENS`; безлимита нет. `finish_reason=length` считается ошибкой
батча, truncated JSON не парсится и не принимается.

Каждый батч валидируется отдельно. Если батч упал, для его findings
используется детерминированный fallback, а общий статус становится
`fallback`; ошибки и provenance сохраняются в `batch_provenance` и
`validation_errors` без скрытия. `succeeded` возможен только когда все батчи
прошли валидацию и все findings получили items. Если summary отклонён из-за
чужого CVE, в отчёт идёт безопасная сводка без этого CVE.

## Optional exact retrieval

SQL-контракт: `db/optional/003_exact_vector.up.sql` (базовые таблицы) и
`db/optional/014_exact_vector_contract.up.sql` (15-аргументная
`exact_similar_fixes`). Код: `backend/app/ai/vector_runtime.py`, полный typed row —
`backend/app/ai/vector_result.py`. Включён только при
`VECTOR_RETRIEVAL_ENABLED=true`; иначе `retrieval.status=disabled`.

Нужны явные значения: `VECTOR_RETRIEVAL_DSN` (или `DATABASE_URL`),
`VECTOR_WORKSPACE_ID`, `VECTOR_EMBEDDING_SPEC_ID`, `VECTOR_EMBEDDING_PROVIDER`,
`VECTOR_EMBEDDING_MODEL`, `VECTOR_EMBEDDING_VERSION`,
`VECTOR_EMBEDDING_DIMENSION`, `VECTOR_EMBEDDING_METRIC`,
`VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION`,
`VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION`,
`VECTOR_EMBEDDING_REDACTION_VERSION`, `VECTOR_EMBEDDING_BASE_URL`,
`VECTOR_EMBEDDING_API_KEY`; `VECTOR_RETRIEVAL_STATUS` по умолчанию `vetted`.
Драйвер PostgreSQL (`psycopg` или `psycopg2`) — optional dependency.

`VECTOR_WORKSPACE_ID` — только конфигурация корпуса. Retrieval запускается
внутри `current_workspace_scope()` и требует точного совпадения UUID с этим
scope; без scope или с чужим scope возвращается `unavailable` до SQL. Поэтому
прямой вызов `AiService` вне request/scheduler тоже не достаёт корпус.

В коде нет default embedding model/version/dimension/metric: размерность и
модель нельзя выдумывать. Значения заполняются только после реального
`POST /embeddings` и сверки с active row `embedding_specs`. Функция проверяет
точное совпадение provider/model/model_version/metric и версий подготовки;
несовпадение или другой dimension дают `unavailable`, а не «похожую» выдачу.
Каталог выбранного chat-провайдера не даёт подтверждённой embedding-модели,
поэтому рабочие embeddings сейчас не заявляются, а retrieval остаётся
`unavailable`, пока интегратор/D не даст подтверждённый endpoint и spec.
Исторический POST `/v1/embeddings` с `text-embedding-3-small` вернул 403
`token_model_not_allowed`; размерность из ответа не получена и не выдумывается.

Ряд exact retrieval содержит `source_ref`, `redacted_before`, `redacted_fix`,
`rule_version`, `verification_basis`, `limitations`, `example_status`, `distance`,
`metric`. Обязательные фильтры: workspace, language, framework, rule family,
status; `limit` 1..20. `VECTOR_RULE_FAMILY_MAP` переводит `ruleId` в
`rule_family`. Меньше distance — лучше; distance не выдаётся за человеческую
оценку применимости. `vetted`-ряд дополнительно проходит provenance-пол в
`014` (published scanner bundles, verified scopes, совпадающая подпись,
resolved published comparison, review-поля, непустые limitations); это
defense in depth, а не замена `RemediationStore.save()` или DB-грантов.

`db/optional/015_vector_demo_fixture.up.sql` и
`db/optional/fixtures/vector_demo_examples.json` — отдельный demo/fixture:
5 draft-only примеров, 16-мерные synthetic token-hash векторы. Включается явно
через `VECTOR_RETRIEVAL_DEMO_FIXTURE=1` и `VECTOR_RETRIEVAL_STATUS=draft`, не
ходит в сеть и не требует ключа. Demo-таблицы не связаны с
`remediation_examples`, схема запрещает `vetted`, а статус ответа всегда
помечен как demo/fixture. Это проверка storage+exact-механики, не выбор модели и
не доказательство пользы поиска. Подробности и root-сниппет для полей
`SimilarFix` — в [vetted-vector-retrieval.md](vetted-vector-retrieval.md).

Статусы: `disabled`, `unavailable`, `ready`, `ok`, `partial`. Ошибка retrieval
никогда не меняет findings, вердикт или отчёт.

## Локальный индекс элементов `rag/`

Пока pgvector-путь `unavailable`, переиспользование элементов доступно
офлайн: `rag/` индексирует `AnalysisBundle` и примеры правок в файловый
append-only журнал (`events.jsonl`) и выводит векторы (`vectors.jsonl`) из
версионированного spec `local/bm25-hash-tfidf/2` — hashed TF-IDF с BM25-весами,
4096 измерений, cosine. Ни сети, ни БД, ни внешней модели.

Id элемента выводится из содержания (`category|fingerprint`,
`kind|symbolKey|path`, evidence hash, `exampleId`), поэтому повторный скан
пишет новый run в `runs` вместо дубля: `new: 0, reused: N`. Форма ответа —
тот же `SimilarFixesResult`; статусы примеров — те же `draft`/`vetted`/`retired`,
и `vetted` по-прежнему требует реального scanner provenance. Подробности и
ограничения — в [rag/README.md](../rag/README.md).