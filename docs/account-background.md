# Фоновые проверки аккаунтов: schedules, repository watches, webhooks

Задача: убрать demo-only фон. Глобальный scheduler перечисляет due-строки всех
workspace через узкие server-only методы, а каждое исполнение получает
`workspace_scope` из самой строки БД, а не из cookie/`os.environ`.

## Границы изменений

| Файл | Что изменено |
|---|---|
| `backend/app/engine/scheduler.py` | per-row scope для schedules/watches/inbox, leases, recovery, защита от дублей |
| `backend/app/storage/database.py` | server-only `*_server` методы, возвращают `workspace_id` только внутрь процесса |
| `backend/app/api/repository_hooks.py` | cookie-less resolve по watch id, tenant HMAC, scope до эффектов |
| `backend/app/api/repository_watches.py` | webhook-секрет выводится из workspace запроса, не из env |
| `docs/account-background.md` | этот контракт и evidence |

`executor.py`, `contracts/`, auth/session/main/identity/profile не менялись.
Миграция не нужна: 010/011 не трогались, используются существующие колонки
`next_fire_at`, `claim_token/claimed_until`, ledger watches и inbox.

## Guard и контракт для UI

`backend.app.api.session.require_workspace_actor`:

- account-сессия с ролью `user` или `admin` — разрешено;
- legacy demo-сессия с ролью `admin` — разрешено;
- demo-сессия с ролью `user` — `403 ACCOUNT_OR_DEMO_ADMIN_REQUIRED`;
- без сессии — `401`.

Открыто только владельцу своего workspace:

| Метод | Путь |
|---|---|
| `GET/POST` | `/api/schedules` |
| `PATCH` | `/api/schedules/{id}` |
| `GET/POST` | `/api/repository-watches` |
| `PATCH` | `/api/repository-watches/{id}` |
| `POST` | `/api/repository-watches/{id}/check` |
| `POST` | `/api/repository-watches/{id}/webhook/setup` |

Каждый запрос scoped по workspace сессии; чужой id даёт `404`, а не данные.
`webhook/setup` отдаёт секрет один раз только владельцу watch; чужой id — `404`.
Админские `/api/admin/*`, `POST /api/webhook-test`, `/api/webhook-test/logs`,
training и прочие operator-only поверхности остаются на `require_admin`. Same-origin middleware и
существующие rate-limit buckets не менялись.

## Server-only выборка

Обычные методы `DatabaseRepository` остаются scoped по `self.workspace_id`
(ContextVar). Для глобального цикла добавлены методы с суффиксом `_server`,
которые не фильтруют по текущему scope:

- `claim_due_schedules_server(limit, lease_seconds)`;
- `claim_due_repository_watches_server(limit, token, timeout_seconds)`;
- `claim_repository_hook_deliveries_server(limit, token, lease_seconds)`;
- `recover_repository_watch_dispatches_server(limit)`;
- `release_stale_repository_claims_server()`;
- `list_queued_schedule_runs_server(limit, older_than_seconds)`;
- `find_repository_watches_by_id_server(watch_id)`.

Они возвращают `_server_row` с `workspace_id` (для установки ContextVar) и
никогда не вызываются из HTTP-хендлеров, отдающих клиентские DTO. В API-строках
`workspace_id` по-прежнему вырезается `_row`.

## Schedules

1. `claim_due_schedules_server` атомарно выбирает due-строки всех workspace
   (`FOR UPDATE SKIP LOCKED`) и ставит `next_fire_at = now() + lease` (60 s).
   Это одновременно claim и аварийный lease: второй процесс ту же строку не
   возьмёт.
2. Задача входит в `workspace_scope(row["workspace_id"])`, читает workflow и
   ревизию только этого tenant.
3. `create_scheduled_run` одной транзакцией вставляет queued run и ставит
   реальный `next_fire_at`/`last_run_at`. Если процесс умер до commit — нет ни
   run, ни сдвига; повтор после lease создаст ровно один. Если commit прошёл —
   run уже queued.
4. `_dispatch_scheduled_run` запускает `executor` в том же scope;
   `asyncio.create_task` копирует контекст.
5. `list_queued_schedule_runs_server` находит queued runs старше 5 секунд и
   переотправляет их. Дубли внутри процесса отсекает `_active_runs`, между
   процессами — атомарный `claim_run_start` в executor.

Ограничение: отдельной occurrence-таблицы нет; гарантия — at-least-once при
аварии между claim и commit. Успешный commit создаёт один run.

## Repository watches

- Глобальный claim: `claim_due_repository_watches_server` (poll-only,
  `SKIP LOCKED`, lease 30 минут).
- На каждый ряд — `workspace_scope(workspace_id)` и затем существующий
  `check_claimed`.
- Ledger `repository_watch_commits` остаётся ключом идемпотентности:
  `processing -> queued` делает только владелец `claim_token`, поэтому
  overlapping tick/check-now/restart не создаёт второй run на тот же SHA.
- Recovery: `release_stale_repository_claims_server` освобождает просроченные
  claims, `recover_repository_watch_dispatches_server` возвращает queued/stale
  dispatches всех tenant; каждый dispatch получает scope из строки.
- `_schedule_dispatch(run_id, workspace_id)` создаёт задачу с явным scope;
  вызов из уже scoped проверки наследует контекст.

## Webhooks

Публичный путь без cookie: `POST /api/repository-hooks/{watch_id}`.

1. Читается тело с лимитом 1 MiB.
2. `find_repository_watches_by_id_server` ищет watch по непрогнозируемому id.
   Если строк 0 или больше одной (id не глобально уникален в схеме) — `404`,
   tenant не угадывается.
3. `workspace_scope(workspace_id)` ставится до любых эффектов.
4. HMAC считается с фактическим workspace/watch nonce; неверный секрет — `401`
   до разбора события и до записи inbox.
5. Валидный push durable-пишется в `repository_hook_deliveries` (`202`);
   повтор `(workspace, watch, delivery_id)` — `duplicate`, без второго run.
6. Inbox обрабатывает `claim_repository_hook_deliveries_server` (lease 10 минут,
   `SKIP LOCKED`), затем `process_delivery` входит в scope строки. Ошибки
   возвращают доставку в pending с backoff; после `WEBHOOK_MAX_ATTEMPTS` —
   `failed`. Просроченный lease перехватывается заново, не дублируя run.
7. `setup` webhook-секрета использует `request_workspace(request)`, поэтому
   account-admin получает секрет для своего workspace, а не env demo.

## Demo

Строки demo workspace не исключены из глобальных claim-запросов. Задача без
ContextVar видит `configured_workspace_id()` (env/default), поэтому demo
schedule/watch/webhook работает как раньше, но это явный demo-режим, а не
скрытый default для аккаунтов.

## Живая проверка

Изолированный PostgreSQL 17 (docker, tmpfs, отдельная БД), миграции 001–009,
реальный `backend.app.main:app` и global `scheduler` без сети/прод-изменений.

```text
accounts 31fdae8b-... 930af759-...
schedules scoped sch_2d63b92a sch_b3ec19cf
webhooks accepted per tenant, duplicate rejected, wrong-tenant secret 401
webhook lease prevents double processing
schedules executed [{ws A, failed}, {ws B, failed}]
schedule no duplicate on next tick
schedule events scoped
watch queued recovery [{run A, failed}, {run B, failed}]
webhook inbox processed per tenant [{ws A, del-a-2, ignored}, {ws B, del-b-2, ignored}]
schedule queued recovery [{run A, failed}, {run B, failed}]
schedule lease prevents double claim
demo schedule still processed {ws env, failed}
cross-tenant API still 404
BACKGROUND SMOKE OK

watch recovery events:
runs [{ws A, failed}, {ws B, failed}]
events [{ws A, ews A, n=2}, {ws B, ews B, n=2}]
WATCH RECOVERY EVENTS OK
```

Что именно доказано:

- schedules A и B созданы через admin API под своими сессиями и попали каждый
  в свой workspace;
- один подписанный webhook на каждый tenant принят, повтор delivery — duplicate,
  чужой tenant-секрет — `401`, inbox-строки только у своих workspace;
- immediate second claim inbox вернул пусто (lease), повторный tick scheduler
  не создал второй schedule-run;
- watch recovery подняла по одному queued run в каждом workspace; отдельная
  проверка с валидной ревизией дала по 2 `run_events` на run, и у обоих
  `run.workspace_id == event.workspace_id`;
- schedule recovery подняла по одному queued run в каждом workspace;
- demo schedule обработан глобальным scheduler в env workspace;
- A не читает run/artifact B (`404`).

`failed` у run — ожидаемый результат пустого графа проверки, а не сбой
маршрутизации: события и статус записались в правильный workspace.

## Guard evidence

Живой изолированный PG17+ASGI, два обычных аккаунта без SQL-повышения:

```text
ordinary accounts d8acf9fc-... 52459541-...
schedules: user create/list/change own, foreign 404
watches: user create/list/check own, foreign 404
webhook secret owner-only, foreign signature 401, inbox in own workspace
demo user 403 on schedules/watches
legacy demo admin schedules/watches 200
admin surfaces 403, same-origin 403
GUARD SMOKE OK
```

Проверено: `POST/GET/PATCH /api/schedules` и `POST/GET/PATCH
/api/repository-watches`, `check`, `webhook/setup` работают под обычным
`role=user`; чужой schedule/watch id — `404`; подпись чужим секретом — `401`;
demo-user — `403`; legacy demo-admin — `200`; `/api/admin/overview` и
`/api/webhook-test/logs` для обычного аккаунта — `403`; POST с чужим Origin —
`403`.

## Ограничения

1. Schedules/watch API открыты verified account (`user`/`admin`) своего
   workspace и legacy demo-admin; demo-user и аноним — `401/403`. Админские
   overview/config/secret endpoints не открывались.
2. Нет отдельной occurrence-таблицы; при падении процесса до commit возможен
   повторный запуск после lease (at-least-once, не exactly-once).
3. Recovery queued run полагается на атомарный claim executor-а; если процесс
   умер после claim, run может остаться `running` — это существующее
   ограничение executor, не фонового слоя.
4. Реальный git-import (скачивание архива) не выполнялся; создание/check
   watch делали только read-only resolve публичного GitHub-репозитория.
   HMAC/enqueue/lease/settlement проверены без import-а.
