# Web debug audit — 2026-11-21

Проверка frontend nedra «как новичок»: от login до отчётов, сравнения и мобильного вида.
Работа шла в worktree `app-map-integration`, backend/engine/storage и `SecurityMapScreen`/`ui/security-map` не менялись (map — отдельный worker).

Окружение проверки: Vite dev `127.0.0.1:3000` с `VITE_PROXY_TARGET=http://127.0.0.1:18080` для старого публичного образа; финальные state/pause-проверки — Vite `127.0.0.1:3001` с актуальным backend из integration-dev на `127.0.0.1:18082` (temp data dir, та же PG). Сессия из `~/.config/nedra/runtime.env` (код не печатался). Проверка — живой браузер Playwright, не unit/mock.

## Итог

- Приложение проходит основной путь: вход, загрузка ZIP, выбор сценария/артефакта, сохранение draft, запуск, live SSE, история запусков, находки/evidence, отчёт, сравнение, расписание, AI Trust Layer.
- Исправлены реальные UX/state-баги: 403 от dev-прокси, 0 событий SSE из-за named events, 404 отчёта во время running, гонки при переключении запусков/валидации, stale evidence, stale `gemini-*`, перекрывающая fixed-кнопка logout, отсутствие обновления live-статуса при молчащем SSE.
- Backend worker исправил валидацию cron и добавил `PATCH /schedules/{id}`. UI проверил на актуальном backend (порт 18082): pause → `enabled=0`, resume → `enabled=1`; тестовая запись удалена. В публичном контейнере нужен rebuild, но stale-блокер из UI снят.

## Матрица checked / fixed / unverified

| Область | Checked | Fixed | Unverified |
|---|---|---|---|
| Auth | wrong/right code, logout, re-login | logout overlap, человеческие ошибки | истечение сессии |
| Upload | реальный ZIP, sample-артефакты, training без env | training error, селект артефакта | training с настроенным `CG_TRAINING_PROJECT` |
| Editor | workflows, artifact, save, reset, add/delete node, validation error, click-to-connect | stale model, reset label, validation race, workflow switch seq guard | — |
| Run/live | trigger, 30–39 SSE, polling, terminal verdict, history SSE on old run | named SSE, polling, history race, report 404, history connect | failed run без отчёта вживую |
| Findings/report | filters, evidence, JSON/MD 200, run switch | selectedFindingId derived from current findings; failed/queued report state | failed run без отчёта вживую |
| Comparison | same-run, swap, pair result, SQL pattern | guards, reset result, refresh | — |
| Schedule | create, invalid input, refresh, pause/resume on backend 18082 | pause/resume UI, enabled 0/1, cron format | — |
| AI Trust | synthetic validator | error state, `errors || []` | новые дорогие AI-прогоны не повторялись |
| Responsive | 390×844 и 1280×900: editor/live/findings/report/comparison | responsive classes | визуальная проверка скриншотов моделью недоступна |
| Console | чистый reload | 0 app errors | исторические 401/400 от намеренных negative-проверок |

## Живые проверки

| Сценарий | Что проверено | Результат |
|---|---|---|
| Login | неверный код, затем код из runtime env | «Неверный код доступа»; после верного кода workspace загрузился |
| Logout | кнопка «Выйти» в Navbar | вернулся access gate, сессия и данные очищены |
| Upload ZIP | реальный `examples/demo/sql-before-after/before.zip` через file input | модалка закрылась, «Артефакт выбран: before.zip» |
| Sample/training | кнопки sample и organizer training | training без `CG_TRAINING_PROJECT` даёт понятное сообщение, не падает |
| Editor | выбор сценария, `Сбросить draft`, `Сохранить draft` | 11 блоков/13 связей; reset вернул 12→11; save — «Draft сохранён, граф валиден» |
| Validation | добавление `Secret Scanner` без входа + попытка запуска | ошибка «требует подключения артефакта»; запуск заблокирован notice-сообщением |
| Nodes | добавление/удаление блока, duplicate id в артефактах | новые блоки добавляются, удаление чистит рёбра, повторного id в select нет |
| Connection | перетаскивание нового ребра в headless | не воспроизвелось (координатный drag не создал edge); существующие 13 рёбер рендерятся. Это ограничение проверки, код `onConnect` не менялся — проверить рукой в браузере |
| Run/SSE | trigger training-project | 39 SSE events, статус «Завершён», verdict fail, 6 findings, 11 node runs, артефакт `art_5693c726f331` |
| Polling fallback | завершение run без опоры на SSE | live-заголовок обновился опросом за 2 с, кнопка «Обновить» работает |
| Run history | выбор старых запусков в select | `c8b41afe` → у находок count 1; `2ab05023` → 6; данные не липнут от предыдущего run |
| Findings | фильтры анализаторов/риска, карточка `CF-SQL-PARAM-INTERPOLATION` | Локация, Evidence, Taint Trace, severity/confidence отображаются |
| Report | JSON и Markdown ссылки | оба 200; JSON 27026 B, Markdown 16172 B (`application/json` и `text/plain`) |
| Comparison | одинаковые runs, пара `c8b41afe`/`52efd83f`, swap | same-run: disabled + «Baseline и Current совпадают»; пара: «Недостаточно доказательств», SQL «Паттерн исчез»; swap меняет select и чистит старый результат |
| Schedule | создание, невалидный формат, `interval:3600`, pause/resume на backend 18082 | pause → `enabled=0`, UI «На паузе»; resume → `enabled=1`; тестовая запись удалена |
| AI Trust | синтетический стресс-тест | «ВАЛИДАТОР УСПЕШНО ЗАБЛОКИРОВАЛ ГАЛЛЮЦИНАЦИЮ», 3 нарушения; при ошибке API показывается alert, не пустой экран |
| Mobile 390×844 | editor, findings, live, report, comparison | `documentElement.scrollWidth === 390` на всех; горизонтального overflow нет |
| Desktop 1280×900 | те же экраны | overflow нет; таблица сравнения скроллится внутри контейнера |
| Console | чистый reload без действий | 0 app errors; исторические 401/400 в логе относятся к намеренным проверкам неверного кода/training |

Скриншоты (локальные, `artifacts/` в `.gitignore`):

- `artifacts/audit-desktop-editor-2026-11-21.png`
- `artifacts/audit-desktop-live-2026-11-21.png`
- `artifacts/audit-desktop-findings-2026-11-21.png`
- `artifacts/audit-desktop-report-2026-11-21.png`
- `artifacts/audit-desktop-comparison-2026-11-21.png`
- `artifacts/audit-desktop-schedule-2026-11-21.png`
- `artifacts/audit-mobile-editor-2026-11-21.png`
- `artifacts/audit-mobile-findings-2026-11-21.png`
- `artifacts/audit-mobile-live-2026-11-21.png`
- `artifacts/audit-mobile-report-2026-11-21.png`
- `artifacts/audit-mobile-comparison-2026-11-21.png`

## Исправленные баги

1. **403 на `/api/session` из Vite dev.** Backend проверяет Origin/Referer. В `vite.config.ts` добавлен `VITE_PROXY_TARGET` и принудительная подмена `origin`/`referer` на target в `proxyReq`.
2. **SSE давал 0 событий.** Backend шлёт `event: run_status` и другие named events; `EventSource.onmessage` их не получает. В `App.tsx` добавлены `addEventListener` на `run_status`, `node_status`, `artifact_status`, `finding_summary`, `coverage`, `edge_data`, `delivery_status`. После фикса — 39 событий.
3. **404 отчёта каждые 2 секунды во время running.** `loadRunDetails` больше не запрашивает `/report`, пока run не terminal. После завершения отчёт загружается.
4. **Live не обновлялся, если SSE молчал.** Добавлен polling раз в 2 секунды при `isRunning`; заголовок/статус/находки обновляются даже без событий.
5. **Переключение истории запусков могло применить старый ответ.** Добавлены `selectedRunRef` и `runLoadSeq`; устаревшие ответы отбрасываются. Аналогичный seq-guard добавлен для валидации draft (быстрые изменения не перетирают свежий результат).
6. **Быстрое A→B переключение сценария могло показать A при выбранном B.** `handleSelectWorkflow` получил `workflowSelectSeq`: ответ старого `getWorkflow` игнорируется, logout инкрементирует счётчик. Проверено с задержкой запроса default workflow: select остался на Team B.
7. **История SSE не подключалась при выборе старого run.** Теперь EventSource открывается на initial load и на каждый `handleSelectRun` с закрытием предыдущего; события дедуплицируются по `seq` и ограничены 1000. Проверено: выбор `c8b41afe` показывает 39 событий именно этого run.
8. **Findings показывали evidence от прошлого run.** `FindingsView` теперь хранит `selectedFindingId`, а объект вычисляет через `findings.find(id)`. Если новый run переиспользует id с другим evidence, UI берёт новый объект; если id исчез — selection сбрасывается. `App` дополнительно чистит findings/report/nodeRuns до загрузки run.
9. **Stale `gemini-*` в UI.** Модель показывается read-only как `reformboss · deepseek-v4.1-flash-1017`; ключ остаётся только на сервере.
10. **Fixed logout перекрывал кнопку «Удалить блок».** Logout перенесён в Navbar; глобальная fixed-кнопка удалена.
11. **Отчёт запрашивался для running.** См. п.3; заодно убран 404-спам в консоли.
12. **Сравнение позволяло бессмысленные действия.** Same-run guard, disabled «Сравнить», сообщение при <2 runs, swap, сброс результата при смене выбора, `onRefreshRuns`.
13. **SSE-события с огромным payload/`Invalid Date`.** В live-ленте timestamp с fallback `—`, payload обрезается до 240 символов; полный JSON остаётся в title.
14. **Расписание: создание без контроля и отсутствие паузы.** Добавлена проверка формата (`interval:N` или 5-field cron), кнопка «Обновить», статус «Активно/На паузе» и кнопка pause/resume через `PATCH /schedules/{id}`. Ошибка backend показывается в модалке, не теряется.
15. **AI Trust падал без `result.errors`.** Добавлены `error` state, alert и защита `result.errors || []`.
16. **Мобильная вёрстка.** Navbar/палитра/инспектор/live/findings/report/comparison получили responsive-классы; проверено на 390×844.
17. **Очередь, выполнение и failed без отчёта.** Live различает «В очереди» (amber), «Выполняется» (blue) и terminal-статусы; report для queued/running показывает «появится после завершения», для failed — причину из `node_runs.error_message`, а не вечный пустой экран. Security verdict остаётся отдельным бейджем.

## Backend-блокеры и ограничения

- **Расписание.** На актуальном backend (integration-dev, порт 18082) `PATCH /schedules/{id}` работает: pause вернул `enabled=0`, resume `enabled=1`; UI показал «На паузе»/«Активно». Тестовая запись удалена из `runtime.schedules`. Публичный контейнер всё ещё требует rebuild, но UI-контракт проверен.
- **Training sample.** Локальный backend без `CG_TRAINING_PROJECT` отдаёт 400; UI показывает человеческий текст. Для проверки самого training нужен env с путём к training-project.
- **Report 404 до завершения run.** Backend отдаёт 404, пока report не создан; frontend это учитывает, но API-поведение остаётся.
- **Map.** `SecurityMapScreen.tsx` и `ui/security-map/**` — зона другого worker; не трогались.
- **Connection.** Проверено click-to-connect: Manual Trigger out → новый Project Input trigger (ребро создалось, ошибка «требует триггера» ушла), затем Project Input artifact → Secret Scanner artifact (13→15 рёбер). Осиротевший Secret Scanner дал ожидаемую validation-ошибку; draft сброшен.

## Проверки

```bash
cd frontend && npm run build
# tsc + vite build: 0 ошибок, dist собран

cd frontend && npx tsc -b --pretty false
# 0 ошибок
```

Чистый reload приложения: 0 app errors в console (исторические 401/400 — намеренные проверки wrong code/training).

## Изменённые файлы

`frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/vite.config.ts`, `Navbar.tsx`, `WorkflowCanvas.tsx`, `NodePalette.tsx`, `InspectorPanel.tsx`, `CustomNode.tsx`, `LiveRunView.tsx`, `FindingsView.tsx`, `EvidenceCard.tsx`, `ReportView.tsx`, `ComparisonView.tsx`, `ScheduleModal.tsx`, `AiTrustModal.tsx`, `UploadModal.tsx`.

`docs/web-debug.md` — этот отчёт.

Push/main не трогались.