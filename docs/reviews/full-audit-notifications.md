# Полный аудит: notifications

Дата: 2026-11-22. Владелец: worker task_2b6a03847cba, без помощников.

База проверена до любых правок: `35d27d30ae44fd955824001e18eff895197c933c`.
Коммит исправлений: `47cbbd29fcdb31eb38a88112c4885cfb8c6c3322`.
Последующий коммит отчёта меняет только этот отчёт и поясняющий SQL-комментарий.

**Результат: исправления внесены, доступные локальные проверки прошли; полная интеграционная приёмка НЕ завершена из-за отсутствия разрешённого PostgreSQL. SQL-изменения требуют отдельного живого прогона перед интеграцией.**

## Охват и границы

Изменены только `backend/app/notifications/` и этот отчёт. Прочитаны все файлы notifications, notify policy, notify-ветка executor, validator, lifespan приложения, request workspace/actor helpers, места Telegram enqueue и UI перехода к run. API вне notifications, engine/scheduler, telegram/runtime и frontend не изменялись. Исторический `notifications/schema.sql` не изменялся.

Начальное дерево чистое; HEAD совпал с exact base. Доступны orca, python, uv; системные postgres/initdb/psql отсутствуют, системный Python без fastapi/psycopg. Для выполнения использован уже существующий интерпретатор `/home/rodion/orca/workspaces/41l1-n3dra/backend-facts-integration/.venv/bin/python`: httpx 0.28.1, psycopg 3.3.9, FastAPI 0.165.0. Чужой checkout не изменялся и его исходники не импортировались: `PYTHONPATH=.` указывает на данный checkout.

Все одноразовые пробы и синтетические данные: `/tmp/nedra-audit-notifications/`. Секреты, credentials и пользовательские данные не читались. Production, порт 18080, службы, контейнеры и volumes не использовались. Запрещённый podman start не повторялся и не обходился. Внешних HTTP-запросов и реальных уведомлений не было. Для настоящего HTTP использовался только Unix socket в указанной временной папке, без TCP listener; ASGI запросы выполнялись внутри процесса.

## Дефекты и исправления

### 1. Ошибка валидации отражала секрет из входного запроса

Воспроизведение на базе: ASGI PUT `/notifications/settings` с объектом вместо строки `webhook_url`, содержащим синтетический маркер, возвращал 422 и весь маркер в `detail[].input`. Проверка длины в обработчике не защищала от ошибок Pydantic, возникающих раньше обработчика. Неверный тип в другом поле также мог отразить URL.

Исправление: локальный `NotificationRoute` перехватывает `RequestValidationError` и возвращает постоянный текст 422 без входного тела, значений и resolver/exception text. Область действия — только notification routes.

После: настоящие ASGI запросы проверили неверный объект URL, неверный bool, null URL, лишнее поле и повреждённый JSON: **5/5 получили 422 без ввода и синтетического маркера**. Основное приложение и его middleware отдельно не запускались.

### 2. Выключенные настройки принимали заведомо испорченный URL

На базе `_prepare_settings(NotificationSettings(webhook_url=...))` принимал `:bad` и CR/LF внутри URL при `enabled=false`: `_check_structure` не обращался к `parts.port`, а urlsplit молча удаляет часть управляющих символов. Это сохраняло непригодную настройку вместо понятного отказа.

Исправление: проверять управляющие символы/пробелы и корректность порта до сохранения вне зависимости от opt-in. При включении сохраняется полная существующая policy-проверка.

После: **6/6 отклонены с 422**: нечисловой порт, 0, 65536, CR/LF, NUL, внутренний пробел. Пустое поле по-прежнему сохраняет текущий URL; отдельная чистая проба подтвердила включение с существующим literal-loopback URL без DNS/POST. Ошибки в старых сохранённых данных не мигрировались автоматически.

### 3. Stop возвращался раньше завершения собственного рабочего потока

Причина: отмена корутины, ожидающей `asyncio.to_thread`, не останавливает поток. `stop()` отменял loop и сразу завершал gather; lifespan после этого закрывает DB pool, хотя deliver ещё мог писать исход отправки.

Воспроизведение: реальный Python thread с threading.Event; старый `stop()` вернулся с `finished=false`. Для пробы не заменялись DB или HTTP интеграции: проверялось именно владение настоящим потоком.

Исправление: `_run_in_thread` удерживает shielded task до завершения потока, затем сохраняет отмену вызывающей корутины. Использован для initialize/claim/deliver; после остановки во время claim новый deliver не запускается, lease остаётся восстанавливаемым.

После: stop оставался незавершённым до освобождения настоящего потока; повторная отмена worker task также не позволила stop вернуться раньше. После release поток завершён и runtime.task=None. **Проба прошла.** Полный lifespan с PostgreSQL не запускался. Если операция БД зависнет без собственного timeout, корректная остановка также будет ждать её; глобальную timeout-политику БД этот модуль не меняет.

### 4. Workflow webhook ошибочно отвергал часть 2xx и зависел от тела ответа

Причина: `WebhookNotifier.send` признавал только 200/201/202/204, а `client.post` полностью буферизовал ненужное тело до чтения результата. Успешный приёмник с медленным/большим телом мог стать сетевой ошибкой после уже принятого сообщения.

Воспроизведение и проверка: настоящий httpx HTTPTransport, настоящий HTTP ответ по Unix socket; подменён только выбор транспорта для направления в Unix socket, HTTP ответ не мокался. Приёмник записал синтетический runId и исходный Host, реальных получателей нет.

- HTTP 206 с объявленным длинным телом и паузой дольше read timeout: база `('failed', None)`, исправление `('succeeded', 206)`.
- HTTP 203 с полным пустым телом: база `('failed', 203)`, исправление `('succeeded', 203)`.

Исправление: читать только заголовки через stream и признавать весь диапазон 2xx. Полный URL и тело ответа в DeliveryResult не попадают. DNS pinning, TLS/SNI по реальной сети, внешний receiver и бот не проверялись.

### 5. Повторная authorize того же lease снова разрешала POST

Подтверждено чтением исходного пути: после первой authorize `authorized_at` уже заполнен, но второй вызов с тем же действующим lease проходил все Python guards. UPDATE с `authorized_at IS NULL` обновлял 0 строк, однако функция безусловно возвращала action=send и выдуманное attempts+1. Штатный одиночный loop не вызывает функцию дважды намеренно, но контракт «одно разрешение на один lease» был нарушен.

Исправление: `authorized_at` проверяется в заблокированной строке, а action=send возвращается только по фактическому UPDATE RETURNING attempts. Заполненный authorized_at или нулевой UPDATE дают lost.

После: исходники разбираются, diff проверен. **Повтор двух реальных транзакций PostgreSQL не выполнялся; проверка этого исправления ограничена чтением кода.**

### 6. Opt-out мог потерять ещё не видимое завершение и разрешить старый повтор после opt-in

Последовательность по исходным SQL: completion trigger читал enabled=true и вставлял pending в незавершённой транзакции без dispatch lock; save_settings(false) не видел строку и заканчивал cancel UPDATE; completion затем коммитил pending. При повторном opt-in до claim старое событие снова подходило для отправки. Аналогично уже authorized отправка могла вернуть retry в pending после opt-out, а быстрое повторное включение разрешало старый retry. Потеря worker после opt-out добавляет путь через reclaim authorized lease.

Исправление в собственных модулях:

- Новый forward-файл `dispatch_guard.sql` добавляет BEFORE trigger на terminal status с тем же workspace advisory lock, что save_settings/authorize/finish. Вставка completion теперь сериализуется с отменой настроек.
- Новая колонка `retry_cancelled` фиксирует отмену будущих повторов для уже authorized строки при disable/смене URL/flags. Уже начатый запрос всё ещё записывает настоящий успешный или неуспешный исход.
- finish берёт workspace lock и не возвращает отменённый повтор в pending. authorize отказывает и после reclaim такого lease.
- initialize применяет forward-файл после неизменённого базового schema.sql в той же транзакции. `ADD COLUMN IF NOT EXISTS`, CREATE OR REPLACE FUNCTION и переустановка собственного trigger позволяют повторный startup.

**Это исправление не прошло PostgreSQL исполнение/конкурентную проверку.** SQL не применялся ни к одной БД. Перед интеграцией нужны: fresh и existing startup дважды; обе очередности completion/opt-out; finish(retry=True) одновременно с opt-out; disable/enable до finish; worker crash после authorization, opt-out и reclaim; 2 workspace, stale lease и attempts cap. Новая колонка не восстанавливает историю отмен, случившихся до установки исправления.

## Остальные проверенные границы

Чтением текущих исходников установлено:

- completion trigger охватывает succeeded/failed/cancelled/interrupted независимо от trigger_type: ручной, schedule, repository watch и push run при записи terminal status попадают в один inbox. Реальные schedule/watch/webhook runs в этом checkout не запускались.
- PK inbox `(workspace_id,run_id)` плюс ON CONFLICT DO NOTHING предотвращает повторную запись при повторном terminal UPDATE. Настройки и операции read/inbox scope явно используют workspace; join с runs/workflows содержит workspace, claim — намеренно серверный по всем workspace, authorize/finish защищены workspace+run+lease. Практический cross-tenant SQL прогон не выполнен.
- Completion payload содержит конкретный runId, executionStatus, securityVerdict, completedAt, stable eventId `completion:<runId>` и Idempotency-Key. Это at-least-once: crash после приёма POST, но до finish допускает повтор; receiver должен дедуплицировать ключ. Для совпадающих runId разных workspace receiver должен разделять назначения: ключ не содержит workspace.
- Завершённые cancelled/interrupted runs не выдаются за succeeded; HTTP 3xx не следует redirects; 408/429/5xx и сетевые ошибки допускают не более трёх авторизованных попыток. Timing/restart/backoff/journal честность на БД остались непроверенными.
- Workflow webhook имеет другой контракт и не является durable retry outbox; сам по себе не даёт exactly-once. Его runId и X-Run-ID позволяют привязку к run.
- UI читает item.run_id и передаёт его onOpenRun; браузерный переход к реальному run не проверялся.
- Literal-IP policy реально выполнена: **11/11 ожидаемых решений** (opt-in отсутствует, loopback v4/v6, private, metadata/link-local, CGNAT, внешний HTTPS 443, HTTP/неверный порт, query/userinfo). DNS не вызывался. Это проверка существующей engine policy, engine не менялся.
- Реально выполнены `_public_settings`, mask_destination и IPv6 `_pinned_url`: полный URL скрыт, userinfo/path/query/fragment отсутствуют в маске, pinned IPv6 заключён в скобки.

## Переданный межобластной дефект

`backend/app/engine/executor.py:873`: каждая NOTIFY-нода использует `delivery_id=f"del_{run_id}"`, хотя `runtime.deliveries` имеет PK `(workspace_id,id)`. Валидатор не запрещает несколько notify-нóд, значит вторая запись одного run конфликтует с первой; внешний POST уже мог успеть начаться. Передано координатору `msg_3b9797eae1e1`, engine не изменялся. Практический executor+DB прогон недоступен.

## Команды и фактические результаты

Использованный Python указан выше; всем прогонам заданы `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.`.

```text
git rev-parse HEAD
35d27d30ae44fd955824001e18eff895197c933c (перед работой)

python /tmp/nedra-audit-notifications/local_probe.py
real HTTP over local Unix socket (no TCP listener): baseline= ('failed', None) fixed= ('succeeded', 206)
ASGI malformed-settings: 5/5 controlled 422; input not echoed
disabled destination structure: 6/6 rejected
blank keep-URL / public redaction / IPv6 pinning: passed
stop waits for owned thread including repeated cancellation: passed
ALL LOCAL PROBES PASSED

python /tmp/nedra-audit-notifications/status_probe.py
HTTP 203 complete empty response: baseline= ('failed', 203) fixed= ('succeeded', 203)

python /tmp/nedra-audit-notifications/pure_probe.py
real policy, literal IP only, no DNS/network: 11/11 expected decisions

ast.parse всех backend/app/notifications/*.py
all notifications Python sources parsed

git diff --check
exit 0, без замечаний
```

Сначала Unix socket bind отказал EPERM в sandbox; после разрешённой escalation та же локальная проба прошла, TCP-порты не открывались. Orca runtime также потребовал разрешённую escalation после runtime_access_denied; доставлены сообщения координатору. Это не отказ automatic approval reviewer, запрещённые контейнерные действия не выполнялись.

## Открытые блокеры и дальнейшая приёмка

1. Нужен разрешённый изолированный PostgreSQL: forward SQL, tenant isolation, реальные retries/leases/opt-out races не проверены исполнением. Об этом отправлен blocker `msg_4cf706627cf5`; существующие исторические отчёты не использованы как доказательство.
2. Межобластной delivery_id конфликт должен исправить владелец executor.
3. Реальные TLS/DNS, внешний webhook, Telegram и production run не проверялись и в рамках данного задания наружу не отправлялись. Внешняя доставка здесь не объявляется успешной.

Локальные дефекты исправлены и проверены в описанных границах; полную готовность уведомлений этот отчёт не подтверждает.

## Дополнение: контракт runtime upgrade и блокировок

Повторное поручение координатора: task_6bff2febb34e, base этого продолжения `f673bea9f6151e7f2a0df16a44d7e03e59e92ab8`. Новые правки затрагивают только `dispatch_guard.sql`, `store.py` и этот отчёт. PostgreSQL по-прежнему не запускался.

При первом forward upgrade существующей notification schema теперь применяется fail-closed переход: если `retry_cancelled` ещё нет, добавляется колонка, все legacy pending/sending помечаются `retry_cancelled=true`, а ещё не authorized строки переводятся в skipped с очищенным lease. У legacy строки нет достоверной истории opt-out, поэтому её согласие нельзя доказывать текущим enabled=true. Authorized строка сохраняет фактический исход уже начатой отправки, но её retry запрещён даже после reclaim. Повторное применение forward-файла при существующей колонке **не** отменяет новые pending строки. История delivered/failed/skipped не переписывается. Это точечное намеренное изменение поведения при первом upgrade: legacy очередь не возобновляется автоматически.

Дополнительно обнаружен статический дефект времени lease: `authorize` начинал транзакцию, ждал workspace advisory lock и сравнивал lease с `now()` — временем начала транзакции. Длительное ожидание могло разрешить уже просроченный lease. Сравнение изменено на `clock_timestamp()` после получения lock и строки. PostgreSQL воспроизведение этого случая остаётся в списке обязательных проверок.

### Exact upgrade contract для владельца data

1. Использовать точные файлы `backend/app/notifications/schema.sql` (неизменённый base) и `backend/app/notifications/dispatch_guard.sql` (новый forward upgrade). Их последовательно исполняет текущий `store.initialize`, включая существующий runtime v5; по просьбе координатора владелец data также должен включить этот контракт в свой fresh/existing runtime upgrade, до начала dispatch.
2. Prerequisites: существуют `data_contract.workspaces` и `runtime.runs` с `(workspace_id,id)`; base notification SQL создаёт настройки/outbox, `authorized_at` и capture trigger. Forward SQL требует эти объекты, создаёт retry_cancelled и BEFORE lock trigger; отдельно без base SQL его запускать нельзя.
3. Base + forward должны коммититься атомарно. В notification initialize уже берётся `pg_advisory_xact_lock(hashtext('nedra-completion-notifications-v1'))`; любой второй путь установки должен использовать **этот же DDL lock**, чтобы не устанавливать их параллельно. Ошибка SQL должна оборвать startup/upgrade; запрещено проглатывать ошибку и запускать dispatcher со старой схемой.
4. Исторические ledger migrations и базовый notification SQL не менять. При добавлении в общий versioned runtime upgrade следует ссылаться на forward-файл и учитывать, что notification initialize всё ещё применяет тот же идемпотентный контракт.
5. После upgrade проверить существование boolean NOT NULL DEFAULT false `retry_cancelled`, включённый BEFORE trigger `lock_run_completion_dispatch` на `runtime.runs` и capture trigger, а также сохранность старых финальных rows и отмену legacy active rows. Полагаться только на runtime version marker недостаточно.

### Единый порядок блокировок и отмена повторов

Dispatch lock: `pg_advisory_xact_lock(hashtext('nedra-completion-notifications-dispatch'), hashtext(workspace_id::text))`.

- `save_settings`: workspace lock → read/write notification_settings → UPDATE completion rows. Не берёт run row locks.
- `authorize`: workspace lock → completion row FOR UPDATE → settings read → guarded UPDATE RETURNING. Проверяет текущий lease, token, status, отсутствие authorized_at и retry_cancelled до action=send.
- `finish`: workspace lock → completion row FOR UPDATE → guarded UPDATE. При retry_cancelled оставляет фактический failed вместо pending, успешный HTTP остаётся delivered.
- Completion: BEFORE terminal trigger берёт workspace lock до AFTER capture INSERT. Для UPDATE PostgreSQL уже может удерживать run row lock; остальные notification пути не берут run row locks, поэтому обратной зависимости run-row/workspace-lock внутри этой области нет.
- `claim`: только completion row через SKIP LOCKED; **не запрашивает workspace lock в той же транзакции**. Она завершается до authorize. `abort` также использует только completion row. Не добавлять workspace lock после уже взятого completion row lock.

`retry_cancelled` — необратимый признак отмены именно события, а не временная проверка текущего enabled. Opt-in не снимает его; новые terminal runs получают новую строку со значением false. Поэтому отдельный settings generation счётчик здесь не нужен: pending/unauthorized события при opt-out окончательно skipped, authorized события сохраняют исход и навсегда лишаются повторов, включая reclaim после смерти worker. SQL correctness этого порядка и свежесть snapshot capture после ожидания BEFORE lock требуют живого PostgreSQL прогона.

### Точный список оставшихся DB сценариев

- Fresh install, upgrade base runtime v5 без notifications и upgrade существующих notification rows; повторный startup не отменяет новые pending и не меняет terminal history; ошибка forward SQL не допускает startup dispatcher.
- Legacy pending и sending без authorized_at → skipped; legacy authorized sending → разрешён только настоящий finish, retry/reclaim не отправляет заново; delivered/failed/skipped/read_at не меняются.
- Completion получает lock первой: opt-out ждёт и затем видит/отменяет pending; opt-out первой: completion ждёт и записывает skipped по новым настройкам. Проверить также INSERT уже terminal run и повтор terminal UPDATE без duplicate inbox.
- Авторизация до opt-out: успешный finish → delivered, неуспешный с retry=True → failed без повторов; opt-out до authorize → skip/no action=send.
- Disable → enable того же URL до finish и до reclaim: старый retry не оживает. Смена URL/flags → возврат исходных значений: тот же запрет.
- Finish(retry=True) выигрывает lock до disable: pending затем отменяется; disable выигрывает раньше finish: durable retry_cancelled запрещает pending.
- Crash после authorize, lease expiration, opt-out/opt-in, reclaim: retry_cancelled сохраняется; старый token не может finish/authorize; новый worker не отправляет отменённое событие.
- Две authorize одного token → ровно одна send, фактический attempts увеличивается один раз; ожидание workspace lock дольше lease → lost по clock_timestamp, без POST.
- 408/429/5xx/network failure максимум 3 авторизованные попытки, delayed pending, restart recovery; успешный, skipped и exhausted run не claim-ятся снова.
- Два workspace с одинаковым run_id: settings/read/cancel/finish одного не меняют другой; параллельный claim не выдаёт одну строку дважды; чужой lease не записывает исход.

В этом продолжении реально повторены literal-IP policy 11/11, ast.parse всех Python notification файлов и git diff --check; все прошли. Эти команды не доказывают выполнение новых SQL веток, миграции или гонок.

## Integration delta: единый владелец notification schema

Поручение task_7319f176e7f6, исходный HEAD `52c34600884b1fba8ca96c875bf1d6ddc120689e`. По согласованному с data contract `storage.database.initialize_notification_schema(conn=None)` теперь единственный владелец установки notification schema. `notifications.store.initialize()` только вызывает этот helper без conn; собственное открытие connection, raw SQL, DDL lock и чтение SQL-файлов из store удалены вместе с неиспользуемым импортом Path. Исключения не перехватываются: ошибка helper прерывает runtime startup.

Обязанность helper при conn=None — владеть своей транзакцией/закрытием; при переданном conn из init_db — исполнять атомарно base schema.sql и exact forward dispatch_guard.sql под согласованным notification DDL lock в текущей транзакции без самостоятельного commit/close. Runtime v5→v6 принадлежит владельцу data. Предыдущий контракт прямого исполнения SQL в store.initialize заменён этим делегированием; содержимое SQL этим integration delta не менялось. `database.py` и data checkout не изменялись, data ветка не мерджилась.

Фактически выполнено: ast.parse всех notification Python файлов прошёл; git diff --check прошёл; реальный import notifications завершился ожидаемым `ImportError: cannot import name 'initialize_notification_schema' from 'backend.app.storage.database'`, потому что data helper ещё отсутствует в этом изолированном checkout. Это **pending combined verification**, а не успешный запуск приложения. Этот коммит нельзя использовать отдельно от соответствующего data commit. После объединения обязательны настоящий import/startup, свежая и existing v5→v6 установка, повторный startup, исключение helper с отказом startup и остальные перечисленные PostgreSQL проверки. Никакие старые результаты локальных проб не означают прохождение текущего combined import.
