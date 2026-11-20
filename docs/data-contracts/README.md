# Контракт данных — proposal v0.1

Это первый пакет C для обсуждения A/B/D, **не согласованный командой контракт**. Приложение, scanner, executor и API здесь не реализуются. PostgreSQL + optional pgvector уже выбраны пользователем; поля и правила ниже предлагаются на согласование.

- [JSON Schema](contracts.schema.json) — источник формы JSON, draft 2020-12, корень `SecurityMapDto`; остальные типы доступны через `$defs`.
- [TypeScript](contracts.ts) генерируется `node docs/data-contracts/generate-types.mjs`, без новых dependencies. Форматы, границы и ссылки проверяются отдельно.
- [Иллюстративный bundle](illustrative-bundle.json) вручную составлен для формы и UTF-8/CRLF probe: **не результат B и не security finding**. Hash/version placeholders не являются настоящим анализом.
- [Проверка связей](validate-bundle.mjs) дополняет Schema; принимает `AnalysisBundle` и optional `Map<path, Uint8Array>` неизменённых исходных bytes. Исходники не возвращает и не сохраняет. Это проверка контракта, не scanner и не гарантия redaction.
- [SQL](../../db/README.md) — порядок миграций, владение, атомарная публикация; [mapping](mapping.md), [сравнения](comparisons.md), [проверка](verification.md).

## Идентичность и доверие

`RunIdentity`: workspace/run/snapshot IDs, исходный `artifactHash`, hash manifest snapshot и точная workflow revision. Snapshot не является архивом: после TTL повторный запуск требует повторной загрузки. UUID — идентификаторы, не доказательство доступа. Каждый endpoint A авторизует workspace и строит запрос с **workspaceId + runId + snapshotId**, включая masked-source/evidence. Совпадение одного evidence UUID недостаточно.

`AnalysisSignature`: точные версии **и** hashes parser/rules/advisory/config, optional domain policy revision/version/hash и `hmacKeyVersion`. Hash — SHA-256, 64 lowercase hex. Версия без hash не доказывает одинаковые bytes. Для неприменимого advisory используется согласованная immutable пустая база с версией, а не null/«latest». AI provider/model/prompt/validator хранятся отдельно: смена модели не меняет статические факты.

`Finding.confirmed` = подтверждённый статический паттерн; не эксплуатация. Severity и confidence независимы. Diagnostic не является Finding. Coverage содержит per-family/per-file/per-rule scopes: пустой граф не доказывает отсутствие проблем. `complete` требует всех запрошенных scopes без parse errors, skipped/unsupported и исчерпанных лимитов; полноту ожидаемого множества scopes определяет B по manifest/config, а не число строк в БД.

CodeFactsBundle.coverage сохраняется целиком в scoped immutable `graph_coverage` и участвует в bundle hash: graph-only skipped scopes/unresolved symbols не заменяются агрегатом scanner coverage.

Source facts B, user domain policy и AI explanation — три отдельных слоя. Policy содержит источник, immutable hash/version и непустой массив typed `resources`: `role_access` задаёт `allowedRoles` (например, staff-only pickup), `ownership` задаёт `ownerField`, `trustedIdentity`, `roleExceptions`; смешивать поля двух kinds нельзя; неподтверждённая policy остаётся `assumption`. Ребро `policy_assumption` ссылается именно на policy данного run. `resolution=resolved` говорит о разрешённой связи кода, не об исправлении находки. Любая сохранённая AI explanation ссылается только на published immutable bundle; draft-основание не принимается. AI `accepted` означает прохождение validator D; **не** человеческую проверку исправления. `vetted` example требует человека и сохранённого основания.

## Координаты и bytes

`path` — case-sensitive относительный POSIX путь из принятого manifest: `/`, без leading/trailing slash, `.`/`..`, повторных slash, backslash, drive prefix и control chars. Unicode имена не нормализуем молча: intake A отклоняет дубли после выбранной нормализации; финальную NFC policy нужно подтвердить A/B. Path хранится как принят в snapshot. Rename считается новым anchor без явной карты переименований B.

`sourceHash` = SHA-256 **оригинальных распакованных bytes файла**, до декодирования и redaction; `artifactHash` — bytes принятого ZIP, не распакованного каталога. `snapshotManifestHash` вычисляет SQL по отсортированным `{path,sourceHash,byteLength,mediaType}`. Это разные hashes. BOM, CRLF и non-ASCII входят в file hash как есть. Binary/invalid UTF-8 не получает выдуманных текстовых координат: diagnostic + unsupported coverage; в текстовый EvidenceRef не попадает.

Диапазон bytes `[byteStart, byteEnd)`, начало с 0, в оригинальном файле. Позиции `(line,column)` и `(endLine,endColumn)` также полуоткрытые, строки/колонки с 1; колонка считает Unicode **code points**, не UTF-16 units, graphemes или экранные клетки; tab = один code point. LF начинает следующую строку, CR в CRLF входит в bytes, но не текстовую колонку. Исключение: UTF-8 BOM, если есть, считается U+FEFF в колонках первой строки. Конец строки = `lengthInCodePoints+1`. Диапазон через newline заканчивается позицией следующей строки. Например `а\r\nб`: `б` — line 2/column 1, bytes `[4,6)`. Для пустого anchor допустим нулевой диапазон; UI не рисует его как доказательство символов. Bytes/columns относятся к исходнику, **не** к длине `redactedSnippet`; UI использует сохраняющий расположение mask A.

SQL проверяет hash/file membership, порядок и byte bounds. Реальное соответствие строк/колонок исходным bytes проверяет ingestion validator перед удалением исходника; Schema не умеет эти межполевые условия. Все JSON integer находятся в JS safe integer range. SQL byte_length/duration/event seq ограничены тем же диапазоном; PostgreSQL timestamp выдаётся RFC3339 UTC.

## Устойчивый fingerprint

Предлагаемый рецепт B/C, `fp/v1`: SHA-256 UTF-8 от compact JSON массива (без пробелов, `JSON.stringify`):

```text
["fp/v1", category, ruleId, normalizedPath, symbolKey,
 normalizedContextHash, role, secretHmacOrNull]
```

Семантический symbol включает package/class/function, overload signature и различающий локальный anchor. `role` различает source/sink/guard/manifest occurrence. `normalizedContextHash` B считает из нормализованных токенов/структуры без trivia и номеров строк; значение секрета до этого вырезается. Конкретную нормализацию, версии и устойчивость на неоднозначных повторениях **согласовывает B**. Номер строки, UUID run и текст AI в fingerprint не входят. `ruleVersion` отдельно; несовместимость версий проверяется signature. Перенос строк сохраняет fingerprint; одинаковые повторения могут дать коллизию семантических anchors — тогда matching `inconclusive`, не выбираем первый. Поэтому SQL индекс fingerprint **не unique**.

SECRET `secretHmac` = HMAC-SHA-256 ключом вне БД: UTF-8 compact JSON `["secret/v1", workspaceId, detectorFamily, canonicalSecretValue]`; canonicalSecretValue B определяет для detector family до хэширования, без произвольного trim/lowercase. Сырой secret не сохраняется, не логируется и не отправляется D. `hmacKeyVersion` в signature сохраняется независимо от TTL ZIP; смена ключа ломает сопоставимость. Не используем обычный SHA секрета из-за перебора. HMAC workspace-specific, сопоставления между workspace запрещены. Recipe/version — предложение для B; DTO раскрывает только digest и key version, ключ в DTO не передаётся.

## Что решить на первом стыке

| Владелец | Решение до реализации |
|---|---|
| A | Авторизация workspace и SQL roles, artifact store/TTL, UTF-8/path/BOM/mask policy, workflow definition schema, границы маленькой publish transaction и event sequence lock |
| B | Реальные scanner envelopes каждой семьи, vocabulary graph kinds, semantic token normalization/overloads/duplicate anchors, coverage expected scopes и signature hashes |
| D | Grounding validator + redaction versions; настоящий embedding provider/model/version/dimension/metric и preparation versions; всё pending до реального вызова |
| A/B/C | Алгоритм matching и разрешённые критерии changed/introduced, поведение rename и неполных scopes; acceptance SQL/JSON вместе |
| A/B/D | Подтвердить proposal v0.1 либо прислать изменения; подтверждений в этом пакете нет |

Вывод: пакет можно обсуждать и прогонять в локальном PostgreSQL; подключать его как утверждённый интерфейс до ответа A/B/D рано.

## Два вида domain policy

`DomainPolicyResource` — discriminated union по `kind`, дополнительные поля запрещены. `role_access`: `{kind:"role_access", resource:"pickup.export", allowedRoles:["MECHANIC"]}`; `ownership`: `{kind:"ownership", resource:"ticket", ownerField:"owner_id", trustedIdentity:"Principal.id", roleExceptions:[]}`. Это иллюстрации **формы**, не подтверждённые business rules учебного проекта. SQL `policy` хранит только `{resources:[...]}`, shape проверяется `valid_domain_policy_document`; metadata/source/approval находятся в явных колонках revision. Пока пользователь/команда не подтвердили ограничение — `approvalStatus="assumption"`, `approvedBy/approvedAt=null`. Имена ролей и resources финализируют A/B; разрешённая роль сама по себе не доказывает trusted owner identity.
