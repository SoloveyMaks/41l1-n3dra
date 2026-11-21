# Release handoff — 41l1-n3dra / nedra

Короткий пакет для backend-разработчика и жюри: что реально проверено, что
ещё pending и что нельзя заявлять. Контракты — в
[docs/data-contracts](data-contracts/README.md), решения — в
[PROJECT-DECISIONS.md](../PROJECT-DECISIONS.md).

## Состояние

- Публичный стенд `https://nedra.soloveyka.bond` собран из `9862e19`: старый UI,
  один общий код, без user/admin. **Новый UI ещё не public.**
- Локальная `dev` — exact `b6abde4`; в ней user/admin, принятые исправления и
  два post-AFTER фикса: Markdown-полнота `9bcb5fa`, свежесть/авторизация
  админки `6a9dd99`.
- Три независимых Sol-отчёта имеют AFTER85: logic закрыл artifact A/B и
  failed-report, visual — основные мобильные блокеры, structure — 4 из 6.
- Review стенда 18082 на exact `b6abde4` — READY. Structure выполняет узкий
  независимый FINAL двух остатков; результат FINAL ещё не заявлен.
- Root build PASS. **Финальная приёмка, публичный релиз и «все проверки
  пройдены» не заявляются.**
- `main` — после приёмки. Merge/push в `main` делает координатор, не worker.
- Хост — локальный ПК; `Linger=no`, после reboot нужен пользовательский login.

## Независимые отчёты

| Отчёт | AFTER85 | Что закрыто и что осталось |
| --- | --- | --- |
| [Sol logic](reviews/sol-logic.md) | `85acb5a` | artifact A/B и failed-report закрыты новыми живыми runs; quick 403/no-PUT, downloads, SSE, evidence и comparison без нового блокера; admin counters там не перепроверялись |
| [Sol visual](reviews/sol-visual.md) | `85acb5a` | основные мобильные блокеры закрыты: карта/детали, фильтры, результат, active admin; desktop без замеченной регрессии; edge labels и часть comparison — частично, не блокируют |
| [Sol structure](reviews/sol-structure.md) | `85acb5a` | 4 из 6 закрыты; остались Markdown «100%» и устаревающий admin overview |

Исторические baseline/AFTER в этих отчётах не переписаны. Этот handoff их не
исправляет задним числом. [Report coverage](report-coverage.md) фиксирует
каноническую полноту отчёта: `100%` только при `complete` и подтверждённых
scopes; иначе «не установлена/недоступна/неизвестна».

## Выполнено живыми проверками

- Logic AFTER85: сохранённый draft A + trigger B дают B во всех scanner inputs,
  findings, map, report и official JSON; поздняя ошибка Condition даёт
  `executionStatus=failed` в run, официальном JSON и Markdown.
- Visual AFTER85: на 390 читаются карта/детали, фильтры, карточка результата и
  active admin; desktop основной путь без новой визуальной регрессии.
- Structure AFTER85: привязка результата к проекту, понятный выбор сравнения,
  рекомендация по выбранному run и повторная маскировка старых поверхностей
  проверены; 4 из 6 блокеров закрыты.
- Root build PASS.

## Pending

| Что | Статус |
| --- | --- |
| Узкий независимый FINAL Markdown-полноты и admin freshness/auth | идёт; review 18082 exact `b6abde4` READY, FINAL не заявлен |
| Публичная пересборка/деплой | pending; production остаётся `9862e19` |
| Публичный smoke нового UI | не выполнялся; после deploy нужен отдельный |
| Финальная приёмка | не заявлена |
| Неблокирующие visual-остатки: короткие edge labels, часть comparison-текста на 390, конструктор на 390 | частично/не перепроверено |

## Принятые исправления

| Что | Коммит/док | Эффект |
| --- | --- | --- |
| Повторный intake snapshot | `006`, `113bfeff`, `3f56903`; [repeated-snapshot](repeated-snapshot.md) | один content snapshot может иметь несколько artifacts/runs; hash без origin отклоняется; manifest/files не переписываются |
| Один artifact на проверку | `9143178`; [run-artifact](run-artifact-consistency.md) | явно выбранный artifact — источник для findings, карты, отчёта и публикации; draft — только fallback; AFTER85 подтвердил живыми runs |
| Защита канонического вердикта | `9083f04`; [verdict-consistency](verdict-consistency.md) | не-`fail` при частичном/неподтверждённом покрытии даёт `inconclusive`, а не `pass` |
| Фактический `executionStatus` | `e0126f6`; [report-execution-status](report-execution-status.md) | позднее падение condition даёт `failed` в run и отчёте; AFTER85 подтвердил |
| Synthetic comparison guard | `df33cee`; [comparison-origin](comparison-origin.md) | `contract_scenario`/`manual_fixture` не дают `comparable=true` |
| Каноническая полнота отчёта | `9bcb5fa`; [report-coverage](report-coverage.md) | `100%` только complete+verified; partial/unavailable/unknown не выдаются за полноту; Markdown исправлен |
| Свежесть/авторизация admin overview | `6a9dd99`, `f464eee`, `b6abde4`; [interface-structure](interface-structure.md) | overview обновляется после run/refresh, stale ответы после logout/смены роли отбрасываются |

Post-AFTER фиксы `9bcb5fa` и `6a9dd99` находятся в exact `b6abde4`; их узкий
FINAL ещё не заявлен. Наличие кода не считается закрытием без FINAL.

## Что реально работает в локальном `dev`

- User: три шага «выбрать проект → запустить проверку → результат», без
  конструктора и без `PUT draft`; проблемы, карта, сравнение и отчёт.
- Admin: отдельный вход по `CG_ADMIN_ACCESS_CODE`, `adminAvailable`,
  `GET /api/admin/overview`; группы «Обзор», «Проекты и проверки», «Сценарий»
  (конструктор, расписания), «Результаты», «Система» (анализаторы/покрытие,
  AI/память, доступ).
- Права: `user` не создаёт сценарий, не делает `PUT draft`, не видит
  расписания, AI-тест, training и admin overview (403). `admin` эти действия
  выполняет.
- Роль — операторское ограничение поверх одного общего workspace. Аккаунтов,
  списков доступа, приватных кабинетов и multi-tenant-изоляции нет.
- `security-map` показывает узлы/связи, unknown-связи, evidence и покрытие;
  неизвестная связь не выдаётся за безопасное состояние.
- Публичный контейнер `9862e19` (старый): Team B run `cc781c1b-...`,
  15 findings, 43 nodes, 32 edges, 67 evidence; default engine остался `backend`.

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
bundle и не заменяет общее сравнение.

## Инварианты, которые нельзя ломать

- `workspaceId`, `runId`, `snapshotId` — UUID и не переписываются; snapshot
  immutable после seal; canonical envelope сохраняет identity.
- Один content snapshot может иметь несколько artifact origins (`006`), но
  проверка использует один явно выбранный artifact.
- Runtime — PostgreSQL без SQLite; факты — `data_contract`, API-состояние —
  `runtime`; миграции идут ledger-ом.
- Факты/доказательства, AI-объяснения и политика предметной области разделены;
  AI не меняет проблемы, покрытие и вердикт. Политика предметной области не
  подключена к канонической подписи — не выдавать допущения за работающее
  ограничение.
- Карта доступа и данных (code facts/evidence) ≠ workflow-граф исполнения.
- `pass` не означает ноль проблем или полную проверку; без полного
  подтверждённого покрытия честный не-`fail` — `inconclusive`.
- `executionStatus` отчёта — фактическое выполнение, отдельно от вердикта.
- Полнота отчёта — canonical completeness, а не parser ratio; `100%` только
  при `complete` и подтверждённых scopes.
- Сравнение: synthetic origin не становится `comparable=true`; потеря покрытия
  и неоднозначный anchor не дают `resolved`; `deletedFiles` — не «исправление».
- SQL-проверка `KOTLIN-SQL-INJECTION` — узкий per-file source pattern; общее
  сравнение остаётся `inconclusive`.
- AI — только ReformBoss, default `deepseek-v4.1-flash-1017`; ключ не в логах.
- Embeddings optional, retrieval выключен и `unavailable` (403
  `token_model_not_allowed`). Это векторные представления текста для точного
  поиска, без ANN-индекса. Активных спек 0, fake vectors нет.
- `vetted` remediation требует реального scanner provenance, published fixed
  bundle, checked coverage и published comparison `resolved`; synthetic
  `contract_scenario` — только draft. Проверенных примеров 0.

## Перед слиянием в main

1. Дождаться узкого независимого FINAL двух post-AFTER фиксов и зафиксировать
   его результат; не закрывать по факту наличия коммита.
2. Пересобрать `dev` и отдельно проверить публичный стенд; production пока
   `9862e19`, новый UI ещё не public.
3. Выполнить публичный smoke нового UI: user/admin, три шага, проблемы, карта,
   сравнение, отчёт, redaction.
4. Обновить PR и слить `dev → main` с сохранением истории. Merge/push делает
   координатор. Финальную приёмку объявляет координатор, не этот handoff.

## Оставшиеся ограничения

- Публичный стенд отстаёт от `dev`: user/admin, AFTER85 и post-AFTER фиксы ещё
  не опубликованы.
- FINAL двух остатков structure не заявлен; после FINAL/deploy статус
  публикации обновляет root или отдельный follow-up.
- Общее сравнение остаётся `inconclusive`; SQL-проверка узкая; vetted-примеров 0.
- Embeddings optional: 403, точный поиск без ANN-индекса, активных спек 0.
- Политика предметной области не подключена; допущения видны как допущения.
- Parser/anchor/runtime graph ограничения остаются видимыми. Полное покрытие и
  отсутствие уязвимостей не заявляются.
- Shared demo workspace не является системой приватных кабинетов; хост должен
  работать, user manager — быть запущен.
- Production readiness и независимый VPS не заявляются.
