# Полный аудит: анализаторы, AI/RAG, codefacts, reference checks

- База: `35d27d30ae44fd955824001e18eff895197c933c` (dev).
- Код на момент отчёта: `49f40257a876a86248cb83bb0550982a4e5ee9c1`.
  Сам отчёт — отдельный docs-only коммит поверх этого SHA.
- Ветка/worktree: `SoloveyMaks/audit-all-analysis-1122`,
  `/home/rodion/orca/workspaces/41l1-n3dra/audit-all-analysis-1122`.
- Область: `analyzers/`, `backend/app/analyzers/`,
  `backend/app/integrations/`, `backend/app/ai/`,
  `backend/app/reference_checks/`, `backend/app/codefacts/`,
  `packages/vector/`, `rag/`.

Проверка только чтением исходников и локальными одноразовыми пробами.
Реальные LLM-провайдеры, ключи, production-БД, `podman`, контейнеры,
порты, службы и внешние отправки не использовались.

## Окружение и инструменты

- Системный `python3 3.14.7` — для stdlib-частей (`analyzers.selftest`,
  `rag.selftest`, `rag.store`, `analyzers.contract_export`).
- `tree-sitter`/`tree-sitter-kotlin`/`pydantic`/`fastapi` в системном Python
  отсутствуют; для backend-анализаторов использован **только чтением**
  интерпретатор соседнего worktree
  `/home/rodion/orca/workspaces/41l1-n3dra/backend-facts-integration/.venv/bin/python`
  (Python 3.12.15, `tree-sitter`, `pydantic 2.14.4`). Ничего в том worktree
  не менялось.
- Node 22 — `analyzers/validate_bundle.mjs`, `packages/vector/verify-cli.mjs`.
- `podman` не запускался; `podman start nedra-mini-integration-pg` не
  повторялся. PostgreSQL-зависимые пути (published envelopes, exact-vector SQL,
  server-mode reference checks) не проверялись живьём — это открытый блокер
  окружения, а не зелёный статус.
- Временные данные: `/tmp/nedra-audit-analysis/`.

## Что проверено живьём

| Проверка | Команда | Фактический результат |
| --- | --- | --- |
| Selftest анализаторов | `python3 -m analyzers.selftest` | `SELFTEST PASSED: 74 проверок` |
| Selftest RAG | `python3 -m rag.selftest` | `RAG SELFTEST PASSED: 50 проверок` |
| Vector demo harness | `node packages/vector/verify-cli.mjs` | exit 0, `failures: []`, draft-only/фильтры/spec/workspace негативы подтверждены |
| SQL before/after demo | `…/venv/bin/python examples/demo/sql-before-after/run_real_scan.py` | before `SAST findings=1`, after `0`; оба scope `observed_sql` проверены |
| Bundle + контрактный валидатор | `python -m analyzers.contract_export … && node analyzers/validate_bundle.mjs …` | синтетический Kotlin/Gradle проект: `{"findings":3,"evidence":17,"nodes":21,"edges":16}`, exit 0 |
| Bundle с CRLF/emoji/кириллицей | то же | evidence bytes/line/column совпали, exit 0 |
| Bundle с битым UTF-8 | то же + backend `CanonicalMapBuilder` | битый файл пропускается, evidence по нему не строится |
| RAG isolation | `Store.ingest_bundle` чужим workspace | `WorkspaceMismatch`, cross-workspace запись отклонена |
| RAG SimilarFix | `similar_fixes(..., statuses=("vetted","draft"))` | ровно 8 полей контракта, `metric=cosine`, пример остаётся draft |
| AI explanation validator | прямые вызовы `AiResponseValidator.validate` | чужая версия, чужой CVE (в т.ч. lower-case), версия для не-SCA — отклонены |
| AI scan validator | прямые вызовы `validate_ai_scan_response` | unknown fileRef, CVE, critical severity, quote вне окна — отклонены; точный quote принят с серверными byte-координатами |
| AI excerpt redaction | `select_sources` + `payload_for_model` | synthetic secret/URI-пароль в excerpt отсутствуют |
| Live backend SECRET/SCA/SAST | прямые вызовы анализаторов на `/tmp/nedra-audit-analysis/proj*` | см. дефекты ниже |

## Исправленные дефекты

### 1. RAG `SimilarFix` содержал поле вне контракта

- Воспроизведение: `python3 -m rag.selftest` →
  `RAG SELFTEST FAILED: rag: SimilarFix содержит ровно поля контракта`.
- Причина: `rag/store.py::similar_fixes()` добавлял `status` в item, тогда как
  `docs/data-contracts/contracts.ts` и `$defs/SimilarFix` требуют ровно 8
  полей и `additionalProperties: false`. README `rag/` и selftest это
  фиксируют.
- Исправление: item снова содержит только поля контракта; фильтрация по
  `statuses` сохранена. Полный ряд со `status` остаётся в Python DTO
  `backend/app/contracts/schemas.py` для pgvector-пути; синхронизация
  JSON/TS-контракта — у владельца контрактов, не менялась.
- Проверка: оба selftest зелёные; прямой `similar_fixes` на draft-примерах
  отдаёт 8 ключей.
- Коммит: `748408a`.

### 2. Backend SAST терял SQL-поток через локальный `val` и общие JDBC sink

- Воспроизведение: `examples/demo/sql-before-after` before-снимок
  (`val sql = "…" + frameTag; jdbcTemplate.query(sql)`) давал
  `SAST findings=0`, хотя `docs/sql-before-after.md` фиксирует 1 находку.
  Синтетический controller с `jdbc.queryForList(sql)` и
  `jdbc.query("… '$frameTag'")` давал 0 SQL-находок.
- Причина: после привязки правила к AST-аргументам проверялся только текст
  `args[0]`; локальная переменная не разворачивалась. Список sink был
  захардкожен под имена `jdbcTemplate.*` и не содержал `queryForList`,
  `queryForObject`, `queryForMap`, `queryForRowSet`, `batchUpdate`,
  `prepareStatement`.
- Исправление: в `sast_analyzer.py` добавлено разрешение локальных
  `property_declaration` той же функции (с защитой от циклов), сопоставление
  JDBC-метода на любом receiver и обязательное наличие SQL-ключевого слова,
  чтобы не ловить произвольные `query/update`.
- Проверка: demo before `1`, after `0`; синтетический проект даёт 2 находки
  `KOTLIN-SQL-INJECTION` (локальный `val` и прямая интерполяция), безопасный
  `?`-запрос не срабатывает; `analyzers.selftest` не затронут.
- Ограничение: межпроцедурный поток, поля класса, конкатенация через
  промежуточные вызовы по-прежнему не отслеживаются; это записано в
  `coverageWarnings` находки.
- Коммит: `c84d935`.

### 3. Backend SCA не видел многострочные и Groovy-зависимости

- Воспроизведение: `build.gradle.kts` с
  `implementation(\n "org.apache.logging.log4j:log4j-core:2.14.1"\n)` и
  Groovy `build.gradle` с `implementation '…:log4j-core:2.14.1'` —
  `ScaAnalyzer` возвращал 0 находок и 0 диагностик, хотя версия уязвима
  (`CVE-2021-44228`) и файл прочитан.
- Причина: regex-ы применялись к каждой физической строке и требовали
  `implementation(...)`; Groovy-формы без скобок и named-args не
  поддерживались.
- Исправление: зависимости ищутся в comment-stripped тексте целиком с
  переносом byte-смещения в `(line, column)`; поддержаны `implementation "…"`,
  `implementation("…")`, Groovy named-args `group: / name: / version:`,
  многострочные вызовы. Комментарии по-прежнему вырезаются лексером.
- Проверка: многострочный log4j найден (`line 9-10`), Groovy-строки и
  Groovy named-args найдены; Kotlin-проект даёт те же 14 advisory-находок;
  fingerprint одно-строчных объявлений не изменился (line-relative start).
- Ограничение: `add("implementation", ...)`, BOM/platform, version catalog
  без версии, квалификаторы вроде `33.0.0-jre` обрабатываются как раньше
  (диагностика/неразрешённая версия); полный Gradle graph не исполняется.
- Коммит: `11a37b2`.

### 4. Codefacts строил evidence по тексту с replacement-символами

- Воспроизведение: `.kt` с одним невалидным UTF-8 байтом до маршрута.
  `extract_facts` + `CanonicalMapBuilder` выдавали evidence с `byteStart=236`,
  но сырые байты в этом диапазоне начинались на байт раньше: U+FFFD занимает
  3 байта вместо 1–2 исходных. Контрактный валидатор дополнительно декодирует
  raw как `utf-8` с `fatal:true`, то есть такой файл в bundle непроверяем.
- Причина: `decode_source` заменяет невалидные последовательности, а
  `make_location` считает byte-смещения по уже заменённому тексту.
- Исправление: `extractor.py` пропускает парсинг файла с `decode_error`,
  выдаёт path-only диагностику `CF-DECODE-ERROR` (без выдуманных координат),
  файл остаётся в `files_read`, coverage scope получает `partial`.
- Проверка: для проекта с битым контроллером `nodes=0, edges=0, evidence=0`,
  coverage `partial`; контрактный bundle для валидных Unicode/CRLF файлов
  по-прежнему проходит `validate_bundle.mjs`.
- Ограничение: находки в таком файле не извлекаются вообще — это осознанный
  fail-closed вместо ложных координат. Файл виден в диагностике и покрытии.
- Коммит: `49f4025`.

## Найдено, но не исправлено (вне владения или требует чужих данных)

1. **Reference checks: pinned fixture устарел.** Живой запуск
   `python -m backend.app.reference_checks docs/reference-checks/fixtures/reference-suite.json /tmp/.../compatible-bundle.json`
   вернул `inconclusive`, `0/3` находок, `0/2` контролей. Причина не в
   компараторе: `compatibility.reasons` содержит
   `parserHash differs` и `rulesHash differs` — pinned manifest
   `reference-suite.json` собран более старым `analyzers/cg26` (файлы
   парсера/правил менялись после `ae1fd48`). Checked-in
   `docs/reference-checks/reports/compatible.json` утверждает `3/3` и
   `compatible:true`; это исторический артефакт, не текущее состояние.
   Нужен пересбор эталонного bundle владельцем reference-ветки под
   зафиксированный SHA; подделывать hashes нельзя. Server-mode с авторитетными
   хэшами не проверялся без БД.
2. **Custom checks sandbox в этом worktree отсутствует.** В `HEAD` нет
   `backend/app/custom_checks/` и `db/migrations/013_custom_checks.up.sql`,
   хотя `db/README.md` и `docs/vetted-vector-retrieval.md` на них ссылаются.
   На `main 98c276e` sandbox фактически лежит в
   `backend/app/custom_checks/sandbox.py` (плюс `service.py`, `catalog.py`,
   `models.py`, `store.py`, `api/custom_checks.py`, миграция `013`).
   Read-only аудит main-кода: bwrap с `--unshare-all`, `--clearenv`,
   ro-bind проекта и `/input`, tmpfs `/work` 1 MiB, `/dev` 64 KiB без
   `/dev/shm`, два слоя `prlimit` (`nproc=1`, AS/CPU/FSIZE/NOFILE), seccomp
   блокирует `memfd_create` и SysV shm; availability probe проверяет форк,
   потоки, subprocess, memfd, shmget, большой malloc, env, cwd и запись
   `/work`, при провале — `unavailable` без host-fallback. Runtime-проверка
   не выполнялась: запуск untrusted sandbox/контейнеров запрещён заданием.
   Интеграция и runtime-проверка — за владельцем custom-checks.
3. **Sandbox-подобные subprocess вне области.** `backend/app/api/sql_pattern.py`
   (Node CLI, engine) и `backend/app/api/comparison.py` (Node CLI, data)
   запускают внешние процессы через tempdir; они не проверялись и не
   менялись.
4. **SECRET без `CG26_HMAC_KEY`.** `secret_analyzer.compute_secret_hmac`
   использует случайный `_LOCAL_HMAC_KEY` при отсутствии ключа, поэтому
   fingerprint SECRET-находок меняется между процессами. Это задокументировано
   как `local-demo-1`/несравнимое с production; ключ и конфиг не менялись.
5. **Backend SECRET сканирует комментарии.** Синтетический
   `// password = "…"` даёт `SECRET-PASSWORD-LITERAL`, тогда как Team B
   (`analyzers/cg26/secrets.py`) комментарии игнорирует. Это расхождение
   движков, а не утечка значения: evidence маскируется. Оставлено как
   консервативное поведение backend-правила; при желании владельцу стоит
   выбрать единый контракт.
6. **SCA qualifier prefilter.** `_check_dependency` отвергает версии с
   произвольным квалификатором (`33.0.0-jre`) до сравнения, хотя
   `version_comparator` умеет их сравнивать; выдаётся
   `sca.unresolved-version`, не молчаливый пропуск. Это соответствует
   задокументированному ограничению «квалификаторы дают диагностику»;
   расширение списка — решение владельца правил.
7. **AI provider code.** `backend/app/ai/provider.py` в этом worktree всё ещё
   описывает ReformBoss (`REFORMBOSS_API_KEY`, `api.reformboss.com`).
   Реальные вызовы не выполнялись; смена LLM-провайдера — отдельная задача
   AI runtime, не этот аудит, и конфиги/секреты не трогались.

## Покрытие и ограничения

- Живыми прогонами покрыты: `/`-анализаторы SECRET/SCA/SAST на синтетических
  Kotlin/Gradle проектах, RAG store, vector demo, AI validator/scan
  validator/selection, contract bundle и его байтовые координаты, SQL
  before/after demo, reference-checks raw CLI.
- Только чтением покрыты: `backend/app/integrations/team_b.py`,
  `backend/app/integrations/full_analysis.py`, `backend/app/reference_checks/*`
  (server mode), `backend/app/codefacts/publish.py`, `store.py`,
  `identity.py`, `backend/app/ai/provider.py`, `service.py`,
  `vector_runtime.py`, main-код custom checks.
- Не проверялись: PostgreSQL/pgvector-функции, published envelopes,
  production retrieval, server-mode reference attestation, LLM-провайдер,
  пользовательский сервис `18080`, любые контейнеры/службы/порты.
- Merge backend+Team B проверен на синтетическом проекте через
  `_backend_scan_results` + `merge_full_results` (без БД): SECRET 4+2→4
  (2 дубля), SAST 5+1→5 (1 дубль), SCA 14+0→14; падение одного движка
  остаётся диагностикой, а не потерей второго.
- Ни один непроверенный путь не назван прошедшим.

## Открытые блокеры

- Нет доступной integration-БД/pgvector: SQL/hash/publish/retrieval проверки
  остаются статическими. `podman start nedra-mini-integration-pg` повторять
  запрещено.
- Reference suite: pinned снимок найден и эталон пересобран (см. follow-up
  ниже); server-mode target по-прежнему требует опубликованный bundle из БД.
- Custom-check sandbox и миграция `013` есть только на `main 98c276e`;
  интеграция — координатором, не этим worktree.

## Follow-up: reference suite пересобран

- Pinned snapshot существует: `docs/reference-checks/fixtures/project-reference`
  на коммите `ae1fd4805c3ce8cfc533be99a601b0e2d3aa95a1`; его
  `snapshotManifestHash=6be987bf…5772` и `artifactHash=fd93a4cb…3608`
  совпадают с манифестом.
- Причина старого mismatch: манифест был подписан деревом `ae1fd48`
  (`parserHash 45a0c139…`, `rulesHash 8731ddbc…`), актуальный код
  `49f4025` даёт `fc9747f7…`/`25598be9…`; raw CLI честно возвращал
  `incompatible`, `0/3`.
- Эталонный bundle пересобран `analyzers.contract_export` на снапшоте
  `project-reference` актуальным анализатором (`49f4025`): файл
  `docs/reference-checks/reference-bundle.json` sha256
  `72bb792cef15bd37dafaaa968277f43b1eac1b3a4834b22f59a688747d2b13ed`,
  канонический content sha256
  `a5868546ba086c95815c7927760b5376d74c410689c9a3b4f537e43cfbc3fd7c`.
  Манифест и `reference-suite.reference.json` обновлены этими значениями;
  expected findings/fingerprints не менялись — они совпали с пересборкой.
- Проверка reference side без БД:
  `ReferenceSuiteRegistry.load_reference_snapshot` +
  `verify_reference_snapshot` → `verified=true`,
  `expectedFindingsPresent=true`, все checks `true`.
  `validate_bundle.mjs` на эталонном bundle — exit 0.
- Отчёты пересобраны: `compatible` — `inconclusive`, 3/3 находки, 2/2
  контроля (SCA coverage `unavailable`, не pass); `failure` — `fail`, 0/3;
  `incompatible`/`coverage-loss` сохранили прежние негативные сценарии.
  Server-mode target всё ещё не проверен без published envelope/БД.

## Итоговые коммиты области

```
49f4025 Недра: не строить координаты доказательств по битому UTF-8
c84d935 Недра: прослеживать локальный SQL val и общие JDBC sink в SAST
11a37b2 Недра: находить многострочные и Groovy зависимости SCA
748408a Недра: вернуть SimilarFix к полям контракта без status
```
