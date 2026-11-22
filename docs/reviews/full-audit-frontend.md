# Полный аудит frontend и security-map

- База задания: `35d27d30ae44fd955824001e18eff895197c933c` (`dev`).
- HEAD на старте работы: `4cb7c7b0509a5a0028dc261e5faf0a08a374d1e4` (один коммит после базы, получен из `main`-линии; worktree уже содержал незакоммиченные UI-исправления).
- Коммит с исправлениями: `a7e5549ba77e128222500c191ffde8d8fce3da12`.
- Финальная полировка toolbar (крупнее подписи, перенос однострочного режима на 1760 px): `4770ae9c82268725a43ce9bfbe5cc40bf02283c2`.
- Область: `frontend/` кроме `frontend/src/miniapp/`, `ui/security-map/`. Backend API, БД, миграции, конфиги и секреты не менялись.

## Что проверено

Прочитаны и сопоставлены с backend-контрактами: auth/access gate, App run/scenario/artifact state, SSE и polling, AccountModal/history, UploadModal (ZIP/repository), ScheduleModal (расписания и webhooks), LiveRunView, FindingsView/EvidenceCard/ResponsibilityPanel, ReportView/export, ComparisonView, ReferenceChecks, NotificationCenter, AdminShell/AdminPanels, SecurityMapScreen, repository-graph, `ui/security-map` model/MapView/SecurityMap. Проверены состояния loading/empty/error/recovery, stale-ответы, мобильная вёрстка 390 px и desktop, доступ user к admin-маршрутам, длинные названия, открытые палитра и инспектор.

Живой браузерный E2E не выполнялся: безопасного стенда NEDRA с backend нет. Порт `18080` — пользовательский/производственный сервис, запрещён; порт `3000` принадлежит другому worktree и проксирует не NEDRA API; новые listeners не открывались. Вместо этого выполнены локальные пробы реального кода и статический layout-harness из настоящих React-компонентов (подробности ниже). Это не доказывает интеграцию auth/API/SSE.

## Найденное и исправленное

### 1. ScenarioBuilder отклонял допустимую связь AI-гипотез в отчёт

`frontend/src/api/scenarioBuilder.ts` — `NODE_PORTS.build_report` не содержал вход `ai_scan_result`, хотя backend `NodeKind.BUILD_REPORT` его объявляет, а `CustomNode` уже рисует вход и исполнитель передаёт AI_SCAN в отчёт.

- Воспроизведение: `requestScenarioPreview` с графом `ai_scan.ai_scan_result → build_report.ai_scan_result` возвращал `SCENARIO_PREVIEW_INVALID` («повреждённый черновик»).
- Причина: клиентский mirror `backend/app/contracts/node_registry.py` разошёлся с реестром.
- Исправление: добавлен `ai_scan_result` во входы `build_report`; любой неизвестный порт по-прежнему отвергается.
- Проверка: проба `verify-ports-current.cjs` на реальном `requestScenarioPreview` — валидный AI→report граф `accepted`, неизвестный порт `SCENARIO_PREVIEW_INVALID`; та же проба на старой таблице даёт `SCENARIO_PREVIEW_INVALID`.

### 2. Ложное «Граф валиден» и устаревшие ошибки при смене сценария/запуска

В App не было состояния проверки: до ответа сервера и при смене draft показывался зелёный вердикт от предыдущего графа; при недоступности валидатора старые ошибки оставались на экране.

- Воспроизведение: задержанный/отклонённый `POST /workflows/{id}/validate` в только что открытом сценарии, либо смена сценария при уже полученных ошибках предыдущего.
- Причина: `validationIssues` были единственным источником вердикта и не очищались при switch; `handleTriggerRun` проверял только массив ошибок, который мог быть пустым до проверки.
- Исправление: введено `validationState: checking | ready | unavailable`; Canvas показывает «Проверяем граф…», «Граф не проверен» или число ошибок только для проверенного черновика. Старые issues не выдаются за новый результат; запуск блокируется, пока граф не проверен или проверка недоступна; при смене сценария/шаблона/чужого запуска состояние сбрасывается и запускается новая проверка. Для запуска того же сценария с артефактом выбранного run draft перепроверяется, а не остаётся с вердиктом прошлого артефакта.
- Проверка: `verify-validation-badge-current.cjs` (настоящий React 19 SSR `WorkflowCanvas`): `checking` — только «Проверяем граф…» и без drawer; `ready` без ошибок — «Граф валиден»; `ready` с двумя ошибками — «Ошибок: 2» и drawer; `unavailable` — «Граф не проверен». Handler-проба: `trigger_checking=0`, `trigger_unavailable=0`, `trigger_ready=1`.

### 3. Между аккаунтами сохранялся идентификатор сценария

`resetWorkspace` очищал draft/artifacts/runs, но не `workflowId`/`workflowIdRef`. После logout и входа другим аккаунтом до завершения `loadInitialData` оставался старый id; кнопки запуска/сохранения могли обратиться к чужому сценарию (backend должен отклонить, но UI не должен предлагать такое действие).

- Исправление: `resetWorkspace` очищает `workflowId` и `workflowIdRef`; `handleTriggerRun` отказывается работать без загруженного сценария.
- Проверка: handler-проба после `handleLogout`: `workflowId=""`, `workflowIdRef=""`, `validationState="checking"`.

### 4. Повторный выбор того же запуска сбрасывал фактическое состояние карты

`handleSelectRun` всегда обнулял `mapLoadState`. При reselect того же run `SecurityMapScreen` (key = runId) не перезапрашивался, карта оставалась на экране, а карточка в LiveRunView утверждала, что карта не запрашивалась.

- Исправление: состояние карты сохраняется только при том же run; для другого run очищается. Сама карта по immutable snapshot остаётся валидной.
- Проверка: handler-проба `sameRunMap={runId:run_A,status:ready}`, `differentRunMap=null`.

### 5. AI Trust Modal мог показать ответ предыдущего запуска

Синтетический тест не имел отмены/поколения: закрытие, смена run или повторный запуск оставляли в state результат старого запроса.

- Исправление: `requestSeq` + сброс при `isOpen`/`runId`; ответ применяется только к текущему поколению; ответ валидируется по форме (`is_valid`, `errors[]`) вместо необработанного `any`.
- Ограничение: интерактивная проверка модалки не выполнялась (нет DOM-harness); проверены сборка и границы типов.

### 6. Главный экран: удалена история из Navbar, переделаны шапка и панель выбора

По требованию пользователя история запусков убрана из Navbar вместе с refresh; история остаётся в AccountModal. Из Navbar/App удалены неиспользуемые `runs`, `currentRunId`, `runsLoading`, `onSelectRun`, `onRefreshRuns`, `RunSummary`-хелперы и `userRuns`; state выбранного run, `handleSelectRun` и профиль/map/report/live сохранены.

Шапка: явные действия «Запустить», ZIP, профиль, выход; история не дублируется; focus-visible и aria-label; шапка не сжимается в layout. Вкладки на ширине от 640 px переносятся целиком, на 390 px — горизонтальная прокрутка с градиентной подсказкой; все вкладки остаются фокусируемыми. Один ряд шапки включается только с 1920 px, где вкладки действительно помещаются.

Панель canvas: «Сценарий» и «Проект для запуска» получили подписи над полями, полноширинные select высотой 36 px, полные названия в option/title, disabled-состояния при отсутствии данных и focus-ring. Кнопки «Новый сценарий», «По описанию», карта, сброс и сохранение имеют одинаковую высоту и не перекрываются. На <1280 px палитра/инспектор переходят в вертикальный поток (палитра — полоса, инспектор — секция ниже canvas), поэтому панель выбора не закрывается fixed-инспектором. При выборе блока на мобильном инспектор прокручивается в зону видимости. ReactFlow остаётся controlled с `fitView`, pan/zoom и прежними обработчиками.

Проверка: статический harness из настоящих `Navbar`, `NodePalette`, `WorkflowCanvas`, `InspectorPanel` + собранный Tailwind CSS, headless Chromium через Orca. Замеры на 390/640/720/768/853/960/1024/1152/1280/1440/1536/1760/1920 px, включая эквиваленты zoom 125/150/200 % (1024/853/640 и 1152/960/720): `scrollWidth == innerWidth`, ключевые controls не обрезаны, попарных пересечений нет, toolbar не выходит за canvas. На 390 px вкладки прокручиваются (намеренно), все основные controls видны. Ограничение: SSR-harness без эффектов/ReactFlow и без backend; это проверка вёрстки, не E2E.

### 7. Сохранены и включены в коммит ранее подготовленные UI-исправления

В worktree до начала работы уже были незакоммиченные правки; они не потеряны и вошли в `a7e5549ba7`:

- App: поколения/seq для initial load, history, account, admin overview, artifact cache; единый `resetWorkspace`; защита выбора run от stale-ответов; проверка workflow/артефакта выбранного run; отмена save/trigger при смене draft.
- `api/securityMap.ts` + `SecurityMap.tsx`: `credentials: include`, `no-store`, проверка production-контракта до рендера; повреждённая карта даёт `MAP_CONTRACT_MISMATCH`, а не падение.
- `ScheduleModal`: epoch контекста и отмена операций при закрытии/смене сценария; защита от поздних ответов.
- `UploadModal`: поколение запросов и abort при размонтировании.
- `FindingsView`: мобильная карточка evidence не выталкивает список и закрывается.
- `WorkflowCanvas`: локальное удаление узлов/связей через `onBeforeDelete`, Backspace/Delete.
- Navbar: перенос controls вместо обрезания.

## Команды и фактические результаты

Сборка (изолированная копия, существующие зависимости, без чужих `node_modules`):

```text
/tmp/nedra-audit-frontend/build-current/frontend
npm run build
✓ 2223 modules transformed
dist/assets/index-*.css  155.90 kB (gzip 27.11 kB)
dist/assets/index-*.js   972.30 kB (gzip 270.49 kB)
✓ built
```

Локальные пробы (реальный исходный код, in-process фикстуры; это не backend-интеграция):

- `probe-current.cjs` — exact-source handler execution App: `inspector` artifact B согласован с draft/run; `foreignRun` переключает workflow/artifact; `profileAfterLogout=null`; `triggerCallsAfterLogout=0`; overlapping history — новый ответ; `initialDataAfterLogout` пуст; `afterLogout` очищает workflow; `trigger_checking/unavailable=0`, `trigger_ready=1`; `sameRunMap` сохраняется, `differentRunMap=null`.
- `verify-ports-current.cjs` — `ai_scan_result → build_report` `accepted`; неизвестный порт `SCENARIO_PREVIEW_INVALID`; на старой таблице валидный граф отклоняется.
- `verify-validation-badge-current.cjs` — четыре состояния бейджа/панели ошибок.
- `verify-telemetry-current.cjs` — baseline не анимирует историю; свежий edge pulse только выше baseline; terminal run не live и не пульсирует; out-of-order node event не откатывает статус.
- `ports-map-probe.cjs` — повреждённая карта и coverage → `MAP_CONTRACT_MISMATCH`; валидная карта → `MapView`; API отклоняет повреждённый ответ.
- Layout-harness: замеры выше; после финального toolbar-sizing повторно проверены 390/640/720/768/853/960/1024/1152/1280/1440/1536/1760/1920 px — снова без обрезаний и пересечений; скриншоты `/tmp/nedra-audit-frontend/accept-*.png`, `/tmp/nedra-audit-frontend/final-*.png`.

`git diff --check` — без ошибок. Коммит содержит 12 файлов; backend, миграции, конфиги и секреты не затронуты.

## Доступ user к admin

Проверено по owning source: ветка `if (!isAdmin)` рендерит пользовательский shell; AdminShell/AdminPanels доступны только при `session.role === 'admin'`; вход по админ-коду проверяет возвращённую сервером роль и отзывает сессию при несоответствии; `refreshAdminOverview` не выполняется для не-admin. Серверная авторизация остаётся единственной границей; клиентских обходов не найдено. E2E с реальными cookie/ролями не выполнялся (нет стенда).

## main vs dev и интеграция

`main 98c276e6585a89c417b007d6fd0411505df3be14` на 4 коммита впереди `dev` и содержит `CustomCheckModal`, `CustomFindingsPanel`, палитру/Inspector/App-wiring пользовательских проверок, `custom_check` в реестре сценария, backend sandbox и migration 013. В этом worktree этих файлов нет; `main` не мержился и не менялся.

- `scenarioBuilder.ts` в `main` уже содержит и `custom_check`, и `ai_scan_result`; коммит `a7e5549ba7` восстанавливает только недостающий `ai_scan_result` на `dev`-линии. При объединении нужно сохранить обе строки и не потерять `custom_check` wiring.
- `App.tsx`, `Navbar.tsx`, `WorkflowCanvas.tsx`, `InspectorPanel.tsx`, `LiveRunView.tsx`, `FindingsView.tsx` в `main` имеют custom-check изменения. Наши правки затрагивают те же файлы (главная шапка, toolbar, inspector breakpoint, validation state); автоматический merge может конфликтовать. Нужна ручная интеграция координатора с сохранением обеих сторон; я main не мержил.

Интеграция custom checks в main не проверялась и не считается подтверждённой.

## Открытые блокеры и пробелы

- Нет безопасного запущенного NEDRA backend/БД для живого browser E2E: auth (cookie/role), реальные ZIP/repository импорты, SSE, report export, admin routes и мобильные взаимодействия не проверены в браузере.
- `podman start nedra-mini-integration-pg` ранее не был одобрен reviewer и прежняя сессия получила 403; команда не повторялась и не обходилась. Это остаётся блокером окружения.
- Порт 18080 (пользовательский/производственный) и БД не трогались; новые listeners не открывались.
- Layout-harness статический: не исполняет React-эффекты, интерактивный выбор/scrollIntoView, реальный ReactFlow canvas и админский shell. Админ-вёрстка проверена по коду и общим компонентам, отдельного admin-harness нет.
- На 390 px вкладки шапки прокручиваются по горизонтали (с градиентом); это осознанное решение, а не скрытие: все вкладки доступны клавиатурой/свайпом. На 640–1919 px они помещаются целиком, с 1920 px шапка становится однострочной.
- `ui/security-map` standalone `tsc --noEmit` не запускался; frontend-сборка потребляет его исходники и проходит. Отдельная проверка пакета остаётся неподтверждённой.
