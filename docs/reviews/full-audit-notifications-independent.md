# Независимое ревью: notifications — финальные F2/F3/F4/F6/F7

Дата: 2026-11-22. Режим: read-only по чужим веткам; изменения только в разрешённых notification-файлах и двух review-документах.

## Идентичность и границы

- Worktree: `/home/rodion/orca/workspaces/41l1-n3dra/audit-review-notifications-1122`, ветка `SoloveyMaks/audit-review-notifications-1122`.
- Точная база store/guard: `87e906710bb1f1c2d136f98774ea792ac449d8cc`; файлы взяты через `git show`, без reset/clean/merge и без checkout чужой ветки.
- Data: HEAD `9efe14e09ff56af694f468f2e765e72ce934b866`; gate `cbfc878626dee8f6e5d0f65bf1d5213c5f7cedfd`; helper `d2ba621`/`f132489`.
- Изменены только: `backend/app/notifications/store.py`, `backend/app/notifications/dispatch_guard.sql`, `docs/reviews/full-audit-notifications.md`, этот отчёт. Store/guard базируются на `87e9067`; координатор отдельно разрешит конфликт с `87`.
- Запреты соблюдены: PostgreSQL, containers, services, listeners, volumes, внешняя сеть, production, credentials и реальные уведомления не затрагивались. Пробы — только `/tmp/nedra-audit-review-notifications`.
- Combined-дерево для проб: `87e9067` + наши store/guard + `database.py` из data `9efe14e`. Это симуляция combined merge, не запуск приложения.

## Вердикт

F2/F4/F6 исправлены в коде; F3/F8 readiness-контракт реализован в data commit `cbfc878` и совместим с нашим guard; F1 снят для combined snapshot (data helper закоммичен), но изолированная review-ветка всё ещё зависит от data merge. F7 остаётся честным ограничением. **Это не подтверждение пригодности продукта:** PostgreSQL-матрица не исполнена, combined startup/DDL/триггеры/гонки не проверены живьём.

## Что изменено в этой ветке

1. `store.py` (exact base `87e9067`): `initialize()` делегирует data helper; добавлен `_connection()` с `SET TRANSACTION ISOLATION LEVEL READ COMMITTED` для каждого notification-соединения; `claim`, `authorize`, `finish`, `save_settings` используют `clock_timestamp()` для времени эффекта.
2. `dispatch_guard.sql` (exact base `87e9067`): durable `runtime.notification_migrations` + marker `legacy_active_consent_purge_v1`; purge только при отсутствии marker, в одной транзакции, под `LOCK TABLE`; fail-closed проверка `retry_cancelled boolean NOT NULL DEFAULT`; terminal trigger отклоняет транзакцию, если isolation не `read committed`.
3. Оба review-документа: обновлены статусы, stale-текст `87e9067` про DDL lock/store.initialize исправлен; PG-матрица сохранена как непроверенная.

## Статусы находок

### F1 — RESOLVED В COMBINED, зависимость ветки остаётся

- Изначальный факт: `87e9067` импортировал `initialize_notification_schema`, которого не было ни в одном ref; `import backend.app.notifications` падал.
- Сейчас helper закоммичен (`d2ba621`, data HEAD `9efe14e`), и combined-симуляция с data `database.py` + нашими store/guard импортируется: `combined import OK`.
- Изолированная review-ветка (без data merge) по-прежнему не запускается: это ожидаемая зависимость, не новый блокер. Финальный merge/конфликт с `87e9067` и data делает координатор.
- Доказательство: **реально исполнено** (import combined); PostgreSQL startup не выполнялся.

### F2 — FIXED В КОДЕ (source-only)

- `dispatch_guard.sql:12-18` создаёт `runtime.notification_migrations(name text primary key, applied_at timestamptz not null default clock_timestamp())`.
- `dispatch_guard.sql:20-55`: `LOCK TABLE ... EXCLUSIVE`; если marker `legacy_active_consent_purge_v1` отсутствует — при необходимости `ADD COLUMN retry_cancelled boolean NOT NULL DEFAULT false`, затем `retry_cancelled=true` для pending/sending, unauthorized pending/sending -> `skipped`, затем `INSERT` marker. Если marker есть — rows не трогаются.
- Колонка больше не является ключом миграции: БД, прошедшая `47cbbd2` (колонка есть, marker нет), получает purge; БД, прошедшая `52c3460`, тоже. Это закрывает F2.
- Первый marker-run сознательно fail-closed отменяет **все** активные rows, включая созданные после раннего hotfix: достоверной per-row истории согласия нет. Это документированный компромисс, не тихая потеря terminal history.
- `dispatch_guard.sql:56-68`: при наличии marker, но отсутствии/неверной колонки — `RAISE EXCEPTION`, purge не повторяется.
- Data gate `cbfc878` требует marker row; наш guard и data gate должны ехать одним merge, иначе startup fail-closed.
- Доказательство: **по исходникам**; SQL на PostgreSQL не исполнялся. Residual: первая marker-миграция может отменить новые active rows на БД, где уже применялся частичный hotfix.

### F3 — CONTRACT РЕАЛИЗОВАН У DATA (source-only)

- Data `cbfc878` (`database.py`): `_notification_contract_ready` — catalog-only проверка marker row, `retry_cancelled boolean NOT NULL DEFAULT false` (через `pg_get_expr`), обоих триггеров; `_apply_notification_schema` при ready возвращается без DDL и без advisory wait.
- Если не ready: `_wait_notification_ddl_lock` — `pg_try_advisory_xact_lock(hashtext('nedra-completion-notifications-v1'))`, 50 попыток по 0.1 s (5 s), затем `DatabaseUnavailable`; после lock — повторный readiness check, `SET LOCAL lock_timeout='5s'`, scripts один раз, verify, иначе fail-closed.
- `store.initialize()` только вызывает helper; своего DDL/installer в notifications нет. `init_db` + `store.initialize` на готовой схеме не делают DDL.
- Согласовано с актуальным data owner `ctx_b9e273e2ca13` (прежний `ctx_95b2bdfd615c` завершён) через orchestration; подтверждение `msg_2696833173a2`; точные константы — в `cbfc878`.
- Доказательство: **по исходникам/коммиту**; PostgreSQL не запускался. Residual: первый install при работающем старом инстансе всё ещё может дать deadlock victim/abort; `lock_timeout=5s` и trylock ограничивают ожидание, но конкурентный сценарий требует PG-теста. Нормальный ready startup DDL не выполняет.

### F4 — FIXED FAIL-CLOSED (source-only)

- `dispatch_guard.sql:74-83`: на terminal `INSERT/UPDATE` trigger проверяет `lower(current_setting('transaction_isolation')) = 'read committed'`; иначе `RAISE EXCEPTION` до advisory lock и до capture. Это не даёт AFTER trigger прочитать stale `enabled=true` под REPEATABLE READ/SERIALIZABLE.
- `store.py:69-81` (`_connection`) ставит `SET TRANSACTION ISOLATION LEVEL READ COMMITTED` первым statement каждой notification-транзакции; все `settings/save_settings/inbox/mark_*/claim/authorize/abort/finish` используют его.
- Поддерживаемая isolation: READ COMMITTED. Неподдерживаемая: terminal run update аварийно завершается (fail-closed), уведомление не создаётся.
- Доказательство: **по исходникам**; PostgreSQL не запускался. Residual: операционный эффект аварийного отказа при RR/SERIALIZABLE нужно явно документировать/проверить.

### F5 — FIXED В ДОКУМЕНТАХ

- `docs/reviews/full-audit-notifications.md:75` обновлён: store делегирует helper, DDL lock/readiness — у data.
- В follow-up явно указано, что текст upgrade contract в ревизии `87e9067` про «store.initialize берёт DDL lock/исполняет SQL» устарел; актуальный владелец — `initialize_notification_schema`, gate `cbfc878`.

### F6 — FIXED (source-only)

- `store.py:207-224`: `claim` использует `clock_timestamp()` и для candidate-условий (`next_attempt_at`, `lease_until`), и для `lease_until=clock_timestamp()+45s`.
- `store.py:227-...`: `authorize` пишет `authorized_at=clock_timestamp()`; `lease_valid` уже был `clock_timestamp()`.
- `store.py:311-335` `finish`: `delivered_at`/`next_attempt_at` через `clock_timestamp()`; `store.py:129` `save_settings.updated_at` тоже.
- Доказательство: **по исходникам**; PG не запускался. Residual: claim, ждавший table lock дольше 45 s, теперь не выдаёт уже истёкший lease; PG-тест нужен.

### F7 — ЧЕСТНОЕ ОГРАНИЧЕНИЕ

- `runtime.py` вне разрешённого набора файлов; pin остаётся на `decision.addresses[0]` (`runtime.py:92`, `webhook.py:57-60`).
- Address failover не добавлен: безопасно отличить connect-failure до отправки от неопределённого исхода после отправки в этом пакете нельзя, а повторный POST при неопределённом результате запрещён. Никакого нового duplicate-path не введено.
- Существующая at-least-once семантика (`finish(retry=True)` после сетевой ошибки) остаётся; receiver обязан дедуплицировать по `Idempotency-Key`/eventId. Это ограничение, а не исправление F7.
- Доказательство: **по исходникам**; реальные DNS/TLS/внешний приёмник не проверялись.

### F8 — DATA VERIFICATION (source-only)

- Data gate `cbfc878` проверяет default через `pg_get_expr(...) IN ('false', '''f''::boolean')`, а не только `attnotnull`.
- Наш guard добавляет `DEFAULT false` и дополнительно требует `atthasdef`; при marker+неверной колонке падает fail-closed.
- Доказательство: **по исходникам/коммиту**; PG не запускался.

## Реально исполненные проверки

На combined-дереве (`87e9067` + наши store/guard + data `9efe14e`):

```text
import backend.app.notifications                  -> combined import OK
ast.parse backend/app/notifications/*.py          -> OK
probe_router.py  malformed 6/6 + structure 10/10  -> FAILURES: []
probe_async.py   cancel/repeat/stop/exception      -> 4/4 PASS
probe_webhook_uds.py 200/203/206/302               -> PASS
probe_policy.py literal IP                         -> 11/11
git diff 35d27d3..87e9067 --check                  -> exit 0
```

Router: 422 без эха synthetic marker; blank keep-URL с `expected_url`; GET/PUT redaction. Async: отмена ждёт реальный thread, повторная отмена не возвращает stop раньше, exception доставляется. UDS: 206 с длинным телом/паузой не буферизуется; 3xx не следует. Policy: literal IP, без DNS.

**Не исполнено:** PostgreSQL, marker SQL, trigger bodies, DDL locks, isolation check, MVCC races, реальный startup/lifespan, TLS/DNS/внешний webhook. Никакой fake DB PASS не заявляется.

## Непроверенная PostgreSQL-матрица

1. Combined startup с data `9efe14e` + notifications `87e9067` + наши store/guard; fresh install; existing v5 без notifications; существующие notification rows; повторный startup.
2. F2: `35d27d3→new` (колонки нет), `47cbbd2→new` (колонка есть, marker нет), `52c3460→new`; marker present -> rows не трогаются; rollback purge+marker при ошибке; первый marker-run и его overbroad cancel.
3. F3: ready startup — только catalog reads, без advisory/ACCESS EXCLUSIVE; not ready — trylock 5 s, `lock_timeout=5s`, повторный readiness, scripts ровно один раз; два конкурентных startup; `init_db`+`store.initialize` без второго DDL; 55P03 -> DatabaseUnavailable.
4. F3 deadlock: startup DDL одновременно с terminal UPDATE `runtime.runs` и `save_settings`/`authorize`; поведение `lock_timeout` и deadlock detector.
5. F4: terminal INSERT/UPDATE под READ COMMITTED/RR/SERIALIZABLE; `_connection` RC; abort при RR/SERIALIZABLE; отсутствие capture.
6. F6: claim ждёт table lock >45 s; lease/eligibility/authorized_at/finish timestamps; reclaim после ожидания.
7. F8: default false expression variants; marker present + malformed column; missing triggers -> repair/verify.
8. Прежняя матрица: completion/opt-out в обе стороны; disable/enable и URL A→B→A до finish/до reclaim; authorized in-flight success/fail; crash после authorize, lease expiry, opt-out/opt-in, reclaim; retry_cancelled сохраняется; две authorize одного token; attempts cap; 408/429/5xx/network/3xx; два workspace с одинаковым run_id; чужой lease.

## Остаточные риски и границы

- F3: ready startup больше не делает DDL, но первый install в rolling deploy всё ещё может стать deadlock victim; нужен PG-тест и, при необходимости, отдельная migration-фаза до обслуживания.
- F4: RR/SERIALIZABLE terminal run update теперь аварийно завершается; это fail-closed, но операционный эффект должен быть принят.
- F2: первая marker-миграция отменяет все active rows; на БД с частичным hotfix это может отменить и более новые pending.
- F7: pin на `addresses[0]`; при недоступном первом адресе доставка исчерпает попытки, даже если другие DNS-адреса живы; address failover не добавлен.
- Combined merge с `87e9067` и data `9efe14e` не выполнен; координатор разрешает конфликт. Изолированная review-ветка зависима от data helper.
- PostgreSQL/TLS/DNS/внешний приёмник не проверялись; продуктовая готовность не заявляется.

## Exact refs

- notifications base: `87e906710bb1f1c2d136f98774ea792ac449d8cc`.
- data HEAD: `9efe14e09ff56af694f468f2e765e72ce934b866`; readiness gate: `cbfc878626dee8f6e5d0f65bf1d5213c5f7cedfd`; helper: `d2ba621`/`f132489`.
- This branch report commit: exact SHA выдан в `worker_done`; коммит содержит только перечисленные четыре файла.
