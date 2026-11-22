# Решения по пунктам case.txt — NEDRA на `fc34a52`

Проход по каждому пункту `case.txt` (Cyber Garden 2026, «AI Security Workflow»)
с фактическим состоянием и решением. База состояния — `fc34a52`
(`SoloveyMaks/nedra-requirements`); статусы проверялись по коду, маршрутам и
миграциям этой ревизии, а не по формулировкам README.

Опорные материалы: `PROJECT-DECISIONS.md`; `docs/presentation/requirements-current.md`
(на этой ветке каталога `docs/presentation/` нет — документ взят из git:
`git show SoloveyMaks/audit-all-data-1122:docs/presentation/requirements-current.md`);
`docs/reviews/mentor-checklist-current.md`; `docs/reviews/full-audit-*.md`;
`README.md`; `docs/organizer-method-verification.md`; `docs/JURY-WALKTHROUGH.md`;
`docs/mentor-defense.md`.

## Как читать решения

- **закрыто** — на `fc34a52` есть реализация (файлы/маршруты/миграции) и
  доказательство.
- **закрывается сейчас (&lt;воркер&gt;)** — база в коде есть, недостающая дельта
  делается в текущей волне указанным воркером; доказательство — его тест/прогон.
- **принимаем границу** — честная граница с обоснованием: доработка не
  требуется или не планируется в этом цикле.
- **вне scope** — пункт не про наш продукт или проверяется не нами.

Доказательства на базе: `pytest backend/tests -q` (на `fc34a52` — три файла:
`test_telegram_buttons`, `test_telegram_miniapp`, `test_responsibility_users`);
`backend/test_e2e.py`; `python -m analyzers.selftest`; reference-checks API
(`GET /api/references`, `POST /api/compare`); живые прогоны из
`docs/reviews/mentor-checklist-current.md` (run `a9bfe494-bb2d-4a35-8b32-f0e4ca75c0fa`,
13 этапов `succeeded`, 15 findings, DOCX 45 667 байт);
`docs/organizer-method-verification.md` (сверка с эталоном организаторов
`TBagirov/training@fe1fcbe` — 15/15); `npm run build` / `npm run lint`.

---

## 1. Основные задачи

| # | Требование (коротко) | Фактическое состояние на `fc34a52` (файлы/маршруты) | Решение | Что доказывает закрытие |
| --- | --- | --- | --- | --- |
| 1.1 | Визуальный drag-and-drop конструктор: ноды, параметры, связи без кода | `frontend/src/components/WorkflowCanvas.tsx` (React Flow `@xyflow/react` 12), `NodePalette.tsx`, `InspectorPanel.tsx`; реестр `backend/app/contracts/node_registry.py` — 15 типов нод; `GET /api/node-types`, `POST /api/workflows`, `POST /api/workflows/{id}/validate` | **закрыто** | Живой Chromium-прогон из mentor-checklist: создание/настройка/связь/сохранение/reload, ревизия 11→12 без потери имён; `npm run build`, `npm run lint` |
| 1.2 | Базовые ноды: триггеры (кнопка, расписание), ветвление if/else/сравнение/regex, уведомление с одним каналом на выбор | Триггеры: `NodeKind.MANUAL_TRIGGER`/`SCHEDULE_TRIGGER`, `backend/app/engine/scheduler.py` (croniter), таблица `runtime.schedules`; ветвление: `NodeKind.CONDITION`, операторы `equals/not_equals/greater_than/less_than/regex` в `backend/app/engine/validator.py:309-321` (неизвестный оператор отклоняется); уведомление: `NodeKind.NOTIFY` → `backend/app/notifications/webhook.py` (HMAC `X-Signature-SHA256`, маскировка, pinning адреса), `POST /api/webhook-test` | **закрыто** | Один канал на выбор реализован как HTTP webhook (ТЗ требует один из Telegram/SMTP/webhook, не все три); SMTP не нужен, Telegram — отдельный канал, его доставку дорабатывает волна **TelegramRuns**; живой run mentor-checklist: notifications API 200, 13 этапов succeeded |
| 1.3 | Собственные ноды проверки: секреты, SCA, SAST Kotlin/Spring Boot/Gradle + классификация критичности | `backend/app/analyzers/secret_analyzer.py`, `sca_analyzer.py`, `sast_analyzer.py`, `version_comparator.py`, `advisory_catalog.py`, `advisories.json` (45 advisory); SAST на tree-sitter Kotlin, свои эвристики; severity/confidence в `contracts/schemas.py`, `NodeKind.POLICY_GATE` | **закрыто** | `python -m analyzers.selftest`; сверка с эталоном организаторов: SECRET 5/5, SCA 5/5, SAST 5/5 (`docs/organizer-method-verification.md`); ранжирование в живом UI: 8 critical → 6 high → 1 medium (mentor-checklist §3.7) |
| 1.4 | AI-анализ: агрегация находок, объяснение простым языком, рекомендации исправления | `backend/app/ai/service.py` (батчи, provenance, детерминированный fallback), `ai/provider.py` (по умолчанию OpenCode Go `deepseek-v4.1-flash`), `ai/validator.py`; ноды `AI_EXPLAIN`/`AI_SCAN`, `countsAsFindings:false` (`executor.py:915`, `reports/builder.py:59`) | **закрывается сейчас (AiEvidence)** | Дельта: валидация упоминаний path/line в свободном тексте объяснения (сейчас валидируются только findingId, CVE и fixed version — `ai/validator.py:74-189`). Доказательство закрытия: детерминированные тесты AiEvidence (валидная ссылка проходит, выдуманный файл/строка отклоняется, текст без ссылок не страдает); живой вызов с provenance — mentor-checklist §3.9 |
| 1.5 | Визуализация выполнения в реальном времени: статус каждого блока и передаваемые между ними данные | SSE: `backend/app/engine/sse.py`, `GET /api/runs/{id}/events` (переподключение реплеит сохранённые события), таблица `runtime.run_events`; UI: `LiveRunView.tsx`, `ExecutionEdge.tsx` + `execution-motion.css` (data-атрибуты из live telemetry), `runTelemetry.ts` | **закрывается сейчас (LiveEdges, MapFiles)** | Дельты: LiveEdges — анимировать только рёбра, по которым реально прошли данные (`edge_data`), skipped-ветвь не выглядит исполняющейся; MapFiles — честный отдельный этап карты (start/end/error/duration) и файловый режим поверх snapshot manifest. Доказательство: живой run mentor-checklist (13 node_runs succeeded); тест выбора активных рёбер и чистая функция файлового дерева; `npm run build`/`lint` |

## 2. Задачи со звёздочкой

| # | Требование (коротко) | Фактическое состояние на `fc34a52` | Решение | Что доказывает закрытие |
| --- | --- | --- | --- | --- |
| 2.1 | Библиотека шаблонов: публикация workflow с описанием, просмотр, копирование, редактирование под себя | Endpoint/таблицы/UI публикации workflow нет (grep по `backend/app/api`, `db/migrations`, `frontend/src` пуст). Есть только заводской шаблон `handleResetToTemplate` (`frontend/src/App.tsx:1103`) и именованные копии; `/api/custom-checks/templates` — шаблоны Python-проверок, не workflow | **закрывается сейчас (WorkflowLibrary)** | Дельта: `publish/list/get/copy` + миграция `018`, валидация существующим `engine/validator.py`, UI-компонент библиотеки. Доказательство: тесты publish → list → copy даёт валидный draft; невалидный workflow отклоняется; scope workspace. Граница: «общая» библиотека — внутри одного workspace; публичного multi-tenant обмена нет по `PROJECT-DECISIONS.md` (роли user/admin над одним workspace) |
| 2.2 | CI/CD: запуск из пайплайна (HTTP/webhook/конфиг), автостарт на коммит или PR/MR, возврат статуса и падение сборки при critical | Push-webhook с HMAC: `POST /api/repository-hooks/{watch_id}` (`backend/app/api/repository_hooks.py`); опрос коммитов: `backend/app/api/repository_watches.py`, admin-экран «Новых коммитов» (мин. 60 с, default 300); PR/MR-события hook не обрабатывает; grep `check_run|commit_status|statuses` по `backend/app` — пусто, генератора CI-конфига нет; в `docs/mentor-defense.md` строка «CI-статус … План» | **закрывается сейчас (CiStatus)** | Дельты: (1) commit status/check-run при critical, (2) автостарт по `pull_request`, (3) шаблон CI-конфига вызова webhook. Доказательство: тесты с фейковым GitHub API — push и pull_request запускают run, статус отправлен или честно пропущен без токена, повтор не дублирует; HMAC/идемпотентность webhook не нарушены |
| 2.3 | AI-генерация сценария по текстовому запросу: распознавание триггеров/сканеров/каналов, отрисовка преднастроенного workflow | `backend/app/scenario_builder/` (`catalog.py`, `service.py`), `backend/app/api/scenario_builder.py`; `frontend/src/components/ScenarioPromptModal.tsx` (413 строк); planner соединяет `AI_SCAN` с отчётом | **закрыто** | Mentor-checklist §3.4: preview 200/valid=true с тремя сканерами + ИИ + отчётом; неизвестный intent отклонён; 255 допустимых комбинаций intents прошли owning validator; диалог открыт в браузере. Граница (честно): custom-скрипты planner не генерирует — работает закрытый intent-каталог |

## 3. Технические требования — минимальные

| # | Требование (коротко) | Фактическое состояние на `fc34a52` | Решение | Что доказывает закрытие |
| --- | --- | --- | --- | --- |
| 3.1 | Работающий прототип: принять файлы/ссылку на Kotlin/Spring Boot Gradle-проект, выполнить собранный workflow, отправить итоговое уведомление с отчётом | Ввод: `POST /api/artifacts` (ZIP), `POST /api/artifacts/repository` (публичные GitHub/GitLab, pinned commit), `POST /api/artifacts/sample` и admin-only `POST /api/artifacts/sample/training`; исполнение: `POST /api/workflows/{id}/runs` (202), `backend/app/engine/executor.py`; уведомление: NOTIFY → `notifications/webhook.py`, но payload сейчас — summary (runId/verdict/counts), без findings/ссылки на отчёт; изолированного e2e-скрипта нет: `scripts/nedra-e2e.sh` отсутствует | **закрывается сейчас (NotifyReport, ReleaseHardening)** | Дельты: NotifyReport — в payload ссылка на существующий `GET /api/runs/{id}/report/export.*` + маскированный список findings (id/severity/rule/path/line) с признаком усечения, честный skip без отчёта/токена; ReleaseHardening — python-docx в `deploy/requirements.txt` (уже есть: `python-docx==1.2.0`) + изолированный `scripts/nedra-e2e.sh` до зелёного. Доказательство: тест NotifyReport с локальным HTTP-приёмником; реальный прогон e2e-скрипта (команды/статусы/длительность в отчёте); базовый живой прогон на dev-базе `fc34a52` (**LiveBaseE2E**): миграции → аккаунт → импорт ZIP → run → terminal → отчёты/карта/SSE |
| 3.2 | Собственные анализаторы (SECRET/SCA/SAST) без готовых сканеров и чужих правил; LLM не заменяет анализаторы | `backend/requirements.txt` — fastapi, croniter, tree-sitter, psycopg, python-docx; grep `semgrep\|gitleaks\|trivy\|detekt` по `backend/`, `analyzers/`, `frontend/src`, `db/` — пусто. Свои правила: `analyzers/cg26/`, `backend/app/analyzers/*`; `AI_SCAN` — только гипотезы с `countsAsFindings:false`, verdict/counts не меняют | **закрыто** | `python -m analyzers.selftest`; `docs/organizer-method-verification.md` — 15/15 против эталона организаторов; reference-checks: `GET /api/references`, `POST /api/compare`; происхождение правил — собственные (`analyzers/README.md`) |
| 3.3 | Интеграция LLM для объяснения; проверка структуры ответа, соответствия файлам/строкам и отсутствия выдуманных CVE | `ai/provider.py` (OpenCode Go default, generic `AI_PROVIDER`/`AI_BASE_URL`/`AI_MODEL`), `ai/service.py` (батчи, provenance, fallback), `ai/validator.py`: JSON-форма, `findingId` только из фактов, CVE только из `advisoryIds` находки, `suggestedFixedVersion` только из advisory этой находки; prompt содержит file/line, но свободный текст explanation/remediation на path/line не проверяется | **закрывается сейчас (AiEvidence)** | Дельта AiEvidence: упоминание несуществующего path/line → отклонение/degraded с причиной, provenance сохраняется. Доказательство: детерминированные тесты (валидная ссылка, выдуманная строка, текст без ссылок); живой вызов с provenance и `providerErrors=[]` — mentor-checklist §3.9. Побочно generic-провайдер и embeddings дорабатывает **AiProviderVector** (см. 4.2) |
| 3.4 | Интерфейс: drag-and-drop редактор и отображение статусов/результатов; React Flow разрешён, n8n/Node-RED — нет | React Flow (`@xyflow/react`) только как библиотека графа; своя логика: `backend/app/engine/{executor,validator}.py`, своя палитра/инспектор; grep `n8n\|node-red` по `frontend/src`, `backend/app` — пусто; статусы и результаты: `LiveRunView.tsx`, `FindingsView.tsx`, `ReportView.tsx` | **закрыто** | Mentor-checklist: живой UI-прогон (создание/связь/сохранение/reload, этапы, свои результаты, 390 px без переполнения, нет React Flow #008 и page errors); `npm run build`, `npm run lint` |
| 3.5 | Соблюдение ограничений по безопасности: сканирование только учебных/предоставленных репозиториев | Никакого фонового сканирования нет: анализ выполняется только по явному выбору артефакта (ZIP или публичный репозиторий, `backend/app/artifacts/repository.py` — только `github.com`/`gitlab.com`, sanitize путей); технического allowlist репозиториев организаторов в продукте нет | **принимаем границу** | Обоснование: ТЗ ограничивает деятельность команды во время кейса (сканируем `TBagirov/training` и свои учебные проекты — соблюдается процессно, `docs/JURY-WALKTHROUGH.md`: «учебные проекты организаторов или свои»), но не требует от продукта allowlist: платформа по смыслу работает с любым проектом, который пользователь явно выбрал. Отсутствие allowlist — не незакрытое обязательное требование (`requirements-current.md`). Доказательство соблюдения: единственный путь анализа — явный импорт с валидацией хоста; в репозитории и в прогонах — только учебные данные |

## 4. Технические требования — дополнительные

| # | Требование (коротко) | Фактическое состояние на `fc34a52` | Решение | Что доказывает закрытие |
| --- | --- | --- | --- | --- |
| 4.1 | Модульная расширяемость: новая нода с минимальными правками ядра | Штатные точки расширения: `NodeKind` (enum, 15 значений) + `NODE_REGISTRY` (`contracts/node_registry.py`) + правила `engine/validator.py` + ветки `engine/executor.py` + frontend-зеркала (палитра/инспектор/порты); внешние адаптеры: `config.engine=team_b` для scanner nodes, отдельные API-роутеры подключаются в `main.py`; `NodeKind.CUSTOM_CHECK` добавлен как пример | **закрыто** | Добавление custom-check и AI-блоков в mentor-волне не потребовало переписывания ядра; тесты контракта (`POST /api/workflows/{id}/validate`), независимый `docs/reviews/sol-structure.md` — PASS/FINAL |
| 4.2 | Закрытый контур: переключение внешней/локальной LLM настройкой без правки логики | `AI_PROVIDER=openai-compatible` + `AI_BASE_URL`/`AI_MODEL`/`AI_API_KEY` (документировано в `docs/ai-runtime.md`); общий resolver `ai/provider.py` используется explain/AI-scan/scenario builder; без обязательной привязки к ReformBoss на `fc34a52` уже не осталось. Embeddings/retrieval: по `PROJECT-DECISIONS.md` — `unavailable` (403 `token_model_not_allowed`), `embedding_specs` pending, fake vectors нет | **закрывается сейчас (AiProviderVector)** | Дельта: тесты generic-провайдера без `REFORMBOSS_API_KEY` (фейковый OpenAI-совместимый сервер, отсутствие ключа = bounded error), embeddings adapter с честным `disabled`. Доказательство: `pytest backend/tests` с `test_ai_*`/`test_vector_*`; живой Ollama/vLLM-прогон остаётся границей — переключение доказывается конфигурацией и тестом шлюза, а не реальной локальной моделью |
| 4.3 | Безопасное хранение настроек и секретов: шифрование/Vault, маскировка в интерфейсе, без открытого текста в логах | Шифрования/Vault нет: grep `fernet\|vault\|encrypt` по `backend/app`, `deploy/` — только комментарий intake про отказ от encrypted ZIP-entries. `secretToken` webhook хранится в конфиге ревизии открытым текстом. Маскировка есть: `backend/app/analyzers/redaction.py` (`cg-redaction/2`), `notifications/redact.py` (`mask_destination`), `security/redaction.py`; секреты в `~/.config/nedra/runtime.env` (mode 600), не в argv и не в логах | **принимаем границу** | Обоснование: требование — из дополнительных («плюс при оценке архитектуры»). Маскировка в интерфейсе и логах закрыта (это вторая половина пункта); шифрование at rest — честно не сделано: в БД/token storage открытый текст при том, что доступ к БД только серверный, а ключи живут в файле 600. Полноценный Vault требует отдельного слоя с управлением ключом — большая работа без владельца в этой волне; кандидат в следующую волну (раздел 8), не выдаётся за закрытое |
| 4.4 | История запусков и логирование: когда запускался, что найдено в прошлый раз, какой отчёт | Таблицы `runtime.runs/node_runs/findings/reports/run_events/deliveries` (`backend/app/storage/runtime_schema.sql`); `GET /api/profile/history` с серверными фильтрами project/workflow/status/since/until и пагинацией (`backend/app/api/profile.py:33-52`, `storage/profile.py:history_page`), UI-фильтры в `AccountModal.tsx`; второй короткий endpoint `GET /api/auth/history` поддерживает только `limit` (`backend/app/api/auth.py`) | **закрывается сейчас (HistoryFilters)** | Дельта: серверные фильтры и пагинация для auth-истории, корректная пустая история, переход к run, изоляция workspace. Доказательство: тесты `backend/tests/test_history*` (фильтры/пагинация, чужой workspace → отказ, пустая выборка); mentor-checklist §3.12 (история 1/0, чужие run/artifact → 404) |

## 5. Входные данные (обязательные элементы тестового набора)

| # | Требование (коротко) | Фактическое состояние на `fc34a52` | Решение | Что доказывает закрытие |
| --- | --- | --- | --- | --- |
| 5.1 | Файлы исходников с намеренно оставленными секретами | Учебные проекты в репозитории: `backend/app/demo/sample_bank_app.zip` (`certs/service-key.pem`, `application.properties`), `docs/reference-checks/fixtures/project-reference/src/.../OrderClient.kt` (`sk-live-...`), `application.yml` (`tok-live-...`); проект организаторов `TBagirov/training@fe1fcbe` проверен локально | **закрыто** | `docs/organizer-method-verification.md`: SECRET 5/5, 0 пропущенных, 0 лишних; reference-suite: `REF-SEC-003/004` с fingerprint |
| 5.2 | `build.gradle.kts` и конфиги Spring Boot с уязвимыми зависимостями | `sample_bank_app.zip`: spring-boot 2.5.0, log4j-core 2.14.1, jackson-databind 2.9.8, `gradle/libs.versions.toml`; в `TBagirov/training` — Gradle Kotlin DSL + `gradle.lockfile` (commons-text 1.9, jsoup 1.14.1 и др.) | **закрыто** | Организаторская сверка SCA: 5/5 по совпадению group/artifact/версии/advisoryId, включая версии из lockfile и property/локальные переменные |
| 5.3 | Фрагменты кода с логическими ошибками безопасности (для SAST) | `project-reference/.../OrderLookupController.kt` (SQL-конкатенация в `JdbcTemplate.query`), контроллеры в `sample_bank_app.zip`; в `TBagirov/training` — SAST-фрагменты | **закрыто** | Организаторская сверка SAST: 5/5; reference-suite `expectedFindings` по категориям |
| 5.4 | Справочник уязвимостей для SCA в JSON с нормализованными диапазонами версий | `backend/app/analyzers/advisories.json` — 45 записей, `vulnerable_ranges: ">= 2.9.0, < 2.9.10.4"`, `fixed_versions`, severity; `analyzers/fixtures/vulnerabilities.json` — снапшот OSV Maven (sha256 зафиксирован) | **закрыто** | `analyzers/selftest.py` идёт по этому справочнику; `ai/validator.py` берёт допустимые fixed versions только из него; организаторский справочник «передан как вход SCA» (organizer-method-verification) |
| 5.5 | Эталонные находки для открытого проекта | Организаторы: `ground-truth/training-findings.json` в `TBagirov/training`; наши: `docs/reference-checks/fixtures/reference-suite.json` (3 `expectedFindings` + 2 `expectedControls` с fingerprint/symbolKey) и `examples/demo/sql-before-after/raw-*.json` | **закрыто** | Сверка 15/15 против эталона организаторов; `POST /api/compare` сопоставляет опубликованный bundle с reference suite (`docs/reference-checks/README.md`) |
| 5.6 | Скрытая вариация для финальной проверки (те же классы проблем) | В репозитории отсутствует и не должна присутствовать; финальную проверку выполняют организаторы | **вне scope** | Точность на скрытой вариации не заявляется — это честная граница доказательства, зафиксирована в `PROJECT-DECISIONS.md` («что ещё не доказано»). Локально доказана только открытая вариация (5.1–5.5) |

## 6. Требования к презентации

| # | Требование (коротко) | Фактическое состояние на `fc34a52` (материалы/код) | Решение | Что доказывает закрытие |
| --- | --- | --- | --- | --- |
| 6.1 | Проблема, решение и достижение цели; целевой пользователь | `docs/mentor-defense.md` (раздел «Как мы поняли кейс»: разработчик без безопасника, три принципа), `docs/JURY-WALKTHROUGH.md` (путь пользователя 3–5 минут), `README.md`, `PROJECT-DECISIONS.md`; на ветке `SoloveyMaks/audit-all-data-1122` дополнительно `docs/presentation/creator-facts.md`, `guide-01-data.md` | **закрыто** | Материалы приняты в mentor-волне (`docs/mentor-session-2026-11-21.md`); это документы, а не проверка продукта |
| 6.2 | Демонстрация работающего прототипа на тестовом репозитории: конструктор → самописные сканеры → AI-рекомендации → оповещение | Механика прогона есть: `docs/JURY-WALKTHROUGH.md` (сценарий показа), живые прогоны mentor-checklist (run `a9bfe494…`, публичный API-путь импорт → запуск → отчёт в `docs/deployment-final.md`); изолированного воспроизводимого e2e нет (`scripts/nedra-e2e.sh` отсутствует) | **закрывается сейчас (ReleaseHardening, LiveBaseE2E)** | Доказательство: `scripts/nedra-e2e.sh` — реальный прогон до зелёного (миграции → аккаунт → импорт `examples/demo` ZIP → run → terminal → JSON/JSONC/Markdown/DOCX + карта, cleanup по trap); отчёт LiveBaseE2E по dev-базе `fc34a52` (HTTP-статусы `/runs`, `/nodes`, `/events`, `/security-map`, `/findings`, отчёты; стабильность повторного запуска и SSE-replay). Финальный показ через UI на репозитории организаторов — пункт 8.2 |
| 6.3 | Перечень реализованных нод и логика: алгоритмы SECRET/SCA/SAST, классификация, передача данных между нодами | `contracts/node_registry.py` — 15 типов (триггеры, три сканера, merge/branch, condition, policy gate, AI explain/scan, build report, notify, custom check); алгоритмы: `analyzers/{secret,sca,sast}_analyzer.py` + `version_comparator.py`; передача: типы портов `PortType` (trigger/artifact/scan_result/report/delivery_result) в реестре и валидаторе; отчёт по анализаторам — `docs/analyzers-report.md` | **закрыто** | `GET /api/node-types` отдаёт реестр; mentor-checklist §3.3 (свой блок отдельным этапом), §3.7 (порядок по severity); документированные алгоритмы в `docs/analyzers-report.md` и `analyzers/README.md` |
| 6.4 | Интеграция AI и проверка качества: какая модель, как передаются данные, структура ответа, соответствие файлов/строк, отсутствие выдуманных CVE, оценка полезности | Модель/канал: `ai/provider.py` (OpenCode Go `deepseek-v4.1-flash`, batching `AI_BATCH_SIZE`, provenance в `ai/service.py`); валидация: `ai/validator.py` (JSON shape, findingId из фактов, CVE только из advisoryIds, fixed version только из advisory); file/line в свободном тексте — дельта волны. Документация: `docs/ai-runtime.md`. «Оценка полезности» — материала и владельца нет | **закрывается сейчас (AiEvidence)** | Доказательство дельты: тесты AiEvidence (валидная ссылка проходит, выдуманная отклоняется, текст без ссылок не страдает); живой вызов с provenance — mentor-checklist §3.9 (6 из 8 файлов, `status=partial`, `providerErrors=[]`). Оценка полезности вынесена в раздел 8 — пункт без владельца |
| 6.5 | Архитектура решения; легкость добавления ноды, внешнего сервиса, переключения на локальную LLM | Описание: `PROJECT-DECISIONS.md`, `docs/data-contracts/`, `docs/interface-structure.md`, `docs/ai-runtime.md`; добавление ноды — см. 4.1; внешний сервис — роутер/адаптер (`main.py` include); локальная LLM — `AI_PROVIDER`/`AI_BASE_URL` (см. 4.2) | **закрыто** | Независимые reviews: `sol-structure.md` — PASS/FINAL, `sol-logic.md`, `sol-visual.md` — приняты (mentor-checklist); переключение LLM доказано конфигурацией и (в волне) тестом шлюза; живой прогон реальной локальной модели остаётся границей 4.2 |
| 6.6 | Реализованные звёздочки и планы развития | Сделано: AI-генерация сценария (2.3), push-webhook/опрос коммитов; в волне: библиотека шаблонов (2.1), CI-статус (2.2); планы: `docs/mentor-defense.md` — таблица статусов («приватный доступ — план», «CI-статус … План», «retrieval … следующий шаг») | **закрывается сейчас (WorkflowLibrary, CiStatus)** | Доказательство: тесты воркеров (publish→list→copy; push/PR + честный статус); раздел «Краткая таблица статусов» + «Честные границы» в `docs/mentor-defense.md` отделяет сделанное от планов |

## 7. Что важно (критерии оценки)

| # | Критерий | Решение | Основание |
| --- | --- | --- | --- |
| 7.1 | Качество нод и точность сканеров, включая скрытую вариацию | **принимаем границу** | На открытой вариации доказано: 15/15 против эталона организаторов, reference suite, selftest, ложные срабатывания в сверке не подтверждаются (0 лишних). Скрытую вариацию мы не видим — точность на ней не заявляется (5.6). Это пробел доказательства, а не отсутствие анализаторов |
| 7.2 | AI-анализ и практическая польза рекомендаций | **закрывается сейчас (AiEvidence)** | Корректность интеграции (структура/CVE/версии) закрыта, file/line — дельта AiEvidence; «практическая польза» (оценка полезности) без владельца — раздел 8 |
| 7.3 | Интеграции и доп. функционал: один канал оповещений на выбор + шаблоны/CI/AI-генерация | **закрывается сейчас (NotifyReport, TelegramRuns, WorkflowLibrary, CiStatus)** | Webhook-канал работает (1.2), его отчёт-дельта — NotifyReport; Telegram: база есть (`backend/app/telegram/`, миграции 010–012, 016), дельта — доставка по завершении run, порог severity, тихие часы, настройки по проекту/ветке (**TelegramRuns**, миграция 017); звёздочки — 2.1/2.2 |
| 7.4 | Техническое качество и архитектура, удобство UX/UI | **закрыто** | `sol-logic`/`sol-visual` приняты, `sol-structure` FINAL, `sol-migration` PASS (README); mentor-checklist: 390/1440, без page errors; `npm run build`/`lint`. Неблокирующие визуальные остатки (короткие edge labels и др.) честно перечислены в `PROJECT-DECISIONS.md` |
| 7.5 | Презентация: живая демонстрация и аргументированные решения | **закрывается сейчас (ReleaseHardening, LiveBaseE2E)** | Воспроизводимый e2e и живой прогон на базе `fc34a52` закрывают механику показа; аргументация решений уже зафиксирована в `PROJECT-DECISIONS.md` и `docs/mentor-defense.md` |

---

## 8. Доделать вне текущей волны

Обязательные пункты case.txt, которые открыты и у которых нет владельца
среди воркеров текущей волны. Это список для следующей волны.

1. **Оценка полезности AI-рекомендаций** (презентация 4: «оцените полезность
   рекомендаций»; критерий 7.2: «практическая польза»).
   Сейчас закрыта только корректность (структура/CVE/строки — AiEvidence),
   сама оценка никем не назначена.
   *Что доказывает закрытие:* задокументированная оценка на открытом проекте:
   сколько рекомендаций полезны/частично/нет, на каких примерах, с сохранёнными
   ответами модели; итог в защитных материалах.
2. **Живой UI-прогон демонстрации на репозитории организаторов
   `TBagirov/training` перед защитой** (презентация 2: «покажите работу
   прототипа на тестовом репозитории организаторов»).
   Механика прогона закрывается волной (6.2), но конкретный прогон через
   интерфейс именно на организаторском проекте никем не назначен:
   `POST /api/artifacts/sample/training` (admin, `CG_TRAINING_PROJECT`)
   загружает проект, сквозной путь через UI на нём не прогонялся.
   *Что доказывает закрытие:* чек-лист по `docs/JURY-WALKTHROUGH.md` на
   training-проекте — статусы этапов, findings, карта, отчёт, оповещение.

Кандидаты-дополнительные (не обязательные, тоже без владельца в волне):

- **Шифрование/Vault для настроек и секретов** (пункт 4.3) — если решать
  буквально: слой шифрования at rest с управлением ключом и маскирование
  `secretToken` в ревизии.
- **Технический allowlist области сканирования** (пункт 3.5) — только если
  захотим закрыть требование буквально; по тексту ТЗ он не требуется
  (решение — «принимаем границу»).

---

## 9. Сводка

| Решение | Количество | Пункты |
| --- | ---: | --- |
| закрыто | 16 | 1.1, 1.2, 1.3, 2.3, 3.2, 3.4, 4.1, 5.1–5.5, 6.1, 6.3, 6.5, 7.4 |
| в работе — закрывается сейчас | 14 | 1.4 (AiEvidence), 1.5 (LiveEdges, MapFiles), 2.1 (WorkflowLibrary), 2.2 (CiStatus), 3.1 (NotifyReport, ReleaseHardening), 3.3 (AiEvidence), 4.2 (AiProviderVector), 4.4 (HistoryFilters), 6.2 (ReleaseHardening, LiveBaseE2E), 6.4 (AiEvidence), 6.6 (WorkflowLibrary, CiStatus), 7.2 (AiEvidence), 7.3 (NotifyReport, TelegramRuns, WorkflowLibrary, CiStatus), 7.5 (ReleaseHardening, LiveBaseE2E) |
| принимаем границу | 3 | 3.5 (область сканирования — процессный контроль), 4.3 (Vault — маскировка закрыта, шифрование нет), 7.1 (точность на скрытой вариации не заявляется) |
| вне scope | 1 | 5.6 (скрытая вариация — проверяется организаторами) |
| **итого пунктов в разделах 1–7** | **34** | |
| вне текущей волны (обязательные без владельца) | 2 | 8.1 оценка полезности рекомендаций, 8.2 живой UI-прогон на `TBagirov/training` |
| кандидаты-дополнительные | 2 | Vault/шифрование, allowlist (не обязательные) |

Волна закрывает все открытые обязательные пункты, кроме двух из раздела 8.
Сильная подтверждённая часть: собственные анализаторы (15/15 против эталона
организаторов), конструктор, отчёт с экспортом, AI-объяснение с валидатором.
Ключевые живые доказательства на базе: run `a9bfe494…` (mentor-checklist) и
сверка `TBagirov/training@fe1fcbe` (organizer-method-verification); сквозной
изолированный e2e добавляют ReleaseHardening и LiveBaseE2E в этой волне.

Источники: `case.txt`; `PROJECT-DECISIONS.md`; `README.md`;
`docs/presentation/requirements-current.md` (ветка `SoloveyMaks/audit-all-data-1122`);
`docs/reviews/mentor-checklist-current.md`; `docs/reviews/full-audit-*.md`;
`docs/organizer-method-verification.md`; `docs/JURY-WALKTHROUGH.md`;
`docs/mentor-defense.md`; `docs/ai-runtime.md`; `docs/reference-checks/README.md`;
код `fc34a52`: `backend/app/contracts/node_registry.py`,
`backend/app/engine/{executor,validator,scheduler,sse}.py`,
`backend/app/analyzers/*`, `backend/app/ai/{provider,service,validator}.py`,
`backend/app/notifications/webhook.py`, `backend/app/api/{router,profile,repository_hooks,repository_watches,report_exports,scenario_builder}.py`,
`backend/app/storage/runtime_schema.sql`, `db/migrations` (001–013, 016),
`frontend/src/components/*`, `analyzers/*`, `examples/demo/*`.
