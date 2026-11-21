# Полный аудит Mini API — ограниченная локальная проверка

Дата: 2026-11-22, Asia/Tokyo. Область: `backend/app/telegram/miniapp.py` и `miniapp_auth.py`; документация контракта — `docs/telegram-miniapp.md`.

Исходная точная база: `35d27d30ae44fd955824001e18eff895197c933c` (`dev`). Проверенное состояние кода: `0c4488afc3307bca1035e20bb6861bc4fb39fa5a` поверх `69beddd27bd8d99c4b0f0d015aa91b339c1f61db`, worktree `audit-all-miniapi-1122`, ветка `SoloveyMaks/audit-all-miniapi-1122`. Отчёт и `docs/telegram-miniapp.md` закоммичены отдельно после этого SHA; итоговый HEAD указан в `worker_done`.

**Вывод:** восемь подтверждённых проблем исправлены. Локальные пробы ниже прошли на реальном router/auth/exporter/executor. Полная интеграция root app + PostgreSQL + реальный импорт/фронт остаётся непроверенной: доступ к БД и контейнеру запрещён ограничением задачи. Известная остаточная зависимость — `backend/tests/test_telegram_miniapp.py` всё ещё задаёт `TELEGRAM_ENABLED=0` для HTTP-класса; это файл вне моей области, исправление не внесено (см. блокеры).

## Охват и границы

Прочитаны оба модуля целиком, owning-код middleware Origin/session/rate limits, workspace ContextVar, executor и его semaphore, router lifespan, exporter, `accounts.get_user`, storage-контракт Telegram-владельца из `audit-all-telegram-1122` на `a6ed0de42c781dbc84c949c787bca2ceac9b785b`, текущие `frontend/src/miniapp/api.ts`, `status.ts`, `MiniApp.tsx` и `frontend/src/types/index.ts`.

Проверены auth/bind/bootstrap/settings/start/detail/report, подпись и срок bearer/initData, scoped bearer, Origin, отзыв по epoch, disabled bot при explicit secret, чужие данные, ошибочные входы, admission pending+running, отмена/остановка задач и восстановление мест после ошибок.

Пробы запускают настоящие `MiniAppAuth`, HMAC/токены, FastAPI router через `httpx.ASGITransport`, `request_origin_allowed`, `WorkflowExecutor.execute_run` и его `asyncio.Semaphore`, `load_export_bundle`/`build_jsonc` и lifespan-merge FastAPI. PostgreSQL, `TelegramRepository` и `DatabaseRepository` в HTTP-пробах заменены локальными объектами с точными сигнатурами: без разрешённой БД это доказывает проводку и обработчики, но не SQL-изоляцию. `checks.py` печатает `all bounded checks passed; no PostgreSQL/remote integration claimed`.

Исторический `/tmp/nedra-mini-integration-review.md` прочитан только как контекст; его DB-пробы не повторялись. `podman start nedra-mini-integration-pg` не повторялся и не обходился. Внешних API-запросов, портов, изменений production/18080, контейнеров, volumes, служб, пользовательских конфигов и секретов не было. Новых тестовых файлов в проект не добавлено.

## 1. Приём запусков не ограничивал pending+running; отмена оставляла queued

**Воспроизведение:** в локальном ASGI router удержаны оба permit настоящего executor semaphore. До правки шесть последовательных POST `/runs` дали `[202,202,202,202,202,202]`, создали 6 импортов/строк и 6 ожидающих фоновых задач при Mini capacity=2. После отмены все 6 строк оставались `queued`.

**Причина:** semaphore охватывал подготовку запроса и освобождался сразу после `asyncio.create_task`; executor имел отдельную очередь. Задачи не хранились владельцем и не отменялись lifespan shutdown. Executor не обрабатывает `CancelledError` перед/внутри своего semaphore как терминальный статус.

**Исправление:** router admission-очередь без ожидающих permit. Свободное место забирается `get_nowait`; переполнение сразу даёт `429 RUN_CAPACITY_REACHED` + `Retry-After: 5` до settings/workflow/import. Одна отслеживаемая задача владеет местом от подготовки до завершения executor, включая ожидание его semaphore. HTTP `202` отдаётся только после создания строки run и повторной проверки живой привязки. Done callback освобождает место ровно один раз.

Ошибки/отмена завершают ещё `queued`/`running` run как `failed`/`cancelled` с `inconclusive` и `finished_at`; уже терминальный статус не перезаписывается. При сбое cleanup router перестаёт принимать новые запуски (`503`), чтобы не копить неизвестные pending-строки. Импорт/intake и DB-потоки shielded: отмена удерживает место до реального завершения I/O, затем выполняется cleanup; поздний `create_run` после отмены не появляется.

**После:** `[202,202,429,429,429,429]`, ровно 2 импорта/строки/задачи. Lifespan shutdown даёт `[cancelled,cancelled]`, фоновых задач 0, следующий POST — `503`. Четыре ошибки импорта — четыре `502`; три ошибки `create_run` — три `500`; затем пять запусков проходят `202` и завершаются `succeeded` через управляемый исполнитель. Неожиданное исключение executor после `202` даёт `failed`. Отмена во время заблокированного DB-потока, включая повторную отмену задачи, оставляет первую строку `cancelled`; следующий запуск проходит. Сбой cleanup даёт следующий `503`.

**Ограничения:** capacity действует на router/process, не является распределённым лимитом нескольких процессов. Graceful shutdown проверен локально; SIGKILL/падение процесса не выполняет lifespan и может оставить строки `queued`/`running`. Durable crash recovery Mini API не реализован в принадлежащих области файлах; scheduler recovery относится к schedule/repository-watch. Shielded I/O при зависшем импорте может задержать shutdown; импорт ограничен своими таймаутами.

## 2. Явный секрет bearer обходил disabled/invalid bot config

**Воспроизведение:** `MiniAppAuth(..., token_secret=...)` возвращал `_secret()` при token provider, который всегда бросает `MiniConfigError`; вызовов provider было 0. Production `_load_bot_token_from_settings` не проверял `TELEGRAM_ENABLED`, а `assert_live` не читал конфигурацию бота.

**Исправление:** `_secret` всегда сначала проверяет bot provider. Default provider требует `TELEGRAM_ENABLED` из того же набора, что root lifespan (`1/true/yes`), затем читает и валидирует owning `load_bot_settings`. `assert_live` делает ту же проверку перед повторной проверкой binding/account. Это дополнение к active account/workspace/epoch, а не замена.

**После:** provider вызван 1 раз и ошибка отвергнута. При `TELEGRAM_ENABLED=0` default service отклоняет `_secret` и `assert_live`; настоящий loader при enabled=1 принимает синтетическую корректную конфигурацию и отклоняет невалидный формат token. Через HTTP: `POST /auth` с explicit secret при выключенном боте возвращает `503 TELEGRAM_MINIAPP_UNAVAILABLE`, bearer bootstrap — `503`.

**Ограничения/контракт:** `Binding.binding_epoch` остаётся положительным int, `get_binding(chat_id)` сохраняется; durable non-reused поколения делает Telegram/storage-владелец (sequence в его ветке). Его код в этот checkout не переносился; delete/rebind на реальной БД не проверен. Active account проверяется `accounts.get_user`; SQL disabled/workspace guards — в storage-владельце.

## 3. Некорректный Unicode bearer давал внутреннее исключение

**Воспроизведение:** `verify_mini_token` с lone-surrogate строкой и `v1.é.YQ` бросал `UnicodeEncodeError` до controlled `MiniTokenError`; HTTP-слой классифицировал это как `AUTH_UNAVAILABLE` вместо невалидного токена.

**Исправление:** ограничение длины до обработки и явная ASCII base64url проверка обеих подписанных частей до ASCII encode/HMAC. Для допустимого bearer число символов равно числу байтов, лимит 4096 сохраняется.

**После:** оба входа дают `MiniTokenError`; действующий token проходит, истёкший/изменённый отвергаются. Роль `user`, TTL≤900 и типы claims сохраняются.

**Ограничение:** это прямая проверка parser, не утверждение о том, какие именно некорректные Unicode-значения пропустит реальный HTTP-сервер.

## 4. Лимит JSON срабатывал после полной буферизации тела

**Воспроизведение:** реальный Starlette Request, 100 chunk по 1024 байта, лимит 2048: исходный `_json_body` читал все 100 chunk перед `413`.

**Исправление:** чтение `request.stream()` с проверкой размера перед добавлением очередного chunk в ограниченный `bytearray`; ранняя проверка `Content-Length` сохранена. Глубоко вложенный JSON переводится из `RecursionError` в `INVALID_JSON`/400.

**После:** тот же `413` после 3-го chunk, остаток тела handler не читает; JSON глубиной 1500 возвращает `400`.

**Ограничение:** transport/middleware могут иметь собственную буферизацию; это изменение её не контролирует.

## 5. Сбой зависимости /auth маскировался под неверный initData

**Воспроизведение:** синтетический backend authenticator бросает `RuntimeError`: исходный `/auth` возвращает `401 INVALID_INIT_DATA`, хотя это не ошибка подписи пользователя.

**Исправление:** `InitDataError` отдельно остаётся `401`; непредвиденный сбой зависимости на auth/проверке bind — `503 AUTH_UNAVAILABLE` с общим текстом. Лог содержит только тип исключения, не raw token/initData/code/driver text.

**После:** тот же `/auth` возвращает `503 AUTH_UNAVAILABLE`, исходный marker отсутствует в ответе. Невалидные подписи по-прежнему отвергаются чистой проверкой initData.

**Ограничение:** реальный PostgreSQL outage не создавался; исключение зависимости намеренно синтетическое.

## 6. Счётчики Mini API не совпадали с текущим фронтом

**Воспроизведение:** `_run_summary` для строки `total=3, critical_count=1, high_count=2` отдавал `critical_findings`/`high_findings`, а `critical_count`/`high_count` отсутствовали. Текущий `frontend/src/miniapp/status.ts:95-97` читает `critical_count`, `high_count`, `medium_count`; frontend `RunSummary` требует также `low_count`, `info_count`.

**Исправление:** явная безопасная проекция пяти severity `_count` вместе с прежним backend `RunSummary`; старые поля сохранены для совместимости. Frontend не изменялся.

**После:** строка `total=6, critical=1, high=2, medium=3` даёт все ожидаемые `_count`; соответствующие `_findings` совпадают. Bootstrap/detail HTTP-проба с настоящей проекцией проходит.

**Ограничение:** браузер/Telegram WebView не запускался; доказаны контракт и данные обработчика.

## 7. Bearer оставался формата v1; legacy-токены принимались

**Воспроизведение:** модуль из `0c4488afc3^` (то есть `69beddd`) подписывал и принимал `v1.<payload>.<sig>` с payload `"v":1`. В `reconcile.py` старый `verify_mini_token` принял сконструированный v1-токен; текущий отвергает. На HTTP v1-токен с валидной HMAC возвращает `401 INVALID_TOKEN`.

**Исправление:** `TOKEN_PREFIX="v2"`, payload `"v":2`, HMAC считается по `v2.<base64url(payload)>`; `verify_mini_token` требует и префикс, и payload-версию, иначе `MiniTokenError`. Derived-секрет доменно отделён меткой `nedra-miniapp-token-v2`; явный `TELEGRAM_MINIAPP_TOKEN_SECRET` не менялся. Старые v1-клиенты получают 401 и должны заново вызвать `/auth`; frontend это уже делает при 401.

**После:** `identity_v2.py`: `prefix=v2`, `payload_version=2`, legacy v1 → `401 INVALID_TOKEN`; действующий v2-токен проходит bootstrap. `checks.py`: 4 негативных token-случая отвергнуты, TTL≤900, роль user. `unittest` InitDataTests+TokenTests: 11/11 OK.

**Ограничение:** это намеренный cutover: все ранее выданные v1 bearer перестают работать сразу. Store/сессий перевыпуска нет, клиент получает новый токен через `/auth`.

## 8. `get_settings` читался без идентичности и мог отдать настройки нового владельца

**Воспроизведение:** в `69beddd` оба чтения (`bootstrap` и `start_run`) вызывали `repo.get_settings(identity.chat_id)`. `reconcile.py` с базовым router: после симуляции rebind между `authorize` и чтением bootstrap вернул `scenario="new-owner-secret-scenario"` нового владельца.

**Причина:** token identity проверялась, но SQL-чтение `telegram.settings` шло только по `chat_id`; между проверкой и чтением чат мог быть перепривязан.

**Исправление:** оба чтения передают полную тройку `user_id`, `workspace_id`, `binding_epoch` в согласованный `TelegramRepository.get_settings(chat_id, *, user_id, workspace_id, binding_epoch)` (ветка Telegram-владельца `a6ed0de`). При несовпадении storage возвращает дефолты, а финальный `assert_live` завершает запрос `401 TOKEN_REVOKED`; строка нового владельца не читается.

**После:** тот же `reconcile.py` на текущем router вернул `scenario=None` и записал вызов ровно `(old_user, old_workspace, 7)`; `start_run` без `scenarioId` получил `409 SCENARIO_REQUIRED` до `get_workflow`/импорта. `identity_v2.py` подтверждает HTTP-путь. Сигнатура `a6ed0de` проверена `inspect.signature`: `(self, chat_id, *, user_id=None, workspace_id=None, binding_epoch=None) -> Settings`; `Binding.binding_epoch` присутствует.

**Ограничение:** реальный SQL и race с настоящим rebind не исполнены без PostgreSQL. Интеграция обязана включить storage-коммит `a6ed0de` (или эквивалент); на старом storage вызов с kwargs даст `TypeError`, поэтому cutover этих веток должен быть атомарным.

## Соответствие текущему фронту

- `frontend/src/miniapp/api.ts` строит пути от `${API_BASE}/telegram/mini`; auth/bind/bootstrap/settings/runs/detail/report совпадают с `MINI_PREFIX`.
- `MiniBootstrap`, `MiniRunDetail`, `MiniSettingsPatch`, `MiniRunInput`, `MiniReport` соответствуют ответам: `{user,settings,scenarios,runs}`, `{run,findings}`, `{deliveryMode,scenario}`, `{runId}` (202), blob + `Content-Disposition`.
- `status.ts` использует `critical_count/high_count/medium_count/low_count/info_count` — теперь отдаются (finding 6). `execution_status`/`security_verdict` — строки backend-модели.
- `MiniApp.tsx` при 401 переавторизуется, при 409 `TELEGRAM_NOT_BOUND` показывает ввод кода, `scenarioId` передаёт всегда; backend принимает и отсутствие.
- Frontend не изменялся. Реальный браузер/Telegram WebView не запускался.

## Другие проверенные границы

- bind/auth: `200/200`, ответ ровно `{token,expiresAt}`, срок ≤900 секунд; bootstrap `200` с `{user,settings,scenarios,runs}`.
- settings: `alerts` + `scenario=null` сохраняются; foreign scenario — `400 SCENARIO_NOT_FOUND` до импорта; ownership-guard SQL прочитан.
- detail/report: `200/200`, JSONC через настоящие `load_export_bundle`/`build_jsonc`, report `no-store`; missing/foreign fixture — `404/404`. Реальная cross-workspace SQL-изоляция не доказана; owning-методы имеют `workspace_id` predicate.
- Cookie без Authorization — `401`; Mini bearer отвергается обычным `verify_session`. Токен не является кабинетной сессией.
- Изменение epoch — `401 TOKEN_REVOKED`; inactive synthetic account — `401`; account/binding workspace mismatch — `401`.
- `request_origin_allowed`: no Origin / same Origin → true; foreign / null → false. Root middleware прочитан: Origin и IP rate limit выполняются до Mini exemption, Mini-ответы получают `no-store`, cookie не создаётся. Root middleware не запускался из-за DB-инициализации; real reverse proxy не проверен.
- 4 негативных token-случая и 4 initData-случая (duplicate, tamper, expired, future) отвергнуты; правильная подпись/identity принята.
- Lifespan: FastAPI 0.165 `include_router` сливает `APIRouter.lifespan` с root; shutdown отменяет Mini-задачи до закрытия DB pool.

## Команды и фактические результаты

Доступны `/usr/bin/python` (3.14.7), `/tmp/nedra-venv/bin/python` (FastAPI 0.165.0, httpx 0.28.1, Pydantic 2.14.4), git, rg, orca. Зависимости не устанавливались. Все одноразовые данные/исходники/логи — только под `/tmp/nedra-audit-miniapi/`.

```sh
CG_MAX_CONCURRENT_RUNS=2 CG_MINIAPP_MAX_CONCURRENT_RUNS=2 \
CG_DATA_DIR=/tmp/nedra-audit-miniapi/data PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
/tmp/nedra-venv/bin/python -u /tmp/nedra-audit-miniapi/checks.py

CG_DATA_DIR=/tmp/nedra-audit-miniapi/data PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
/tmp/nedra-venv/bin/python -u /tmp/nedra-audit-miniapi/surface.py

CG_DATA_DIR=/tmp/nedra-audit-miniapi/data PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
/tmp/nedra-venv/bin/python -u /tmp/nedra-audit-miniapi/identity_v2.py

CG_DATA_DIR=/tmp/nedra-audit-miniapi/data PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
/tmp/nedra-venv/bin/python -u /tmp/nedra-audit-miniapi/reconcile.py

CG_MAX_CONCURRENT_RUNS=2 CG_MINIAPP_MAX_CONCURRENT_RUNS=2 \
CG_DATA_DIR=/tmp/nedra-audit-miniapi/data PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
/tmp/nedra-venv/bin/python -u /tmp/nedra-audit-miniapi/final_checks.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" /tmp/nedra-venv/bin/python -m unittest \
backend.tests.test_telegram_miniapp.InitDataTests backend.tests.test_telegram_miniapp.TokenTests

git diff --check
```

Фактические результаты: `checks.py` — все assertions, включая `[202,202,429,429,429,429]`, `[cancelled,cancelled]`, 502/500 recovery, `disabled_bot_operations=2`, origins `[true,true,false,false]`; `surface.py` — bind/auth/bootstrap/detail/report 200, foreign 400/404, epoch/inactive/cookie 401, `disabled_bot_explicit_secret=503`, `disabled_bot_auth=503`; `identity_v2.py` — `v2/2`, legacy v1 401, scoped settings default; `reconcile.py` — до: `new-owner-secret-scenario`, после: `null`, вызов `(old_user,old_workspace,7)`, контракт `a6ed0de` подтверждён; `final_checks.py` — counts, deep JSON 400, cleanup failure → 503, scoped bearer не сессия. `unittest`: 11/11 OK. Все скрипты exit 0; `git diff --check` чистый.

## Открытые блокеры и зависимости

1. **PostgreSQL/root app acceptance.** Запрещённый `podman start nedra-mini-integration-pg` не повторялся и не обходился; старые DB-пробы, другой контейнер/сервер/production DB не использовались. Нужен отдельно разрешённый стенд для real root app + PostgreSQL + merged `telegram.binding_epoch_seq` migration и disabled guards. На нём повторить auth/bind/bootstrap/settings → настоящий intake/executor → detail/report; saturated executor → 2 accepted + immediate 429; cancel/shutdown → terminal rows; unbind/rebind старого token → 401; disabled account/bot → отказ.
2. **`backend/tests/test_telegram_miniapp.py` (вне моей области, не менялся).** `MiniAppHttpTests.setUpClass` (`:203`) ставит `TELEGRAM_ENABLED="0"` и ожидает `200` от `/auth`; после обязательной проверки bot enabled это `503 TELEGRAM_MINIAPP_UNAVAILABLE` (подтверждено HTTP-пробой). Нужно поставить `1` в этом классе (ASGITransport не запускает lifespan/бот) и обновить `_sign_claims` (`:144-148`) на v2, иначе role/TTL-кейсы проходят только потому, что v1 отвергается. `RunStartTests` использует explicit provider и не затронут.
3. **Storage cutover.** `get_settings` с identity kwargs существует в `audit-all-telegram-1122` (`a6ed0de`, коммит `b46823b`); без него текущие вызовы дадут `TypeError`. Sequence-миграция — зависимость data-владельца; координатор сообщил, что номер `013` переносится на `016` из-за занятого `013_custom_checks`. В этом checkout миграции не менялись. Отчёт Telegram-владельца на `a6ed0de` фиксирует Mini API на `69beddd`; его пункты про token `v1` и unscoped `get_settings` к текущему `0c4488afc3` уже не относятся.
4. **Engine cancellation.** Mini API сам финализирует отменённые `queued`/`running` runs. `WorkflowExecutor.execute_run` ловит `Exception`, но не `CancelledError`; ручные/scheduler-пути вне Mini API зависят от engine-владельца.
5. **Crash recovery/глобальная ёмкость.** SIGKILL не выполняет lifespan; process admission не является распределённым. Это эксплуатационные ограничения, не обещание текущего кода.
6. **Документация.** `docs/telegram-miniapp.md` обновлён в этом worktree: v2/legacy cutover, admission pending+running, 429/503, `TELEGRAM_ENABLED` gate, scoped settings read. `docs/telegram-bot.md` не менялся.

## Коммиты

- `926f114ec95c2c486c83156ef8db1d76bf548aec` — disabled/invalid bot и Unicode bearer.
- `425889def673e7bc315f1c734ced94566d505e2c` — bounded admission и завершение отменённых запусков.
- `69beddd27bd8d99c4b0f0d015aa91b339c1f61db` — сохранение admission при отмене I/O и счётчики фронта.
- `0c4488afc3307bca1035e20bb6861bc4fb39fa5a` — bearer v2/payload2, отказ v1, identity-scoped `get_settings`.
- Отчёт и `docs/telegram-miniapp.md` — отдельный коммит после `0c4488afc3`.

Чужие модули и ветки не менялись; push/merge не выполнялись.