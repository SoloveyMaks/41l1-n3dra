# Ограниченное независимое ревью импорта и наблюдения репозиториев

Проверяющий: Sol; 2026-11-21. Исходная база — `334e7bde6f10dfe63075380642b7b8af1b7a72b2`, обычный merge в отдельный worktree; затем `3fdb149b2d73f143ab9f9ad1a8d7068fd20b0307` и объединённая UI/backend база `98a86499`. Финальная проверка включает `4aebaadf57defe0551e9782c6488bce6659a6ece` (атомарный старт), `b8db040407d01bddece2d6a42bdda37e642b1540` (URL400), `7d4325e6be4dbb78661c7aee820bee1ac23abdfc` (догрузка артефакта), собственный merge `dad835d990f39f6271d6199475f7339a65cb8cc1`. Контракт — `/tmp/nedra-repository-contract.md`. Исходники не менялись; этот отчёт — единственный собственный файл репозитория.

## Вердикт

**В проверенных границах backend можно принимать с описанными ограничениями восстановления уже начатых запусков.** Найденные повторное исполнение после lease и HTTP 500 исправлены и независимо перепроверены; регрессия отмены до старта не обнаружена. Исправление отсутствующего артефакта UI подтверждено чтением исходников; живая проверка этого UI выполнена владельцем, моя браузерная попытка не состоялась из-за недоступного стенда. Полного независимого UI acceptance этот отчёт не утверждает; новый пользовательский редактор находится вне этого ревью.

## Находки и повторные проверки

### Повторное выполнение одного run после истечения lease — исправлено в 4aebaad

`backend/app/engine/scheduler.py:459–489`, `backend/app/storage/database.py:1051–1065`, `backend/app/engine/executor.py:60–90`.

Dispatcher помечает ledger как running и получает lease на 1800 секунд, затем ждёт свободного executor semaphore. В runtime run пока остаётся queued. Если ожидание превышает lease, второй процесс/recovery получает тот же run, проверяет queued и тоже встаёт в очередь executor. После освобождения semaphore оба вызывают `_execute_run`: внутри него нет атомарного queued→running сравнения и смены статуса. Один SQL claim в обычном коротком сценарии эту гонку не исключает.

Собственное воспроизведение: `/tmp/sol-repositories-proof/leaseprobe.py`, результат `lease.json`. Настоящие PG, DatabaseRepository, два RepositoryWatchService и настоящий WorkflowExecutor; до dispatch заняты оба слота semaphore. Только длительность lease в памяти одноразового процесса ускорена с 1800 до 0 секунд, SQL не подменялся. После двух dispatch и освобождения слотов trace зафиксировал **два начала `_execute_run` для `run-lease`**. Пустой workflow штатно проваливает валидацию, поэтому сканеры и провайдеры не запускались. Это fault-injection проверки времени, а не утверждение, что наблюдалось реальное 30-минутное ожидание.

Исправление `4aebaad`: `claim_run_start` атомарно выполняет workspace-scoped UPDATE runtime.runs WHERE execution_status=queued RETURNING id **после** получения executor semaphore; проигравший выходит до анализа. Собственный финальный `/tmp/sol-repositories-proof/finalprobe.py` на настоящей PG повторил ускоренное истечение lease: два входа в executor, **одно** сохранённое событие run_status=running, итог failed/done (пустая ревизия штатно отклонена валидатором). После отмены до старта queued/queued/recoverable сохранились; последующее выполнение того же run завершилось failed/done. Ошибка повторного анализа закрыта.

Остаток по исходникам: если проигравший обработчик завершится, пока победитель ещё работает, его settlement может очистить token ledger, поэтому победитель не запишет done по своему старому token и terminal runtime run останется с ledger running. Owner сообщил об этом отдельно; источник подтверждает возможность (`scheduler.py:517–536`, `database.py:1106–1141` до добавления gate, смещение после gate +21). Это техническая несогласованность ledger, не повтор анализа и не фальшивый succeeded: публичный runtime status остаётся авторитетным, повторное исполнение закрыто SQL gate, recovery running-run не переигрывает. Неблокирующее ограничение; отдельного живого воспроизведения этого варианта здесь нет.

### Некорректный URL watch даёт HTTP 500 — исправлено в b8db040

`backend/app/engine/scheduler.py:207`, `backend/app/api/repository_watches.py:55–63`.

`urlsplit(url.strip())` стоит вне try и может бросить ValueError, например на `https://[github.com/repo`. Route ловит только WatchValidationError/WatchUnavailableError. Собственный запрос через настоящий ASGI app и admin session вернул **500**; тот же ввод в `/api/artifacts/repository` вернул **400**. Реальный HTTP-провайдер не вызывался. Исправление `b8db040` ловит ValueError при urlsplit/hostname/port и переводит в WatchValidationError. Собственные финальные запросы настоящему ASGI app: malformed IPv6, нечисловой порт и явный :443 на watch вернули **400**, malformed IPv6 на import также **400**; provider не вызывался.

### Созданный фоновым watch артефакт отсутствует в уже открытом UI — source fix 7d4325e6

`frontend/src/App.tsx:133–179`, `frontend/src/App.tsx:248–256`, `frontend/src/components/UserDashboard.tsx:112`, `frontend/src/components/AdminPanels.tsx:144`.

Список artifacts загружается при старте и дополняется собственным импортом. Refresh runs и открытие другого run не загружают его артефакт. Если scheduler создал новый артефакт после старта страницы, lookup по `run.artifact_id` ничего не находит: сведения о repository/SHA не показываются, пользовательский результат получает «Проект не выбран». Это независимо найденный вывод из исходников. В `7d4325e6` ensureArtifact точечно запрашивает runInfo.artifact_id, объединяет параллельные запросы, а добавление в состояние выполняется после прежней sequence/selectedRunRef проверки. Ответ другого запуска не подставляет источник в текущий результат. По source review найденный пробел закрыт.

**Чужое live evidence, не моя проверка:** UI owner сообщил о реальном стенде с пустым начальным списком, последующем admin check-now, открытии нового run пользователем без reload: один GET /api/artifacts/art_…, показ gitlab-org/gitlab-test / master / ddd0f15, очистка старого результата импортом, отсутствие admin-запросов в user session. Сообщение worker содержит SHA и точный стенд 5199/API18099; результаты не выдаются за независимо воспроизведённые.

**Моя попытка:** отдельный локальный Playwright Chromium начал login к http://127.0.0.1:5199, получил ECONNREFUSED до выполнения сценария. Файл access.env прочитан внутри процесса, значения не печатались. Playwright MCP также не вернул состояние и был остановлен. Координатор явно поручил не восстанавливать уже заменяемый UI стенд и завершить этот bounded review; новый редактор будет проверяться отдельно. Скрипт ui probe не завершился, UI результата ui.json нет.

### Известная потеря queued-run до старта — исправлена в 3fdb149

База `334e7bd`: `_dispatch_run` безусловно вызывает finish в finally, ledger становится done даже при исключении до старта либо CancelledError во время ожидания executor; runtime run остаётся queued и recovery его больше не видит. Ранее обнаружено координатором, подробно повторно не аудировалось.

На `3fdb149` собственные PG-пробы показали:

- отмена dispatch, ожидающего **настоящий** WorkflowExecutor semaphore: runtime queued, ledger queued, recovery возвращает тот же run;
- исключение на границе executor (явно внедрённый RuntimeError): queued/queued, тот же run доступен recovery;
- два параллельных redispatch затем завершают **одну** запись run: runtime failed, ledger done, count=1; настоящий executor использует пустой workflow и штатно отклоняет его без сканирования;
- неуспешная попытка самого probe изменить run identity была отклонена реальным immutable trigger; fixture исправлен созданием подходящей ревизии до run, защитные триггеры не отключались.

## Что независимо проверено

Собственный PostgreSQL 16 в отдельном контейнере `sol-repositories-proof3`, `--network none`, без TCP-портов; Unix socket и CG_DATA_DIR только под `/tmp/sol-repositories-proof/`. Production, стенд 18082, секретные конфиги и чужие базы не использовались. Команды запускались из своего checkout через `/tmp/nedra-venv/bin/python`; зависимости уже были установлены.

| Проверка | Собственное наблюдение |
|---|---|
| Fresh runtime | schema_meta = 4 |
| Настоящее обновление v3→v4 в отдельной временной БД | version 4, обе новые таблицы, ранее сохранённая строка workflow полностью совпала до/после |
| 8 параллельных dispatch claim одного run | ровно 1 победитель при действующем lease |
| Ошибка download/import, затем повтор | baseline SHA не изменён, failed commit разрешает повторный claim |
| Workspace scope | другой workspace не читает watch и не захватывает dispatch |
| Авторизация настоящего ASGI app | anonymous import 401; user watch GET 403; user invalid import 400 |
| URL parser | 8 запрещённых форм отклонены: локальный host, HTTP, credentials, порт, query, traversal, provider subpath |
| GitLab subgroup canonicalization | `https://gitlab.com/group/sub/repo.git/` → `https://gitlab.com/group/sub/repo` |
| Настоящая ZIP-обработка | provider-root снят, `.env` сохранён, node_modules исключён; symlink внутри исключаемого каталога всё равно отклонён до фильтра |
| Временный ускоренный лимит download | при лимите 16 байт oversized ответ отклонён 413 |

Дополнительная собственная **модель HTTP в памяти**, не настоящий GitHub/GitLab: `/tmp/sol-repositories-proof/importprobe.py`, `import.json`. Весь production-код импорта/распаковки/intake/sidecar работал, только httpx transport подменён MockTransport. При явном SHA `bbbb…` отправлен ровно запрос `https://codeload.github.com/a/repo/zip/<SHA>`, branch resolve повторно не вызывается; manifest и сохранённый sourceRef содержат тот же полный SHA, файл на диске содержит ожидаемые байты. Resolve default branch затем explicit import также делает загрузку по SHA, а не по имени ветки. Ответ 302 с Location на loopback отклонён 502 без следования redirect. Это подтверждает маршрутизацию/фиксацию SHA в коде, но **не** доступность реального провайдера и не проверку его текущих ответов.

Пути локальных доказательств: `/tmp/sol-repositories-proof/{probe.py,baseline.json,fixprobe.py,fix.json,leaseprobe.py,lease.json,importprobe.py,import.json,finalprobe.py,final.json}`. Это одноразовые проверки, в репозиторий не добавлены.

## Оценка границ и оставшиеся ограничения

- По исходникам importer принимает только canonical HTTPS github.com/gitlab.com; credentials, query/fragment, порт, иной host и `%` в path запрещены. API/download URL строятся из проверенных сегментов и SHA; `trust_env=False`, `follow_redirects=False`. Произвольного outbound URL из пользовательского ввода в этих путях не обнаружено. DNS/компрометация самого provider не моделировались.
- Потоковые лимиты: JSON 256 KiB, архив 50 MiB, файл 25 MiB, распакованное содержимое 250 MiB, 5000 файлов, глубина 32, путь 512; HTTP timeout и semaphore по 2. Все записи проверяются на тип/путь до service-filter. Directory records не входят в file-count: синтетические 6000 пустых директорий + один файл принимаются, но транспортный предел архива сохраняется; это не представляется доказанным безлимитным ресурсом или отдельным release blocker.
- Scheduler передаёт head SHA явным аргументом import_repository; атомарная транзакция связывает ledger, frozen artifact, workflow revision и queued run, затем двигает last_seen_commit. Этот cursor означает «зафиксирован в очереди», **не** «анализ успешно окончен»; при download/storage failure он не двигается.
- После реального process death **уже начатого** run автоматического replay нет: recovery выбирает только runtime queued (`database.py:1123–1135` после 3fdb149). В собственном subprocess настоящий executor остановлен `_exit(91)` сразу после записи running и до сканирования; после истечения lease ledger/runtime остаются running, recovery не выбирает run, watch cursor уже содержит SHA. Это существующая граница executor/recovery, координатором явно принята как limitation: не выдавать за завершённый анализ и не обещать автоматическое возобновление running-run. Отдельной новой ошибкой exactly-once не считается.
- `_rate_bucket` включает import в существующий upload лимит 10/мин, но watch create/check-now собственного middleware bucket не имеют. Watch API admin-only, provider concurrency и DB claim ограничивают работу; это неблокирующее замечание по частоте ручных запросов, не обход пользовательской авторизации.
- Аддитивный upgrade выполняется под runtime advisory DDL lock; новые rows и FK имеют workspace scope. Проверка не исследует гипотетическую порчу schema_meta/DB owner tampering.
- Private repo/token flow вне начального scope; submodules/LFS не скачиваются. Polling с minimum interval60 подтверждён кодом; webhooks не обещаются.

## Объединённый UI: чтение исходников

На `98a86499` и после точечного `7d4325e6` импорт вызывает общий `handleArtifactLoaded`, выбирает новый artifact, обновляет project_input и через resetRunSelection закрывает SSE, увеличивает sequence и сбрасывает старые findings/report/run. Устаревший ответ loadRunDetails проверяет sequence и selectedRunRef. Result source берётся по **run.artifact_id**, не по текущему draft artifact — корректно; отсутствующий в первоначальном списке run artifact теперь подгружается точечно с прежним stale guard.

User ветка App возвращается до admin shell/ScheduleModal; refreshAdminOverview имеет role guard, training import скрыт. ScheduleModal не делает admin-запросов при adminAllowed=false. Текст нового watch честно описывает baseline без начального скрытого скана и периодический опрос; default300/min60 показаны. Source review не заменяет живой браузер: независимого UI verdict нет; live evidence владельца и недоступность моей попытки указаны выше. На этом ограниченное ревью завершено, открытых подтверждённых backend blockers после исправлений не осталось.
