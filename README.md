# 41l1-n3dra

Проверка безопасности Kotlin/Spring Boot: визуальный редактор сценариев,
собственные SECRET/SCA/SAST, доказательства, карта доступа, AI-объяснения,
сравнение и отчёт. Пользователь собирает проверку блоками и загружает ZIP
или публичный GitHub/GitLab; системные настройки остаются в отдельной админке.

## Статус

- Публичный стенд: https://nedra.soloveyka.bond. Опубликован код `bde900c`,
  образ `dd25e853d469`, frontend `index-DabRgDEM.js`.
- Восстановлена прежняя панель пользователя: вкладки, история, редактор,
  палитра и инспектор. Свои сценарии создаются, сохраняются и запускаются.
- Импорт публичного репозитория фиксирует точный коммит. Администратор
  настраивает периодический опрос ветки и запуск при новом коммите.
- Runtime PostgreSQL обновлён с v3 до v4 без сброса данных. Контрактные
  миграции и persistent volumes сохранены; перед выкладкой сделан backup.
- Независимые PostgreSQL/ASGI и UI-проверки закрыли найденные блокеры;
  публичный API-путь импорт → свой сценарий → запуск → отчёт пройден.
- Это проверенный прототип с ограничениями покрытия, не полный аудит.

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
python -m backend.app.storage.migrate        # 001/002/004/005/006 (+003 при DB_ENABLE_VECTOR=1)
uvicorn backend.app.main:app --env-file .env --port 8000
```

Backend сам применяет ledger-миграции при старте. `DB_ENABLE_VECTOR=1` включает
optional `db/optional/003` только при доступном pgvector; fake vectors не
создаются. Без `DATABASE_URL`/драйвера/доступной БД старт падает с понятной
ошибкой, без DSN в тексте.

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

Shared workspace: аккаунтов и приватных кабинетов нет; роли `user`/`admin`
ограничивают операторские действия. Хост — локальный ПК; `Linger=no`, после
reboot нужен login. Покрытие частичное; embeddings 403/disabled, retrieval
выключен; vetted-примеров 0. Общее сравнение `inconclusive`, SQL-проверка узкая.
Неблокирующие visual-остатки — короткие edge labels, часть comparison-текста на
390; конструктор на 390 не перепроверялся.

Полный security-аудит, «все функции проверены» и production readiness не
заявляются. Полная сводка — в [release handoff](docs/RELEASE-HANDOFF.md).
