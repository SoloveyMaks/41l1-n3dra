# Матрица требований case.txt: NEDRA против combined `18fb5c8`

Дата фиксации: 22.11.2026. Источник требований — `case.txt` организаторов Cyber
Garden 2026 (кейс банка «Центр-инвест»). Источник состояния — actual source
combined `18fb5c85c4d94e8df0300d81445f0fbaada611ea`; frontend-полировка
`e46e166774d8ce462f7561d3e7a93acf840da0df` проверялась отдельно и **не влита** в
combined. README combined устарел (runtime v5 и «sandbox отсутствует» неверны):
статусы ниже основаны на owning source и поиске владельцев, а не на README.

## Как читать статусы

- **Реализовано** — owning source содержит полную функцию; текущая сборка не
  обязана быть развёрнута, чтобы считать функцию существующей.
- **Реализовано, current E2E не подтверждено** — функция есть в исходниках, но
  полный путь текущего combined (UI → API → PostgreSQL → отчёт/уведомление) не
  прогонялся в этой проверке. Это не отсутствие функции.
- **Частично** — реализована только часть требования или есть существенный
  разрыв в контракте/покрытии.
- **Не реализовано** — поиск по owning source, API-маршрутам, миграциям и
  frontend не нашёл реализации; перечислено, что именно найдено вместо неё.

Границы доказательств этой проверки: полная новая сборка с PostgreSQL и
внешними сервисами не проходила; sandbox actual runtime не запускался; frontend
`e46e166` не merged/deployed; публичный deployment SHA не проверен. Поэтому
формулировка «не подтверждено» относится к интеграционной приёмке, а не к
наличию кода. Текущий provider ReformBoss переводится другим работником на Go;
это незавершённая текущая правка, не результат и не доказательство.

## 1. Обязательные требования (минимальные)

| Требование case.txt | Owning source / поиск | Статус | Что доказано и где граница |
| --- | --- | --- | --- |
| Сквозной работающий прототип: принять проект → выполнить workflow → отправить уведомление с отчётом | `backend/app/artifacts/`, `backend/app/engine/executor.py`, `backend/app/notifications/webhook.py`, `backend/app/reports/` | **Частично** | Все звенья есть в source, но уведомление отправляет summary, а не отчёт; единый E2E текущего combined не прогонялся. Это не отсутствие прототипа, а разрыв контракта и приёмки |
| Принимает файлы или ссылку на учебный Kotlin/Spring Boot Gradle-репозиторий | `backend/app/artifacts/`, `backend/app/artifacts/repository.py`, `backend/app/api/router.py` | **Реализовано, current E2E не подтверждено** | ZIP и публичные GitHub/GitLab, pinned commit, sanitize/paths; текущий HTTP+DB путь не прогонялся |
| Выполняет собранный workflow | `backend/app/engine/executor.py`, `backend/app/engine/validator.py` | **Реализовано, current E2E не подтверждено** | executor, ограниченная очередь, SSE, отмена; полный PG/UI run текущего combined не подтверждён |
| Логика if/else, сравнение и регулярные выражения | `backend/app/engine/executor.py` (`CONDITION`), `backend/app/engine/validator.py`, `backend/app/contracts/node_registry.py` | **Реализовано** | Операторы `equals`, `not_equals`, `greater_than`, `less_than`, `regex`; неизвестный оператор отклоняется валидатором. Это source-доказательство, отдельный live-прогон условий не заявляется |
| Отправляет итоговое уведомление с отчётом о потенциальных уязвимостях | `backend/app/engine/executor.py` (NOTIFY), `backend/app/notifications/webhook.py`, `backend/app/api/report_exports.py` | **Частично** | Webhook отправляет summary (runId, verdict, counts), а не отчёт с findings: findings/report доступны отдельно через report exports, ссылка в payload не вкладывается. SMTP нет; Telegram — отдельный канал, не выбор внутри Notify |
| Отчёт с найденными проблемами и рекомендациями | `backend/app/reports/`, `backend/app/api/report_exports.py` | **Реализовано, current E2E не подтверждено** | JSON/JSONC/Markdown/DOCX и AI-секция есть; живой HTTP-экспорт с текущей БД не проверялся |
| Свои анализаторы SECRET (regex/эвристики) | `backend/app/analyzers/secret_analyzer.py` | **Реализовано** | Регулярные выражения, маскирование, severity; без готовых сканеров |
| Свои SCA с версиями и advisory | `backend/app/analyzers/sca_analyzer.py`, `version_comparator.py`, `advisory_catalog.py` | **Реализовано** | Разбор Gradle/Kotlin DSL, version catalog, сравнение диапазонов, advisory id |
| Свои SAST для Kotlin/Spring Boot | `backend/app/analyzers/sast_analyzer.py`, `analyzers/cg26/` | **Реализовано** | tree-sitter Kotlin + собственные эвристики; не готовый detekt/правила |
| Классификация критичности | `backend/app/contracts/schemas.py`, `backend/app/engine/validator.py`, policy gate | **Реализовано** | severity/confidence в контракте, пороги policy; не заявляется точность на скрытой вариации |
| Запрет готовых сканеров и чужих наборов правил | `backend/requirements.txt`, `deploy/requirements.txt`, `backend/app/analyzers/`, `analyzers/cg26/` | **Реализовано** | В зависимостях и коде нет Semgrep/Gitleaks/Trivy/detekt; происхождение собственных правил независимо не заверялось |
| LLM не заменяет анализаторы | `backend/app/engine/executor.py` (`AI_SCAN`), `backend/app/reports/builder.py` | **Реализовано** | AI-гипотезы advisory, `countsAsFindings:false`, counts/verdict не меняют |
| Интеграция LLM для объяснения/обобщения | `backend/app/ai/provider.py`, `backend/app/ai/service.py` | **Реализовано, current E2E не подтверждено** | OpenAI-совместимый `/chat/completions`, batching, provenance, fallback; текущий provider переводится на Go |
| Проверка структуры ответа, отсутствие выдуманных CVE/версий | `backend/app/ai/validator.py`, `backend/app/ai/service.py` | **Реализовано** | JSON-форма, findingId, CVE только из advisory, fixed version только из advisory; duplicate/missing отклоняются |
| Соответствие ссылок на файлы и строки фактическим находкам | `backend/app/ai/validator.py`, `backend/app/ai/service.py` | **Частично** | findingId и advisory проверяются; prompt содержит file/line, но свободный текст explanation на соответствие path/line не валидируется |
| Drag-and-drop конструктор и свои блоки | `frontend/src/App.tsx`, `components/WorkflowCanvas.tsx`, `InspectorPanel.tsx`, `backend/app/contracts/node_registry.py` | **Реализовано, current E2E не подтверждено** | 15 типов блоков, порты, ревизии; frontend `e46e166` не в combined |
| Живое отображение хода и данных | `backend/app/engine/sse.py`, `frontend/src/components/LiveRunView.tsx`, `run_events` | **Реализовано, current E2E не подтверждено** | SSE-события и статусы узлов; текущий browser/API путь не прогонялся |
| Своя логика и UI, без n8n/Node-RED | `backend/app/engine/`, `frontend/src/components/NodePalette.tsx` | **Реализовано** | Собственный executor/validator; React Flow только библиотека графа |
| Сканирование только учебных/своих репозиториев | `backend/app/artifacts/repository.py`; выбранный authorized training/demo scope | **Реализовано** | ТЗ ограничивает область применения и демо, а не требует технического allowlist в продукте. Продукт принимает публичные GitHub/GitLab и ZIP; соблюдение обеспечивается выбранным учебным/демо-scope и процессным контролем. Отсутствие allowlist — не незакрытое обязательное требование |

## 2. Дополнительные технические требования

| Требование case.txt | Owning source / поиск | Статус | Что доказано и где граница |
| --- | --- | --- | --- |
| Модульная расширяемая архитектура: новая нода с минимальными правками | `contracts/schemas.py`, `contracts/node_registry.py`, `engine/validator.py`, `engine/executor.py`, frontend mirrors | **Реализовано** | Штатные точки расширения: `NodeKind` + `NODE_REGISTRY`, правила валидатора, ветка executor и frontend-зеркало портов/палитры/инспектора; новый анализатор/канал/внешний сервис добавляется через эти слои без переписывания ядра. Правки в нескольких слоях — нормальная цена нового типа, а не доказательство несоответствия; plugin API ТЗ не требует |
| Поддержка закрытого контура: переключение внешней/локальной LLM настройкой | `backend/app/ai/provider.py` (`AI_BASE_URL`, `AI_MODEL`), `backend/app/scenario_builder/service.py` | **Частично** | base URL и модель меняются env; resolver всё равно требует `REFORMBOSS_API_KEY` и помечает provider как `reformboss`; локальный Ollama/vLLM runtime не проверялся |
| Шифрование/Vault для настроек и секретов; маскирование в UI/логах | поиск по `backend/app`, `deploy/`: нет vault/encrypt/kms/fernet | **Не реализовано** | Vault/шифрования нет; токены и `secretToken` лежат в конфигурации/revision без шифрования. Маскировка evidence и HMAC — отдельный слой, он не заменяет encryption at rest |
| История запусков и логирование выполнения | `backend/app/storage/runtime_schema.sql` (`runs`, `node_runs`, `run_events`, `reports`), `backend/app/reports/` | **Реализовано, current E2E не подтверждено** | История, статусы и отчёты предусмотрены; живая PostgreSQL-проверка текущего combined не выполнялась |
| Маскирование секретов в интерфейсе и логах | `backend/app/analyzers/redaction.py`, `backend/app/codefacts/source.py`, `backend/app/notifications/redact.py` | **Реализовано** | `cg-redaction/2`, redacted evidence, маскирование URL/секретов; это не шифрование хранилища |

## 3. Задачи со звёздочкой

| Требование case.txt | Owning source / поиск | Статус | Что доказано и где граница |
| --- | --- | --- | --- |
| Публичная библиотека workflow-шаблонов: публикация, просмотр, копирование, редактирование | поиск по `backend/app/api`, `db/migrations`, `frontend/src` | **Не реализовано** | Нет endpoint/таблицы/UI публикации и копирования workflow. Есть встроенный заводской шаблон (`handleResetToTemplate`) и именованные копии, но это не общая библиотека. `/api/custom-checks/templates` — шаблоны Python-проверок, не workflow |
| CI/CD: генерация конфигурации или запуск из pipeline через HTTP/webhook | `backend/app/api/repository_hooks.py`, `docs/repository-import.md` | **Частично** | Push webhook с HMAC и ручной установкой есть; генератора CI-конфига нет |
| Автостарт на новый коммит или Pull/Merge Request | `backend/app/api/repository_hooks.py`, `api/repository_watches.py` | **Частично** | Polling-watch и push запускают проверку; hook принимает только push, PR/MR события игнорируются |
| Возврат статуса в pipeline и падение сборки при critical | поиск `check_run`, `commit_status`, `statuses`, CI API | **Не реализовано** | Обратного статуса и CI-гейта нет; verdict остаётся внутри приложения |
| AI-генерация сценария по текстовому описанию | `backend/app/scenario_builder/`, `backend/app/api/scenario_builder.py`, `frontend/src/components/ScenarioPromptModal.tsx` | **Реализовано, current E2E не подтверждено** | Закрытый intent-каталог, LLM-план, серверная валидация и preview; custom-скрипты не генерируются; текущий provider-переход не проверен |

## 4. Требования к презентации

| Требование case.txt | Owning source / материалы | Статус | Что доказано и где граница |
| --- | --- | --- | --- |
| Проблема, решение и достижение цели | `case.txt`, `docs/presentation/creator-facts.md`, `docs/presentation/guide-01-data.md` | **Реализовано** | Материалы и формулировки есть; это не проверка продукта |
| Работающий прототип и демонстрация на тестовом репозитории | `backend/app/engine/`, `api/`, `artifacts/`; формат демо — запись/QR | **Реализовано, current E2E не подтверждено** | Код пути есть; текущий combined не прогонялся, запись демо не создана |
| Перечень реализованных нод и логика проверки | `contracts/node_registry.py`, `analyzers/`, `engine/executor.py` | **Реализовано** | 15 типов, SECRET/SCA/SAST, передача данных и policy |
| AI-интеграция и качество рекомендаций | `ai/service.py`, `ai/validator.py`, `ai/provider.py` | **Частично** | Структура/CVE/версии проверяются; file/line в свободном тексте не валидируются; текущий provider-переход не завершён |
| Архитектура и расширяемость | `docs/presentation/guide-01-data.md`, `contracts/`, `storage/` | **Реализовано** | Компоненты и данные описаны; локальная LLM — частично (см. выше) |
| Задачи со звёздочкой и планы развития | этот документ, `docs/presentation/creator-facts.md` | **Частично** | AI-план и push webhook есть; библиотека шаблонов и CI-статус отсутствуют |
| Качество нод на скрытой вариации | analyzers + reference suite | **Реализовано, current E2E не подтверждено** | Организаторские 15 — эталонный fixture, не результат нашего сканера; текущий reference 3/3 + 2/2 synthetic, `inconclusive` из-за SCA. Точность на скрытой вариации не заявляется (это пробел доказательства, не отсутствие анализаторов) |
| Один канал уведомлений на выбор | `engine/executor.py` (NOTIFY), `notifications/webhook.py` | **Реализовано** | Выбран и реализован HTTP webhook; SMTP отсутствует, Telegram — отдельный канал, не выбор внутри Notify. Это соответствует «одному каналу на выбор», но не трём сразу |

## Итог

Полного соответствия «всё ТЗ реализовано» нет. Сильная часть — собственные
анализаторы, доказательства, AI-объяснение с валидатором, конструктор, карта,
сравнение и отчёты; это подтверждено owning source и локальными пробами, но не
единым E2E текущего combined. Незакрытые обязательные пункты: привязка отчёта к
уведомлению (webhook отправляет summary, а не findings/report) и валидация
file/line в свободном тексте AI. Ограничение области применения соблюдается
выбранным authorized training/demo scope; технический allowlist ТЗ не требует,
поэтому его отсутствие не является незакрытым пунктом. Из дополнительных не
закрыто шифрование/Vault; локальная LLM — частично. Модульность реализована
через контракты/реестр/валидатор/executor/frontend-зеркало; plugin API не
требуется. Незакрытые звёздочки: публичная библиотека workflow-шаблонов,
генерация CI-конфига и возврат статуса в pipeline, PR/MR автостарт. `e46e166`
не merged, sandbox и PG не запускались, публичный SHA не проверен. Текущий
перевод provider ReformBoss на Go — незавершённая правка, не результат.

Источники: case.txt; backend/app/artifacts/repository.py; backend/app/api/repository_hooks.py; backend/app/api/repository_watches.py; backend/app/engine/executor.py; backend/app/engine/validator.py; backend/app/notifications/webhook.py; backend/app/ai/provider.py; backend/app/ai/validator.py; backend/app/scenario_builder/service.py; backend/app/analyzers/secret_analyzer.py; backend/app/analyzers/sca_analyzer.py; backend/app/analyzers/sast_analyzer.py; backend/app/storage/runtime_schema.sql; db/migrations/013_custom_checks.up.sql; docs/repository-import.md; docs/presentation/guide-01-data.md
