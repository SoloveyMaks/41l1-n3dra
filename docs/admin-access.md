# Admin access and roles

Публичный demo использует один общий workspace и один общий пул demo-данных.
Это не user-account система: роли `user`/`admin` — операторский gate поверх
общего workspace, без ACL, отдельных аккаунтов и переписывания схемы БД.

## Вход

Два независимых кода в окружении сервера:

| Переменная | Назначение |
|---|---|
| `CG_ACCESS_CODE` | обычный вход (`role: "user"`) |
| `CG_ADMIN_ACCESS_CODE` | отдельный вход в админпанель (`role: "admin"`) |

`CG_ADMIN_ACCESS_CODE` не должен совпадать с `CG_ACCESS_CODE`. Если он не задан
или совпадает с пользовательским кодом, admin-вход выключен:
`adminAvailable: false`, `POST /api/session {role:"admin"}` → `503` с понятной
ошибкой. Публичный запуск при этом не падает: без admin-кода обычный demo
работает как раньше.

Рекомендуется генерировать admin-код отдельно на deploy и не печатать его в
логи, argv или API. В репозитории коды не хранятся.

### Public demo

```text
GET /api/session
200 {"authenticated": false, "authRequired": true, "adminAvailable": true, ...}
# Set-Cookie отсутствует

POST /api/session {"accessCode":"<user-code>","role":"user"}
200 {"authenticated":true,"role":"user","adminAvailable":true,...}

POST /api/session {"accessCode":"<admin-code>","role":"admin"}
200 {"authenticated":true,"role":"admin","adminAvailable":true,...}

POST /api/session {"accessCode":"<user-code>","role":"admin"}
401 Неверный код администратора

POST /api/session {"accessCode":"кириллица-неверно","role":"user"}
401 Неверный код доступа
POST /api/session {"accessCode":"😀-wrong","role":"admin"}
401 Неверный код администратора
POST /api/session {"accessCode":"<user-code>","role":[]}
400 role должен быть user или admin
POST /api/session {"accessCode":"<user-code>","role":{}}
400 role должен быть user или admin
```

Поле `role` принимается только строкой `user` или `admin`: список, объект,
число, `null` и прочие значения дают `400`, а не внутреннюю ошибку. Коды
сравниваются как UTF-8 bytes постоянным временем; неверный тип кода или
не-ASCII/emoji ввод дают обычный `401`, без вывода значений кодов в ответ или
логи.

Без admin-кода:

```text
GET /api/session -> 200, adminAvailable:false
POST /api/session {"role":"admin", ...} -> 503
POST /api/session {"role":"user", ...}  -> 200, role:user
```

### Local mode

Локальный режим (`CG_PUBLIC_DEMO` не равен `1`) не включает неявный auto-admin:
роль всегда подписана в токене. Для локальной админпанели задайте отдельный
`CG_ADMIN_ACCESS_CODE`; без него admin-вход также вернёт `503`. Автоматическая
local-сессия на GET остаётся `user`.

## Токен

Подписанный HttpOnly cookie `cg_session`:

- текущий формат `v2.<workspace>.<role>.<issued>.<nonce>.<hmac>`;
- роль находится только внутри подписанного payload;
- старый `v1.<workspace>.<issued>.<nonce>.<hmac>` принимается как `user`;
- подмена подписи, workspace, роли или срока → `401`/сессия не принимается;
- `role` в JSON body не повышает права без второго кода.

Кнопка «выйти из админки в user» означает новый login с пользовательским кодом.
Она не отзывает серверные права у старого admin-токена: права определяются
подписанным токеном, а не состоянием UI.

## Матрица доступа

| Операция | user | admin |
|---|---:|---:|
| upload artifact / sample | да | да |
| читать runs, findings, map, evidence, report, comparisons | да | да |
| trigger существующего workflow | да | да |
| validation (`POST /workflows/{id}/validate`) | да (read-only) | да |
| node-types | да (read-only) | да |
| создать workflow / `PUT /workflows/{id}/draft` | **403** | да |
| список / создание / PATCH schedules | **403** | да |
| AI synthetic-test (`POST /ai/synthetic-test`) | **403** | да |
| webhook-test logs (`GET /webhook-test/logs`) | **403** | да |
| training artifact (`POST /artifacts/sample/training`) | **403** | да |
| `GET /api/admin/overview` | **403** | да |

Anonymous в публичном режиме на закрытых маршрутах получает `401`; user на
admin-маршрутах — `403`.

## Admin overview

`GET /api/admin/overview` только для admin. Это снимок реального состояния, а
не декоративная панель:

- counts: projects/artifacts, workflows, schedules, schedulesEnabled, runs,
  running, queued, failed;
- DB health: bounded `SELECT 1`, latency, errors только по типу; ledger
  migrations, если таблица доступна;
- AI: provider name/model и `configured`, без API key, base URL и credentials;
- vector: `enabled`, реальная доступность расширения, установленная версия,
  runtime `status`, реальное число активных embedding specs; никакого fake
  `active` при `0`;
- uploads: фактические лимиты архива/распаковки/файлов/ratio/path depth;
- workspace: `shared: true`, `userAccounts: false`, явное описание модели
  доступа «shared demo workspace; signed role gates operator actions»;
- auth: только booleans `publicDemo`/`adminAvailable`, без значений кодов.

Пример фактического ответа (значения сокращены):

```json
{
  "counts": {"projects": 1, "artifacts": 1, "workflows": 1, "schedules": 0,
             "schedulesEnabled": 0, "runs": 1, "running": 0, "queued": 0, "failed": 0},
  "db": {"healthy": true, "latencyMs": 0.3,
         "migrations": {"applied": 4, "latestAppliedAt": "..."}},
  "ai": {"provider": "opencode-go", "model": "...", "configured": true},
  "vector": {"enabled": false, "available": true, "installedVersion": null,
             "status": "disabled", "activeEmbeddingSpecs": 0},
  "uploads": {"maxArchiveBytes": 52428800, "maxUncompressedBytes": 262144000},
  "workspace": {"shared": true, "userAccounts": false},
  "auth": {"publicDemo": true, "adminAvailable": true}
}
```

Секреты, DSN, base URL провайдера, API keys и значения access-кодов в ответ не
попадают.

## Verification

Живая проверка на temporary PostgreSQL/workspace:

- anonymous `GET /api/session` — 200, без `Set-Cookie`;
- anonymous admin overview — 401;
- user login / GET session — `role:user`, user admin overview — 403;
- user create workflow / draft PUT / schedules / AI / logs / training — 403;
- user validate, upload, trigger workflow, findings/map/report — 200, run
  succeeded;
- admin login — 200 `role:admin`, overview — 200 с реальными counts;
- admin create workflow / draft PUT / schedules / AI / logs — 200, training
  guard прошёл (400 из-за отсутствия `CG_TRAINING_PROJECT`);
- user code в body с `role:admin` — 401, повышения нет;
- старый v1-токен — user, admin-маршрут 403; подмена подписи — 401;
- role `[]`/`{}`/число/null — 400; неверная кириллица/emoji в user/admin
  коде — 401, без 500 и без печати секретов;
- public без admin-кода — startup OK, `adminAvailable:false`, admin login 503;
- admin-код, равный user-коду, — admin-вход выключен.

Ограничения demo: один общий workspace, без user accounts/ACL. Роль не
изолирует данные между пользователями, а только запрещает операторские
изменения и доступ к админ-снимку.