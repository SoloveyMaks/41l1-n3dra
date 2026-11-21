# Release handoff — 41l1-n3dra / nedra

Единая текущая сводка: что проверено, что принято и что ещё pending. Контракты —
в [docs/data-contracts](data-contracts/README.md), решения — в
[PROJECT-DECISIONS.md](../PROJECT-DECISIONS.md).

## Текущее состояние

- Exact candidate — `c7057ee98283805cb90d831a52789d60aa92abcc`, запушен в
  `origin/dev`. App source не менялся после `b6abde4`; собранный asset — `index-DA9FQkXU.js`.
- FINAL Sol structure — `8d8e0ca`, принят в `dev`: закрыты оба остатка AFTER85
  (Markdown-полнота и admin freshness/auth). Review стенда 18082 на exact
  `b6abde4` был READY перед FINAL.
- Три независимых review приняты: logic, visual, structure FINAL. Все шесть
  исходных структурных препятствий закрыты **в проверенных путях**. Это не
  полный аудит и не release-wide approval.
- Production deploy `c705` сейчас выполняет app owner. **Public updated не
  заявлен**; до public smoke публичный стенд считаем прежним `9862e19`.
- Root build PASS. `main`/финальная приёмка не выполнялись.
- Хост — локальный ПК; `Linger=no`, после reboot нужен пользовательский login.

## Три review

| Review | Итог | Доказательство |
| --- | --- | --- |
| [Sol logic](reviews/sol-logic.md) | принят AFTER85: artifact A/B и failed-report закрыты живыми runs | новые runs на `85acb5a`, official JSON/Markdown |
| [Sol visual](reviews/sol-visual.md) | принят AFTER85: основные мобильные блокеры закрыты, desktop без новой регрессии | 390/1440, карта/фильтры/результат/admin |
| [Sol structure](reviews/sol-structure.md) | принят FINAL: оба остатка AFTER85 закрыты | clean/sample exports и admin refresh/polling |

Все шесть исходных structural препятствий: привязка результата к проекту,
честная полнота, разделение workspace/выбранной проверки, понятное сравнение,
путь к рекомендации и повторная маскировка — закрыты в проверенных путях.
Неблокирующие остатки (короткие edge labels, часть comparison-текста на 390,
конструктор на 390) не объявляются закрытыми. Ссылки и evidence review можно
показывать; секретных значений в них нет.

## FINAL: что проверено живьём

- Clean и sample: JSON/Markdown без `100%`; `partial`, `completeness_known=false`,
  русские причины пропущенных runtime-областей. Sample сохраняет FAIL и 13
  находок (12 confirmed + 1 potential).
- Admin manual refresh: экран и backend дают **165 = 165** проверок.
- Terminal polling: overview обновляется **172 → 173**, совпадает с backend; без
  ручного refresh.
- Выбранная старая проверка сохраняется; «Последняя загруженная проверка» не
  подменяется выбранной.
- Принудительный 503 для overview: общие счётчики показывают «—» с объяснением,
  факты выбранной проверки остаются.
- `user` не делает запросов `/api/admin/overview` (0) во время обновления и
  запусков.

Это узкий FINAL двух остатков, не новый полный аудит. Исторические выводы
reports не переписаны; старые сохранённые отчёты с `100%` не пересоздавались.

## Completed vs pending

| Этап | Статус |
| --- | --- |
| Logic AFTER85 | выполнен и принят |
| Visual AFTER85 | выполнен и принят |
| Structure FINAL | выполнен и принят (`8d8e0ca`) |
| Все 6 structural blockers в проверенных путях | закрыты |
| Root build / app source | PASS; source unchanged `b6` |
| Deploy `c705` | выполняется app owner |
| Public updated | **не заявлен** |
| Public smoke | pending |
| `main` / финальная приёмка | pending |
| Полный аудит и release-wide approval | не заявлены |

## Принятые исправления

| Что | Коммит/док | Эффект |
| --- | --- | --- |
| Повторный intake snapshot | `006`, `113bfeff`, `3f56903`; [repeated-snapshot](repeated-snapshot.md) | один content snapshot может иметь несколько artifacts/runs; hash без origin отклоняется |
| Один artifact на проверку | `9143178`; [run-artifact](run-artifact-consistency.md) | выбранный artifact — источник для findings, карты, отчёта и публикации; draft — fallback |
| Защита канонического вердикта | `9083f04`; [verdict-consistency](verdict-consistency.md) | не-`fail` при частичном/неподтверждённом покрытии даёт `inconclusive` |
| Фактический `executionStatus` | `e0126f6`; [report-execution-status](report-execution-status.md) | позднее падение condition даёт `failed` в run и отчёте |
| Synthetic comparison guard | `df33cee`; [comparison-origin](comparison-origin.md) | synthetic origin не даёт `comparable=true` |
| Каноническая полнота отчёта | `9bcb5fa`; [report-coverage](report-coverage.md) | `100%` только complete+verified; partial/unavailable/unknown честно названы |
| Свежесть/авторизация admin overview | `6a9dd99`, `f464eee`, `b6abde4`; [interface-structure](interface-structure.md) | обновление после run/refresh/polling; stale ответы после logout/смены роли отбрасываются |

## Что работает в локальном `dev`

- User: три шага «выбрать проект → запустить проверку → результат», без
  конструктора и без `PUT draft`; проблемы, карта, сравнение и отчёт.
- Admin: отдельный вход по `CG_ADMIN_ACCESS_CODE`, `adminAvailable`,
  `GET /api/admin/overview`; группы «Обзор», «Проекты и проверки», «Сценарий»
  (конструктор, расписания), «Результаты», «Система» (анализаторы/покрытие,
  AI/память, доступ).
- `user` не создаёт сценарий, не делает `PUT draft`, не видит расписания,
  AI-тест, training и admin overview (403). `admin` эти действия выполняет.
- Роль — операторское ограничение поверх одного общего workspace. Аккаунтов,
  списков доступа, приватных кабинетов и multi-tenant-изоляции нет.

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
репозиторий они не попадают.

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

Это demo-доказательство узкого `source_pattern`; оно не заменяет общее
сравнение.

## Инварианты

- `workspaceId`, `runId`, `snapshotId` — UUID и не переписываются; snapshot
  immutable после seal.
- Один content snapshot может иметь несколько artifact origins (`006`), но
  проверка использует один явно выбранный artifact.
- Runtime — PostgreSQL; факты — `data_contract`, API-состояние — `runtime`.
- Факты/доказательства, AI-объяснения и политика предметной области разделены;
  AI не меняет проблемы, покрытие и вердикт. Политика предметной области не
  подключена к канонической подписи — допущения не являются работающим
  ограничением.
- `pass` не означает ноль проблем или полную проверку; без полного
  подтверждённого покрытия честный не-`fail` — `inconclusive`.
- `executionStatus` отчёта — фактическое выполнение, отдельно от вердикта.
- Полнота отчёта — canonical completeness, а не parser ratio; `100%` только
  при `complete` и подтверждённых scopes.
- Сравнение: synthetic origin не даёт `comparable=true`; потеря покрытия и
  неоднозначный anchor не дают `resolved`; `deletedFiles` — не «исправление».
- SQL-проверка `KOTLIN-SQL-INJECTION` — узкий source pattern; общее сравнение
  остаётся `inconclusive`.
- Embeddings optional, retrieval выключен и `unavailable` (403). Активных спек
  0, fake vectors нет. `vetted`-примеров 0.

## Перед main и публикацией

1. Дождаться завершения deploy `c705` и подтвердить exact revision на публичном
   стенде. **Не объявлять public updated до подтверждения.**
2. Выполнить публичный smoke нового UI: user/admin, три шага, проблемы, карта,
   сравнение, отчёт, redaction.
3. Проверить, что public smoke не изменил ограничения: shared workspace,
   local PC/login, embeddings 403/disabled, vetted 0, general comparison
   `inconclusive`, SQL proof узкий.
4. После public smoke обновить статус публикации root/отдельным follow-up.
5. `main` и финальную приёмку выполняет координатор; worker не push/merge main.

## Оставшиеся ограничения

- Public updated не подтверждён; deploy `c705` ещё выполняется.
- Public smoke нового UI pending; public остаётся `9862e19` до подтверждения.
- Неблокирующие visual-остатки: короткие edge labels, часть comparison-текста
  на 390; конструктор на 390 повторно не проверялся.
- Общее сравнение остаётся `inconclusive`; SQL-проверка узкая; vetted 0.
- Embeddings optional: 403, точный поиск без ANN-индекса, активных спек 0.
- Политика предметной области не подключена; допущения видны как допущения.
- Parser/anchor/runtime graph ограничения остаются видимыми. Полное покрытие и
  отсутствие уязвимостей не заявляются.
- Shared demo workspace не является системой приватных кабинетов; хост —
  локальный ПК, после reboot нужен login.
- Production readiness и независимый VPS не заявляются.