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
| `AI_PROVIDER` | `opencode-go` (default), `reformboss` (legacy), `openai-compatible` или любое другое имя объявленного шлюза (`ollama`, `vllm`, …) |
| `OPENCODE_API_KEY` | ключ OpenCode Go |
| `AI_API_KEY` | общий override ключа; для Go/legacy имеет приоритет, для local-шлюза опционален |
| `REFORMBOSS_API_KEY` | legacy-ключ ReformBoss |
| `AI_BASE_URL` | override base URL; для Go default `https://opencode.ai/inference/go/v1`, для legacy `https://api.reformboss.com/v1`; для local-шлюза обязателен |
| `AI_MODEL` / `LLM_MODEL` | override модели; Go default `deepseek-v4.1-flash`, legacy default `deepseek-v4.1-flash-1017`; для local-шлюза обязателен |
| `AI_TIMEOUT_SECONDS` | таймаут запроса, default 30 |
| `AI_MAX_TOKENS` | верхний предел на батч, default 4096 |
| `AI_BATCH_SIZE` | findings на запрос, default 4 (допустимо 1..8) |
| `AI_TEMPERATURE` | default 0 |
| `AI_JSON_MODE` | `1` только если шлюз реально поддерживает `response_format=json_object` |

Правила выбора: явный `AI_PROVIDER` побеждает. `AI_PROVIDER=opencode-go`
берёт только `AI_API_KEY`/`OPENCODE_API_KEY` и Go-endpoint; legacy-ключ и
legacy-endpoint при этом не используются. Если `AI_BASE_URL` указывает на
`api.reformboss.com`, Go-провайдер заменяет его на Go endpoint; другие
local/proxy override сохраняются. `AI_PROVIDER=reformboss` берёт
`AI_API_KEY`/`REFORMBOSS_API_KEY` и legacy-endpoint; Go-ключ сам по себе его
не включает. Любое другое имя `AI_PROVIDER` (`openai-compatible`, `ollama`,
`vllm`, …) — описанный оператором шлюз: только `AI_BASE_URL` + явный
`AI_MODEL`/`LLM_MODEL`, ключ опционален, чужой default endpoint, модель и
секрет не подмешиваются. Без `AI_PROVIDER` сначала выбирается Go при наличии
`OPENCODE_API_KEY`, затем generic `AI_API_KEY` (local при заданном
`AI_BASE_URL`, иначе Go), затем legacy; если ключей нет вовсе, но заданы и
`AI_BASE_URL`, и `AI_MODEL`/`LLM_MODEL`, это keyless local-шлюз (Ollama/vLLM).
Для local-шлюза всегда нужен явный `AI_MODEL`/`LLM_MODEL`: без модели конфиг
не считается настроенным, чтобы не угадывать чужую модель и не подставлять
legacy-имя модели в generic-путь.

Remote-провайдеры (Go, legacy) без ключа не резолвятся:
`resolve_provider_config()` возвращает `None` (fail-closed), а не берёт
чужой секрет. Пустой ключ у local-шлюза валиден: заголовок `Authorization`
тогда не отправляется. `provenance.provider` всегда называет реально
использованный провайдер — `openai-compatible` для generic-шлюза, объявленное
имя для `AI_PROVIDER=ollama`, `reformboss` только когда действительно выбран
legacy.

Legacy-совместимость: если `REFORMBOSS_API_KEY` нет в процессе, старое
значение всё ещё читается из `~/.omp/agent/.env`; Go и local ключи читаются
только из окружения. Продукт не открывает внешнюю credential DB. Ключ не
попадает в argv, логи, provenance или отчёт.

Go-запросы используют собственный `User-Agent` продукта
`NEDRA/0.1 (+https://nedra.soloveyka.bond)` и штатный
`x-opencode-session` — непрозрачный стабильный id логической AI-операции
(одинаковый для батчей/повторных попыток, без user identity и секретов).
Legacy и local запросы получают только продуктовый `User-Agent` и не несут
Go-заголовка; при пустом ключе `Authorization` не отправляется вовсе.

Провайдер не настроен (remote без ключа, либо неполный конфиг шлюза: нет
`AI_BASE_URL` или нет `AI_MODEL`/`LLM_MODEL`) → `resolve_provider_config()`
возвращает `None`, и запрос не выполняется: `status=fallback`,
`provenance.fallback_reason=provider_not_configured`. Ошибка уже
выполненного запроса — типизированный `AiProviderError` с замаскированным
текстом (bounded, без утечки ключа и без скрытия HTTP-статуса).

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

Единственная сетевая точка этого пути — `EmbeddingsAdapter` в
`backend/app/ai/vector_runtime.py` (`POST {VECTOR_EMBEDDING_BASE_URL}/embeddings`).
Конфиг спеки — `VECTOR_EMBEDDING_PROVIDER/MODEL/VERSION/DIMENSION/METRIC`;
adapter возвращает вектор только если ответ ей соответствует: модель
совпала, размерность ровно `VECTOR_EMBEDDING_DIMENSION`, все значения
конечны. Расхождение — типизированная ошибка → `unavailable`, а не «похожий»
вектор и не «успех». Ключ обязателен для включённого пути (попадает в
`missing`); при пустом значении (прямая сборка конфига) заголовок
`Authorization` не отправляется. Каждый успешный запрос даёт
`EmbeddingProvenance` (provider, model, dimension, metric, нормализованный
endpoint-роут без секретов, latency, request id) — она дописывается в
`retrieval.reason`; `embeddingSpecId` строки и поля
`retrieval.embedding*` — тот же спек.

При выключенном runtime статус остаётся `disabled` с исходной причиной:
adapter не вызывается, сетевых запросов нет и никакие embedding-факты не
выдумываются — `embeddingModel`/`dimension`/`metric` в `disabled`-ответе это
отражение конфигурации, а не результат измерения.

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

## Тесты

`backend/tests/test_ai_fake_server.py` — loopback fake OpenAI-сервер
(`/chat/completions` + `/embeddings`; записывает Authorization, User-Agent и
`x-opencode-session`, ответы программируются).

`backend/tests/test_ai_provider.py`:

1. generic-провайдер (`AI_BASE_URL`+`AI_MODEL`+`AI_API_KEY`) работает без
   каких-либо `REFORMBOSS_*`/`OPENCODE_*` настроек, provenance —
   `openai-compatible`, ключ не попадает в provenance;
2. keyless local-шлюз (Ollama/vLLM) резолвится и выполняет запрос без
   `Authorization`;
3. remote без ключа, неполный конфиг шлюза, чужой `AI_PROVIDER` без
   `AI_BASE_URL` → `None` (bounded, ничего не угадывается);
4. HTTP 401 с телом, содержащим `sk-...`, и недоступный endpoint →
   типизированный `AiProviderError` с замаскированным текстом.

`backend/tests/test_vector_runtime.py`:

1. выключенный runtime — `disabled` с исходной причиной, ни одного запроса и
   ни одного выдуманного embedding-факта;
2. неполный/некорректный спек (`METRIC`, `DIMENSION`, `STATUS`) →
   `unavailable`, не `ready`;
3. adapter возвращает вектор+`EmbeddingProvenance` только при совпадении
   модели и размерности; расхождение → отказ;
4. embeddings без БД, без scope и при ошибке спека — всегда `unavailable`,
   никогда не `ok`.

Запуск: `PYTHONPATH=. python -m pytest backend/tests -q`.
