# 41l1-n3dra

AI Security Workflow для Cyber Garden 2026: собственные проверки Kotlin/Spring Boot, визуальный workflow и объяснения с доказательствами.

**Сейчас в ветке:** схема PostgreSQL, DTO, миграции, exact retrieval pgvector, подключаемый React Flow SecurityMap и модуль сравнения immutable bundles. Карта проверена на ручном pickup-preview; настоящий scanner/API и общий интерфейс ещё требуют интеграции A/B. Схема `0.1` остаётся предложением для согласования A/B/D.

## Начать здесь

- [Решения проекта и порядок сборки](PROJECT-DECISIONS.md)
- [Пакет C: данные и карта](docs/c-data-handoff.md)
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

SQL рассчитан на PostgreSQL 16+. Основные миграции применяются по порядку `001`, `002`; optional `003` требует установленный pgvector. Команды и ограничения — в [db/README.md](db/README.md). Не применять к чужой рабочей БД без разбора.

JSON Schema Draft 2020-12 — источник формата; TypeScript генерируется командой `node docs/data-contracts/generate-types.mjs`. Semantic validator проверяет ссылки и координаты дополнительно к Schema. Локальные probes PostgreSQL/pgvector, AJV и TypeScript прошли; внешняя embedding model и scanner integration ещё не проверены.

Учебный источник — [TBagirov/training, зафиксированный snapshot](https://github.com/TBagirov/training/tree/fe1fcbe212669783480666b9b60147c8a8c6eef4). Он не включён в ветку. В `pickup-path.illustration.json` сохранены source repository/commit/root, hashes и ranges для воспроизведения проверки. Ground truth не является входом анализатора. Fixture — ручная иллюстрация, не scanner output.
