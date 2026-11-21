# Полный аудит NEDRA: accounts / API / artifacts

Дата: 2026-11-22. Worktree: `audit-all-accounts-1122`, ветка `SoloveyMaks/audit-all-accounts-1122`.

Исходная exact база: `35d27d30ae44fd955824001e18eff895197c933c` (HEAD проверен до любых правок, рабочее дерево было чистым). Проверенный SHA кода после исправлений: `d9135667e859ee5bf55f4ce27ab6d07537f00f34`; следующий коммит добавляет только этот отчёт.

## Результат и границы

Исправлены подтверждённые ошибки cookie, переключения account → demo, обработки ZIP, сохранения относительных путей репозитория, канонизации webhook URL, изоляции журнала и некорректного текста регистрации. Локальные проверки прошли; **полная интеграционная проверка accounts/tenant/logout/disabled и durable webhook/watch не выполнена из-за отсутствия разрешённой PostgreSQL**. Это открытый блокер, не успешная приёмка всего backend.

Прочитаны все разрешённые API-файлы, `storage/accounts.py`, `storage/profile.py`, `artifacts/*`; для понимания scoping и pinning также `main.py`, `storage/database.py`, `engine/scheduler.py`, identity/store и migration 009. Исключённые API-файлы и Telegram/engine/database не изменялись. Старые отчёты использованы только как контекст, их результаты не засчитывались.

Не менялись production, пользовательский сервис 18080, БД, контейнеры, volumes, службы, сеть или прокси. Портов не открывалось; внешних provider/API запросов не было. Миграции не менялись. Моки БД или провайдеров не использовались.

## Дефекты: воспроизведение, причина, исправление, проверка

### 1. Некорректная подпись cookie вызывает исключение

До: `verify_session(valid_payload + '.é')` бросал `TypeError: comparing strings with non-ASCII characters is not supported`. Это выполняется до проверки подписи и доступно без авторизации.

Причина: `hmac.compare_digest` получал Unicode-строки. Исправление: сравнение байтов. После: `None`; настоящий локальный ASGI `GET /auth/me` с non-ASCII cookie отвечает **401**. Доступ к account DB не требовался. Отзыв v3 после logout этим не проверен.

Коммит: `fc5258d443231d29080a74e18e79e17c57f62ea4`.

### 2. Переход account → demo сообщает успех, но выдаёт непригодную сессию

До: вызов точного `create_session` с текущей account-сессией workspace A возвращал `authenticated=true` и demo-cookie для A; `verify_session(cookie)` возвращал `None`. Demo verifier принимает только настроенный demo workspace, а route брал workspace текущего аккаунта.

Исправление: demo login всегда использует `configured_workspace_id()` и сообщает тот же workspace. После: cookie валидна, workspace в ответе совпадает с проверенным токеном и настроенным demo workspace. Тело route извлекалось через AST из реального файла, без импорта singleton, который инициализирует БД; это проверка точной функции, **не всей middleware-цепочки**.

Коммит: `fc5258d443231d29080a74e18e79e17c57f62ea4`.

### 3. Повреждённый ZIP и конфликт file/directory доходят до HTTP 500

До: реальный ZIP с испорченным CRC бросал `BadZipFile`; архив с `src` как файлом и `src/A.kt` бросал `FileExistsError`. Upload route перехватывает их как общую ошибку 500, хотя это пользовательский ввод.

Исправление: заранее проверяются file/directory и ancestor-конфликты, неподдерживаемый codec; ошибки чтения ZIP переводятся в `SafeIntakeError` с удалением только собственной незавершённой распаковки. После: реальные CRC, parent collision, file/directory collision и codec 99 отклоняются `SafeIntakeError`. В HTTP route это уже существующая ветка **400**, но сам upload endpoint целиком без БД не запускался. Обычный настоящий `UploadFile` успешно прочитан потоково, распакован и записан в файловый store; содержимое `class A` проверено чтением файла.

Коммит: `4580db14257aa3ccaa00d076c78662e2dd9471cf`.

### 4. Нормализатор скрывает запрещённые компоненты и пропускает служебный каталог

До: `_normalize_entry_name('src/./A.kt')` возвращал `src/A.kt`, потому что `PurePosixPath` убирает `.` до проверки. `zipfile` обрезает NUL в `filename`, сохраняя полное имя только в `orig_filename`. `__MACOSX` сравнивался через casefold с набором, содержащим смешанный регистр, и не распознавался.

Исправление: проверка сегментов исходной строки и `orig_filename`, нижний регистр служебного имени в обоих intake/sanitizer. После: ZIP с `src/./A.kt`, NUL в исходном central/local header и `__MACOSX` отклоняется; provider sanitizer удаляет `__MACOSX`, сохраняя полезный `mobile/A.kt`. Проверены также traversal `..`, absolute/drive paths, backslash, повторный slash, symlink, case duplicates и лимит числа файлов (3 в отдельном процессе). Прямое чтение за пределами root до правки не доказано: исправляется фактическое нарушение заявленного контракта имён.

Коммит: `4580db14257aa3ccaa00d076c78662e2dd9471cf`.

### 5. Двойное снятие корня ломает repository-relative paths

До: настоящий provider ZIP `repo-123/mobile/build.gradle.kts`, `repo-123/mobile/src/A.kt` после sanitizer и исходного intake превращался в `build.gradle.kts`, `src/A.kt`. Потеря `mobile/` делает координаты findings несовместимыми с путями репозитория.

Исправление: repository import отключает эвристику снятия wrapper после provider sanitizer через keyword `strip_wrapper_root=False`; обычные пользовательские ZIP сохраняют прежнюю эвристику. После: реальная sanitize → intake → snapshot цепочка сохраняет **`mobile/build.gradle.kts`, `mobile/src/A.kt`**, а обычный `project/build.gradle.kts`, `project/src/A.kt` снимает только `project/`. Исходный intake воспроизводился из exact базы отдельным Python-модулем. Скачивание GitHub/GitLab не проверялось; SHA и архивный URL проверены локальными функциями, не удалённым содержимым.

Коммит: `4580db14257aa3ccaa00d076c78662e2dd9471cf`.

### 6. Webhook сравнивает URL по более слабым правилам, чем importer

До: URL с credentials, явным портом, query и fragment превращался hook parser в обычный `https://github.com/owner/repo`, хотя importer тот же URL запрещает. Поэтому signed payload мог сообщать другой адрес при совпавшем нормализованном пути.

Исправление: hook использует тот же строгий parser репозитория. После: восемь классов запрещённых URL отклоняются; нормальный HTTPS URL с `.git/` и разным регистром сравнивается корректно. Проба реальных HMAC-функций приняла правильную подпись и отвергла изменённое тело; проверены branch match/ignore, стабильные GitHub/GitLab delivery IDs и лимит тела push. Обход подписи не найден и не заявляется; durable idempotency без PG не доказана.

Коммит: `4580db14257aa3ccaa00d076c78662e2dd9471cf`.

### 7. Журнал тестовых webhook смешивает workspace и читает тело до лимита

До: реальный `webhook_test_receiver` записывал событие workspace A в глобальный массив, а точный `get_webhook_logs` для account admin B возвращал запись A. Проверено с синтетическим marker без пользовательских данных. Это подтверждённая утечка между workspace. Дополнительно route сначала делал `request.body()`, затем проверял лимит 64 KiB, позволяя целиком выделить память под слишком большое тело.

Исправление: запись содержит внутренний workspace, чтение фильтруется по текущему workspace и убирает служебный ключ. Payload проходит существующую маску значений; тело читается потоково и останавливается на лимите, заявленный большой Content-Length отклоняется сразу.

После: журнал A виден A, журнал B пуст, `workspace_id` не выдаётся; тело 65537 байт получает **413**. Проверялись точные route-функции с настоящими `Request` и потоками, без singleton DB/middleware. Для anonymous local receiver остаётся явный configured demo workspace, как предусмотрено текущим local-mode маршрутом.

Коммит: `75b474061fdf499fe8f7e98c75d0754ed5222d31`.

### 8. Admin overview неверно сообщает модель доступа

Код при любой сессии выдавал `shared=true`, `userAccounts=false`, несмотря на реализованные аккаунты и личные workspace. Исправление: `shared` и описание зависят от mode сессии, `userAccounts=true`. Это подтверждено чтением точного response builder; целый admin overview с реальными DB counts/vector не исполнялся, runtime-проверка остаётся за PG-блокером.

Коммиты: `75b474061fdf499fe8f7e98c75d0754ed5222d31`, `d9135667e859ee5bf55f4ce27ab6d07537f00f34` (описание модуля).

### 9. Некорректный Unicode при регистрации вызывает 500

До: `register_user` с допустимой длиной пароля и lone surrogate бросал `UnicodeEncodeError` при scrypt, ещё до БД. Email с NUL и displayName с surrogate также не проверялись перед передачей драйверу.

Исправление: проверка UTF-8 и NUL в email, пароле и непустом имени, с `AccountError`. После: локальный настоящий ASGI `/auth/register` на трёх JSON bodies отвечает **400**. Отдельный настоящий scrypt дал разные соли, принял правильный пароль и отверг неправильный. HTTPX по умолчанию сам не сериализует lone surrogate: для корректной передачи негативного запроса использован ASCII JSON с escape; первоначальная ошибка клиента не засчитывалась как результат приложения.

Коммит: `d9135667e859ee5bf55f4ce27ab6d07537f00f34`.

## Команды и фактические результаты

Все временные скрипты/артефакты: `/tmp/nedra-audit-accounts/task-032c129a/`. Использован уже существующий `/tmp/nedra-venv/bin/python`, Python 3.14, FastAPI 0.165.0, Pydantic 2.14.4. Системный `python` не содержит FastAPI; ничего не устанавливалось.

```bash
git rev-parse HEAD
git status --short
command -v orca python uv node psql postgres initdb
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/baseline.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/deeper.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/verify.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/extra.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/auth-edge.py
git diff --check
```

- `verify.py`: **49 checks passed**, exit 0; отрицательные пути ZIP, настоящая распаковка и файловые snapshots, parser/HMAC/session, точные route bodies и ограниченный ASGI auth/profile. `verified.json` содержит список.
- `extra.py`: **5 дополнительных checks**, exit 0; unsupported compression, реальный streaming UploadFile, workspace sidecar и provider filter.
- `auth-edge.py`: non-ASCII cookie **401**, password/name/email с недопустимым текстом **400/400/400**, exit 0.
- AST parsing изменённых Python файлов и `git diff --check`: успешны.
- Первый sandboxed ASGI запуск завис на AnyIO worker wakeup; traceback через 10 секунд показал idle worker/asyncio selector. Тот же ограниченный локальный скрипт, разрешённый auto-review вне sandbox, завершился за 0.26 с. Сокеты/порты и сеть пробам не нужны.
- `import backend.app.api.router` при намеренно отсутствующем DSN: **exit 2**, `DatabaseUnavailable: DATABASE_URL не задан`. Это фактическое подтверждение блокера полного приложения, не зелёная интеграция.
- Обычный git commit сначала встретил read-only служебную `.git` worktree; разрешённая эскалация записала коммиты только выданной ветки. Push/merge не выполнялись.

## Что проверено только чтением и что осталось

`DatabaseRepository` берёт workspace из ContextVar, методы workflow/artifact/run/findings/report/revision фильтруют workspace; profile history/summary строят параметризованные запросы со workspace сессии. V3 verifier сверяет подпись с серверной строкой; `load_session` отвергает revoked/disabled/expired, logout вызывает серверный revoke. Watch API использует `require_workspace_actor`; hook после поиска единственного opaque watch id входит в его workspace; inbox SQL имеет уникальность delivery, commit ledger — watch+SHA; importer строит download URL по валидированному SHA. Это **статический охват текущей базы**, не повторение realPG результатов из старых отчётов.

Открытые блокеры:

1. Нет разрешённой disposable PG/DSN и локальных `psql/postgres/initdb`. Не выполнены register/login/logout/replay/disabled с реальными строками, конкурентная tenant isolation, SQL history filters, full middleware, durable webhook replay/watch claims и scheduler dispatch. Координатору отправлена escalation `msg_c32e88c7a8eb`. Запрещённый `podman start nedra-mini-integration-pg` и любые обходы не предпринимались.
2. Удалённые GitHub/GitLab не вызывались: скачивание pinned archive, provider API и установка hooks не доказаны. Проверены только настоящие локальные части импорта/подписей/канонизации.
3. Demo v1/v2 остаются stateless: logout удаляет cookie, но копию demo-токена нельзя отозвать до TTL. Это существующее явное ограничение demo-модели; безопасное полноценное серверное аннулирование требует отдельного решения о хранении demo sessions, его не добавлял в рамках точечных правок.

Готовы четыре локальных коммита исправлений и этот отчёт. Для полной приёмки нужен отдельный разрешённый PG-прогон; текущее завершение worker не должно представляться как прохождение непроверенной интеграции.
