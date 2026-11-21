# 41l1-n3dra

Проверка безопасности Kotlin/Spring Boot: собственные SECRET/SCA/SAST,
понятный результат, карта доступа, сравнение проверок и отчёт. Пользователь
загружает ZIP и запускает готовый сценарий; конструктор и системные настройки
живут в отдельной админке.

## Статус

- Публичный стенд [nedra.soloveyka.bond](https://nedra.soloveyka.bond) собран из
  `9862e19`: там ещё старый интерфейс, один общий код и нет user/admin-разделения.
- Локальная `dev` (`b6abde4`) содержит user/admin и принятые исправления:
  `006`/происхождение снимков, один артефакт на проверку, защита канонического
  вердикта, фактический статус выполнения и каноническая полнота отчёта.
- Три независимых Sol-отчёта имеют AFTER85: logic закрыл artifact A/B и
  failed-report, visual — основные мобильные блокеры, structure — 4 из 6.
  Два остатка structure исправлены в `b6abde4`: Markdown-полнота `9bcb5fa`,
  свежесть/авторизация админки `6a9dd99`.
- Review стенда 18082 на точном `b6abde4` — READY; structure выполняет узкий
  независимый FINAL этих двух исправлений. Root build PASS.
- **Финальная приёмка не заявлена.** Новый UI ещё не опубликован; «все проверки
  пройдены» не говорим.
- `main` — после приёмки; merge/push делает координатор, не worker.

## Проверить проект за 3 шага

1. **Выберите проект** — ZIP или «Попробовать пример». Поддерживается
   Kotlin/Spring Boot.
2. **Запустите проверку** — используется существующий сохранённый сценарий.
   Пользователь не редактирует граф и не вызывает `PUT draft`.
3. **Посмотрите результат** — проблемы, фрагмент кода, карта доступа, сравнение
   проверок и отчёт.

Админка открывается отдельным кодом и сгруппирована так: **Обзор**,
**Проекты и проверки**, **Сценарий** (конструктор и расписания), **Результаты**,
**Система** (анализаторы/покрытие, AI и память, доступ и состояние).

## Что внутри

- `backend/` — FastAPI, executor, SECRET/SCA/SAST, code facts, каноническая
  публикация в PostgreSQL, сравнение, AI-объяснения и отчёты.
- `frontend/` — React/Vite: трёхшаговый пользовательский экран и отдельный
  админский shell. Конструктор — в админке, не на главном экране.
- `ui/security-map` — React Flow карта доступа и данных.
- `packages/comparison` — сравнение published envelopes.
- `db/` — PostgreSQL-миграции; `data_contract` хранит канонические факты,
  `runtime` — API-состояние.
- `deploy/`, `scripts/nedra-*.sh` — локальный контейнерный запуск.

## Начать здесь

- [Интерфейс: пользователь и администратор](docs/interface-structure.md)
- [Решения проекта](PROJECT-DECISIONS.md)
- [Release handoff](docs/RELEASE-HANDOFF.md)
- [Сценарий показа для жюри](docs/JURY-WALKTHROUGH.md)
- [Словарь продукта](docs/PRODUCT-GLOSSARY.md)
- [Sol logic: AFTER85](docs/reviews/sol-logic.md) — artifact A/B и failed-report
- [Sol visual: AFTER85](docs/reviews/sol-visual.md) — мобильные блокеры
- [Sol structure: AFTER85](docs/reviews/sol-structure.md) — 4 из 6 и остатки
- [Report coverage](docs/report-coverage.md) — каноническая полнота отчёта
- [Контракты данных](docs/data-contracts/README.md), [SQL ↔ DTO](docs/data-contracts/mapping.md)
- [Локальный деплой](docs/DEPLOYMENT.md), [AI/retrieval](docs/ai-runtime.md)

## Локальный запуск (PostgreSQL)

PostgreSQL 16+; pgvector не обязателен. Python 3.11+ (в контейнере 3.12).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/41l1_n3dra
python -m backend.app.storage.migrate        # 001/002/004/005/006 (+003 при DB_ENABLE_VECTOR=1)
uvicorn backend.app.main:app --env-file .env --port 8000
```

Backend сам применяет ledger-миграции при старте. `DB_ENABLE_VECTOR=1` включает
optional `db/optional/003` только при доступном pgvector; fake vectors не
создаются. Без `DATABASE_URL`/драйвера/доступной БД старт падает с понятной
ошибкой, без DSN в тексте.

Frontend:

```bash
cd frontend && npm ci && npm run build       # production build
# отдельный dev-сервер: npm run dev          # /api проксируется на :8000
```

Локальная сессия: `POST /api/session` с `role: "user"` или `role: "admin"`.
Админский вход требует отдельный `CG_ADMIN_ACCESS_CODE`; если он не задан или
совпадает с пользовательским, `adminAvailable: false`. Публичный режим
`CG_PUBLIC_DEMO=1` не выдаёт cookie сам.

## Инварианты

- Одна проверка — один явно выбранный артефакт. Если артефакт не выбран, берётся
  артефакт из сохранённого черновика; смешивать архивы между проблемами, картой
  и отчётом нельзя.
- `006_snapshot_origins`: один снимок проекта может иметь несколько загрузок и
  проверок; manifest и файлы не переписываются.
- `pass` не означает «ноль проблем»: это отсутствие блокирующих по политике при
  данном покрытии. Пока каноническое покрытие частичное, честный вердикт —
  `inconclusive`.
- `executionStatus` отчёта — фактическое выполнение, а не вердикт безопасности.
- Общее сравнение остаётся `inconclusive`; SQL-проверка узкая
  (`source_pattern`). Синтетический источник никогда не даёт `comparable=true`.
- AI-объяснения — отдельный слой и не меняют проблемы, покрытие и вердикт.
  Векторные представления текста необязательны, retrieval выключен; сейчас недоступны (403),
  проверенных примеров 0. Политика предметной области не подключена к
  канонической подписи; допущения не являются работающим ограничением.
- Workspace общий: аккаунтов, списков доступа и приватных кабинетов нет.
  Роль `user`/`admin` ограничивает операторские действия, а не изолирует данные.

## Ограничения

Публичный стенд `9862e19` старый: новый UI ещё не public, после пересборки
нужен отдельный публичный smoke. AFTER85 закрыл artifact A/B, failed-report и
основные мобильные блокеры. Два остатка structure — Markdown-полнота `9bcb5fa`
и admin freshness/auth `6a9dd99` — уже в `b6abde4`; их узкий FINAL ещё идёт.
Неблокирующие visual-остатки: короткие edge labels, часть comparison-текста на
390, конструктор на 390 повторно не проверялся.

Хост публичного стенда — локальный ПК; `Linger=no`, после reboot нужен
пользовательский login. Полные ограничения и честные статусы — в
[release handoff](docs/RELEASE-HANDOFF.md)
и [словаре](docs/PRODUCT-GLOSSARY.md). Production readiness и полное покрытие не
заявляются.