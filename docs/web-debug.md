# Web debug audit — 2026-11-21

Проверка frontend nedra «как новичок»: от login до отчётов, сравнения и мобильного вида.
Работа шла в worktree `app-map-integration`, backend/engine/storage и `SecurityMapScreen`/`ui/security-map` не менялись (map — отдельный worker).

Окружение проверки: Vite dev `127.0.0.1:3000` с `VITE_PROXY_TARGET=http://127.0.0.1:18080`, контейнер `nedra-app`, сессия из `~/.config/nedra/runtime.env` (код не печатался). Проверка — живой браузер Playwright, не unit/mock.

## Итог

- Приложение проходит основной путь: вход, загрузка ZIP, выбор сценария/артефакта, сохранение draft, запуск, live SSE, история запусков, находки/evidence, отчёт, сравнение, расписание, AI Trust Layer.
- Исправлены реальные UX/state-баги: 403 от dev-прокси, 0 событий SSE из-за named events, 404 отчёта во время running, гонки при переключении запусков/валидации, stale evidence, stale `gemini-*`, перекрывающая fixed-кнопка logout, отсутствие обновления live-статуса при молчащем SSE.
- Остался backend-баг расписания: `POST /schedules` принимает произвольную строку, а scheduler трактует всё, кроме `interval:N` и `*/N`, как 60 секунд. UI теперь не отправляет произвольный cron, но корень — в backend. Подробно ниже.

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
| Schedule | пустой ввод, `not-a-cron`, `0 0 1 1 *`, refresh списка | пусто/невалидно — понятная ошибка, запись не создаётся; refresh показывает 0; поддерживаются только `interval:N` и `*/N * * * *` |
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
6. **Findings показывали evidence от прошлого run.** `FindingsView` сбрасывает `selectedFinding`, если его нет в новом списке; `App` чистит findings/report/nodeRuns перед загрузкой нового run.
7. **Stale `gemini-*` в UI.** Модель показывается read-only как `reformboss · deepseek-v4.1-flash-1017`; ключ остаётся только на сервере.
8. **Fixed logout перекрывал кнопку «Удалить блок».** Logout перенесён в Navbar; глобальная fixed-кнопка удалена.
9. **Отчёт запрашивался для running.** См. п.3; заодно убран 404-спам в консоли.
10. **Сравнение позволяло бессмысленные действия.** Same-run guard, disabled «Сравнить», сообщение при <2 runs, swap, сброс результата при смене выбора, `onRefreshRuns`.
11. **SSE-события с огромным payload/`Invalid Date`.** В live-ленте timestamp с fallback `—`, payload обрезается до 240 символов; полный JSON остаётся в title.
12. **Расписание принимало что угодно.** Добавлена клиентская проверка: только `interval:N` (N>0) и `*/N * * * *`; произвольный 5-field cron блокируется. Добавлена кнопка «Обновить» списка.
13. **AI Trust падал без `result.errors`.** Добавлены `error` state, alert и защита `result.errors || []`.
14. **Мобильная вёрстка.** Navbar/палитра/инспектор/live/findings/report/comparison получили responsive-классы; проверено на 390×844.

## Backend-блокеры и ограничения

- **Расписание (корень в backend).** `POST /schedules` не валидирует `cronOrInterval`; `scheduler._parse_interval` для любой строки без `interval:` и без `*/` возвращает 60 секунд. Поэтому `not-a-cron` и `0 0 1 1 *` ставятся на запуск каждые ~60 с. Frontend теперь такие значения не отправляет, но API остаётся уязвимым. Тестовые записи `sch_b513f055`, `sch_0d6eb1a2`, `sch_8310be74` удалены из `runtime.schedules`; backend-код не менялся.
- **Training sample.** Локальный backend без `CG_TRAINING_PROJECT` отдаёт 400; UI показывает человеческий текст. Для проверки самого training нужен env с путём к training-project.
- **Report 404 до завершения run.** Backend отдаёт 404, пока report не создан; frontend это учитывает, но API-поведение остаётся.
- **Map.** `SecurityMapScreen.tsx` и `ui/security-map/**` — зона другого worker; не трогались.
- **Connection drag.** Автоматический координатный drag в headless не создал новое ребро; это не доказательство бага. Ручную проверку стоит повторить в живом браузере.

## Проверки

```bash
cd frontend && npx tsc -b --pretty false
# 0 ошибок
```

Чистый reload приложения: 0 app errors в console (исторические 401/400 — намеренные проверки wrong code/training).

## Изменённые файлы

`frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/vite.config.ts`, `Navbar.tsx`, `WorkflowCanvas.tsx`, `NodePalette.tsx`, `InspectorPanel.tsx`, `CustomNode.tsx`, `LiveRunView.tsx`, `FindingsView.tsx`, `EvidenceCard.tsx`, `ReportView.tsx`, `ComparisonView.tsx`, `ScheduleModal.tsx`, `AiTrustModal.tsx`, `UploadModal.tsx`.

`docs/web-debug.md` — этот отчёт.

Push/main не трогались.