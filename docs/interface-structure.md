# Интерфейс: пользователь и администратор

Пользовательский экран и админка разведены по ролям. Конструктор сценария больше
не является стартовым экраном; обычный посетитель видит только «Проверка проекта».
Админка остаётся отдельным shell-ом с боковой навигацией.

## Пользователь

Главный путь — три шага:

1. **Выберите проект** — список загруженных проектов, ZIP или «Попробовать пример».
2. **Запустите проверку** — стандартный сохранённый сценарий, без редактирования.
3. **Посмотрите результат** — выполнение и безопасность показаны раздельно.

Результат ведёт в «Проблемы», «Карту доступа», «Сравнить проверки» и «Отчёт».
История проверок показывает имя проекта, дату и человеческий вердикт; технические
ID и SSE не выводятся в основном пути. Пользователь не видит конструктор,
расписания и AI-тест. Его запуск **не вызывает** `PUT /workflows/{id}/draft`;
проверка идёт по уже сохранённому сценарию и выбранному артефакту.

Словарь: `findings` — «Проблемы», `evidence` — «Фрагмент кода/Доказательство»,
`workflow` — «Сценарий проверки», `snapshot` — «Снимок проекта», `run` —
«Проверка», `pass` — «Нет блокирующих проблем по политике» (если findings > 0) или
  «Проблем не выявлено в проверенной части» (если findings = 0), `fail` —
«Найдены проблемы», `inconclusive` — «Неполная проверка».

## Администратор

Отдельный `AdminShell` с группами:

- **Обзор** — реальные счётчики, последняя проверка, состояние DB/model/vector.
- **Проекты и проверки** — проекты и история, переход в результаты.
- **Сценарий** — существующий конструктор и расписания.
- **Результаты** — ход проверки, проблемы, карта, отчёт, сравнение.
- **Система** — анализаторы/покрытие (readonly), AI и память исправлений,
  доступ и состояние без секретов.

Обзор использует `GET /api/admin/overview` (реальный DTO: `counts`, `db`, `ai`,
`vector`, `uploads`, `workspace`, `auth`). Если endpoint недоступен, панель
показывает реальные локальные счётчики и явно пишет, что DTO не отдан.

## Access gate

- По умолчанию — вход обычного пользователя по `CG_ACCESS_CODE`.
- Ссылка «Вход для администратора» открывает отдельную форму.
- `POST /api/session` получает `{ accessCode, role: "user" | "admin" }`.
- Админ-код — отдельный `CG_ADMIN_ACCESS_CODE`; `adminAvailable` приходит в
  `/api/session`. Админ-вход не повышает права обычным кодом.

## Компоненты

- `UserDashboard.tsx` — трёхшаговый главный экран пользователя.
- `AdminShell.tsx` — боковая навигация и каркас админки.
- `AdminPanels.tsx` — обзор, проекты, анализаторы, AI, доступ.
- `AccessGate.tsx` — user/admin вход.
- Старые `WorkflowCanvas`, `LiveRunView`, `FindingsView`, `ReportView`,
  `ComparisonView`, `SecurityMapScreen` сохранены в админке и результатах.

`SecurityMapScreen.tsx` и `ui/security-map/**` не изменялись — это зона
map-worker. Backend в этом worktree не менялся.

## Проверки

- `npm run build` — успешно; `npx tsc -b` — 0 ошибок.
- Пользователь 1440 и 390: sample → запуск → проблемы → фрагмент кода →
  карта → отчёт; `scrollWidth` равен viewport.
- Запуск пользователя: в network только `POST /workflows/{id}/runs`, без
  `PUT /workflows/{id}/draft`.
- Админ 1440 и 390: неверный админ-код → ошибка; верный → обзор с реальными
  счётчиками (проекты/проверки из `/admin/overview`, модель
  `deepseek-v4.1-flash-1017`, vector `disabled`), конструктор, расписание pause/resume.
- Локальный backend — latest dev 9be2d3b (role API 4012fb5 + migration 006 + policy 9083f04)
  на 18082; migration ledger 5 записей. Публичный образ 9862e19 ещё без
  role API, после merge/rebuild проверить admin на публичном HTTPS.
- Не проверено в этом worktree: публичный admin login до пересборки backend,
  полный CRUD проектов/пользователей (его и нет в контракте).

## Скриншоты и коммиты

- `artifacts/ui-user-check-2026-11-21.png` — user, 1440.
- `artifacts/ui-admin-overview-2026-11-21.png` — admin overview.
- `artifacts/ui-admin-schedule-2026-11-21.png` — admin schedule.
- UI commits: `c09f887`, `1587294`; merge latest dev: `c96e606`, `a9c7495`.

## Admin overview freshness

`GET /api/admin/overview` is fetched only for an admin session: on role entry,
after a manual run-history refresh, and after a new run reaches terminal state
(including polling fallback). Node-level SSE events do not refresh it. Responses
are sequence-guarded; logout/role changes invalidate them, and a failed refresh
clears stale counters instead of showing them as current. Refreshing workspace
counts never changes the selected run.

## Ограничения

- Нет выдуманных пользователей/CRUD: админка показывает только реальные
  artifacts/workflows/runs/schedules и фактические DTO.
- AI-поиск исправлений не изображается; показывается фактический `ai_status`
  отчёта.
- Технические ID/SSE остаются в деталях админки, но не в user-пути.
- Секреты не выводятся: gate принимает код, env/ключи не попадают в UI или argv.