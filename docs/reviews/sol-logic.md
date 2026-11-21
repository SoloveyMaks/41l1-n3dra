# Sol: независимое ревью логики и состояний

**Вердикт по baseline `95da50b`: показывать жюри как готовый результат пока нельзя.** Подтверждены смешивание артефактов A/B, противоречие execution status в отчёте и неверно обозначенная полнота проверки. Успешное выполнение workflow не означает безопасность проекта.

Ревью завершено 21.11.2026, модель Sol. Чужие отчёты Sol не читались. Исходники не менялись; собственные fixtures созданы только в локальном review-стенде, общие workflows/schedules не изменялись. Единственный файл отчёта — `docs/reviews/sol-logic.md`.

## Ревизия и среда

Начальный HEAD совпадал с local dev: `95da50bbae3e74f7776499c5d0cfb40227576648`; `088c843` — предок, merge при старте не требовался. По указанию координатора дальнейшее ревью зафиксировано на baseline `95da50b`, хотя local dev позднее обновился.

Живой стенд: `http://127.0.0.1:18082/`, отдельная БД `nedra_sol_review4`, ревизия `95da50b` подтверждена владельцем. Первый вход попал в перезапуск стенда и не считался проверкой; после READY все результаты ниже получены на восстановленном стенде. Самостоятельных перезапусков и деплоя не было.

Orca browser: собственный изолированный профиль `sol-logic-review`, вкладка `a98ebc42-5b18-40e7-828e-2a4a0be115d5`. Коды читались программно из файла режима 600; при браузерном входе передавались CLI в памяти процесса с подавленным выводом, без OS argv. HTTP-проверки использовали отдельные cookie jars; cookies/коды/credentials в отчёт не помещались. Исходники sample с URI credentials не выводились.

Ограничения продукта не выдавались за выполненные возможности: один общий workspace без отдельных аккаунтов; embeddings отключены после недоступности403; vetted=0; при `expectedScopesVerified=false` general comparison остаётся inconclusive; отдельный SQL proof ограничен source pattern.

## Подтверждённые баги

### 1. Критично: trigger выбирает B, сканеры получают A из draft

**Места:** `backend/app/api/router.py:367`, `backend/app/api/router.py:387`, `backend/app/engine/executor.py:374`, `backend/app/engine/executor.py:477`; user no-PUT: `frontend/src/App.tsx:463`, `frontend/src/App.tsx:592`.

**Шаги:**

1. Создать два собственных ZIP: A с endpoint `GET /sol-review-a` в `ReviewA.kt`, B с чистым `ReviewB.kt`.
2. Создать отдельный workflow с `project_input.config.artifactId=A`; оставить scanners → merge → policy → report, без AI/notify.
3. Пользователем вызвать `POST /api/workflows/<own>/runs` с `artifactId=B`.
4. Сопоставить run, output Project Input, паспорт и canonical map.

**Ожидается:** выбранный B определяет вход всех scanners, findings, evidence, карту и отчёт.

**Факт:** workflow `wf_47a966df`; A=`art_538871223025`, B=`art_5c771d7b537c`; run `afe84eee-dd31-4b51-bfca-c44def375191` записал B, но output `node_project` содержал A/`sol-logic-A.zip`. Scanners получили A; паспорт содержит 12 строк A, а canonical map уже относится к B: scope `src/main/kotlin/ReviewB.kt`, snapshot `73a4a5d3-7857-5a91-9efe-4ad8ba77b23b`. Исполнение succeeded, вердикт inconclusive, findings=0 — это не делает происхождение данных правильным.

Причина: API передаёт B как `default_artifact_id`, а Project Input выбирает `config.artifactId` раньше него. Merge затем использует default B для интеграции карты. User-путь намеренно не сохраняет draft, поэтому локальный выбор B не исправит сохранённый A. Влияние: результаты другого проекта под именем выбранного, недостоверные отчёты и evidence.

Координатор получил source finding и независимую живую репродукцию сразу. Fix `9143178` заявлен принятым в local dev, **на baseline не установлен и здесь не проверен**.

### 2. Высоко: «Полнота 100%» при partial canonical coverage 1/3

**Места:** `backend/app/reports/builder.py:18`, `backend/app/reports/builder.py:40`; отображение `frontend/src/components/UserDashboard.tsx:261`, `frontend/src/components/ReportView.tsx:77`, `frontend/src/components/AdminPanels.tsx:47`.

**Шаги:** завершить свой workflow на небольшом Kotlin ZIP, открыть карточку результата, report и карту одного run.

**Ожидается:** метрика полноты явно ограничена областью проверки и учитывает пропуски либо имеет другое честное название.

**Факт:** у run `afe84eee-dd31-4b51-bfca-c44def375191` паспорт `files_scanned=3`, `completeness_score=1.0`, warnings=[], а map `filesTotal=3`, `filesVisited=1`, `status=partial`, skippedScopes=[runtime-configuration,runtime-guards,binaries-and-archives]. На отдельном user UI run `42ae0f9c-9f95-40ac-9f39-a1e9f0a2363a` карточка показала 100%; карта прямо сообщила «1 из 3 файлов проверено» и перечислила runtime-пропуски.

Процент вычисляется только из количества parser errors и unresolved dependencies. Не учитываются даже files_scanned=0, files_skipped, отсутствующие scanners, skippedScopes, limitsReached и expectedScopesVerified. Значение `files_scanned=3` само по себе не выдумано: secret scanner прочёл три текстовых файла; оно не доказывает полноту всех видов анализа. Влияние: пользователь получает противоречивую картину охвата и может принять неполный анализ за полный. Verdict inconclusive остаётся честным, но процент от этого не становится полнотой.

### 3. Высоко: failed run скачивается как execution succeeded

**Места:** `backend/app/reports/builder.py:168`; `backend/app/engine/executor.py:282`, `backend/app/engine/executor.py:688`.

**Шаги:** создать собственный workflow на основе предыдущего, расположить Build Report раньше дополнительного Condition; соединить Condition с policy и задать field=`sol_missing_field`. Валидатор пропускает граф, отсутствующее поле проявляется при исполнении. Запустить и запросить run/report.

**Ожидается:** итоговое execution status одинаково в runtime, официальном JSON и представлении отчёта; failure не маскируется успешной генерацией report.

**Факт:** workflow `wf_f80e0b1e`, run `41afc81c-e1c9-468e-afab-c4968e7d9b75`: Build Report succeeded, Condition failed с «поле 'sol_missing_field' отсутствует», runtime `execution_status=failed`, `security_verdict=inconclusive`. Report HTTP200, `official_json.metadata.executionStatus="succeeded"`.

Причина: статус JSON задан константой до финализации; late condition делает ранний return без согласования уже созданного отчёта. Влияние: скачанный официальный документ противоречит ходу выполнения. Координатору передано немедленно. Fix `e0126f6` заявлен принятым в dev, **этим ревью не проверен**.

### 4. Средне: смена проекта оставляет результат другого проекта без подписи

**Места:** `frontend/src/App.tsx:502`, `frontend/src/App.tsx:516`; `frontend/src/components/UserDashboard.tsx:246`.

**Шаги:** в user UI выбрать из истории sample run с 13 находками (`59c9b428-7c7c-46ea-8812-81bdb26951d7`), затем в select проекта выбрать собственный `sol-logic-A.zip`.

**Ожидается:** сброс результата, выбор результата проекта A либо явная подпись «результат sample/run…».

**Факт:** шаг 1 уже `sol-logic-A.zip`, шаг 3 всё ещё «Найдены проблемы», 13, покрытие85% от sample. Названия проекта и run ID у результата нет. Problems/map/report тоже остаются выбранным sample run. После запуска A данные обновились правильно.

Причина: выбор artifact меняет только artifact/draft, не selected run и связанные состояния. Запоздавшие ответы при выборе run отдельно защищены seq/ref; это другая, подтверждённая проблема несвязанного project/run state. Влияние: пользователь может приписать старые проблемы новому проекту.

### 5. Средне: admin overview показывает старые counters и выбранный run как «последний»

**Места:** `frontend/src/App.tsx:493`; `frontend/src/components/AdminPanels.tsx:44`, `frontend/src/components/AdminPanels.tsx:51`.

**Шаги:** войти admin, открыть overview; создать свой run, зайти в Projects, вернуться overview; сравнить с новым `GET /api/admin/overview`.

**Ожидается:** обновляемый snapshot либо явная давность и refresh. Последний run определяется по истории, а не текущему выбору.

**Факт:** экран сохранял 21 projects/89 runs, свежий API уже отдавал 21/91. Собственный новый run: `e6950ae2-2cf5-4701-8b2d-2f65f852a816`. Карточка «Последняя проверка» продолжала показывать `sol-logic-A.zip`06:17, хотя в истории был более новый sample06:19 и свои новые runs. API snapshot получался только при смене session.role, counts имели приоритет над обновляемой history. Само admin overview API отдало реальные свежие counters.

Влияние: оператор видит устаревшее состояние системы без предупреждения; выбор старого run также меняет карточку с заголовком «последняя». Общее число проблем в этой панели фактически является findings выбранного run, не workspace total.

## Что прошло живую проверку

### Роли и пользовательский запуск

| Действие | Anonymous | User | Admin |
|---|---:|---:|---:|
| GET session | 200, unauthenticated | 200, user | 200, admin |
| GET admin overview | 401 | 403 | 200 |
| GET schedules | 401 | 403 | 200 |
| PUT draft собственного workflow | — | 403 | отдельно не выполнялся |
| POST schedule | — | 403 | 200, собственная fixture |
| POST training artifact | — | 403 | не запускался |

Браузерный user login, logout и admin login прошли. User UI запуск собственного A создал run `42ae0f9c-9f95-40ac-9f39-a1e9f0a2363a`, дошёл до terminal succeeded/inconclusive и вернул активную кнопку запуска. Инструментирование fetch сохраняло только method/url: POST validate, POST runs, **PUT=0**. Общий default workflow этим не изменялся.

### Counts, evidence, report и SSE

На собственных scanner fixtures счётчики совпали между runtime run, GET findings, map findings и official JSON findingsCount: before=1, fixed=0, deleted=0, coverage_loss=1. `kotlin_file_count` включает `.kt` и `.kts`: число2 на ZIP с одним `.kt` и `build.gradle.kts` следует реальной реализации intake, не равно числу Kotlin classes.

Собственный UI run открыл `GET /sol-review-a` на карте, evidence button и source fragment `ReviewA.kt:5–6`. API evidence для SQL before дал200 с совпавшими workspace/run/snapshot/evidence и sourceHash карты. Тот же evidence ID в deleted run дал404, старый source не подставился. Ни одно из этих действий не доказывает полный таint/runtime analysis.

Сохранённый SSE stream before run вернул29 событий до terminal; `after=3172` вернул25 событий, все seq строго больше3172, без дублей и в порядке возрастания. UI умеет подписывать named events, закрывает terminal stream; poll fallback есть в коде. Принудительный сетевой обрыв/reconnect и гонка delayed responses не разыгрывались, поэтому не объявляются проверенными. Есть мелкая разница контракта: historical `node_id` против live `nodeId`, live payload без timestamp (`backend/app/engine/sse.py:19`, `:43`); существенного влияния на status/verdict в этом проходе не обнаружено.

Official JSON и Markdown скачались HTTP200 для четырёх собственных completed runs, runId/verdict/findingsCount согласованы с API; Markdown содержит правильный run ID. Исключение execution status на failed run описано выше.

### Comparison: исправление, удаление, потеря покрытия

Все четыре ZIP созданы независимо для ревью, использован отдельный workflow `wf_e0c2f9d5` с пустой config artifact binding и без AI/notify.

| Fixture | Run | Execution | Security verdict | Findings |
|---|---|---|---|---:|
| before: JDBC SQL concatenation с RequestParam | `09878771-8d7b-43a9-b049-1431e79c358a` | succeeded | fail | 1 |
| fixed: JDBC parameter `?`, name argument | `1938162f-8d5a-43fe-9e88-579c2df4a75b` | succeeded | inconclusive | 0 |
| deleted: `SolSql.kt` удалён из ZIP | `55a455ed-266c-4184-a987-41185e7d7e56` | succeeded | inconclusive | 0 |
| coverage_loss: Kotlin syntax error | `ebd2c1fb-dc0e-4051-85ac-cfa5895cfa4c` | succeeded | fail | 1 |

- Before→fixed: general comparison200, comparable=false, item inconclusive; причина — A/B не подтвердили полноту expected scopes. Отдельный SQL200: `pattern_disappeared`, scope=`source_pattern`.
- Before→deleted: general200, item `file_deleted`, deletedFiles содержит `src/main/kotlin/SolSql.kt`; SQL тоже `file_deleted`. Удаление не названо исправлением.
- Before→coverage_loss: general200 inconclusive; SQL200 inconclusive. Passport parser_errors=1. Исчезновение/сбой анализа не повысили уверенность в исправлении.

Это подтверждает работу ограниченного SQL proof и осторожного comparison, **не** end-to-end vetted fix или полную безопасность после исправления.

### Admin editor и schedules

Конструктор открылся, селектор переключился с default на собственный `wf_47a966df`, загрузил сохранённый draft и отдал validate. Создание собственных workflows через admin API и запрет user PUT проверены. Добавление/перетаскивание/удаление nodes, ручное сохранение draft и гонки выбора workflow в браузере не разыгрывались.

Собственное расписание `sch_22cfb3e6`, own workflow, artifact B: create200 на interval31536000; pause/resume/final pause200; неправильный cron `0 0 31 2 *` получил400. Экран расписаний показал собственную строку «На паузе» с правильными workflow/artifact. Финально расписание выключено. Чужие кнопки и расписания не менялись. Автоматическое срабатывание scheduler и восстановление после рестарта не проверялись; interval выбран длинным, чтобы не создавать фоновую нагрузку.

## Доказательства и открытый объём

Локальные диагностические материалы (временные, без access codes): `/tmp/sol-logic-live.json`, `/tmp/sol-logic-extra.json`, `/tmp/sol-logic-downloads.json`; одноразовые scripts `/tmp/sol-logic-live.py`, `/tmp/sol-logic-extra.py`. Для повторения достаточно own workflow/artifact/run IDs и шагов выше; fixtures сохранены в review DB для focused retest. Эти материалы не публикуются как production данные.

Не проверены: принудительный SSE reconnect/offline, искусственная задержка ответов при быстром переключении run/project, scheduler execution/restart, всё редактирование графа, реальные внешние уведомления, embeddings/retrieval и vetted pipeline. Последние возможности не изображались работающими. Production и другие worktrees не трогались; тестовые наборы, TDD и build-as-proof не использовались.

## Вердикт

Baseline `95da50b` **ещё нельзя представлять жюри как готовый достоверный путь от выбранного проекта к отчёту**. Главные блокеры: A/B смешивание, failed→succeeded в официальном отчёте, 100% «полнота» при partial analysis; отдельно надо убрать неоднозначность project/run и обновлять overview. Роли403, user no-PUT, run-scoped evidence, cautious deleted/coverage-loss comparison и узкий SQL proof реально сработали.

Заявленные исправления `9143178` и `e0126f6` требуют независимого focused retest на конкретной новой ревизии. Сообщения о merge не являются проверкой исправления; этот отчёт сохраняет вывод по замороженному baseline.
