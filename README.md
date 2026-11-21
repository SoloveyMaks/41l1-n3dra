# 41l1-n3dra

Проверка безопасности Kotlin/Spring Boot: визуальный редактор сценариев,
собственные SECRET/SCA/SAST, доказательства, карта доступа, AI-объяснения,
сравнение и отчёт. Пользователь собирает проверку блоками и загружает ZIP
или публичный GitHub/GitLab; системные настройки остаются в отдельной админке.

## Статус

- Исходники включают пользовательский редактор, аккаунты/workspace,
  GitHub/GitLab import/watch/hooks, Telegram и Mini App. Наличие кода не означает,
  что весь путь проверен в текущем публичном образе.
- Публичный адрес: https://nedra.soloveyka.bond. Текущие production SHA/image ID
  нужно сверять на хосте; старые идентификаторы из handoff не являются текущим
  статусом. Этот аудит ничего не выкладывает.
- Runtime schema в исходнике — v5. Применённые исторические миграции сохраняют
  checksum; запуск backend может обновить БД уже при импорте модуля.
- [Release handoff](docs/RELEASE-HANDOFF.md) описывает оставшиеся проверки,
  [release-аудит](docs/reviews/full-audit-release.md) — фактический охват.
- Это прототип с ограничениями покрытия; production readiness не заявляется.

## Проверить проект

1. Войдите обычным пользователем и откройте редактор.
2. Выберите сценарий либо создайте именованную копию; настройте блоки и связи.
3. Нажмите «ZIP или репозиторий», загрузите проект и запустите проверку.
4. Смотрите ход выполнения, проблемы, доказательства, карту и отчёт.

Запуск предварительно сохраняет актуальный сценарий и фиксирует его ревизию.
История и стартовый результат относятся к выбранному сценарию.
Админка открывается отдельным кодом: обзор, проекты, сценарии, расписания,
результаты и система. Пользователь не получает доступ к системным API.

## Что внутри

- `backend/` — FastAPI, executor, SECRET/SCA/SAST, code facts, каноническая
  публикация в PostgreSQL, сравнение, AI-объяснения и отчёты.
- `frontend/` — React/Vite: редактор блоков и результаты для пользователя;
  отдельный административный shell для системных настроек.
- `ui/security-map` — React Flow карта доступа и данных.
- `packages/comparison` — сравнение published envelopes.
- `analyzers/` — собственные SECRET/SCA/SAST и выгрузка `AnalysisBundle`.
- `rag/` — файловое хранилище элементов и векторов: переиспользование находок,
  узлов и проверенных правок между запусками без реляционной схемы.
- `db/` — PostgreSQL-миграции; `data_contract` хранит канонические факты,
  `runtime` — API-состояние.
- `deploy/`, `scripts/nedra-*.sh` — локальный контейнерный запуск.

## Начать здесь

- [Интерфейс: пользователь и администратор](docs/interface-structure.md)
- [Решения проекта](PROJECT-DECISIONS.md)
- [Release handoff](docs/RELEASE-HANDOFF.md)
- [Сценарий показа для жюри](docs/JURY-WALKTHROUGH.md)
- [Понимание кейса и речь для защиты](docs/mentor-defense.md)
- [Импорт репозитория и наблюдение](docs/repository-import.md)
- [Словарь продукта](docs/PRODUCT-GLOSSARY.md)
- [Sol logic](docs/reviews/sol-logic.md) — принят AFTER85
- [Sol visual](docs/reviews/sol-visual.md) — принят AFTER85
- [Sol structure](docs/reviews/sol-structure.md) — принят FINAL
- [Sol migration](docs/reviews/sol-migration.md) — 007/PG16 PASS
- [Report coverage](docs/report-coverage.md) — каноническая полнота отчёта
- [Контракты данных](docs/data-contracts/README.md), [SQL ↔ DTO](docs/data-contracts/mapping.md)
- [Локальный деплой](docs/DEPLOYMENT.md), [AI/retrieval](docs/ai-runtime.md)
- [Анализаторы пакета B](analyzers/README.md) — SECRET/SCA/SAST и `AnalysisBundle`
- [RAG-хранилище элементов](rag/README.md) — переиспользование фактов между запусками
- [Отчёт по анализаторам](docs/analyzers-report.md)

## Локальный запуск (PostgreSQL)

PostgreSQL 16+; pgvector не обязателен. Python 3.11+ (в контейнере 3.12).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/41l1_n3dra
python -m backend.app.storage.migrate        # ledger db/migrations; optional при DB_ENABLE_VECTOR=1
uvicorn backend.app.main:app --env-file .env --port 8000
```

Backend сам применяет ledger-миграции при старте. `DB_ENABLE_VECTOR=1` включает
optional `db/optional/003`, `014` и `015` только при доступном pgvector; fake
vectors в реальные таблицы не пишутся. `015` — отдельный `draft`-only demo/fixture с
synthetic-векторами для проверки exact-механики; он не входит в реальную выдачу
и не является выбором embedding-модели. Без `DATABASE_URL`/драйвера/доступной БД
старт падает с понятной ошибкой, без DSN в тексте.

```bash
python -X utf8 -m analyzers.selftest                      # 74 проверки
python -X utf8 -m analyzers.compare <snapshot>             # 15/15, controls 6/6
python -X utf8 -m analyzers.contract_export <snapshot>/training-project \
    --db <snapshot>/vulnerability-database/vulnerabilities.json \
    --out analyzers/examples/bundle.json --rag-store .rag/store
node analyzers/validate_bundle.mjs analyzers/examples/bundle.json <snapshot>/training-project
```

`--rag-store` дополнительно индексирует выгрузку в файловое `rag/`: элементы
получают id из содержания, поэтому второй прогон того же кода даёт
`new: 0, reused: 148`. Это отдельный слой переиспользования, а не замена
PostgreSQL-таблиц канонических фактов; ограничения — в
[rag/README.md](rag/README.md).

Frontend:

```bash
cd frontend && npm ci && npm run build       # production build
# отдельный dev-сервер: npm run dev          # /api проксируется на :8000
```

Локальная сессия: `POST /api/session` с `role: "user"` или `role: "admin"`.
Админский вход требует отдельный `CG_ADMIN_ACCESS_CODE`; если он не задан или
совпадает с пользовательским, `adminAvailable: false`. Публичный режим
`CG_PUBLIC_DEMO=1` не выдаёт cookie сам. Для запуска установите стабильный
`CG_SESSION_SECRET`; секреты не помещайте в команды или историю shell.
`backend/requirements.txt` включает python-docx для DOCX-отчётов; в deployment
версии всех зависимостей закреплены отдельно в `deploy/requirements.txt`.

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
- Аккаунты получают отдельные workspace; demo-сессия использует общий env
  workspace. Изоляция всех API требует отдельной живой проверки, наличие
  ContextVar само по себе её не доказывает.

## Ограничения

Demo-сессии работают в общем env workspace; обычные аккаунты имеют отдельный
workspace. Хост — локальный ПК; запуск после reboot зависит от user systemd
manager и linger. Покрытие частичное; retrieval и embedding-провайдер зависят
от окружения. Общее сравнение может оставаться `inconclusive`; SQL-проверка
узкая. Исторические UI-ревью не заменяют проверку текущей версии.

Полный security-аудит, «все функции проверены» и production readiness не
заявляются. Полная сводка — в [release handoff](docs/RELEASE-HANDOFF.md).
