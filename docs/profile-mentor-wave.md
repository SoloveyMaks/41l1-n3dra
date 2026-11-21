# Профиль и история запусков: реальные аккаунты

Волна 24h. Первая версия (nonce-псевдоним) заменена настоящей аутентификацией:
регистрация, вход, выход, `me`, история запусков и изоляция по пользователю.
Demo-вход сохранён отдельным явным режимом и больше не называется профилем.

## Контракт

| Слой | Решение |
|---|---|
| Схема | `account` (не `auth`, чтобы не столкнуться с Supabase `auth`) |
| Пользователь | `account.users`: `user_id`, нормализованный `email`, scrypt-хэш, `role`, личный `workspace_id` |
| Пароль | stdlib `hashlib.scrypt`, n=16384, r=8, p=1, соль 16 байт, min 10 / max 256 символов |
| Сессия | подписанный токен v3 + строка `account.sessions`; logout отзывает строку |
| Workspace | новый UUID на аккаунт; строка в `data_contract.workspaces` создаётся при регистрации |
| Demo | v1/v2 токены и `/api/session` остаются, помечены `mode=demo`, общий env workspace |
| Admin | `CG_ADMIN_ACCESS_CODE` работает как раньше; регистрация всегда `role=user` |

## Токен и проверка

```text
v3.<session_id>.<user_id>.<workspace>.<role>.<issued>.<nonce>.<hmac_sha256>
```

Проверка на каждом запросе:

1. HMAC над первыми семью сегментами;
2. окно `issued` (`SESSION_TTL_SECONDS`);
3. строка `account.sessions`: не отозвана, не истекла, пользователь не disabled;
4. `session_id/user_id/workspace/role/issued` в токене совпадают со строкой БД.

Старые v1/v2 проверяются по прежней схеме и получают `mode=demo`, `userId=null`.
Подделанная подпись, отозванная сессия и истёкший срок дают `401`.

## API

| Метод | Путь | Доступ |
|---|---|---|
| `POST` | `/api/auth/register` | без сессии; в `CG_PUBLIC_DEMO=1` требует `accessCode` |
| `POST` | `/api/auth/login` | без сессии |
| `POST` | `/api/auth/logout` | отзывает текущую v3 сессию, стирает cookie |
| `GET` | `/api/auth/me` | сессия; для demo возвращает `mode=demo`, `user=null` |
| `GET` | `/api/auth/history?limit=20` | сессия; запуски только своего workspace |
| `GET` | `/api/profile` | только account-сессия; demo получает `403 DEMO_SESSION` |

Регистрация:

```json
POST /api/auth/register
{"email": "alice@example.com", "password": "correct horse battery staple"}
```

Ответ (`200`): `authenticated`, `mode=account`, `user{userId,email,displayName,role,workspaceId,createdAt}`,
`sessionId`, `expiresInSec`. Cookie `cg_session` — HttpOnly, SameSite=Lax, Secure при HTTPS.

Ошибки: `400 INVALID_ACCOUNT`, `409 EMAIL_TAKEN`, `401 INVALID_CREDENTIALS`,
`403 DEMO_ACCESS_CODE_REQUIRED`, `401 AUTH_REQUIRED`.
Логин намеренно не различает «нет такого email» и «неверный пароль».

`GET /api/auth/history` отдаёт `runtime.runs` своего workspace, свежие первыми;
`workspace_id` наружу не уходит. `GET /api/profile` добавляет агрегаты:
`runs`, `lastRunAt`, `byStatus`, `byVerdict`, `totalFindings`.

Demo-сессия:

```json
POST /api/session {"role": "user"}
→ {"authenticated": true, "mode": "demo", "user": null, "workspaceId": "<env>"}
GET /api/profile → 403 {"code": "DEMO_SESSION", ...}
```

## Изоляция

Все runtime-таблицы уже scoped по `workspace_id` (`PK (workspace_id, id)`).
Не хватало одного: глобальный `DatabaseRepository` был жёстко привязан к env
workspace. Теперь:

- `identity.workspace_id()` читает request/background `ContextVar`;
- `DatabaseRepository.workspace_id` — свойство, возвращающее текущий scope;
- middleware `workspace_scope` ставит scope из проверенной сессии и сбрасывает
  после ответа;
- `asyncio.create_task` и `anyio.to_thread` копируют контекст, поэтому фоновая
  задача запуска видит workspace своего запроса;
- `os.environ` не мутируется, поэтому одновременные запросы двух аккаунтов не
  перетирают друг друга;
- scheduler/repository watches без контекста работают на env workspace — это
  явный demo-режим, а не silent default для аккаунтов.

Проверка владения не добавляет новых правил: чужой `workflow/artifact/run/report`
просто не находится в workspace и даёт `404`; списки возвращают только свои строки.
Admin-guards (`require_admin`) не изменены.

`GET /api/profile` для demo — `403`, потому что nonce-сессия не является
аккаунтом. Скрывать это только в UI запрещено контрактом.

## Миграции

- `008_profile_history.up.sql` — прежняя схема `profile` с bookmarks; больше не
  используется, но не удаляется: данные не теряем.
- `009_accounts.up.sql` — `account.users`, `account.sessions`, индексы, sentinel.
  Аддитивно, транзакционно, `IF NOT EXISTS`, без down/destructive SQL.
- Ledger `public.schema_migrations` фиксирует 009 автоматически при старте backend
  или через `python -m backend.app.storage.migrate`.
- Старые данные (env workspace, v1/v2 cookie, demo admin) остаются рабочими.

Регистрация создаёт личный workspace; runtime-схема общая, поэтому DDL на
каждого пользователя не выполняется.

## Фоновые запуски и scheduler

Ручной запуск: `POST /api/workflows/{id}/runs` создаёт run в workspace запроса и
`asyncio.create_task` наследует scope. `WorkflowExecutor`/`SSEBroker` не менялись:
они по-прежнему используют один `db_repo` и `workspace_id()`, которые теперь
читают контекст.

Ограничение: `WorkflowScheduler` и `RepositoryWatchService` запускаются без
request-контекста и остаются на env (demo) workspace. Для per-user schedules и
watches нужен явный workspace в scheduler-задаче (root integration/AI owner) —
это не делается молчаливым `os.environ` или гонкой потоков. `executor.py` и
`contracts/` в этой волне не правились.

## Эксплуатация

```bash
CG_SESSION_SECRET=<постоянный секрет>   # иначе v3-сессии умрут после рестарта
CG_SESSION_TTL_SECONDS=43200            # опционально
CG_RATE_LOGIN=10 CG_RATE_REGISTER=5     # per-IP, 60s window
CG_PUBLIC_DEMO=1 CG_ACCESS_CODE=...     # регистрация дополнительно требует accessCode
```

Без `CG_SESSION_SECRET` процесс работает на эфемерном секрете: это допустимо
только для локального demo.

## Ограничения

1. Нет подтверждения email, сброса пароля, смены пароля и удаления аккаунта.
2. Нет UI; контракт backend-only.
3. Регистрация в публичном demo закрыта кодом доступа; в локальном режиме открыта.
4. Повышение до `admin` — только SQL (`UPDATE account.users SET role='admin'`),
   эндпоинта нет. Demo-admin остаётся отдельным входом по коду.
5. Per-user schedules/watches не включены (см. выше).
6. Старая схема `profile` (008) оставлена как историческая; её bookmarks не
   участвуют в выдаче.

## Живая проверка

Изолированный PostgreSQL 17 в docker (`--tmpfs`, отдельная БД), миграции
001–009, реальный `backend.app.main:app` через ASGI. Прод не тронут.

Проверено:

- регистрация A и B, у каждого свой workspace UUID, отличный от env;
- повторная регистрация → `409`, неверный пароль → `401`;
- `me`/`session` → `mode=account`;
- B не видит workflow/artifact/run/report/evidence A: `404`, списки без A;
- A не видит workflow B; `GET /api/workflows` создаёт demo-default только в
  своём workspace;
- фоновый run A создаётся в workspace A и пишет туда `run_status`/`node_status`/
  `edge_data` (env workspace не тронут);
- два одновременных фоновых run (A и B) под `asyncio.gather`: каждый run и все
  его `run_events` лежат в своём workspace, env не изменён, списки после
  фоновой работы изолированы;
- `logout` отзывает v3: старый cookie → `401`, новый login работает;
- demo-сессия: `mode=demo`, `/api/profile` → `403 DEMO_SESSION`, admin-эндпоинт
  → `403`;
- параллельные ASGI-запросы A/B под `asyncio.gather` возвращают каждый только
  свои workflows;
- повторный `python -m backend.app.storage.migrate` → `ledger актуален`;
- 009 на отдельной legacy-БД: до миграции `workflows=1`, после — `after=1`,
  имя строки сохранено, `account.users`/`account.sessions` созданы;
- публичный demo: регистрация без `accessCode` → `403`, с кодом → `200`,
  повтор → `409`; старый v2-токен принимается как `mode=demo`;
- `/api/auth/me` с demo-cookie → `mode=demo`, `user=null`, `/api/profile` → `403`.

Отдельный прогон default-workflow (`wf_secure_pr_gate`) аккаунта:

```text
run 4dd39728-... ws 95d41020-...
status running (через 300s)
db row {'ws': '95d41020-...', 'execution_status': 'running', ...}
events: run_status running, node_status node_start succeeded,
        node_project running, edge_data artifactId art_47ebf2ab539a
```

То есть executor и файловый CodeFacts-слой видят workspace аккаунта, а не env:
`run_events` пишутся в его workspace. Полный workflow на sample-артефакте дольше
300 секунд — тест завершён по таймауту, не по ошибке изоляции. Пустой workflow
завершается быстро и даёт честный `failed` с `"Граф пуст"`, тоже внутри своего
workspace.
