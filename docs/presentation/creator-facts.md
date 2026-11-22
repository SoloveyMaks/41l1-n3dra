# NEDRA — факты для создателя презентации

Дата фиксации: 22.11.2026. Документ собран по actual source combined-ветки, а не по
`TEAM-HANDOFF` и не по старым релизным отчётам. Всё, что ниже, имеет явный статус
доказательства; цифры из исторических прогонов не переносятся в текущие слайды.

Структура подстроена под организационный шаблон 16:9: **5 содержательных
слайдов** (название проекта и команды; задача; решение; что сделано + демо;
команда и роли), затем слайд 6 с логотипами партнёров и слайды 7–9 под
графические ассеты. Демонстрация — только в презентации, отдельной видеозаписи
или по QR-коду; демонстрация с устройств индивидуально членам жюри запрещена.

## 0. Зафиксированные ревизии

| Роль | Ревизия | Смысл |
| --- | --- | --- |
| combined (actual source) | `18fb5c85c4d94e8df0300d81445f0fbaada611ea` | рабочее дерево чистое; все факты ниже прочитаны здесь |
| data (интегрирована) | `9efe14e09ff56af694f468f2e765e72ce934b866` | runtime v6, migration 016, readiness-gate уведомлений |
| analysis (интегрирована) | `9d6dc91e3d8eecedf22d189c382d66432abdd2b7` | reference suite: 3/3 + 2/2, `inconclusive` |
| notifications (интегрирована) | `19ceb2cfa0e5f421abb5d65d1b6b50b18f7b55ac` | marker `legacy_active_consent_purge_v1` и fail-closed guard |
| telegram (интегрирована) | `a6ed0de42c781dbc84c949c787bca2ceac9b785b` | binding epochs, callbacks n2 |
| frontend polish | `e46e166774d8ce462f7561d3e7a93acf840da0df` | **не влит** в combined; в combined UI без этой полировки |
| main (custom checks) | `98c276e6585a89c417b007d6fd0411505df3be14` | источник `013_custom_checks` и sandbox |
| current production release | не подтверждён | публичные `bde900c`/`63ca8c1` из старых документов — история, не текущий combined |

## 1. Сначала источники, потом выводы

### 1.1. Исходный кейс

`case.txt` организаторов: **Cyber Garden 2026**, кейс банка **«Центр-инвест»**.
Нужен визуальный конструктор проверок безопасности: свои анализаторы секретов,
SCA и SAST для Kotlin/Spring Boot + Gradle, AI-объяснения, живой ход выполнения,
отчёты и уведомление. Прямо запрещены готовые сканеры/движки и чужие наборы
правил (Semgrep, Gitleaks, Trivy, detekt с security-правилами); LLM нельзя
использовать вместо собственных анализаторов. Разрешены React Flow, свои
regex/парсеры/Kotlin PSI/tree-sitter и библиотеки сравнения версий.

### 1.2. Actual source (combined `18fb5c8`)

- конструктор и клиент: `frontend/src/App.tsx`, `frontend/src/components/WorkflowCanvas.tsx`,
  `frontend/src/components/InspectorPanel.tsx`, `frontend/src/components/NodePalette.tsx`;
- контракты блоков: `backend/app/contracts/schemas.py`, `backend/app/contracts/node_registry.py`;
- исполнение: `backend/app/engine/executor.py`, `backend/app/engine/validator.py`,
  `backend/app/engine/sse.py`;
- анализаторы: `backend/app/analyzers/`, `analyzers/cg26/`,
  `backend/app/integrations/full_analysis.py`;
- факты кода и карта: `backend/app/codefacts/`, `ui/security-map/`;
- сравнение: `packages/comparison/index.mjs`, `backend/app/api/comparison.py`;
- отчёты и экспорт: `backend/app/reports/`, `backend/app/api/report_exports.py`;
- AI: `backend/app/ai/`, `backend/app/scenario_builder/`;
- данные: `db/migrations/` (13 обязательных файлов), `backend/app/storage/`;
- пользовательские проверки: `backend/app/custom_checks/` + `013_custom_checks`;
- импорт/наблюдение: `backend/app/artifacts/repository.py`, `backend/app/api/repository_*`;
- уведомления: `backend/app/notifications/`; Telegram/Mini App: `backend/app/telegram/`,
  `frontend/src/miniapp/`.

### 1.3. Какие отчёты что доказывают

| Источник | Класс | Что даёт | Чего не даёт |
| --- | --- | --- | --- |
| `docs/reference-checks/reports/compatible.json` (`9d6dc91`) | локальная проба | 3/3 находки, 2/2 контроля, `status=inconclusive` из-за недоступного SCA coverage; raw-fixture untrusted | не organizer training, не точность на скрытой вариации |
| `docs/reviews/full-audit-analysis.md` | локальная проба + чтение | reference suite, AI-валидатор, redaction, codefacts | нет server/DB/production |
| `docs/reviews/full-audit-data.md` | чтение + fake-conn пробы | миграции, runtime v6, readiness-gate, отчёты/redaction | SQL не исполнялся, PostgreSQL-gap |
| `docs/reviews/full-audit-engine.md` (`3f9bb10`) | локальные пробы | отмена run, SSE, ограниченная очередь | нет PG/HTTP e2e |
| `docs/reviews/full-audit-release.md` | локальные пробы | build/manifest/cleanup | нет image, PG, production |
| `docs/reviews/full-audit-accounts.md` | локальные пробы | cookie/ZIP/пути/изоляция кода | нет PG/tenant e2e |
| `docs/reviews/full-audit-telegram.md` (`a6ed0de`) | чтение + ASGI | epoch fencing, n2, отказ n1 | нет реального Telegram |
| `docs/reviews/full-audit-miniapi.md` | ASGI-пробы | admission, bearer v2, scoped settings | нет PG/Telegram |
| `docs/reviews/full-audit-miniui.md` | локальная сборка/браузер | mobile, сессии, отчёты | нет полного Telegram-пути |
| `docs/reviews/full-audit-notifications-independent.md` (`f2c0a62`) | независимое ревью | F1/F3/F8 найдены | live PG нет; F1/F3/F8 закрыты в combined |
| `docs/reviews/full-audit-frontend.md` (`e46e166`, вне combined) | статический harness | порты, validation state, layout | коммит **не влит**; browser E2E нет |
| `docs/mentor-checklist-current.md` | историческая live-проба | реальный disposable PG/Chromium, sandbox, отчёты, AI | другой SHA; **не текущая приёмка**, цифры не переносить |
| `docs/organizer-method-verification.md` | историческая проба | 15/15 на открытом training snapshot | **не текущее доказательство**, не гарантия скрытой вариации |
| публичный стенд (старые документы) | история | прежние выкладки `bde900c`/`63ca8c1` | не current combined, не production readiness |

### 1.4. README combined устарел — не цитировать статус оттуда

- README говорит «runtime schema v5»; фактически `RUNTIME_SCHEMA_VERSION = 6`
  (`backend/app/storage/database.py`), миграции 13 файлов, включая `016`.
- README говорит «custom-check/sandbox runtime отсутствует»; фактически в combined
  есть `backend/app/custom_checks/` (bwrap/prlimit/seccomp), нода `custom_check`,
  API `/api/custom-checks` и `013_custom_checks.up.sql`.
- `TEAM-HANDOFF.md` — исторический план; не источник текущего состояния.

## 2. Продукт в трёх предложениях (для жюри)

**Недра** — визуальная платформа проверок безопасности Kotlin/Spring Boot:
пользователь собирает сценарий из блоков, запускает его на ZIP или публичном
репозитории с закреплённым коммитом и получает находки с доказательствами,
карту доступа и отчёт. Проверки выполняют собственные анализаторы SECRET/SCA/
SAST, а не готовые сканеры; LLM объясняет уже найденные факты, её ответ
валидируется и не меняет вердикт. Система показывает не только результат, но и
границы проверки: неполное покрытие даёт «неполная проверка», синтетические
данные не выдают «исправлено», а неизвестные связи карты не маскируются.

## 3. Кейс: требования и запреты → ответ продукта → статус

| Требование кейса | Что сделано | Статус |
| --- | --- | --- |
| Drag-and-drop конструктор, свои блоки и логика | 15 типов блоков, порты, инспектор, ревизии сценария; React Flow (`@xyflow/react`) только как библиотека графа | source + историческая live UI-проба |
| Триггеры: кнопка, расписание; условия; уведомление | `manual_trigger`, `schedule_trigger`, `condition`/`branch_merge`, `notify` HTTP webhook | source |
| Свои SECRET/SCA/SAST без готовых сканеров и чужих правил | `SecretAnalyzer`/`ScaAnalyzer`/`SastAnalyzer` + собственный `analyzers/cg26`; в зависимостях нет Semgrep/Gitleaks/Trivy/detekt security | source + локальные пробы анализа |
| Классификация критичности | severity/confidence в контракте находки; policy gate отдельно от выполнения | source |
| LLM объясняет, но не ищет и не меняет вердикт | отдельный AI-слой: grounded-ответ, валидатор, fallback; AI-гипотезы — `advisory`, `countsAsFindings:false` | source + историческая live-проба |
| Живой ход выполнения и передача данных между блоками | SSE-события, статусы узлов, история run | source + локальная проба engine |
| Своя визуализация и прикладная логика, не n8n/Node-RED | собственный executor/validator и React-клиент | source |
| Только учебные/свои репозитории | продукт умеет публичные GitHub/GitLab; технического allowlist организатора нет — это процессное ограничение демо | **пробел контроля** |
| Секреты шифруются/Vault, маскируются | маскировка evidence/логов, HMAC секретов и webhook-секрет в БД как nonce; отдельного Vault/шифрования пользовательских секретов нет | частично |
| Переключение на локальную LLM | OpenAI-совместимый endpoint, `AI_BASE_URL`/`AI_MODEL`; локальный прогон не проверялся | source, live-пробел |

## 4. Демонстрация: запись или QR, без device-demo

Организаторы разрешают демонстрацию **только в презентации, отдельной
видеозаписи или по QR-коду**; демонстрация с устройств индивидуально членам жюри
**запрещена**. Поэтому в защите не планируется живой показ на ноутбуке/телефоне:
готовится короткая запись экрана (или встроенное видео/QR на слайде 4).

**Раскадровка записи 60–90 секунд** (только то, что реально работает на
записываемой сборке):

1. Редактор: сценарий «Банковский аудит» — триггер → проект → SECRET/SCA/SAST →
   policy → отчёт. Показать валидацию графа и ревизии.
2. Импорт: учебный ZIP организаторов или публичный репозиторий с закреплённым
   коммитом. Проговорить: проверка привязана к снимку и ревизии.
3. Запуск: статусы блоков; отдельно — «выполнение завершено» ≠ «всё безопасно».
4. Находка → доказательство: файл, диапазон, маскированный фрагмент; затем карта
   доступа с явно помеченными неизвестными связями.
5. AI: объяснение и статус валидации/fallback; гипотезы отдельно, counts/verdict
   не меняются.
6. Отчёт: официальный JSON/JSONC/Markdown/DOCX. Сравнение — честный
   `inconclusive`, если scopes не подтверждены; SQL-пример «до/после» отдельно.
7. Custom check в bwrap и/или push webhook — только если попадают в запись и
   реально работают; иначе не включать.

**Правила записи:** не выдавать историческую сборку за current release; если
записывается не текущий combined, подписать версию/дату в титрах. Никаких
выдуманных цифр, «15/15», «100%» и «production ready» в кадре и титрах. Если
живой стенд недоступен, использовать реальные скриншоты проверенной сборки с
подписью «историческая проверка, не current release».

## 5. Реализованные функции: карта для слайдов

| Функция | Owning source | Статус | Безопасная формулировка |
| --- | --- | --- | --- |
| 15 типов блоков | `contracts/schemas.py`, `contracts/node_registry.py` | И | «15 типов блоков: триггеры, проект, три сканера, логика, policy, AI, отчёт, webhook, custom check» |
| Ревизии сценария и валидация графа | `engine/validator.py`, `storage/database.py` | И + П | «Сохранение создаёт ревизию; несовместимый граф не запускается» |
| Ограниченная очередь и отмена run | `engine/executor.py` | И + П (engine audit) | «Очередь ограничена, отмена финализирует run/node/report» |
| Полный анализ: backend + Team B | `integrations/full_analysis.py` | И + П | «Два собственных движка объединяются по идентичности уязвимости, дубликаты схлопываются» |
| Частичное покрытие → `inconclusive` | `reports/`, `policy gate`, `codefacts` | И + П | «Неполная проверка не превращается в PASS» |
| Доказательства и redaction | `codefacts/canonical.py`, `storage/canonical.py` | И + П (data) | «Файл, диапазон, hash, маскированный фрагмент; сырой секрет не хранится» |
| Карта доступа и данных | `backend/app/codefacts`, `ui/security-map` | И + И-Р live | «Отдельный граф фактов кода; неизвестные связи видны» |
| Сравнение published envelopes | `packages/comparison` | И + П | «`resolved` только для двух scanner runs с подтверждёнными scopes» |
| SQL-паттерн до/после | `backend/app/api/sql_pattern.py` | И + И-Р | «Узкое доказательство исчезновения паттерна, не полное исправление» |
| Отчёты JSON/JSONC/MD/DOCX | `reports/`, `api/report_exports.py` | И + П + И-Р live | «Один состав находок в разных форматах» |
| AI-объяснение | `ai/service.py`, `ai/validator.py` | И + П + Л(ист.) | «Объясняет факты; CVE/версии только из advisory; fallback при сбое» |
| AI-гипотезы | `engine/executor.py` (`AI_SCAN`) | И + И-Р | «Advisory-слой, не меняет counts/verdict, не создаёт CVE» |
| Генерация сценария по описанию | `scenario_builder/service.py`, `ScenarioPromptModal.tsx` | И + И-Р | «LLM предлагает draft, сервер валидирует; без автозапуска и автосохранения» |
| Custom checks в sandbox | `custom_checks/`, `013_custom_checks` | И + И-Р live | «Свой Python-скрипт в bwrap без сети; результаты отдельно, verdict не меняют» |
| Импорт репозитория и pinned commit | `artifacts/repository.py`, `api/router.py` | И + И-Р live | «Публичный GitHub/GitLab; проверка привязана к точному коммиту» |
| Наблюдение за коммитами | `api/repository_watches.py` | И + И-Р | «Polling с интервалом; push webhook — ручная установка администратором» |
| Уведомления о завершении | `notifications/`, `storage/database.py` | И + П | «Webhook с настройками, retry и отменой; readiness-gate без DDL на каждом старте» |
| Аккаунты/workspace/история | `api/auth.py`, `storage/accounts.py` | И + П | «Регистрация/вход, история run в PostgreSQL, scoping по workspace» |
| Telegram/Mini App | `telegram/`, `frontend/src/miniapp/` | И + П (ASGI) | «Привязка с поколением, n2 callbacks, scoped чтения; внешний Telegram не проверялся» |
| Ответственная роль | `responsibility/` | И + П | «CODEOWNERS/история файла — подсказка, не обвинение» |

## 6. Архитектура, данные, AI boundary

### 6.1. Компоненты

- **Frontend**: React/Vite; редактор и результаты; отдельный admin shell;
  `ui/security-map` на React Flow; `frontend/src/miniapp` — Telegram Mini App.
- **Backend**: FastAPI; executor с ограниченной очередью; SSE; валидатор графа;
  API с workspace-scope.
- **Анализаторы**: backend SECRET/SCA/SAST + собственный модуль `analyzers/cg26`;
  объединение в `full_analysis`, детерминированный verdict через policy gate.
- **Факты и карта**: codefacts → canonical bundle → immutable publish → граф
  доступа с evidence.
- **Сравнение**: `packages/comparison` принимает только published envelopes;
  проверяет origin/signature/scopes.
- **Отчёты**: JSON/JSONC/Markdown/DOCX, AI-объяснение отдельным блоком.
- **Данные**: PostgreSQL: `data_contract` — канонические факты, `runtime` —
  API-состояние; ledger миграций; runtime schema v6 (13 обязательных миграций,
  включая `013_custom_checks` и `016`).
- **AI**: OpenAI-совместимый провайдер (`AI_BASE_URL`/`AI_MODEL`); ответ
  валидируется, при ошибке — детерминированный fallback; retrieval/embeddings
  по умолчанию выключены.

### 6.2. Поток одной проверки

1. ZIP/репозиторий → sanitize/intake → artifact + snapshot с SHA-256.
2. Сценарий + ревизия → запуск по явно выбранному артефакту.
3. Ноды-сканеры → findings + evidence + coverage; union backend/Team B.
4. Policy gate → verdict; canonical publish (immutable).
5. Карта/сравнение/отчёт; AI-объяснение отдельно; уведомление.

### 6.3. Данные и доверие

- Артефакт, снимок, ревизия и подпись анализа закреплены за run; новая загрузка
  не переписывает старую историю.
- Evidence строится по байтовым/строчным координатам исходного файла; сырые
  секреты не сохраняются — только HMAC и маскированный фрагмент.
- `AnalysisSignature` включает версии и хэши парсеров, правил, advisory-каталога
  и конфигурации; смена движка/каталога ломает сравнимость, а не «молча совпадает».
- Synthetic origin (`contract_scenario`, `manual_fixture`) не может дать
  `resolved`/`introduced`.
- Неподтверждённые scopes → `partial`/`inconclusive`; «0 проблем» не означает
  «безопасно».

### 6.4. AI boundary

| Может | Не может |
| --- | --- |
| объяснить уже найденные факты простым языком | искать уязвимости вместо анализаторов |
| предложить шаги исправления | изменить counts, policy или verdict |
| вернуть гипотезы отдельным advisory-слоем | выдумать CVE/advisory/версию вне каталога |
| быть заменена локальным OpenAI-совместимым endpoint | гарантировать качество без валидации |

Проверка: JSON-структура; `findingId` только из переданных фактов; CVE и
`suggestedFixedVersion` только из advisory этой находки; ответ и вход
маскируются; при провайдере/валидаторе — fallback и явный provenance.

## 7. Матрица: утверждение → owning source/ref → статус доказательства

Статусы: **И** — owning source прочитан в combined `18fb5c8`; **П** — локальная
выполненная проба без PG; **И-Р** — исторический отчёт/проба на другом SHA,
только контекст; **Л** — live-вывод на исторической выкладке/внешнем
провайдере, не current combined; **—** — доказательства нет.

| Утверждение | Owning source/ref | Статус |
| --- | --- | --- |
| Кейс: Cyber Garden 2026, банк «Центр-инвест» | `case.txt` организаторов | И (первичный документ кейса) |
| Визуальный конструктор блоков, портов, инспектора и ревизий | `frontend/src/App.tsx`, `components/WorkflowCanvas.tsx`, `InspectorPanel.tsx`; `engine/validator.py` | И + И-Р (UI-пробы прошлых сборок); **—** browser E2E combined |
| 15 типов блоков | `backend/app/contracts/schemas.py` (`NodeKind`), `node_registry.py` | И |
| Собственные SECRET/SCA/SAST без готовых сканеров | `backend/app/analyzers/`, `analyzers/cg26/`; зависимости без Semgrep/Gitleaks/Trivy/detekt | И + П (analysis audit) |
| Полный анализ backend+Team B, дедупликация, partial | `backend/app/integrations/full_analysis.py`, `docs/full-analysis.md` | И + П |
| Reference suite: 3/3 находки, 2/2 контроля, `inconclusive` | `docs/reference-checks/reports/compatible.json`, `9d6dc91` | П (raw-fixture, SCA coverage unavailable) |
| 15/15 на открытом training snapshot | `docs/organizer-method-verification.md` | И-Р (старый SHA); **не текущее доказательство** |
| AI-объяснение с валидацией и fallback | `backend/app/ai/service.py`, `validator.py`, `docs/ai-runtime.md` | И + П; Л — исторический provider-run |
| AI-гипотезы не меняют verdict и не создают CVE | `engine/executor.py` (`AI_SCAN`), `docs/ai-vulnerability-scanner-plan.md` | И + И-Р (live-проба старой сборки) |
| Генерация сценария по описанию с серверной валидацией | `scenario_builder/service.py`, `api/scenario_builder.py`, `ScenarioPromptModal.tsx` | И + И-Р |
| Карта доступа из фактов кода, неизвестные связи явны | `backend/app/codefacts/`, `ui/security-map/` | И + И-Р (live map старой сборки) |
| Сравнение: `resolved` только при сопоставимых scanner runs | `packages/comparison/index.mjs`, `docs/comparison-origin.md` | И + П (локальный probe) |
| Узкий SQL-паттерн до/после | `backend/app/api/sql_pattern.py` | И + И-Р |
| Отчёты JSON/JSONC/MD/DOCX с одним составом находок | `backend/app/reports/`, `api/report_exports.py` | И + П (data audit); И-Р live 200/DOCX |
| Evidence: файл/диапазон/hash, redaction секретов | `backend/app/codefacts/canonical.py`, `storage/` | И + П |
| Immutable artifact/snapshot/publish, история | `db/migrations/001–007`, `storage/canonical.py` | И + П (static); **—** live PG |
| PostgreSQL ledger и runtime v6 | `db/migrations/`, `backend/app/storage/database.py` | И + П (13 файлов, version=6); **—** SQL execution |
| Аккаунты/workspace/история | `api/auth.py`, `storage/accounts.py` | И + П (accounts audit); **—** PG/tenant e2e |
| Импорт публичных GitHub/GitLab с pinned commit | `backend/app/artifacts/repository.py`, `api/router.py` | И + И-Р live старого стенда; **—** current |
| Watch polling и push webhook (HMAC, dedupe) | `api/repository_watches.py`, `api/repository_hooks.py`, `docs/repository-import.md` | И + И-Р (temporary PG review на старом SHA); **—** current |
| Уведомления о завершении: settings/retry/cancel | `backend/app/notifications/`, `storage/database.py` | И + П (fake-conn/data audit); **—** PG |
| Custom checks в bwrap, отдельные от verdict | `backend/app/custom_checks/`, `013_custom_checks.up.sql` | И + И-Р live старой сборки; **—** current combined live |
| Telegram bot/Mini App: epochs, n2, scoped settings | `backend/app/telegram/`, `frontend/src/miniapp/` | И + П (ASGI); **—** реальный Telegram/WebView |
| Ответственная роль по CODEOWNERS/истории | `backend/app/responsibility/`, `docs/finding-responsibility.md` | И + П; **—** внешний GitHub/GitLab HTTP |
| Сборка с полным SHA и manifest | `scripts/`, `deploy/`; `full-audit-release.md` | П (локально); **—** image/PG/production |
| Переключение на локальную LLM | `backend/app/ai/provider.py` (`AI_BASE_URL`) | И; **—** живой локальный прогон |

## 8. Можно утверждать / нельзя утверждать

### Можно (коротко и честно)

- «Мы сделали визуальный конструктор из 15 типов блоков, а не обёртку над готовым
  сканером».
- «Наши анализаторы ищут секреты, уязвимые зависимости и типовые ошибки
  Kotlin/Spring; найденное подтверждается файлом, диапазоном и доказательством».
- «LLM объясняет факты и предлагает шаги; её ответ проверяется, при сбое остаётся
  детерминированный fallback».
- «AI-гипотезы и пользовательские проверки вынесены отдельно и не меняют
  вердикт».
- «Сравнение не выдаёт “исправлено”, если проверки несопоставимы; synthetic
  origin всегда даёт `inconclusive`».
- «История привязана к коммиту, артефакту и ревизии сценария».
- «Reference на синтетическом наборе: 3/3 находки и 2/2 контроля, итог
  `inconclusive` из-за недоступного SCA coverage».
- «Архитектура допускает добавление блока и переключение AI-endpoint настройкой;
  готовые workflow-платформы не используются».

### Нельзя

- «15/15», «100%», «полное покрытие», «все уязвимости найдены» — это либо
  исторический прогон на открытом снимке, либо отсутствие доказательств.
- «Ускорение в N раз», «production ready», «текущий релиз проверен» — нет
  текущего релизного/image/PG доказательства.
- «AI находит уязвимости» или «AI заменяет анализаторы» — прямо противоречит
  контракту и кейсу.
- «Сравнение доказало исправление» без двух scanner runs и подтверждённых scopes.
- «Секреты хранятся в Vault/шифруются» — реализованы маскировка, HMAC и
  server-side настройки, но не Vault.
- «README описывает текущее состояние» — README combined устарел (v5/sandbox).
- «Публичный стенд — это combined `18fb5c8`» — текущий release SHA не подтверждён.

### Ответы на вероятные вопросы

| Вопрос | Ответ |
| --- | --- |
| Почему не 100%? | Покрытие частичное по дизайну; неподтверждённые scopes дают `inconclusive`, а не ложный PASS. |
| А 15 из 15 было? | Было в исторической пробе на открытом training snapshot; текущий reference — 3/3 + 2/2 synthetic, `inconclusive` из-за SCA. Это не результат всего organizer training. |
| AI что делает? | Объясняет и предлагает remediation по уже найденным фактам; CVE/версии только из advisory; не меняет counts/verdict; есть fallback. |
| Чем отличается от сканера? | Процесс собирается блоками; результат объясним и воспроизводим; факты, покрытие и AI разделены. |
| Production? | Нет заявления; image/PG/preflight не выполнялись, текущий combined не выложен. |
| Секреты? | Сырые секреты не сохраняются; evidence маскируется, HMAC для сопоставления; Vault не реализован. |
| CI/CD? | Есть push webhook и polling-watch; возврата статуса в pipeline и генерации CI-конфига нет. |
| Шаблоны сценариев? | Публичной библиотеки сценариев нет; есть заводской шаблон и именованные копии. Custom-check templates — отдельная функция. |

## 9. Слайд-план ровно под организационный шаблон

Шаблон: 16:9, 5 содержательных слайдов + слайд 6 с логотипами партнёров и
слайды 7–9 под графические ассеты. Не обещаем 8 содержательных слайдов и не
добавляем свои разделы вместо структуры организаторов.

### Слайд 1 — «Название проекта / название команды»

- Заголовок: **Недра**.
- Подзаголовок: «Визуальный конструктор проверок безопасности Kotlin/Spring Boot».
- Команда: `[название команды — заполнить]`; при необходимости `[город/вуз — заполнить]`.
- Одна строка: «Свои анализаторы + доказательства + AI, который не меняет вердикт».
- Доказательная подпись: только source; цифр на слайде нет.

### Слайд 2 — «Задача / как мы поняли кейс»

- Пользователь: небольшая команда без отдельного безопасника.
- Проблема: секреты, устаревшие зависимости и ошибки доступа дешевле найти до
  релиза; корпоративные сканеры тяжелы для маленькой команды.
- Как поняли кейс (3 пункта):
  1. нужен настраиваемый **процесс из блоков**, а не ещё один чат-бот;
  2. проверки — **свои** SECRET/SCA/SAST, без готовых сканеров и чужих правил;
  3. результат — **объяснимый**: доказательство, покрытие и AI не смешиваются.
- Ограничение кейса: сканировать только учебные/свои репозитории.
- Доказательная подпись: `case.txt` организаторов.

### Слайд 3 — «Решение / как подошли»

- Конструктор: 15 типов блоков, порты/инспектор, ревизии сценария.
- Запуск: ZIP или публичный репозиторий с закреплённым коммитом; ограниченная
  очередь, отмена, живой ход.
- Проверки: backend SECRET/SCA/SAST + собственный модуль `analyzers/cg26`;
  объединение находок, policy и честное `inconclusive` при неполном покрытии.
- Доверие: evidence с файлом/диапазоном/hash, маскировка секретов, неизменяемые
  снимки/публикации, подпись анализа.
- AI boundary: объясняет факты, валидируется, CVE/версии только из advisory,
  fallback; AI-гипотезы и custom checks — отдельно, verdict не меняют.
- Мини-схема: `Редактор → executor → свои анализаторы → findings + evidence →
  карта/отчёт`; `PostgreSQL` — история; `AI` — отдельный слой.
- Доказательная подпись: source + локальные пробы; без заявления PG-приёмки.

### Слайд 4 — «Что сделано на хакатоне + демонстрация»

- Сделано: конструктор и 15 блоков, свои анализаторы и union движков,
  evidence/redaction, карта доступа, сравнение, отчёты JSON/JSONC/Markdown/DOCX,
  AI-объяснение и гипотезы, custom checks в bwrap, импорт/watch/push webhook,
  аккаунты/история, уведомления, Telegram Mini App.
- Демонстрация: **видео или QR-код** (см. раздел 4). Демонстрация с устройств
  индивидуально членам жюри запрещена — в плане защиты её нет.
- Честная строка: «Reference на синтетическом наборе — 3/3 находки и 2/2
  контроля, итог `inconclusive` из-за недоступного SCA coverage; полное
  покрытие и production readiness не заявляются».
- Доказательная подпись: source + локальные пробы + reference; без 15/15, 100%,
  ускорения и production ready.

### Слайд 5 — «Команда / роли»

Команда и ФИО пока неизвестны — только placeholders, без выдумывания:

| Роль | Кто | Что закрывает |
| --- | --- | --- |
| Лидер/продукт | `[ФИО — заполнить]` | кейс, сценарий демо, защита |
| Backend/engine | `[ФИО — заполнить]` | API, executor, данные, миграции |
| Анализаторы/данные | `[ФИО — заполнить]` | SECRET/SCA/SAST, evidence, reference |
| Frontend/UX | `[ФИО — заполнить]` | редактор, карта, Mini App |
| AI/QA/release | `[ФИО — заполнить]` | AI-валидатор, проверки, сборка/стенд |

Если ролей меньше — объединить ячейки, но не придумывать людей и звания.

### Слайды 6–9 — партнёрские логотипы и графические ассеты

- Слайд 6: логотипы партнёров из шаблона организаторов (не заменять и не
  придумывать свои).
- Слайды 7–9: графические ассеты шаблона; использовать для скриншотов/схемы, не
  превращать в дополнительные текстовые разделы.
- Если нужен QR на демо — размещать на слайде 4 или на одном из ассет-слайдов,
  не добавляя новую содержательную структуру.

**Проверка перед сдачей:** 5 содержательных слайдов соответствуют шаблону; ни на
одном слайде нет 15/15, 100%, ускорения, production ready, «полного покрытия» и
«все уязвимости найдены»; ФИО и название команды заполнены реальными данными,
а не placeholder’ами.

---

## Внутренний appendix (не для слайдов)

### A. Точные ревизии и проверки

- Структура слайдов: организационный шаблон `Primernaya_struktura_prezentatsii_2026.pptx`
  (16:9, 9 слайдов: 5 содержательных + логотипы партнёров + 3 ассет-слайда).
  Демо по шаблону — только в презентации/видео/QR; device-demo запрещён.
- combined: `18fb5c85c4d94e8df0300d81445f0fbaada611ea`, ветка
  `SoloveyMaks/audit-combined-1122`, worktree чистый на момент чтения.
- В combined входят: data `9efe14e0…` (readiness-gate `cbfc878`),
  notifications `19ceb2c…`, analysis `9d6dc91…`, telegram `a6ed0de…`.
- frontend `e46e166774d8ce462f7561d3e7a93acf840da0df` — **не предок** combined;
  его отчёт `docs/reviews/full-audit-frontend.md` описывает статический harness,
  не browser E2E.
- Reference report: `status=inconclusive`, `matchedFindings=3`,
  `matchedControls=2`, `coverageReasons=["SCA coverage is unavailable"]`,
  `provenance.verified=false`, `provenance.mode=raw-fixture`.
- README combined: строки про «runtime schema v5» и «custom-check/sandbox
  отсутствует» неверны для `18fb5c8`: `RUNTIME_SCHEMA_VERSION=6`, 13 миграций,
  `backend/app/custom_checks/` и `013_custom_checks.up.sql` присутствуют.
- Live/current release: в старых документах фигурируют `bde900c`/`63ca8c1`;
  текущий combined SHA на стенде не подтверждён. Не выдавать старые live-факты
  за текущий release.

### B. Что не проверено (не превращать в зелёный статус)

- PostgreSQL execution: миграции, ledger, immutability-триггеры, RLS/scoping,
  upgrade v5→v6, notification guard, custom-check schema.
- Образ/контейнер/uid10001/rootfs/volume/systemd, backup/restore, cutover.
- Browser E2E combined: auth/cookie, SSE, импорт репозитория, экспорт DOCX,
  админ-маршруты, мобильная вёрстка финальной сборки.
- Реальный GitHub/GitLab import/webhook, реальный Telegram transport/WebView,
  реальный HTTP-получатель уведомлений; SMTP-канал не реализован (только
  значение `channel` в контракте).
- Живой локальный LLM endpoint (Ollama/vLLM) и embeddings/retrieval.
- Сравнение на скрытой вариации organizer training; текущий reference —
  synthetic raw-fixture, не organizer training.
- Точность/полнота анализаторов на произвольных проектах: historical 15/15
  относится только к открытому snapshot и не переносится.
