# Независимое ревью: notifications — первый пакет и integration delta

Дата: 2026-11-22. Режим: read-only, без вложенных агентов. Изменён только этот отчёт.

## Идентичность проверки

- Worktree: `/home/rodion/orca/workspaces/41l1-n3dra/audit-review-notifications-1122`, HEAD `f673bea9f6151e7f2a0df16a44d7e03e59e92ab8`, ветка `SoloveyMaks/audit-review-notifications-1122`.
- База диффа: `35d27d30ae44fd955824001e18eff895197c933c`. Первый пакет: `47cbbd29fcdb31eb38a88112c4885cfb8c6c3322`, отчёт `f673bea`.
- Целевое состояние, на которое указывает координатор: notifications HEAD `87e906710bb1f1c2d136f98774ea792ac449d8cc`, SQL guard `52c34600884b1fba8ca96c875bf1d6ddc120689e`.
- `87e9067`/`52c3460` не выкладывались в рабочий worktree: просмотр через `git show`/`git archive`; reset/clean/restore не выполнялись.
- `initialize_notification_schema` отсутствует во всех git refs. В data worktree `.../audit-all-data-1122/backend/app/storage/database.py` helper есть, но файл `M` (uncommitted) и не входит ни в один ref. Для проб собрано временное combined-дерево в `/tmp/nedra-audit-review-notifications/combined`; это симуляция, не приёмка combined snapshot.
- Ограничения: PostgreSQL, containers, services, volumes, ports, network, production, credentials и реальные уведомления не затрагивались. Ранее запрещённый `podman start` не повторялся и не обходился. Внешний HTTP не использовался; HTTP-проба — только AF_UNIX socket.

## Вердикт

**Не принимать как интеграционно готовый пакет.** F1 — жёсткий блокер: `87e9067` нельзя импортировать/запустить отдельно, и ни в одном ref нет реализации helper. F2/F3/F4 должны быть закрыты или явно приняты как ограничение до приёмки. В проверенном локальном объёме исправления router/URL/stream/2xx/asyncio держатся; SQL и MVCC проверены только по исходникам, живого PostgreSQL нет.

## Находки

### F1 — BLOCKER: HEAD 87e9067 импортирует отсутствующий helper; приложение не стартует

- Файлы/строки: `backend/app/notifications/store.py:29` (`from backend.app.storage.database import get_pool, initialize_notification_schema`), `store.py:68-69` (`initialize()` -> `initialize_notification_schema()`); `backend/app/main.py:22` импортирует `backend.app.notifications` безусловно.
- Последовательность: merge/deploy `87e9067` без data commit -> `import backend.app.notifications` -> `ImportError` -> падает импорт `backend.app.main`; lifespan, health и API не поднимаются. Ошибка возникает на импорте, до `NotificationRuntime.start()` и до любого `try/finally`.
- Факт: в дереве `87e9067` `backend/app/storage/database.py` содержит `get_pool` (строка 111) и не содержит helper. `git grep` по всем refs находит имя только в ветке notifications (в `store.py` и тексте этого отчёта), ни одного определения в `database.py`; `git log --all -S'initialize_notification_schema'` показывает только `87e9067` (саму ссылку). Uncommitted helper в data worktree находится по пути `/home/rodion/orca/workspaces/41l1-n3dra/audit-all-data-1122/backend/app/storage/database.py:259`.
- Доказательство: **реально исполнено** на чистом архиве `87e9067`: `ImportError: cannot import name 'initialize_notification_schema' from 'backend.app.storage.database'`. Для контроля combined-дерево с uncommitted data helper импортируется, но это временная симуляция, не запуск приложения и не проверка БД.
- Последствия: ветка несамостоятельна; merge notifications-only — гарантированный отказ старта. Запись в отчёте «pending combined verification» точна, но неполна: коммита-контрагента не существует, поэтому combined snapshot невоспроизводим из refs.
- Минимальная рекомендация: сначала закоммитить/влить data helper с точным контрактом (`conn=None`/`conn`, DDL lock), затем повторить реальный import и старт приложения; `87e9067` отдельно не мержить.

### F2 — HIGH: fail-closed purge legacy-строк пропускается на БД, где уже применялся 47cbbd2

- Файлы/строки: `backend/app/notifications/dispatch_guard.sql:2-22`; `git show 47cbbd2 -- backend/app/notifications/dispatch_guard.sql`.
- Факт: `47cbbd2` добавил `retry_cancelled boolean NOT NULL DEFAULT false` через `ADD COLUMN IF NOT EXISTS` и **не** делал purge. `52c3460` обернул и добавление колонки, и оба UPDATE в `IF NOT EXISTS (pg_attribute ... retry_cancelled)`. Если БД уже прошла `47cbbd2`, колонка есть -> весь DO-блок, включая `UPDATE ... retry_cancelled=true` и перевод unauthorized pending/sending в `skipped`, не выполняется.
- Последовательность: existing DB с legacy pending/sending (созданными до политики согласия) -> применён `47cbbd2` -> применён `52c3460` -> guard видит колонку и молчит -> legacy pending остаётся `pending`, `retry_cancelled=false` -> claim/authorize отправляет его при текущем `enabled=true`, даже если между созданием и доставкой был disable/enable.
- Последствия: нарушено обещание «never resume their unproven consent»; старый completion может уйти после opt-out/opt-in. Readiness-check data helper (uncommitted `database.py:227-256`) проверяет наличие/форму колонки и триггеров, но не факт выполнения purge, и тоже не поймает пропущенный purge.
- Доказательство: **по исходникам** (два `git show`, точная логика guard). PostgreSQL не запускался.
- Минимальная рекомендация: одноразовый durable marker (строка миграции/version в `runtime.schema_meta` или отдельная notification-migration таблица), независимый от существования колонки; выполнить purge ровно один раз и на пути `47cbbd2→52c3460`, и на пути `35d27d3→52c3460`; добавить оба пути в PG-матрицу.

### F3 — HIGH: новый BEFORE trigger замыкает deadlock-цикл со startup-DDL

- Файлы/строки: `dispatch_guard.sql:24-41` (BEFORE trigger на terminal status берёт workspace advisory lock); `schema.sql:48-55,79-81` (startup SQL берёт ACCESS EXCLUSIVE на `notification_settings`/`completion_notifications` и `runs`); `store.py:93` (`save_settings`: workspace lock -> settings/completion), `store.py:220` (`authorize`: workspace -> completion row), `store.py:302` (`finish`: workspace -> completion row); `main.py:104` (`store.initialize` в lifespan); uncommitted data helper вызывает тот же install из `init_db`.
- Последовательность (rolling deploy/restart при работающем старом инстансе):
  1. T1 (startup DDL) держит ACCESS EXCLUSIVE на `notification_settings`/`completion_notifications`, затем ждёт ACCESS EXCLUSIVE на `runtime.runs` для `DROP/CREATE TRIGGER`.
  2. T2 (`authorize`/`finish`/`save_settings`) уже взял workspace advisory lock и ждёт ACCESS SHARE/row lock на notification-таблицах, удерживаемых T1.
  3. T3 (terminal UPDATE `runtime.runs`) держит RowExclusive/row lock на `runs`, а в новом BEFORE trigger ждёт workspace advisory lock, удерживаемый T2.
  Цикл: T1 -> T3 -> T2 -> T1.
- Последствия: PostgreSQL deadlock detector через `deadlock_timeout` абортит одного участника — startup (crash-loop при повторных рестартах), либо пользовательский `save_settings`/`finish`, либо terminal UPDATE run (500/застрявший run). Даже без deadlock каждый startup берёт ACCESS EXCLUSIVE на `runtime.runs` и блокирует завершения; data helper выполняет scripts дважды за процесс (`init_db` + `store.initialize`), удваивая окно. До нового BEFORE trigger у T3 не было ожидания workspace lock, поэтому именно этот trigger замыкает цикл.
- Доказательство: **по исходникам и семантике блокировок PostgreSQL**; не исполнялось (нет PG). Режимы: `ALTER TABLE`/`DROP TRIGGER` — ACCESS EXCLUSIVE, `CREATE TRIGGER` — SHARE ROW EXCLUSIVE, UPDATE — ROW EXCLUSIVE, SELECT — ACCESS SHARE; в указанном порядке они конфликтуют.
- Минимальная рекомендация: вынести notification DDL из runtime startup в отдельную versioned-миграцию до начала обслуживания (rolling deploy: старые инстансы не обслуживают); как минимум — readiness/version gate, не выполнять `DROP/CREATE TRIGGER` при уже установленном контракте, задать `lock_timeout` и fail-closed с retry, а не ждать бесконечно; добавить PG-тест «DDL одновременно с terminal UPDATE + save_settings/authorize».

### F4 — MEDIUM: AFTER trigger и MVCC — гарантия верна только при READ COMMITTED и VOLATILE

- Файлы/строки: `dispatch_guard.sql:24-36` (BEFORE lock), `schema.sql:57-77` (AFTER `capture_run_completion` читает `notification_settings`), `store.py:93,220,302`.
- Ожидаемый путь: T1 (`save_settings`) коммитит disable; completion-транзакция ждёт advisory lock в BEFORE; AFTER trigger function объявлена без volatility qualifier, т.е. по умолчанию VOLATILE; её `INSERT ... SELECT` получает свежий snapshot после ожидания и видит committed disable -> вставляет `skipped`. Обратный порядок: completion коммитит pending, `save_settings` после lock видит строку и отменяет её. Пул не задаёт isolation (`get_pool` без `SET TRANSACTION ISOLATION`), поэтому фактический уровень — READ COMMITTED.
- Ограничение/провал: при REPEATABLE READ/SERIALIZABLE AFTER trigger использует snapshot транзакции, взятый до opt-out. Completion может вставить `pending`, несмотря на disable; если пользователь успеет сделать enable до claim, `authorize` увидит `enabled=true`, `retry_cancelled=false` и отправит completion, созданный до opt-out. Аналогично `save_settings` после lock в RR может не увидеть конкурирующую вставку. Это не воспроизведено; вывод по документированной семантике VOLATILE/snapshot и исходникам. Сам отчёт (строка 161) признаёт, что свежесть snapshot требует живого PG.
- Последствия: при смене isolation-уровня (или внешнем клиенте/будущем коде) теряется ключевая гарантия opt-out; текущий код не фиксирует это предположение.
- Доказательство: **по исходникам/документации PostgreSQL**, PG не запускался.
- Минимальная рекомендация: явно зафиксировать/проверить READ COMMITTED для транзакций terminal UPDATE `runtime.runs` (и для `save_settings`/`authorize`/`finish`), либо ввести durable settings generation и проверять его в `authorize`; добавить PG-тесты обеих очередностей под READ COMMITTED и RR/SERIALIZABLE. Не объявлять гарантию независимой от isolation.

### F5 — MEDIUM (документация): upgrade contract в отчёте противоречит фактическому delegation

- Файлы/строки: `docs/reviews/full-audit-notifications.md:75` («initialize применяет forward-файл...»), `:145` («Их последовательно исполняет текущий store.initialize»), `:147` («В notification initialize уже берётся pg_advisory_xact_lock...»); фактически `store.py:68-69` только вызывает helper, а DDL lock обязан брать data helper (новый раздел, строка 180).
- Последствия: владелец data может реализовать второй install без того же lock или решить, что store всё ещё применяет SQL; риск параллельного DDL/неверного порядка. Это ровно та integration-зона, где уже возник F1.
- Доказательство: **по исходникам** `87e9067` и тексту отчёта.
- Минимальная рекомендация: в том же integration-коммите обновить строки 75/145/147: единственный владелец — helper, lock `hashtext('nedra-completion-notifications-v1')`, семантика `conn=None`/`conn`, вызовы из `init_db` и lifespan; SQL-файлы не читаются store.

### F6 — LOW: `claim` по-прежнему считает lease через `now()`, а не `clock_timestamp()`

- Файлы/строки: `store.py:198-201` (`lease_until < now()`, `lease_until=now()+interval '45 seconds'`); `52c3460` исправил только `authorize:223` на `clock_timestamp()`.
- Последовательность: claim-транзакция стартует, ждёт table lock (например, startup DDL), затем выдаёт lease с `now()+45s`, который уже в прошлом. Worker вызывает `authorize` -> `clock_timestamp()` показывает истёкший lease -> `lost`, POST не выполняется; строка ждёт следующего reclaim-цикла. Параллельно candidate-условие с `now()` может не увидеть строки, истёкшие за время ожидания. Дублирующего POST нет, потому что `authorize` — гейт.
- Последствия: задержка/лишний цикл reclaim, не потеря и не дубль; при частых DDL-паузах — заметная деградация доставки.
- Доказательство: **по исходникам**; PG не запускался.
- Минимальная рекомендация: использовать `clock_timestamp()` и для `lease_until`, и для candidate-условий (или heartbeat); добавить в PG-матрицу сценарий «claim ждёт table lock дольше 45s».

### F7 — LOW: pinning всегда использует только `addresses[0]`

- Файлы/строки: `runtime.py:92` (`_pinned_url(..., decision.addresses[0])`), `webhook.py:57-60` (`addresses[0]`).
- Последовательность: внешний host с несколькими A/AAAA; `validate_webhook_url` возвращает отсортированный набор; первый адрес недоступен; все 3 авторизованные попытки идут на него, хотя другой валидированный global-адрес жив. Уведомление не доставляется, хотя DNS-ответ содержит рабочий адрес.
- Последствия: ложные failed после исчерпания попыток; SSRF-границы не нарушаются (все адреса прошли policy).
- Доказательство: **по исходникам**; реальный DNS/TLS не проверялся.
- Минимальная рекомендация: перебирать валидированные адреса при connection failure (по-прежнему pinned, без redirects), либо документировать/принять это ограничение.

### F8 — dependency observations (вне reviewed commit)

- Uncommitted data helper `.../audit-all-data-1122/backend/app/storage/database.py:227-256` проверяет `attnotnull`, но не default колонки; NOT NULL без default прошёл бы readiness, а `capture_run_completion` вставляет строку без `retry_cancelled` -> runtime-ошибка на каждом completion. Рекомендация: проверять default/`atthasdef` или явно `SET DEFAULT`.
- Тот же helper вызывается из `init_db` и затем из `store.initialize` — два полных install за процесс; помимо F3 это удваивает DDL-окно. Рекомендация: идемпотентный gate/version, не выполнять DDL повторно.
- Это не находки в `87e9067` (кода нет в refs), но они влияют на combined acceptance.

## Что проверено и держится (в указанном объёме)

- **Router 422 / секрет**: реальные ASGI-запросы на combined-дереве: 6/6 malformed (object URL, bool-string, null URL, extra field, malformed JSON, wrong type в другом поле) -> 422 `{"detail":"Некорректные данные уведомления"}`, синтетический маркер не отражён. Структурные URL: 10/10 отклонены (port 0/65536/нечисловой, CR/LF, NUL, пробел, userinfo, query, fragment, ftp). Blank URL: `webhook_url=None`, `expected_url` = сохранённый URL -> контракт keep-URL соблюдён. GET/PUT не отдают полный URL; `webhook_masked` без path/query/userinfo.
- **asyncio**: 4/4 PASS на реальных потоках: отмена ждёт завершения thread; повторная отмена не возвращает stop раньше; `NotificationRuntime.stop()` ждёт thread и обнуляет task; исключение из thread доставляется вызывающему. Полный lifespan с PostgreSQL не запускался.
- **stream/2xx**: реальный HTTP по AF_UNIX (без TCP listener): `WebhookNotifier.send` -> 200/203/206 succeeded, 302 failed; 206 с объявленным длинным телом и паузой 1 s при timeout 0.3 s не буферизуется. `runtime.deliver` использует тот же `client.stream` и `200 <= code < 300`, 3xx не следует; выполнение runtime-ветки с БД недоступно.
- **Policy**: literal-IP 11/11 (no opt-in, loopback v4/v6, private, metadata/link-local, CGNAT, external HTTPS 443, HTTP external, port 8443, query); DNS не вызывался.
- **Store по исходникам** (без PG): `authorize` проверяет token/status/authorized_at/clock lease/retry_cancelled/settings и возвращает send только по фактическому `UPDATE ... RETURNING attempts`; `finish` под workspace lock и token/authorized_at guard не воскрешает retry при `retry_cancelled`; `save_settings` под тем же lock отменяет только unauthorized pending/sending и ставит retry_cancelled только authorized sending; `claim` — SKIP LOCKED, attempts cap; workspace scope. Это **не** заменяет PG-прогон.
- **Static**: `ast.parse` 6 notification Python files — OK; `git diff 35d27d3..87e9067 --check` — exit 0.

## Обязательная PostgreSQL-матрица (gap)

Живой PostgreSQL в этом окружении недоступен; ни один SQL-вывод ниже не объявляется исполненным:

1. Import/startup после коммита data helper; fresh install, existing v5 без notifications, существующие notification rows; повторный startup.
2. Пути upgrade `35d27d3→52c3460` и `47cbbd2→52c3460` (F2), включая legacy authorized sending.
3. Обе очередности completion/opt-out; disable/enable и URL A→B→A до finish/до reclaim; `finish(retry=True)` одновременно с opt-out.
4. Worker crash после authorize, lease expiry, opt-out/opt-in, reclaim; `retry_cancelled` сохраняется.
5. Две authorize одного token; ожидание workspace lock дольше lease (`clock_timestamp`), claim после table-lock паузы (F6).
6. Startup DDL одновременно с terminal UPDATE и `save_settings`/`authorize` (F3), с `lock_timeout` и без.
7. READ COMMITTED и REPEATABLE READ/SERIALIZABLE для F4.
8. Два workspace с одинаковым run_id; чужой lease; attempts cap; 408/429/5xx/network/3xx.

## Команды/артефакты

- Clean archive: `git archive 87e9067 | tar -x -C /tmp/nedra-audit-review-notifications/tree`.
- Import target: `ImportError ... initialize_notification_schema` (`87e9067`); combined simulation import OK (uncommitted data helper скопирован только в `/tmp`).
- Probes: `/tmp/nedra-audit-review-notifications/probe/{probe_router.py,probe_async.py,probe_webhook_uds.py,probe_policy.py}`; результаты выше.
- Код репозитория не изменялся; добавлен только этот отчёт.

## Итог

Ревью выполнено в read-only режиме; outcome относится к выполнению ревью, не к пригодности продукта. **Verdict: пакет не готов к интеграции** до закрытия F1 и проверки F2/F3/F4 на живом PostgreSQL; исправления router/URL/stream/2xx/asyncio в проверенном объёме держатся.