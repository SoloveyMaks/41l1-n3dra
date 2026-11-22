# Полный аудит NEDRA: accounts / API / artifacts

Дата: 2026-11-22. Worktree: `audit-all-accounts-1122`, ветка `SoloveyMaks/audit-all-accounts-1122`.

Исходная exact база: `35d27d30ae44fd955824001e18eff895197c933c` (HEAD проверен до любых правок, рабочее дерево было чистым). Первый проверенный SHA кода: `d9135667e859ee5bf55f4ce27ab6d07537f00f34`; после дополнительного поручения координатора проверенный SHA кода: `d0d07ef` (полный SHA указан ниже). Следующий коммит изменяет только этот отчёт.

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

В первом проходе готовы четыре локальных коммита исправлений и отчёт; дополнительный проход описан ниже. Для полной приёмки нужен отдельный разрешённый PG-прогон; текущее завершение worker не должно представляться как прохождение непроверенной интеграции.

## Дополнительный проход по поручению координатора

Сообщение `msg_f0109d923f5e` подтвердило отсутствие разрешённой disposable PostgreSQL у координатора и поручило продолжить доступные проверки. База продолжения — `9550ebe240c2e2fe2227430754e353d08431bf2d`, проверенный SHA кода после новых правок — **`d0d07efdcea6696d07a7ba1467bdde5d5f9f123d`**. PG-блокер сохраняется без обходов.

### 10. Fallback старого артефакта обходит ограничения intake

Вызов настоящего `build_snapshot_from_directory` при `CG_MAX_FILE_COUNT=3`, `CG_MAX_UNCOMPRESSED_BYTES=6` до правки успешно создал manifest с **4 файлами и 12 байтами**. Fallback вызывается из `ensure_artifact_meta`, поэтому отсутствие sidecar позволяло пути чтения уже распакованного артефакта обходить лимиты ZIP intake. Кроме того, `read_bytes()` выделял память под весь файл до проверки индивидуального размера; аналогичный порядок был в sample packer.

Исправление: fallback ограничивает count/total до накопления manifest и читает максимум разрешённый остаток + 1 байт, проверяет относительные имена тем же валидатором. Sample packer также ограничивает чтение и проверяет count до чтения следующего файла.

После: `fallback-after.py` на реальных каталогах отклонил превышение count/total/file/path; допустимый каталог дал **2 файла / 5 байт**, sample дал настоящий ZIP **199 байт**. Sparse source с логическим размером **1 ГиБ** оба пути отвергли без полного чтения. Это файловая проверка, а не PG-backed восстановление sidecar через endpoint.

Коммит: `745fa476d1538331e8a8136e4a03d86bf8ffac64` — полное значение доступно в git history этой ветки, сообщение «Недра: соблюдать лимиты при чтении каталогов артефактов».

### 11. Некорректный Unicode в кодах доступа вызывает исключение

До: каждая из `verify_registration_code`, `verify_access_code`, `verify_admin_code` бросала `UnicodeEncodeError` на lone surrogate. Исправление: общий byte-comparison helper отклоняет недопустимый UTF-8 как неверный код.

После: прямые настоящие функции отвергли surrogate и неверный код, приняли правильный; `codes-asgi.py` передал ASCII JSON с escape через локальный ASGI и получил **403** на invite регистрации, **401** на user/admin login, **200** на правильный demo-код. Account endpoint подключён настоящей фабрикой; session route извлечён из точного AST без DB singleton и middleware.

Коммит: `d0d07efdcea6696d07a7ba1467bdde5d5f9f123d`.

### 12. Активная сессия сохраняет старую роль и workspace после изменения аккаунта

Причина доказана текущим SELECT: `load_session` соединял аккаунт только по user_id и возвращал `s.role`/`s.workspace_id`, не сравнивая с актуальными `u.role`/`u.workspace_id`. Старая admin-сессия поэтому сохраняла административные права после demotion, а перенос workspace не отзывал доступ к старому workspace.

Исправление: SELECT требует равенства `s.role = u.role` и `s.workspace_id = u.workspace_id`; несовпавшая сессия считается отсутствующей. Таблицы и миграции не менялись.

Проверка после: **точные исходный и изменённый SELECT**, извлечённые из исходников, исполнены на настоящем SQLite SQL engine с attached `account` и синтетическими строками: current → **1/1** строка до/после, demoted → **1/0**, moved → **1/0**. SQL-параметр `%s` заменён на SQLite `?`, логика запроса не подменялась. Это доказывает семантику новых predicates, **не PostgreSQL, не драйверные timestamps и не полный session lifecycle**; PG-прогон demotion/move обязательный.

Коммит: `d0d07efdcea6696d07a7ba1467bdde5d5f9f123d`.

### Команды продолжения и фактические результаты

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/fallback-before.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/fallback-after.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/session-query.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/codes-asgi.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD /tmp/nedra-venv/bin/python /tmp/nedra-audit-accounts/task-032c129a/verify.py
git diff --check
```

Повторный `verify.py` после новых правок: **49 checks passed**, exit 0. `codes-asgi.py`: **403/401/401/200**, exit 0. Fallback и SQL пробы: результаты выше, exit 0. Все временные данные в прежней уникальной папке `/tmp/nedra-audit-accounts/task-032c129a/`.

### Точная очередь последующей PostgreSQL/ASGI проверки

Это **невыполненные** сценарии; приёмка по ним остаётся открытой:

1. Настоящие register двух пользователей A/B: разные workspace, hash в БД, v3 cookie; повторный email, valid/invalid login, invite. Полный `main:app` с реальным middleware и разрешённой disposable PG.
2. Logout A: cookie стирается, `revoked_at` записан, replay сохранённой cookie даёт 401; B остаётся действующим. Отдельно оба logout route.
3. Disabled/delete/expired/revoked A: `/session`, `/auth/me`, `/profile`, списки и мутации перестают работать под старой v3; B не затронут. Граница состояния, изменённого во время запроса, требует отдельного concurrency прогона.
4. Demotion admin A → user и перенос workspace A → C: прежняя cookie не даёт admin/старый workspace, новый login берёт актуальные роль/workspace. Проверить новый `load_session` именно psycopg/PG, включая role override, если его использует будущий вызывающий.
5. Tenant A/B: чужие artifact/workflow/run/revision/nodes/findings/map/evidence/report/export/events дают 404; чужой artifact не выбирается для собственного запуска. Реальный selected artifact и immutable revision соответствуют исполняемому draft, фоновая задача сохраняет workspace.
6. Параллельные A/B запросы и background jobs не смешивают ContextVar; после ответа scope сброшен; account → demo реально переключает дальнейший запрос на configured demo workspace.
7. Profile history: pagination/count/options/summary согласованы с SQL snapshot; project/workflow/status/date filters применяются до пагинации; чужих строк нет, пустой результат честный. Invalid timezone/range отклоняются до БД.
8. Account user/admin может управлять только своим schedule/watch; demo user получает 403; чужие watch setup/check/patch дают 404, секреты не выдаются. Admin overview корректен для personal/demo и настоящих DB counts.
9. Подписанный push сохраняет inbox в workspace найденного watch до 202; replay одной delivery и одного commit при разных delivery не создаёт второй run; disabled/rotated nonce отклоняет старые события, lease/recovery/dispatch SQL сохраняют идемпотентность после рестарта и конкуренции.
10. Upload через полный endpoint сохраняет runtime artifact и файловый sidecar в одном workspace; CRC/path/codec получают 400, oversized body — согласованный отказ, при отказе persistence нет бесхозной успешной записи. Для repository provider download/remote hook отдельно нужны разрешённые провайдеры; локальные parser/HMAC пробы не заменяют этот этап.
