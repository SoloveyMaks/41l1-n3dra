# Redaction: URI credentials with multiple `@`

## Симптом

`SECRET-URI-CREDENTIALS` находил URI, но маскировал только часть userinfo до
первого `@`. Если в пароле был второй `@` (или percent-encoded форма), хвост
пароля оставался в `redactedEvidence`, trace/description, map evidence,
API masked source, AI context и отчётах.

## Корень

В трёх местах была независимая копия одного и того же допущения:

- `secret_analyzer.py` заменял только `match.group(1)`;
- `codefacts/source.py` использовал regex `user:pass@` до первого `@`;
- `ai/provider.py` использовал `://[^@\s/]+@`.

Regex «пароль до первого `@`» неверен для реальных конфигов: `@` может быть
частью пароля, а percent-encoding и несколько `@` ломают границу.

## Исправление

Добавлен общий presentation-masker
`backend/app/security/redaction.py`:

- находит URI `scheme://`;
- берёт весь authority до `/`, `?`, `#`, пробела или кавычки;
- если в authority есть `@`, маскирует **весь** userinfo до последнего `@`,
  сохраняя только host/port (`***@host:port`);
- не меняет raw source, offsets, `sourceHash` и fingerprints; маскируется
  только представление.

Использование:

- `codefacts/source.py::mask_text` — URI, PEM, assignments, Bearer, длинные
  токены, `${VAR:default}`. Через него идут `make_evidence`,
  canonical evidence, API masked fragment, report/MD.
- `ai/provider.py::mask_sensitive_text` — тот же URI-помощник плюс
  assignment/Bearer/cloud-token/private-key/env-default.
- `secret_analyzer.py` — `redactedEvidence = mask_text(line)`, description и
  trace не содержат значения секрета; raw secret остаётся только локально
  для HMAC fingerprint. Для URI fingerprint по-прежнему использует прежний
  pass-фрагмент, поэтому finding identity не меняется silently.
- `_SECRET_ASSIGN_RE` маскирует и короткие known assignments (`pwd=x`,
  `password=abc`), а не только значения от 4 символов.
- `REDACTION_VERSION` для новых канонических runs — `cg-redaction/2`.

## Presentation egress для старых данных

Старые `/1` findings/maps/evidence/reports не переписываются в БД.
`api/router.py` на выдаче отдельно ремаскирует:

- findings endpoint — `redactedEvidence`, basis, trace (both camel/snake
  DTO fields);
- security-map — map evidence snippets;
- evidence endpoint — `maskedFragment`;
- node-runs — `input_data_masked`/`output_data_masked` and JSON text fields;
- SSE replay/live delivery — only `payload` text values, IDs/config keys not
  rewritten;
- comparison/SQL responses — only known reason/text fields;
- report и `official.json` / `readable.md` downloads — findings, trace, AI
  summary/items, Markdown;
- ID/hash/envelope не меняются. `map.bundleHash` — published source bundle
  reference, не хэш remasked-отображения; он не пересчитывается.

Ответ evidence честно сообщает `redactionVersion: cg-redaction/2`,
`storedRedactionVersion` и `remasked`. TeamB persist оставлен на `/1`:
sanitizer к его storage snippets не применяется, поэтому версия не врёт;
API egress всё равно ремаскирует его на выдаче.

## Проверки

Временный analyzer-run на synthetic canaries, без вывода значений:

- URI с raw `@` внутри пароля и вторым `@` перед host;
- percent-encoded `%40`;
- `user:pass`, пустой user, короткий пароль;
- Bearer, JWT-подобная строка, PEM-подобный ключ;
- `${VAR:default}`, query URL с `@` вне authority;
- короткие `pwd=x`/`password=abc`;
- старый handcrafted DTO с canary в `redactedEvidence`, basis, trace,
  map evidence, report Markdown/JSON и downloads.

Проверено, что canary отсутствует в:

- `mask_text`/`mask_sensitive_text` и `mask_uri_credentials`;
- finding `redactedEvidence`, serialized finding (report JSON), trace;
- source `make_evidence` / masked source;
- report Markdown и official JSON;
- AI masked payload;
- egress remask старых findings/maps/reports;
- live HTTP на isolated temp runtime: nodes, SSE replay, findings,
  security-map, evidence, report и оба download; canary отсутствует,
  canonical `bundleHash`/`sourceHash` неизменны.

Comparison/SQL DTO не содержат source snippet; их reason/reasons всё равно
проходят schema-aware masker. Это отмечено как проверенный канал без
отдельных правок.

Test output: только `redaction_live True`, `report_clean True`,
`ai_payload_clean True`, `masked_source_clean True`,
`egress_remask_clean True`, `http_egress_clean True`; значения не печатались.

Дополнительно: sample `application.properties` читался внутри процесса;
исходный URI-секрет не появился в маскированном finding/source. TeamB
остаётся совместим на уровне adapter/redactionVersion.

## Остаточные риски

- Это не универсальный DLP: невалидные URI с пробелами/сырыми `/` внутри
  userinfo, не-URI форматы и произвольный текст всё ещё требуют отдельных
  правил.
- AI-модель может вернуть секрет в свободном тексте; выход дополнительно
  маскируется best-effort, но гарантия только на уровне известных шаблонов.
- Старые сохранённые артефакты с `cg-redaction/1` не переписываются; egress
  ремаскирует их под `/2`, но stored version остаётся `/1`.
- TeamB persist остаётся `/1`; его API/downloads проходят тот же egress
  remask. Adapter не переписывает TeamB analyzer package.