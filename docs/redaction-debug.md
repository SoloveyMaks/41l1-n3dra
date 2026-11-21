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
  для HMAC fingerprint.

`REDACTION_VERSION` поднят до `cg-redaction/2`; API fallback и TeamB adapter
берут общую константу. TeamB analyzer package не переписывался.

## Проверки

Временный analyzer-run на synthetic canaries, без вывода значений:

- URI с raw `@` внутри пароля и вторым `@` перед host;
- percent-encoded `%40`;
- `user:pass`, пустой user, короткий пароль;
- Bearer, JWT-подобная строка, PEM-подобный ключ;
- `${VAR:default}` и query URL с `@` вне authority.

Проверено, что canary отсутствует в:

- `mask_text`/`mask_sensitive_text` и `mask_uri_credentials`;
- finding `redactedEvidence`, serialized finding (report JSON), trace;
- source `make_evidence` / masked source;
- report Markdown и official JSON;
- AI masked payload.

Test output: только `redaction_live True`, `report_clean True`,
`ai_payload_clean True`, `masked_source_clean True`; значения не печатались.

Дополнительно: sample `application.properties` читался внутри процесса;
исходный URI-секрет не появился в маскированном finding/source. TeamB
остаётся совместим на уровне adapter/redactionVersion.

## Остаточные риски

- Это не универсальный DLP: невалидные URI с пробелами/сырыми `/` внутри
  userinfo, не-URI форматы и произвольный текст всё ещё требуют отдельных
  правил.
- AI-модель может вернуть секрет в свободном тексте; выход дополнительно
  маскируется best-effort, но гарантия только на уровне известных шаблонов.
- Старые сохранённые артефакты с `cg-redaction/1` не переписываются; новые
  runs получают `/2`.
- TeamB полагается на `redactedSnippet` из bundle; adapter не исполняет и не
  переписывает TeamB analyzer package.