# Полный аудит NEDRA: данные, сравнения, отчёты и responsibility

Дата: 2026-11-22. Исходный exact SHA: `35d27d30ae44fd955824001e18eff895197c933c` (dev). Проверенный итоговый код: `e786d543d1df9b25af11d30c91c1da87d79ca145`; отчёт добавлен отдельным следующим коммитом. Worktree: `audit-all-data-1122`, ветка `SoloveyMaks/audit-all-data-1122`.

## Результат и границы

Исправлены пять подтверждённых дефектов: разный состав и счётчики Telegram-отчёта, неполное удаление секретов на экспорте, пропуск секретов в кавычках/Basic Authorization, принятие произвольного хеша workflow definition и неверный владелец вложенных файлов CODEOWNERS.

Реально выполнены локальные Python/Node вычисления, генерация JSONC/Markdown/DOCX, чтение DOCX XML, чтение/запись собственного frozen snapshot в `/tmp/nedra-audit-data/`. Репозиторий-подобный `SavedRows` в export probe — входные словари для генераторов, **не PostgreSQL и не доказательство интеграции**. Ручные envelopes с `origin=scanner` в comparison probe проверяют только вычисления; реального сканирования/публикации они не доказывают.

БД не подключалась. Контейнеры, сервис 18080, production, внешние API и сеть не использовались; запрещённая команда старта контейнера не повторялась. Схемы и исторические миграции не менялись. Push/merge не выполнялись.

## Охват

| Область | Что проверено | Уровень доказательства |
|---|---|---|
| `storage/migrate.py`, `db/migrations/001–012` (без 003), `db/optional/003,014,015` | ledger/checksum ветвления, exact alias 006, advisory locks, маркеры, транзакционные границы; FK/guards публикации и origins; optional namespace | чтение SQL/Python; вычисление SHA-256; без исполнения SQL |
| `storage/database.py`, `runtime_*.sql` | workspace-предикаты и составные ключи, динамический ContextVar, server-wide методы, DDL fresh/legacy/v2–v5, runtime run/revision/artifact guards | чтение; локальное разрешение workspace без подключения |
| `storage/canonical.py`, `comparisons.py`, `remediation.py` | preflight валидация, публикация/идемпотентность, scoped reads/writes, comparison save/read, требования vetted | чтение; локальные валидаторы/хеши; persistence не проверен |
| `packages/comparison/`, `api/comparison.py` | immutable input, signatures/coverage/manifest/provenance, partial/synthetic/unproven/cross-workspace, subprocess limits; отдельно просмотр scoped SQL rule comparison | Node вычисления общего compareBundles; SQL-rule и Python subprocess persistence — чтение |
| `reports/`, `api/report_exports.py`, `telegram/report.py`, `security/` | frozen finding set, counts, redaction, экспорт JSONC/MD/DOCX, unknown/partial coverage, legacy fallback | реальные генераторы, JSON parse и DOCX XML; HTTP/PG и визуальная верстка не проверены |
| `responsibility/`, `api/responsibility.py` | CODEOWNERS matcher и hash-bound snapshot, usernames/profile allowlist, review decision/opinion, ContextVar/executor/admission/deadline код | реальные filesystem операции и чистые функции; async runtime probe заблокирован окружением |

`storage/accounts.py`, `profile.py`, `telegram.py` не изменялись и отдельно не аудировались. Чужие engine/router/auth/source/store читались для понимания границ, не изменялись. Новые файлы тестов и постоянная инфраструктура проверок не добавлялись.

## Исправления

### D1. Telegram JSONC расходился с сохранённым отчётом

**Воспроизведение:** входной official JSON содержал finding `saved-finding`, текущие runtime-строки — `runtime-only-finding`, счётчик run — 99. До исправления web JSONC показывал `saved-finding`, Telegram — `runtime-only-finding`, Telegram `run.counts.total=99` при одном finding.

**Причина:** `telegram/report.py` отдельно читал `get_findings` и run counters, обходя frozen official finding list и общую сборку экспортов.

**Исправление:** Telegram использует `load_export_bundle`, его findings, паспорт, AI и пересчитанные counts. Формат `nedra-telegram-jsonc/1`, имя файла, лимит размера и writer сохранены. Общая сборка также удаляет дублированную слабую redaction-реализацию.

**После:** одинаковые deterministic objects web/Telegram; один `saved-finding`, total=1. Пустой frozen list остаётся пустым; `null`/строка findings в legacy official JSON одинаково переходят на runtime rows. MD и DOCX содержат `saved-finding`, не `runtime-only-finding`.

**Ограничение:** Telegram transport и чтение из настоящей БД не запускались. Общий loader дополнительно читает имя workflow, как обычный экспорт.

Коммит: `2b474ba`.

### D2. Секреты из URL и метаданных выходили в файлы

**Воспроизведение:** `https://example.invalid/v1?access_token=<synthetic>#refresh_token=<synthetic>` в evidence и названии workflow; `api_key` в AI provenance/паспорт. До исправления query-секрет был в web JSONC/Telegram/DOCX, fragment-секрет — во всех четырёх форматах; Telegram provenance отдавался сырым. Использовались только придуманные значения.

**Причина:** evidence проходил только snippet masker, URL fragment возвращался без обработки, DOCX/JSONC получали workflow name и паспорт напрямую, Telegram сохранял AI provenance/coverage без общего sanitizer.

**Исправление:** перед snippet mask применяется URL/prose sanitizer; пути location и workflow name проходят текстовую границу; паспорт/policy — рекурсивную копию; fragment обрабатывается как параметры/текст. Telegram получает общий sanitized bundle. Исходные БД-строки не переписываются.

**После:** ни один из двух проверочных секретов не обнаружен в JSONC, Telegram JSONC, MD или `word/document.xml`; JSON парсится, DOCX открывается как ZIP с XML. Проверены fragment-параметры и fragment `/token/<value>`.

**Ограничение:** проверены конкретные формы секретов, это не математическое доказательство обнаружения любого секрета. Визуальная вёрстка DOCX не проверялась.

Коммит: `2b474ba`.

### D3. Quoted credentials и Basic Authorization не удалялись

**Воспроизведение:** `sanitize_text('password="synthetic quoted value"')` оставлял значение; то же для одинарных кавычек, escaped quotes и `Authorization: Basic <base64>`. Поскольку sanitizer используется на HTTP/export границах, такие legacy-тексты могли покинуть процесс.

**Причина:** regexp bare pairs запрещал кавычку в начале значения, provider masker также не обрабатывал quoted prose и Basic credential.

**Исправление:** bounded quoted-string alternatives с escaped characters, отдельная обработка Basic/Bearer Authorization до generic pairs.

**После:** семь локальных credential cases удалены; `tokenCount=12`, `apiKeyId`, обычный текст, безопасный endpoint/query/fragment остались без изменений. Повторная export probe прошла для всех форматов.

**Ограничение:** provider/source maskers вне owned области не менялись; общий export sanitizer стал сильнее, но прямые потребители иных maskers должны проверяться их владельцами.

Коммит: `f4a0a46`.

### D4. Canonical publisher принимал ложный definitionHash

**Воспроизведение:** `_validate_workflow` принимал `definition={'name':'actual'}` и `definitionHash='a'*64`, хотя `definition_hash(definition)` другой. SQL хранит definition_hash как sha256-тип, но 001/002 не вычисляют соответствие definition для этой записи.

**Причина:** supplied hash проверялся только по формату; вычисление происходило лишь при отсутствии hash. Существующий conflict check тоже опирался на supplied hash.

**Исправление:** валидатор всегда вычисляет canonical hash и сравнивает supplied hash с ним до получения connection.

**После:** неверный hash отклоняется `workflow: definitionHash differs from definition`; верный explicit hash и отсутствие hash принимаются. Проверен вызывающий `codefacts/publish.py`: он использует тот же sort_keys/separators/UTF-8 рецепт.

**Ограничение:** транзакционная публикация и конфликт с уже сохранённой ревизией без БД не проверены; SQL schema не менялась.

Коммит: `1bfb623`.

### D5. CODEOWNERS не распространял правило каталога на его содержимое

**Воспроизведение:** реальный локальный CODEOWNERS `* @fallback\ndocs @documentation\n`, его SHA в сохранённом manifest; lookup `docs/guide.md` давал `@fallback`, хотя каталог совпал с последним правилом `docs`.

**Причина:** matcher принимал завершение только в конце полного пути; совпадение с каталогом перед `/` отбрасывалось.

**Исправление:** конечные состояния DP допускают конец пути и границу каталога, сохраняя bounded polynomial matching.

**После:** `@documentation`, matchedPattern=`docs`; проверены anchored `/docs`, вложенный `src/docs`, граница `mydocs`, wildcard `docs/*`, несовпадение `**/A.kt` с `src/BA.kt`. Изменение CODEOWNERS после сохранения manifest даёт `unavailable`, owners пусты.

**Ограничение:** bounded glob subset сохраняется; GitLab section syntax и удалённая валидация membership/команд не поддержаны и не доказаны. CODEOWNERS — рекомендация владельца, не авторство дефекта.

Коммит: `e786d54`.

## Команды и фактические результаты

В системном Python нет `pydantic`; найден существующий `/tmp/nedra-reports-venv/bin/python` с pydantic 2.14.4, fastapi 0.165.0, python-docx 1.2.0. Установка зависимостей не выполнялась. Node: v22.23.3. `psql`, `postgres`, `initdb`, `pg_config` отсутствуют в PATH.

Все одноразовые программы и данные — `/tmp/nedra-audit-data/`. Они запускают функции текущего checkout, не подменяют provider HTTP и не подключают БД.

```sh
CHECK_AFTER=1 PYTHONPATH=. /tmp/nedra-reports-venv/bin/python /tmp/nedra-audit-data/export_probe.py
CHECK_AFTER=1 PYTHONPATH=. /tmp/nedra-reports-venv/bin/python /tmp/nedra-audit-data/export_fallback_probe.py
PYTHONPATH=. /tmp/nedra-reports-venv/bin/python /tmp/nedra-audit-data/redaction_probe.py
PYTHONPATH=. /tmp/nedra-reports-venv/bin/python /tmp/nedra-audit-data/workflow_probe.py
CHECK_AFTER=1 PYTHONPATH=. /tmp/nedra-reports-venv/bin/python /tmp/nedra-audit-data/codeowners_probe.py
node /tmp/nedra-audit-data/comparison_probe.mjs
PYTHONPATH=. /tmp/nedra-reports-venv/bin/python /tmp/nedra-audit-data/storage_static_probe.py
PYTHONPATH=. /tmp/nedra-reports-venv/bin/python /tmp/nedra-audit-data/responsibility_identity_probe.py
git diff --check
```

Результаты успешных последних запусков:

- Export: одинаковый `saved-finding`, total=1; query/fragment leak=false для каждого из JSONC/Telegram/MD/DOCX XML; empty/null/string fallback пройден.
- Redaction: 7 credential cases masked; safe counts, ID, URL parameters and prose preserved.
- Workflow: wrong hash rejected; correct explicit and omitted hashes accepted.
- CODEOWNERS: nested owner=`@documentation`; directory/anchor/wildcard boundary и hash tamper rejection пройдены.
- Comparison: persistent на полных совместимых входах; partial → inconclusive; synthetic/unproven → incompatible; cross-workspace rejected; исходные объекты не изменились.
- Storage static: четыре storage-класса разрешают оба workspace в соответствующем ContextVar; 11 обязательных SQL-файлов содержат BEGIN/COMMIT; exact 006 SHA `4956db3112a2a61e85fec353c25236859191f9f64dcb91319b45279b85e87d19` совпал с alias target; изменённые байты имеют иной hash; runtime version=5; owned Python AST разобран.
- Responsibility identity: git display name не становится login; небезопасные logins/URL отклоняются; profile URL строится с allowlisted host; COMMENTED не стирает APPROVED; incomplete reviews не получают appliesToMerge.
- `git diff --check`: без ошибок.

Одна первоначальная static probe ошибочно требовала BEGIN первым байтом файла 004 и остановилась на его комментарии; проверка исправлена пропуском SQL-комментариев и затем прошла. Это ошибка одноразовой проверки, не приложения.

## Открытые блокеры и недоказанное

1. **Ни один SQL-путь не исполнен.** Fresh install, legacy runtime upgrade, переходы v2/v3/v4→v5, повтор ledger, exact alias принятие/отказ, rollback, constraints/triggers, concurrent locks и persisted comparisons/remediations не получили DB proof. Нет доступной разрешённой disposable БД; контейнеры/production запрещены. Исторические отчёты не засчитывались как текущая проверка.
2. **Async executor probe не завершилась в окружении.** `timeout 8 ... responsibility_probe.py` дал 124; добавленный wait_for дал TimeoutError. Минимальный независимый `asyncio.run` + `run_in_executor(lambda: 'completed')` печатает `completed`, но тоже зависает на shutdown до timeout 124. Stack dump: event loop ждёт selector, executor worker в wait. Поэтому не объявляется пройденным async ContextVar/admission/cancellation/deadline поведение responsibility; чистые identity checks вынесены и прошли отдельно. В production-код по этому наблюдению правок не делалось.
3. **Tenant isolation доказана только чтением запросов и локальным ContextVar**, без двух настоящих tenant в БД/ASGI. В storage runtime запросы фильтруются workspace, server-wide методы явно предназначены scheduler/hooks и возвращают workspace. DB grants/RLS не проверялись; canonical stores допускают явный workspace для доверенного server caller, клиентская авторизация остаётся выше них.
4. **Migration markers не равны полному DDL-аудиту.** Ранние 001/002/004/005 markers проверяют наличие отдельных объектов; совпадающий ledger checksum не проверяет отсутствие schema drift. Alias 006 ограничен exact target bytes и набором guards. Текущую фактическую схему/ledger и тела функций без БД подтвердить нельзя.
5. **Canonical/runtime различаются по гарантиям.** Runtime findings/reports допускают update, runs не имеют FK на runtime artifact/revision; immutable canonical publish имеет отдельные guards. Это существующая compatibility boundary, не исправлявшаяся новой схемой в этом задании. Нельзя объявлять runtime rows immutable на основании canonical SQL.
6. **Полнота scanner scopes остаётся ограничением продукта.** Текущий `codefacts/publish.py` ставит expectedScopesVerified=false; canonical понижает coverage, comparison не должен объявлять такой run comparable. Проверено чтением и отдельной вычислительной пробой, без нового scanner run.
7. Telegram доставка, GitHub/GitLab file history/PR HTTP, живые export endpoints, DOCX визуальный рендер и внешние источники не запускались. Никакой успешной внешней интеграции отчёт не утверждает.

Схема upgrade не потребовалась; исторические миграции не редактировались. Изменены только разрешённые owning files и этот отчёт. Первичный обычный git commit не смог создать index.lock из-за read-only общего `.git`; повтор с разрешённой эскалацией завершился успешно, automatic approval отказа не было.
