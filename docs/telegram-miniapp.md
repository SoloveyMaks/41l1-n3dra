# Telegram Mini App: backend-контракт

Mini App — отдельная узкая поверхность `/api/telegram/mini/*`. Она **не**
использует cookie-сессию кабинета и не выдаёт её. Вход — только проверенный
на сервере Telegram `initData`, после которого клиент получает короткоживущий
Bearer-токен. Токен привязан к реальной привязке Telegram и к workspace
аккаунта; каждый запрос заново читает привязку и активный аккаунт.

Фронтенд открывается по `https://nedra.soloveyka.bond/miniapp` (бот берёт URL
из `TELEGRAM_MINI_APP_URL`, иначе использует этот адрес).

## Что реализовано

| Метод | Путь | Ответ |
|---|---|---|
| `POST` | `/api/telegram/mini/auth` | `{token, expiresAt}` |
| `GET` | `/api/telegram/mini/bootstrap` | `{user, settings, scenarios, runs}` |
| `PUT` | `/api/telegram/mini/settings` | `{deliveryMode, scenario}` |
| `POST` | `/api/telegram/mini/runs` | `{runId}` (HTTP 202) |
| `GET` | `/api/telegram/mini/runs/{run_id}` | `{run, findings}` |
| `GET` | `/api/telegram/mini/runs/{run_id}/report.jsonc` | сохранённый отчёт |

`/auth` — единственный публичный маршрут. Все остальные требуют
`Authorization: Bearer <token>`.

### `POST /auth`

```json
{"initData": "<строка из Telegram.WebApp.initData>"}
```

Успех:

```json
{"token": "v1.<payload>.<signature>", "expiresAt": 1795297200}
```

`expiresAt` — UNIX-время в секундах, не более чем через 15 минут. Токен не
является сессией аккаунта: роль в нём всегда `user`, cookie-сессию он не
заменяет и админ-прав не даёт.

Ошибки:

- `401 INVALID_INIT_DATA` — подпись не сошлась, данные просрочены/из будущего,
  дубли ключей, битый `user`/`hash`, слишком большой или не-приватный чат;
- `409 TELEGRAM_NOT_BOUND` — Telegram-пользователь подтверждён, но привязки
  к аккаунту нет (или аккаунт отключён / workspace не совпадает). Автопривязки
  и создания аккаунта нет: сначала веб-кабинет → Telegram → код → `/start`;
- `503 TELEGRAM_MINIAPP_UNAVAILABLE` — не настроен бот/секрет.

### `GET /bootstrap`

```json
{
  "user": {"displayName": "Иван"},
  "settings": {"deliveryMode": "both", "scenario": "wf_secure_pr_gate"},
  "scenarios": [{"id": "wf_secure_pr_gate", "name": "Защита PR"}],
  "runs": [{"id": "run_...", "execution_status": "succeeded", "...": "..."}]
}
```

`runs` — последние 20 запусков **только текущего workspace**, в форме общего
`RunSummary`. Наружу не отдаются chat id, email, workspace id и секреты.
`scenario` — `null`, если сценарий не выбран.

### `PUT /settings`

Тело: `{"deliveryMode": "alerts|jsonc|both", "scenario": "wf_..."}`; оба поля
необязательны. `scenario: null` очищает выбранный сценарий. Сценарий обязан
существовать в workspace привязки. Запись идёт с проверкой `binding_epoch`:
если чат успели перепривязать, ответ `409 TELEGRAM_REBOUND`, чужие настройки
не меняются.

### `POST /runs`

```json
{"repositoryUrl": "https://github.com/owner/repo", "scenarioId": "wf_..."}
```

`scenarioId` необязателен: без него берётся сценарий из настроек. Скрытого
дефолта нет — если сценарий не выбран, `409 SCENARIO_REQUIRED`. URL проверяется
как HTTPS и передаётся в существующий `import_repository` (GitHub/GitLab,
без credentials/порта/query). Сценарий проверяется валидатором и обязан
принадлежать workspace привязки.

Запуск создаётся только явным действием пользователя. Перед сетевым импортом
и после него привязка перечитывается; если чат перепривязали — run не
создаётся и executor не вызывается. Импорт ограничен семафором
`CG_MINIAPP_MAX_CONCURRENT_RUNS` (по умолчанию 2) и rate limit
`CG_RATE_MINI_RUN` (по умолчанию 5 запросов в минуту на IP). Артефакт
сохраняется через существующие поля storage-манифеста, затем создаётся
`runtime.runs` и запускается штатный `WorkflowExecutor`.

Типичные ошибки: `400 INVALID_REPOSITORY_URL`, `400/404 REPOSITORY_IMPORT_FAILED`,
`400 SCENARIO_INVALID`, `400 SCENARIO_NOT_FOUND`, `409 SCENARIO_REQUIRED`,
`500 ARTIFACT_SAVE_FAILED`, `500 RUN_START_FAILED`.

### `GET /runs/{run_id}`

`run` — `RunSummary`; `findings` — до 200 подтверждённых findings, прошедших
тот же egress-маскинг, что и обычные `/api/runs/{id}/findings`
(`reports.exports`, `mask_text`). Чужой run неотличим от отсутствующего —
`404 RUN_NOT_FOUND`.

### `GET /runs/{run_id}/report.jsonc`

Отдаёт `application/json` из сохранённого отчёта через
`exports.load_export_bundle` + `build_jsonc`. Если отчёта ещё нет —
`404 REPORT_NOT_FOUND`. Статус и вердикт берутся из сохранённого run; ничего
не дорисовывается.

## Проверка `initData`

Используется только официальный алгоритм Telegram WebApp:

1. Тело — URL-encoded query, значения percent-decoded.
2. Любой дубль ключа (включая `hash`), пустой ключ, битый percent-encoding или
   размер больше 8 KiB — отказ.
3. `hash` — 64 hex; обязательны `auth_date` и `user`.
4. `data_check_string` — все поля, кроме `hash`, сортировка по ключу,
   `key=value` через `\n`.
5. `secret_key = HMAC_SHA256(key="WebAppData", data=bot_token)`.
6. `HMAC_SHA256(key=secret_key, data=data_check_string)` сравнивается с `hash`
   через `hmac.compare_digest` (constant-time).
7. `auth_date` не старше `TELEGRAM_MINIAPP_INIT_MAX_AGE_SECONDS` (по умолчанию
   3600, максимум 86400) и не из будущего больше чем на 60 секунд.
8. `user.id` — целое положительное. Приватный чат Telegram имеет
   `chat_id == user.id`; поэтому binding ищется ровно по подписанному `user.id`.
   `chat_type`, если он есть и не `private`, отклоняется.
9. Только после этого читается `telegram.bindings` и активный `account.users`
   с тем же workspace. Никаких `initDataUnsafe`, `userId` или workspace из
   тела запроса сервер не доверяет.

Бот-токен берётся существующим `load_bot_settings()` (env,
`TELEGRAM_BOT_TOKEN_FILE` или `~/.config/nedra/runtime.env`). В ответы, логи,
argv и ошибки он не попадает.

## Bearer-токен

Компактный формат: `v1.<base64url(payload)>.<base64url(HMAC-SHA256)>`.
Поля payload: `cid` (chat_id), `uid`, `wid`, `ep` (binding_epoch), `role=user`,
`iat`, `exp`, `jti`.

Секрет подписи:

- `TELEGRAM_MINIAPP_TOKEN_SECRET` — рекомендуется, минимум 16 символов;
- иначе выводится из бот-токена через domain-separated HMAC. Смена бот-токена
  инвалидирует выданные токены.

Проверка на каждом запросе: подпись, версия, роль, TTL ≤ 900 секунд, `exp`,
затем живая `telegram.bindings` и активный аккаунт. Если `binding_epoch`,
`user_id` или `workspace_id` разошлись — `401 TOKEN_REVOKED`. Unbind/rebind
отзывает токены без отдельного хранилища.

Осознанная граница: `initData` можно повторно предъявить в пределах его
`auth_date`-окна, а токен — в пределах 15 минут. Это нормальный replay-scope
для Telegram-входа; отдельный одноразовый nonce-store не вводится.

## Cookie-сессия и middleware

В `backend/app/main.py` только точный префикс `/api/telegram/mini/` исключён
из проверки cookie-сессии. При этом:

- проверка Origin/Referer остаётся;
- rate limit остаётся (`CG_RATE_MINI_AUTH`, `CG_RATE_MINI_RUN`);
- cookie не выдаётся, `Cache-Control: no-store` ставится на все mini-ответы;
- workspace ContextVar выставляет сам mini-роутер по проверенному токену.

Обычные `/api/*` и статика не меняются. Mini-роутер монтируется в `main.py`
после штатных роутеров, поэтому существующие lifespan, уведомления, Telegram
polling и раздача frontend сохраняются.

## Переменные окружения

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `TELEGRAM_MINI_APP_URL` | `https://nedra.soloveyka.bond/miniapp` | URL для кнопки бота (использует bot-слой) |
| `TELEGRAM_MINIAPP_TOKEN_SECRET` | derived | секрет Bearer-токенов |
| `TELEGRAM_MINIAPP_INIT_MAX_AGE_SECONDS` | `3600` | максимальный возраст `initData` |
| `CG_MINIAPP_MAX_CONCURRENT_RUNS` | `2` | одновременные импорты/запуски |
| `CG_RATE_MINI_AUTH` | `20` | POST `/auth` в минуту на IP |
| `CG_RATE_MINI_RUN` | `5` | POST `/runs` в минуту на IP |
| `TELEGRAM_ENABLED` | `0` | polling бота; на HTTP Mini App не влияет |

## Фронтенд: обязательные правила

- Токен хранить только в памяти вкладки; **не** писать в `localStorage`,
  `sessionStorage`, cookie или URL.
- Передавать только `Authorization: Bearer <token>`; не добавлять `userId`,
  workspace или `initDataUnsafe` в бизнес-запросы.
- При `401 TOKEN_REVOKED`/`INVALID_TOKEN` заново вызвать `/auth` с актуальным
  `Telegram.WebApp.initData` (он обновляется при открытии).
- На `/bootstrap` показывать `runs`/`scenarios` как есть; не досчитывать
  «пройдено/не пройдено» на клиенте.
- Запуск — только по нажатию; никаких скрытых импортов при открытии страницы.

## Тесты

Чистые тесты подписи/токена идут без БД:

```sh
/tmp/nedra-venv/bin/python -m unittest \
  backend.tests.test_telegram_miniapp.InitDataTests \
  backend.tests.test_telegram_miniapp.TokenTests -v
```

Полный прогон с настоящим PostgreSQL и ASGI (одноразовая база, бот-токен
фейковый, сеть не используется):

```sh
NEDRA_TEST_DATABASE_URL='postgresql://postgres:pass@/nedra?host=/tmp/nedra-pg/sock' \
/tmp/nedra-venv/bin/python -m unittest backend.tests.test_telegram_miniapp -v
```

Покрыто: валидная/подделанная/просроченная/дублированная `initData`,
неверный бот-токен, чужой чат, TTL/роль/подпись токена, unbound → 409,
rebind/unbind → 401, `binding_epoch` при записи settings, чужой workspace →
404, маскинг evidence, `report.jsonc` без отчёта → 404, явный сценарий,
HTTPS-валидация, перепривязка во время импорта (run не создаётся) и успешный
старт с записью артефакта/run и вызовом executor.
