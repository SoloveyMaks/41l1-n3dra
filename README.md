# 41l1-n3dra

AI Security Workflow для Cyber Garden 2026: собственные проверки Kotlin/Spring Boot, визуальный workflow и объяснения с доказательствами.

**Сейчас в ветке:** собственная выгрузка `AnalysisBundle` (пакет B), файловое RAG-хранилище с переиспользованием элементов между запусками, DTO и проверяемые миграции как предложение, подключаемый React Flow SecurityMap и модуль сравнения immutable bundles. Карта проверена на ручном pickup-preview; API и общий интерфейс ещё требуют интеграции A/B. Схема `0.1` остаётся предложением для согласования A/B/D.

## Начать здесь

- [Решения проекта и порядок сборки](PROJECT-DECISIONS.md)
- [Пакет C: данные и карта](docs/c-data-handoff.md)
- [Анализаторы пакета B](analyzers/README.md) — SECRET/SCA/SAST и выгрузка `AnalysisBundle`
- [RAG-хранилище элементов](rag/README.md) — переиспользование фактов и поиск исправлений без реляционной БД
- [Отчёт по анализаторам](docs/analyzers-report.md)
- [Общие DTO и открытые вопросы](docs/data-contracts/README.md)
- [Миграции и сохранение результатов](db/README.md)
- [Первый путь карты: pickup export](docs/security-map/INTEGRATION.md)
- [Подключение и запуск компонента карты](docs/security-map/COMPONENT.md)
- [Модуль сравнения до/после](docs/data-contracts/COMPARISON-ENGINE.md)
- [Что реально проверено](docs/data-contracts/verification.md)

## Ближайшая интеграция

A создаёт snapshot/run и masked-source API и подключает готовый компонент карты; B отдаёт настоящие findings/code facts/coverage; C сохраняет bundle и сравнивает совместимые результаты; D объясняет существующие находки и возвращает проверенные ссылки. Общие schemas/миграции ведёт C, app/router/manifests — A.

Рабочие ветки собираем в `dev`, готовый кандидат после сквозной приёмки — в `main`. Публикация для жюри и свой домен — позже. Эта ветка `task/data-foundation` не выполняет merge или deploy.

## Проверка пакета

```bash
python -X utf8 -m analyzers.selftest                      # 71 проверка
python -X utf8 -m analyzers.compare <snapshot>             # 15/15, controls 6/6
python -X utf8 -m analyzers.contract_export <snapshot>/training-project \
    --db <snapshot>/vulnerability-database/vulnerabilities.json \
    --out analyzers/examples/bundle.json --rag-store .rag/store
node analyzers/validate_bundle.mjs analyzers/examples/bundle.json <snapshot>/training-project
```

Реликционной БД нет: `rag/` хранит элементы в append-only журнале и выводит векторы из объявленного embedding spec. SQL в `db/` остаётся проверяемым предложением пакета C и к runtime не подключён; команды — в [db/README.md](db/README.md). Не применять к чужой рабочей БД без разбора.

JSON Schema Draft 2020-12 — источник формата; TypeScript генерируется командой `node docs/data-contracts/generate-types.mjs`. Semantic validator проверяет ссылки и координаты дополнительно к Schema. Локальные probes PostgreSQL/pgvector, AJV и TypeScript прошли; внешняя embedding model и scanner integration ещё не проверены.

Учебный источник — [TBagirov/training, зафиксированный snapshot](https://github.com/TBagirov/training/tree/fe1fcbe212669783480666b9b60147c8a8c6eef4). Он не включён в ветку. В `pickup-path.illustration.json` сохранены source repository/commit/root, hashes и ranges для воспроизведения проверки. Ground truth не является входом анализатора. Fixture — ручная иллюстрация, не scanner output.
