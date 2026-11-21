# Release handoff — 41l1-n3dra / nedra

Короткий пакет для backend-разработчика и жюри: что реально готово, как это
запустить и что пока нельзя заявлять. Полные контракты — в
[docs/data-contracts](data-contracts/README.md), решения — в
[PROJECT-DECISIONS.md](../PROJECT-DECISIONS.md).

## Состояние

- Публичный стенд `https://nedra.soloveyka.bond` собран из `9862e19`: старый UI,
  один общий код, без user/admin. Это не текущий `dev`.
- Локальная `dev` (`1dfd5b2`) содержит user/admin, админский обзор и принятые
  исправления из таблицы ниже.
- **Релиз не готов.** Sol baseline `95da50b` нашёл blockers; focused retest по UI
  и redaction не выполнен. «Все проверки пройдены» не заявляется.
- `main` — после приёмки. Merge/push в `main` делает координатор, не worker.
- Хост — локальный ПК; `Linger=no`, после reboot нужен пользовательский login.

## Принятые исправления в локальном `dev`

| Что | Коммит/док | Проверяемый эффект |
| --- | --- | --- |
| Повторный intake snapshot | `006`, `113bfeff`, `3f56903`; [repeated-snapshot](repeated-snapshot.md) | один content snapshot может иметь несколько artifacts/runs; hash без origin отклоняется; manifest/files не переписываются |
| Один artifact на проверку | `9143178`; [run-artifact](run-artifact-consistency.md) | явно выбранный artifact — источник для findings, карты, отчёта и публикации; draft — только fallback |
| Защита канонического вердикта | `9083f04`; [verdict-consistency](verdict-consistency.md) | не-`fail` при частичном/неподтверждённом покрытии даёт `inconclusive`, а не `pass`; known fail остаётся fail |
| Фактический `executionStatus` | `e0126f6`; [report-execution-status](report-execution-status.md) | позднее падение condition даёт `failed` и в run, и в отчёте; notify failure не делает анализ failed |
| Synthetic comparison guard | `df33cee`; [comparison-origin](comparison-origin.md) | `contract_scenario`/`manual_fixture` не дают `comparable=true`; причина — в `reasons` |

Это принятые изменения, а не заявление о полном e2e после каждого фикса. Evidence
и границы — в указанных документах. `expectedScopesVerified=false` остаётся
честным ограничением, его нельзя поднимать в `true` без доказанных scopes.

## Sol baseline и focused retest

Независимый baseline: [docs/reviews/sol-structure.md](reviews/sol-structure.md),
версия `95da50b`. Он не переписывается этим handoff.

Зафиксированные blockers:

1. Новый ZIP может показывать старый результат без привязки к проекту.
2. «Полнота 100%» может скрывать неполноту и причину `inconclusive`.
3. Админский обзор смешивает общие счётчики workspace и выбранную проверку.
4. Сравнение требует понятного выбора пары «до/после» и русских причин отказа.
5. Карточка проблемы не ведёт к рекомендации/исправлению.
6. Наблюдавшаяся неполная маскировка sample credential — стоп-фактор до показа.

После baseline в `dev` уже есть правки, но **focused retest не выполнен**.
Минимальный retest перед показом: upload + dropdown + sample → принадлежность
результата; clean run → причина неполноты вместо 100%; старый run → честный
обзор; выбор пары → понятное сравнение; карточка → рекомендация; маскирование
всех поверхностей (карточка, evidence, AI, экспорт, инспектор).

## Что реально работает в локальном `dev`

- User: вход по пользовательскому коду, три шага «выбрать проект → запустить
  проверку → результат», без конструктора и без `PUT draft`; проблемы, карта,
  сравнение и отчёт.
- Admin: отдельный вход по `CG_ADMIN_ACCESS_CODE`, `adminAvailable`,
  `GET /api/admin/overview`; группы «Обзор», «Проекты и проверки», «Сценарий»
  (конструктор, расписания), «Результаты», «Система» (анализаторы/покрытие, AI/память,
  доступ).
- Права: `user` не создаёт сценарий, не делает `PUT draft`, не видит расписания,
  AI-тест, training и admin overview (403). `admin` эти действия выполняет.
- Роль — операторское ограничение поверх одного общего workspace. Аккаунтов, списков доступа,
  приватных кабинетов и multi-tenant-изоляции нет.
- `security-map` показывает узлы/связи, unknown-связи, evidence и покрытие;
  неизвестная связь не выдаётся за безопасное состояние.
- Публичный контейнер `9862e19` (старый): Team B run `cc781c1b-...`,
  15 findings, 43 nodes, 32 edges, 67 evidence; default engine остался `backend`.
  Эти цифры относятся к публичной сборке, не к новому user/admin UI.

## Проверка

### Локально (PostgreSQL)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/41l1_n3dra
python -m backend.app.storage.migrate     # 001/002/004/005/006; +003 при DB_ENABLE_VECTOR=1
uvicorn backend.app.main:app --env-file .env --port 8000
cd frontend && npm ci && npm run build    # статику отдаёт backend
# dev-сервер: npm run dev (proxy /api → 127.0.0.1:8000)
```

Локальная сессия: `POST /api/session` с `role: "user"` или `role: "admin"`.
Админский вход требует отдельный `CG_ADMIN_ACCESS_CODE`; без него
`adminAvailable: false` и admin-вход возвращает 503.

### Локальный контейнер

```bash
scripts/nedra-bootstrap.sh --check     # read-only
scripts/nedra-bootstrap.sh --write     # ~/.config/nedra/runtime.env (600)
scripts/nedra-build.sh
scripts/nedra-up.sh                    # --db-only/--app-only/--recreate-app
scripts/nedra-status.sh
scripts/nedra-stop.sh                  # или nedra-down.sh
scripts/nedra-systemd-install.sh       # tunnel unit не включается
scripts/nedra-tunnel.sh status         # опубликованный отдельный tunnel nedra
```

Секреты, `CG_ACCESS_CODE` и `CG_ADMIN_ACCESS_CODE` — только в
`~/.config/nedra/runtime.env`; скрипты не печатают значения, в argv/логи/
репозиторий они не попадают. Детали образов, owner labels и Cloudflare — в
[DEPLOYMENT.md](DEPLOYMENT.md).

### SQL-проверка и сравнение

```bash
python examples/demo/sql-before-after/prepare_snapshots.py
python examples/demo/sql-before-after/run_real_scan.py
node examples/demo/sql-before-after/build_envelopes.mjs
node examples/demo/sql-before-after/negative_probes.mjs
node packages/comparison/compare-cli.mjs \
  examples/demo/sql-before-after/baseline.published.json \
  examples/demo/sql-before-after/current.published.json
```

Это demo-доказательство узкого `source_pattern`; оно не публикует production
bundle и не заменяет general comparison.

## Инварианты, которые нельзя ломать

- `workspaceId`, `runId`, `snapshotId` — UUID и не переписываются; snapshot
  immutable после seal; canonical envelope сохраняет identity.
- Один content snapshot может иметь несколько artifact origins (`006`), но
  проверка использует один явно выбранный artifact.
- Runtime — PostgreSQL без SQLite; факты — `data_contract`, API-состояние —
  `runtime`; миграции идут ledger-ом.
- Факты/доказательства, AI-объяснения и политика предметной области разделены;
  AI не меняет проблемы, покрытие и вердикт. Политика предметной области
  сейчас не подключена к канонической подписи — не выдавать допущения за
  работающее ограничение.
- Карта доступа и данных (code facts/evidence) ≠ workflow-граф исполнения.
- `pass` не означает ноль проблем или полную проверку; без полного
  подтверждённого покрытия честный не-`fail` — `inconclusive`.
- `executionStatus` отчёта — фактическое выполнение, отдельно от вердикта.
- Сравнение: синтетический источник не становится `comparable=true`; потеря покрытия
  и неоднозначный anchor не дают `resolved`; `deletedFiles` — не «исправление».
- SQL-проверка `KOTLIN-SQL-INJECTION` — узкий per-file source pattern; общее
  сравнение остаётся `inconclusive`.
- AI — только ReformBoss, default `deepseek-v4.1-flash-1017`; ключ не в логах.
- Embeddings optional и `unavailable` (403 `token_model_not_allowed`); это
  векторные представления текста для точного поиска, без ANN-индекса. Активных
  спек 0, fake vectors нет.
- `vetted` remediation требует реального scanner provenance, published fixed
  bundle, checked coverage и published comparison `resolved`; synthetic
  `contract_scenario` — только draft. Проверенных примеров 0.

## Перед слиянием в main

1. Выполнить focused retest blockers из Sol baseline; не закрывать их по факту
   наличия коммита.
2. Проверить маскировку на всех поверхностях: карточка, evidence, AI, экспорт,
   инспектор.
3. Пересобрать `dev` и отдельно проверить публичный стенд; не переносить
   локальные результаты на `9862e19`.
4. Обновить PR и слить `dev → main` с сохранением истории. Merge/push делает
   координатор.

## Оставшиеся ограничения

- Публичный стенд отстаёт от `dev`: user/admin и принятые исправления ещё не
  опубликованы.
- Sol blockers и redaction retest не закрыты; релиз не готов.
- Общее сравнение остаётся `inconclusive`; SQL-проверка узкая; vetted-примеров 0.
- Embeddings optional: 403, точный поиск без ANN, активных спек 0.
- Политика предметной области не подключена; допущения видны как допущения.
- Parser/anchor/runtime graph ограничения остаются видимыми. Полное покрытие и
  отсутствие уязвимостей не заявляются.
- Shared demo workspace не является системой приватных кабинетов; хост должен
  работать, user manager — быть запущен.
- Production readiness и независимый VPS не заявляются.