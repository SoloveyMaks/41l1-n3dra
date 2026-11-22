# Полный аудит: engine, scenario builder, contracts, demo

## Ревизия и метод

- База задания: `35d27d30ae44fd955824001e18eff895197c933c` (dev).
- Проверенное состояние кода: `297926454464dfb3636130bfdaf0c288e3080a5a`.
- Рабочее дерево после проверок: чистое. Этот отчёт добавлен отдельным коммитом после `2979264`.
- Область: `backend/app/engine/`, `backend/app/scenario_builder/`,
  `backend/app/contracts/`, `backend/app/api/scenario_builder.py`,
  `backend/app/api/sql_pattern.py`, `backend/app/demo/`.
- Дополнительно по прямому указанию пользователя исправлен heartbeat
  `backend/app/telegram/runtime.py`; остальные файлы Telegram не менялись.
- Не менялись: `backend/app/miniapi.py`, custom-checks sandbox, миграции,
  production, контейнеры, службы, порты, внешние приёмники.

Проверка велась чтением owning-кода и одноразовыми живыми пробами в
`/tmp/nedra-audit-engine/`: реальные модули engine/scenario/contracts/demo,
реальный Node CLI, in-memory замена только для PostgreSQL-репозитория.
Полный HTTP/DB e2e не выполнялся: доступной тестовой PostgreSQL нет, запуск
`podman start nedra-mini-integration-pg` ранее был отклонён и не повторялся.

## Найденные и исправленные дефекты

### 1. Отмена запуска не финализировала run/node/report; очередь была без предела

Коммиты: `8f048df` (executor), `5c7690d` (SSE).

Исходная причина по коду: `execute_run` держал только `async with self._run_sem`
без предела ожидающих запусков и без обработки `CancelledError`. Отмена
внешней задачи оставляла `runtime.runs.execution_status=running`; узел не
получал статус `cancelled`; отчёт сохранял `executionStatus=running`.
`asyncio.to_thread` и SSE-запись при отмене могли оборваться, не дождавшись
потока/записи.

Исправление:

- предел ожидающих запусков (`MAX_QUEUED_RUNS`, таймаут очереди) и явный
  terminal-статус для переполненной очереди;
- `_await_operation` удерживает задачу через `asyncio.shield` и дожидается
  завершения потоковой операции перед пробросом `CancelledError`;
- отмена фиксирует `node_run.status=cancelled`, `runtime.runs.execution_status=interrupted`,
  SSE `node_status`/`run_status`, переписывает `executionStatus` уже
  сохранённого отчёта и readable Markdown;
- завершённый terminal-статус (`succeeded/failed/interrupted/cancelled`) не
  перетирается поздней отменой;
- SSE: запись seq и рассылка сериализованы, payload маскируется до сохранения,
  добавлены `timestamp`, heartbeat и добор событий из БД; медленный подписчик
  отключается при переполнении очереди и переподключается с `Last-Event-ID`.

Живая проверка (`probe.py`, 50/50): отмена во время узла дала
`run=interrupted`, `node=cancelled`, SSE обоих уровней и отчёт
`executionStatus=interrupted`; поздняя отмена сохранила `succeeded` и не
отправила `interrupted`. Отмена ожидающего запуска дала `interrupted` с
сообщением «Ожидание запуска прервано». Переполнение очереди дало `failed`.
Отдельная проба `_await_operation(asyncio.to_thread(...))` подтвердила, что
отмена ждёт завершения потока (`elapsed≈0.35s` при `sleep 0.35`), а не
освобождает слот сразу.

### 2. Остановка планировщика не дожидалась задач

Коммит: `e0c17e3`.

Исходная причина: `WorkflowScheduler.stop()` только вызывал `cancel()` и
очищал наборы; цикл, расписания, watch-проверки и hook-доставки не
дожидались завершения. Поздние исключения фоновых задач никто не читал.

Исправление: добавлен awaitable `WorkflowScheduler.aclose()`; `stop()`
оставлен синхронным сигналом. `aclose()` снимает задачи до очистки, отменяет
их и ждёт `gather`, включая `RepositoryWatchService.pending_tasks()`.
Done-callbacks логируют необработанные исключения вместо тихого
`Task exception was never retrieved`.

Проверка: живой прогон `start()` → `aclose()` завершил оба цикла, отменил и
дождался искусственно добавленной dispatch-задачи, очистил наборы. Прогон
расписания с fake-DB создал и выполнил run, после чего `_active_schedules`,
`_active_runs` и `_schedule_tasks` пусты.

### 3. SQL-pattern мог писать неограниченный вывод и не ограничивал окружение

Коммит: `3641d88`.

Исходная причина: `subprocess.run` писал stdout/stderr в файлы, а лимит
проверялся только после завершения процесса; отрицательные/нечисловые
переменные окружения могли сломать семафор или таймаут; при таймауте не было
слива обоих pipe.

Исправление: `_bounded_env`, чтение обоих потоков с лимитом и уничтожением
процесса при превышении, явный `kill/wait`, освобождение семафора в `finally`.

Проверка реальным Node 22 и настоящим `packages/comparison/sql-rule-cli.mjs`:
неверный envelope дал non-zero и `SQL rule comparison rejected`; вывод >2 КиБ
дал `SQL_PATTERN_OUTPUT_TOO_LARGE`; `sleep 5` при таймауте 0.2 с был убит;
полный `sql_pattern_proof` с настоящим node-процессом и временным CLI вернул
распарсенный JSON.

### 4. Слот scenario-builder освобождался до завершения потока провайдера

Коммит: `3641d88`.

Исходная причина: при timeout/отмене `inflight` уменьшался в `finally`, хотя
блокирующий поток `builder.preview` продолжал работать; семафор
освобождался позже. Лимит одновременных запросов не отражал реально занятые
потоки.

Исправление: `deferred_release`; при timeout/отмене `inflight` уменьшается
только в callback завершения потока, одновременно с семафором.

Проверка: отмена запроса при `max_concurrent=1, max_queue=0`; пока поток
работал, новый запрос корректно получил 429, после завершения потока —
успешный preview.

### 5. Telegram heartbeat не останавливал задачу при потере доступа

Коммит: `2979264` (по прямому указанию пользователя).

Исходная причина: `_heartbeat_job` при `heartbeat_job() == False` просто
возвращался; parent job продолжал импорт/запуск со старым lease/владельцем.
Один временный сбой БД навсегда прекращал heartbeat.

Исправление: heartbeat получает parent task и отменяет его при потере
lease/владельца; временное исключение БД логируется, цикл продолжается.

Проверка: две живые пробы с fake-repo: потеря доступа отменила parent;
исключение на первом heartbeat не остановило цикл, второй heartbeat отменил
parent. Штатный `backend/tests/test_telegram_buttons.py` — 95/95 (DB-проба
пропущена без `TELEGRAM_TEST_DATABASE_URL`).

### 6. Несколько NOTIFY перезаписывали доставку друг друга

Коммит: `8f048df`.

Исходная причина: `delivery_id=f"del_{run_id}"`; второй NOTIFY в том же run
перезаписывал первую доставку.

Исправление: `del_{run_id}_{node.id}`; SSE `delivery_status` получает
`node_id`.

Проверка: два NOTIFY-узла в одном run дали
`del_run-report_n1` и `del_run-report_n2`.

### 7. Preview с AI_SCAN не связывал гипотезы с отчётом

Коммит: `eea7891` (уже был в HEAD до этой сессии; проверен заново).

Исправление: preview добавляет `AI_SCAN → BUILD_REPORT.ai_scan_result` и
отчёт, даже если пользователь выбрал только `ai_scan`; executor передаёт
`ai_scan_result` в отчёт напрямую и имеет fallback из сохранённого node-run;
`ReportBuilder` выводит гипотезы отдельным advisory-разделом и
`aiScan` в official JSON с `countsAsFindings=false`, `isHypothesis=true`.

Проверка: preview с `intents=["ai_scan"]` содержит AI_SCAN, отчёт и ребро на
`ai_scan_result`, проходит валидатор; полный набор intents даёт 11 узлов,
16 связей и валиден. `_run_node_logic(BUILD_REPORT)` с прямым и fallback
AI_SCAN сохранил Markdown с предупреждением «не подтверждены» и official
JSON `aiScan.status=partial`.

### 8. Валидатор пропускал некорректные id, порты, типы и конфиги

Коммит: `eea7891` (уже был в HEAD; проверен заново).

Исправление: schemaVersion, пустые/дублирующиеся node/edge id, два ребра в
один вход, обязательные входы AI_EXPLAIN/NOTIFY/BUILD_REPORT/BRANCH_MERGE,
типы конфигов POLICY_GATE/NOTIFY/PROJECT_INPUT/CONDITION, regex и конечные
числа, запрет `model`/ключей в AI_SCAN и границы budget, ограничения
совместимости CONDITION_BRANCH.

Проверка: два ребра в `scan_1` отклонены; `artifact → report` отклонён по
типу; AI_SCAN с `model` и `maxFiles=999` отклонён; default и preview валидны.

### 9. Публикация canonical брала устаревший workflow вместо revision run

Коммит: `8f048df`.

Исходная причина: `_publish_canonical` читал `get_workflow(workflow_id)` и
мог опубликовать имя/definition текущей, уже изменённой ревизии, а не той,
на которой выполнялся run.

Исправление: публикация читает `get_workflow_revision(revision_id)` и
собирает workflow из draft ревизии; отсутствующая ревизия даёт явный
`publication={"published": false, "reason": "workflow revision missing"}`.

Проверка: временный `CodeFactsStore` и fake publisher; опубликовано имя из
revision, а не stale workflow; для отсутствующей ревизии записан
`publication=false` с причиной.

### 10. Проверенные без изменений пути

- Выбранный артефакт run имеет приоритет над `project_input.config.artifactId`:
  live-проба вернула `B` при config `A` и run artifact `B`.
- `default_workflow_upgrade`: замороженный SHA совпадает; нетронутый default
  обновляется новой ревизией с сохранением artifactId; пользовательская
  правка не перезаписывается; `--add-ai-scan` сохраняет правку и валиден.
- `backend/tests/test_responsibility_users.py`: 13/13 OK (чужой модуль, только
  регрессионный прогон).
- Голосовой ввод: отдельного backend endpoint нет; браузерный transcript
  отправляется в тот же `/scenario-builder/preview`, где применяются лимиты
  длины и allowlist intents. Frontend-часть не входит в эту область.
- P4/P5: в репозитории нет документов/меток P4/P5; проверены соответствующие
  коду пути (revision/draft/artifact/run, validation, executor/SSE/scheduler).

## Команды и фактические результаты

```text
python3 -m compileall -q backend/app/engine backend/app/api \
  backend/app/scenario_builder backend/app/contracts backend/app/demo \
  backend/app/telegram/runtime.py
→ exit 0

/tmp/nedra-audit-engine/.venv/bin/python /tmp/nedra-audit-engine/probe.py
→ 50/50 passed

/tmp/nedra-audit-engine/.venv/bin/python backend/tests/test_telegram_buttons.py
→ 95/95 checks passed (DB probe skipped)

/tmp/nedra-audit-engine/.venv/bin/python backend/tests/test_responsibility_users.py
→ Ran 13 tests, OK

git status --short
→ пусто
```

Коммиты исправлений:

```text
3641d888582daee32e3fd080e20b3b6a66eacbf5  Недра: ограничить параллелизм и ожидание фоновых проверок
5c7690da2ee535a40212377622917634229dc49a  Недра: не терять события SSE и закрывать медленных подписчиков
8f048df4338d2016a3500c38c579ede3b0174ddb  Недра: ограничить очередь запусков и финализировать отмену
e0c17e3a5dac5f0a40e27ba6e0c2d21f12c68635  Недра: дождаться фоновых задач планировщика при остановке
297926454464dfb3636130bfdaf0c288e3080a5a  Недра: останавливать Telegram-задачу при потере доступа
```

## Открытые блокеры и ограничения

1. **Нет доступной PostgreSQL.** Полный HTTP/lifespan/scheduler e2e и реальные
   транзакции не проверены. Запуск `podman start nedra-mini-integration-pg`
   ранее был отклонён; повторять и обходить отказ нельзя. Пробы executor/SSE/
   scheduler используют in-memory репозиторий, реальные SQL/lease-гонки не
   доказаны.
2. **Frontend не принимает AI_SCAN-preview.** В
   `frontend/src/api/scenarioBuilder.ts:62` у `build_report.inputs` нет
   `ai_scan_result`, хотя backend и registry его отдают. `isPreview` отклонит
   валидный граф, Apply будет недоступен. Файл принадлежит frontend-владельцу.
3. **Интеграция shutdown.** `backend/app/main.py:115` всё ещё вызывает
   синхронный `scheduler.stop()`. Нужен `await scheduler.aclose()` (например,
   через `AsyncExitStack.push_async_callback`) на точном SHA `2979264` или
   новее. `aclose()` идемпотентен и сохраняет `stop()` как сигнал.
4. **Ручные запуски не отслеживаются.** `backend/app/api/router.py:485`
   создаёт задачу `asyncio.create_task` без хранения; `scheduler.aclose()` её
   не видит. При остановке приложения такая задача может быть отменена без
   ожидания. Нужен tracking на уровне API/executor; файл вне этой области.
5. **Custom-checks sandbox** (`reference_checks`/custom checks) не аудировался
   и не менялся: вне владения. `backend/app/api/comparison.py` сохраняет
   старую схему записи вывода в файлы; это отдельная область, здесь не
   исправлялось.
6. **Telegram** проверен только на уровне heartbeat/runtime fake-repo; реальные
   Bot API/БД и смена владельца не запускались. Сетевые вызовы с побочными
   эффектами не выполнялись.

Отчёт не содержит секретов, токенов, URL с credentials или пользовательских
данных. Все временные пробы и данные: `/tmp/nedra-audit-engine/`.
