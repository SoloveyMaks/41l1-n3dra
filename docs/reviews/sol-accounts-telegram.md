# Sol review: accounts, tenant isolation and Telegram

Дата проверки: 2026-11-21. Проверял отдельной веткой после fast-forward merge локальной `dev`; исходный проверенный HEAD: `75c2f09aaa9d2fd49c3de408084be8b8688638ac` (до последующего owner fix). Использовались отдельные PostgreSQL 16/pgvector контейнер и ASGI-приложение; production-порты не трогались. Все приведённые итоговые Telegram proofs повторены с синтетическим токеном и локальным либо отключённым транспортом.

## Итоговая targeted acceptance: 024c300 + 4ab9621

**Все конкретные findings этого backend account/Telegram review закрыты в проверенном scope.** Финальный source HEAD review: `fb4027cde8aa816b411b2d0e1aeb40663a59530c`, содержит exact owner fixes `024c300bd13093fceb24796aef794f26ba7d62ba` (cancellation guard cleanup), `4ab96214d51044eef4a03e9d8505b5d13d7ecc87` (общий recovery limit), `a5df48c301a7eddbfdb71dbd883e261e4cada366` (off-loop DB waits), `4e9e5731c62033b7558ee7563e60890c06a2c9d2` (recovery/leases) и `f82cd4b6ff17a2a3947d62cf7562bff9cd267a5d` (rebind owner/epoch). Это принятие перечисленных исправлений, а не доказательство внешней Telegram-доставки или общей production readiness. Ниже сохранена история находок на старых SHA.

Повторён независимый realPG proof исходной cancellation leak с настоящим `_deliver_outbox` и rebind; внешнего Telegram send не было, credentials синтетические:

* Same-loop guard/rebind: timer **0.200 с**, ticker **11** шагов, max gap **0.021 с**; rebind дождался lock, старый owner/epoch после переключения guard не прошёл.
* Acquire thread заблокирован реальным holder transaction; `_deliver_outbox` отменён **дважды**, пока lock занят. После unlock holder задача корректно завершилась отменой. Новый `SELECT ... FOR UPDATE` с `lock_timeout=200ms` успешен, `idle in transaction` guard сессий **0**.
* Pool после cleanup: size **3**, available **3** (holder и worker connection вернулись); настоящий новый rebind завершился с правильным owner.
* Отдельно настоящий `_release_delivery_guard_safe` ждал очереди worker-а; две отмены вызывающей coroutine не помешали release. Binding row снова свободен, все pool connections доступны.
* На этом же source HEAD total recovery limit повторно прошёл: `[1,2,3,4]` для четырёх exhausted job при четырёх `limit=1`, mixed buckets меняются по одной строке с сохранением run_id, два конкурентных прохода изменили ровно две строки, финальный queued claim один.

Отмена во время реального внешнего HTTP send самостоятельно не проверялась: проверены реальные acquire/cleanup/release и повторные cancellation без подмены Telegram API. Ранее принятые account/tenant, rebind epoch и lease fencing proofs не прогонялись целиком повторно, их owning logic в последнем cleanup fix не менялась. Сохраняется описанное ниже ограничение переноса аккаунта в другой chat: Telegram history каскадно удаляется, runtime run/report этим кодом не удаляются.

## История targeted rechecks

## Targeted recheck: общий recovery limit 4ab9621

Точный owner commit `4ab96214d51044eef4a03e9d8505b5d13d7ecc87` влит в review HEAD `f6ffaba8f67d2b82fb715b69f90002d3dbe3ad60`. Подтверждённый дефект неограниченного exhausted UPDATE **закрыт**. В `recover_jobs` теперь один общий `SELECT ... FOR UPDATE OF j SKIP LOCKED LIMIT` для exhausted, run-backed settlement и retry candidates.

Независимый proof на новых disposable PG, настоящие repository/runtime/importer, `api=None`, запрещённый URL отклоняется до сети:

* Старое воспроизведение теперь даёт `RECOVERY_LIMIT1_EXHAUSTED_CHANGED 1` (раньше 4).
* Четыре exhausted jobs, четыре прохода `limit=1`: cumulative interrupted `[1, 2, 3, 4]`.
* Смешанные exhausted + running/run-backed jobs при `limit=1`: ровно одна изменённая строка за проход, existing run_id сохранён.
* Два конкурентных recovery с `limit=1` изменили ровно две из четырёх exhausted строк. Конкурентные returned queued candidates заканчиваются одним успешным `claim_job`.
* Повторены только recovery guards: два runtime/start подхватили queued ровно один раз; stale retry1/exhausted interrupted, heartbeat/current token и отказы wrong/superseded token, existing run_id без нового import/run, rebind отзывает leased job — всё прошло.

На момент этого recheck общая Telegram acceptance оставалась открытой из-за cancellation acquire leak; она закрыта финальным 024c300 recheck выше. Исходный same-loop deadlock уже закрыт. Внешние Telegram sends не выполнялись.

## Targeted recheck: event loop fix a5df48c

Точный owner commit `a5df48c301a7eddbfdb71dbd883e261e4cada366` влит в review HEAD `0c073808bed45eec83b5e0d0d3b185a7f405e802`. Исходный same-loop deadlock исправлен в повторённом proof: настоящий `acquire_delivery_guard` удерживал binding row lock, coroutine ожидала 0.2 секунды, параллельно настоящий `_handle_start` делал rebind. Получено `timer_seconds 0.201 ticks 11 max_gap 0.021`; loop продолжал работать, rebind дождался освобождения lock, старый owner/epoch после переключения не прошёл новый guard. ASGI/baseline/auth нагрузки не повторялись. **На этом историческом SHA fix ещё не принимался целиком: отмена acquire оставляла lock; исправлено финальным 024c300.**

### High — отмена доставки во время acquire теряет connection с binding lock

`backend/app/telegram/runtime.py:688-690` ожидает `asyncio.to_thread(acquire_delivery_guard)` до входа в внутренний `try/finally`, освобождающий connection (`:704-707`). Отмена coroutine не останавливает функцию, уже запущенную в OS thread. Если отмена пришла во время ожидания чужого binding lock, `guard_conn` ещё не присвоен; thread позже успешно делает `pool.getconn()`/`SELECT ... FOR UPDATE` (`backend/app/storage/telegram.py:797-811`), а результат никто не получает и `release_delivery_guard` не вызывается. Connection остаётся checked-out с открытой транзакцией и удерживаемым binding lock.

Воспроизведение без внешнего send: другой connection удерживал guard того же chat; запущен настоящий `_deliver_outbox` для реального run/outbox row, с синтетическим API credentials. `pg_stat_activity` подтвердил ожидание acquire. Coroutine отменена и завершилась `CancelledError`; исходный holder затем освобождён. Через 0.2 секунды новая транзакция с `lock_timeout=200ms` не смогла заблокировать binding: `CANCEL_DURING_ACQUIRE lockfree False`, найдена **1** `idle in transaction` guard connection. Для очистки только этой disposable PG сессии применён `pg_terminate_backend`; приложение/production не трогались. До API send выполнение не дошло.

Нужен cancellation-safe acquire: сохранить результат фонового acquire и гарантированно освободить connection, даже если ожидающая coroutine уже отменена. Потерянный guard блокирует будущий rebind до закрытия connection; это отдельный дефект от уже исправленной остановки event loop. Bounded exhausted recovery закрыт отдельным 4ab9621 recheck выше.

## Targeted recheck: recovery fix 4e9e573

Owner commit `4e9e5731c62033b7558ee7563e60890c06a2c9d2` проверен после merge локальной `dev` `091e7a44eb93e36b1a565a36fdd489ae2619b298` в review HEAD `dbc9a1a079aba6109c2003099223c9d68bfe7586`. Исходный дефект «нет recovery» исправлен в проверенных сценариях, но **на этом историческом SHA полностью bounded recovery не принят**: найдено нарушение лимита ниже. Same-loop deadlock исправлен отдельным a5df48c recheck выше, но cancellation acquire выявил утечку lock, закрытую финальным 024c300.

### Medium — exhausted jobs обрабатываются без лимита batch

`backend/app/storage/telegram.py:603-621`: первый `UPDATE` в `recover_jobs` переводит все expired importing jobs с `recovery_attempts >= max` в `interrupted`, без `LIMIT` и ограниченного списка ids. `safe_limit` используется только позднее для existing-run settlement и returned candidates. Воспроизведение на новой изолированной PG: создано четыре актуальных owner/epoch job, все claimed и затем переведены в expired lease с attempts=3; один `recover_jobs(limit=1)` изменил **4** job (`RECOVERY_LIMIT1_EXHAUSTED_CHANGED 4`). Это конкретное нарушение критерия bounded recovery и неограниченная транзакция при большом backlog; фактическое зависание на большом объёме не утверждается. Нужен ограниченный claim/settlement exhausted ids за проход.

### Независимый live proof recovery

Использованы новая disposable PG и настоящие `TelegramRuntime`, repository, executor/importer. `api=None`; importer получал HTTP localhost URL, который настоящий URL validator отклоняет до сети. Успешный внешний импорт не подменялся фиктивным ответом: это проверка recovery/claim/error path, не доказательство успешного download/scan.

* Два `Runtime.start()` без Telegram transport одновременно подхватили один queued job; реальная URL rejection произошла один раз, job стал `failed`.
* Два конкурентных PG claim одного queued job дали ровно один lease token. Heartbeat current token работает; неверный и заменённый после retry token не проходят heartbeat/status update/lease validity.
* Expired importing без run_id получил ровно один retry (`recovery_attempts=1`); exhausted стал `interrupted`. Фоновый recovery стартует даже при `api=None`.
* Jobs с existing run_id и expired lease: running run → job `interrupted`, succeeded/failed run → одноимённый terminal job. `run_id` сохранён, importer для этих jobs не запускался, общее число прежних runtime runs осталось **3**, новых run нет.
* Rebind отзывает current lease: heartbeat/lease validity false, cancelled job не claim-ится и не попадает в recovery candidates.
* Upgrade011→012 применил ровно `012_telegram_job_recovery.up.sql`; повтор `[]`. Старый running job и run_id сохранились, attempts=0, lease null; новый `interrupted` принят SQL constraint.

## Targeted recheck: rebind fix f82cd4b

Проверен owner commit `f82cd4b6ff17a2a3947d62cf7562bff9cd267a5d`, включённый в локальный `dev`/review HEAD `b53a3c7465f2930df0bad0f219eebe6e9530b75d`. Старый baseline/auth concurrency повторно не запускался. **На этом историческом SHA rebind целиком не принят: найден блокирующий дефект event loop. Restart recovery проверен отдельным recheck выше; ограничение exhausted batch пока не исправлено.**

### High — синхронный rebind блокирует event loop, пока sender держит delivery_guard

`backend/app/telegram/runtime.py:616-626` держит PostgreSQL binding row lock через `await self.api.send_message/send_document`. В том же event loop polling вызывает `_handle_start`, который синхронно вызывает `consume_binding_code` (`runtime.py:287`); тот ждёт тот же `SELECT ... FOR UPDATE` (`backend/app/storage/telegram.py:241-244`). Пока sync psycopg ждёт sender, event loop не может возобновить sender и освободить lock. DB lock timeout в storage не задан. Асинхронный HTTP timeout также требует работающего event loop, поэтому не разрывает этот цикл.

Независимое воспроизведение без внешней отправки, на настоящей PG: coroutine sender открыл реальный `delivery_guard(item)` и сделал `await asyncio.sleep(0.2)`; соседняя coroutine вызвала настоящий `telegram_runtime._handle_start(chat, None, new_binding_code)`. Watchdog в отдельном OS thread через 1.5 секунды увидел в `pg_stat_activity` ровно один заблокированный binding SELECT и отменил только этот запрос через `pg_cancel_backend`. Вывод: `200ms_timer_actual_seconds 1.504 blocked_rebind_connections 1 cancel QueryCanceled`. Без этой внешней отмены sender и rebind ждут друг друга; страдает весь ASGI event loop. Это не вывод из thread-only успешного lock proof: сценарий специально повторён в одном loop, как runtime polling/outbox.

Нужно сохранять сериализацию send/rebind, но исключить синхронное ожидание binding lock на event loop, включая соседние runtime/API пути. Изменения исходников reviewer не делал; дефект передан координатору.

### Что rebind fix уже исправил в живом proof

* A→B в том же chat: owner/workspace переключились, epoch вырос на 1, settings стали `both`/пустой scenario; B не видит jobs A, A `/api/telegram/me` вернул `bound=false`.
* Старые queued/importing jobs и pending/sending outbox стали `cancelled`; строки сохранились. Stale claimed job не считается current, не claim-ится повторно и не может заменить cancelled на succeeded.
* Stale owner/epoch не может создать job/outbox или обновить settings. Уже claimed delivery не проходит `delivery_is_current`/`delivery_guard`, `finish_outbox(sent)` не воскрешает cancelled. Настоящий `_deliver_outbox(old_item)` завершился до транспорта.
* B может выбрать свой scenario через ASGI; чужой scenario даёт 400. Новый job claim и новый outbox claim принадлежат B. Same-owner bind сохраняет epoch, выбранный scenario и pending outbox.
* Отдельный thread-only concurrency proof подтвердил SQL сериализацию: rebind ждал lock sender (ожидание видно в `pg_stat_activity`), затем прошёл после выхода из guard; guard старого item после rebind вернул false. Этот успешный DB proof не устраняет описанный выше same-loop deadlock.
* На отдельной legacy010 БД миграция применила только `011_telegram_rebind.up.sql`, повтор вернул `[]`; pending/sending отменились, sent/failed и queued job сохранились, owner/workspace/epoch проставлены, workspace sentinel сохранён.

Перенос того же аккаунта в другой chat действительно удаляет telegram jobs/outbox старого chat через существующий `ON DELETE CASCADE`. Это ограничение продукта «один активный chat»: Telegram delivery/job history пропадает, runtime run/report source этим кодом не удаляется. Утечку чужого report этот сценарий не доказывает. Внешний Telegram API и repo download в recheck не вызывались; синтетический env token не удалялся, transport отключён.

## Исходные findings на 75c2f09 (история до rebind fix)

### High — повторная Telegram-привязка переносит только owner, но оставляет состояние старого owner

В `backend/app/storage/telegram.py:227-239` `consume_binding_code` делает `ON CONFLICT (chat_id) DO UPDATE` для binding, но `telegram.settings` вставляет с `ON CONFLICT (chat_id) DO NOTHING`. Поэтому новый аккаунт, привязавший уже занятый chat, получает старый `scenario` и delivery mode. В `backend/app/storage/telegram.py:419-426` `list_jobs` фильтрует только по `chat_id`; `backend/app/telegram/runtime.py:342-369` показывает эти задачи и их `run_id` в `/status`.

Воспроизведение на реальной PG: аккаунт A привязал chat `80001`, выбрал свой workflow и создал job; аккаунт B привязал тот же chat своим одноразовым кодом. После этого `/api/telegram/me` под сессией B вернул scenario A, а `list_jobs(80001)` вернул job A (`user_id` A). Это утечка метаданных и неправильный выбор сценария; доступ к чужому report этим запуском не доказан. Отдельно проверка показала, что claims server-wide сами по себе атомарны, поэтому проблема именно в rebind cleanup/фильтрации.

### Medium — queued/importing Telegram jobs не восстанавливаются после перезапуска runtime

`backend/app/telegram/runtime.py:77-105` при `start()` запускает polling и outbox, но не читает незавершённые строки `telegram.jobs`; `_spawn_job` вызывается только из текущего процесса (`:407-415`), а `claim_job` в `backend/app/storage/telegram.py:361-377` принимает только `status='queued'`. После аварийного завершения процесса уже claimed job остаётся `importing`, и следующий runtime не может его claim-нуть. Штатная отмена внутри `_run_job` отдельно переводит job в `cancelled` (`runtime.py:469-474`); это не доказательство crash recovery.

Воспроизведение: в отдельной PG сохранены реальные строки со статусами `queued` и `importing`; создан новый `TelegramRuntime` с настоящим `TelegramBotAPI`, синтетическим токеном и локальным недоступным HTTPS endpoint. После `start()` через 1.2 секунды статусы остались `['queued', 'importing']`, `spawned=0`, внешний статус `blocked`. Это подтверждает потерю фоновой работы после crash/restart; внешняя доставка и скачивание репозитория не запускались.

## Что подтверждено рабочим

* Регистрация двух обычных `role=user` аккаунтов дала разные workspace, cookie v3 и scrypt-хэши `scrypt$16384$8$1$...` с уникальными солями; logout сделал старый cookie непригодным. Неверный пароль дал 401, правильный login 200; после `disabled_at` текущая account-сессия получила 401.
* Реальный ASGI/PG workflow с SECRET_SCAN и недоступным AI provider завершился детерминированными findings и report; AI стал `degraded`, гипотезы не попали в deterministic findings.
* Чужие workflow/artifact/run/nodes/findings/security-map/evidence/report/events возвращали 404; чужой artifact нельзя было использовать для собственного запуска. Параллельные 80 list/me запросов не смешали tenant и ContextVar сбросился.
* Обычный пользователь мог менять собственные schedule/watch (200), чужие давали 404; demo user получал 403; account admin/webhook logs закрыты 403. Server-wide schedule/watch/hook claims на двух workspace отдали по одной задаче без двойного claim, а чужое завершение hook lease не сработало.
* Подписанный webhook принимался 202, replay становился `duplicate`, неверная подпись 401; доставка сохранялась в workspace watch. Binding code нельзя было повторить в другом chat, а demo binding возвращал 401.
* Переход миграций со схемы 001–007 на 008/009/010 применил ровно новые три файла, сохранил sentinel workspace; повторный `ensure_contract_schema` вернул `[]`. Повторная инициализация repository успешна.
* Локальный ephemeral режим (`CG_PUBLIC_DEMO=0` без session secret) не подмешал account workspace в demo: account видел свой workflow, локальная сессия — только настроенный env workspace.

## Ограничения проверки

Успешная реальная Telegram-доставка не доказана; GitHub/GitLab download и Kotlin/Gradle execution в этом review не запускались. Проверялись локальные ASGI маршруты, SQL claims и lifecycle на синтетических данных; ограничения importer проверены чтением owning source, без повторения предыдущего baseline-аудита. При первом черновом smoke env-токен был ошибочно удалён, что позволяло factory подхватить сохранённую конфигурацию; подтверждённой отправки нет, секреты в вывод не попали. Следующий запуск отклонил automatic approval review, скрипт исправлен, итоговые proofs выше получены отдельным безопасным повтором. UI accounts screen в этот review не входил. Исходный rebind leak закрыт проверенными owner/epoch guards, same-loop deadlock исправлен a5df48c; cancellation acquire lock leak закрыта финальным 024c300, exhausted recovery batch — 4ab9621. Конкретных открытых findings в этом scope не осталось.
