# Анализаторы (пакет B)

Собственные SECRET, SCA и SAST анализаторы для Kotlin + Spring Boot + Gradle.
Без готовых сканеров, без сторонних наборов security-правил и без LLM.

Пакет читает статический снимок учебного проекта и advisory input. Ground truth
не является входом анализатора: он читается только отдельным скриптом сверки.

## Вход

* корень проекта, обычно `<snapshot>/training-project`;
* `vulnerability-database/vulnerabilities.json` из того же снимка;
* опционально `ground-truth/training-findings.json` — только для `compare`.

Учебный источник зафиксирован в
[TBagirov/training@fe1fcbe](https://github.com/TBagirov/training/tree/fe1fcbe212669783480666b9b60147c8a8c6eef4).
Снимок в этот репозиторий не входит.

## Запуск

```powershell
python -m analyzers.scan <snapshot>/training-project `
  --db <snapshot>/vulnerability-database/vulnerabilities.json `
  --out analyzers/examples/submission.json `
  --full artifacts/scan-full.json

python -m analyzers.compare analyzers/examples/submission.json `
  --truth <snapshot>/ground-truth/training-findings.json `
  --out analyzers/examples/comparison.json

python -m analyzers.contract_export <snapshot>/training-project `
  --db <snapshot>/vulnerability-database/vulnerabilities.json `
  --out analyzers/examples/bundle.json `
  --rag-store .rag/store
node analyzers/validate_bundle.mjs analyzers/examples/bundle.json <snapshot>/training-project

python -m analyzers.selftest
```

Нужен Python 3.11+ и стандартная библиотека. Внешние зависимости не требуются.
`artifacts/` в репозитории игнорируется; в `analyzers/examples/` лежат
минимальные примеры выгрузки и сверки.

`scan` пишет:

* `submission.json` — обязательный формат из `docs/submission-format.md`;
* `scan-full.json` — доказательства, диагностика, покрытие, правила и граф.

`contract_export` пишет `AnalysisBundle` из `docs/data-contracts` — ту же
находку с координатами, покрытием, подписями и графом для пакетов A/C/D.
`validate_bundle.mjs` проверяет выгрузку их же чекером: ссылки, диапазоны
строк/колонок/bytes и `sourceHash` по исходным байтам.

## Структура

| Файл | Назначение |
| --- | --- |
| `analyzers/scan.py` | единая точка запуска, сбор `ScanResult` |
| `analyzers/compare.py` | сверка с эталоном, таблица correct/missed/false-positive |
| `analyzers/selftest.py` | фиксированные сценарии проверки |
| `analyzers/fixtures/vulnerabilities.json` | минимальная выборка advisory для self-test (production берёт полный вход снимка) |
| `analyzers/examples/` | примеры выгрузки и сверки на учебном снимке |
| `analyzers/cg26/model.py` | общая модель `Finding`, `Diagnostic`, `Coverage`, `ScanResult` |
| `analyzers/cg26/textfiles.py` | безопасный обход файлов: без symlink, бинарных и больших файлов |
| `analyzers/cg26/kotlin_lexer.py` | лексер Kotlin/Kotlin DSL с offset, строками, интерполяциями и комментариями |
| `analyzers/cg26/kotlin_index.py` | объявления, параметры, вызовы, маршруты, порядок security-правил |
| `analyzers/cg26/project.py` | индекс проекта и свёртка строковых выражений |
| `analyzers/cg26/yaml_lite.py` | разбор YAML/properties с номерами строк |
| `analyzers/cg26/versions.py` | числовое сравнение версий и границ advisory |
| `analyzers/cg26/secrets.py` | пять классов SECRET |
| `analyzers/cg26/gradle.py` | извлечение зависимостей Gradle Kotlin DSL и lock-файла |
| `analyzers/cg26/sca.py` | сопоставление с `vulnerabilities.json` |
| `analyzers/cg26/policy.py` | явная политика владения объектом |
| `analyzers/cg26/sast/` | пять классов SAST |
| `analyzers/cg26/graph.py` | граф маршрутов, правил, обработчиков, источников и sink |
| `analyzers/cg26/contract.py` | выгрузка `AnalysisBundle`: UUID, координаты, coverage, подписи, `fp/v1` |
| `analyzers/contract_export.py` | CLI выгрузки контракта |
| `analyzers/validate_bundle.mjs` | прогон чекера `docs/data-contracts/validate-bundle.mjs` по выгрузке |

## Правила

| ID | Категория | Класс | Что доказывается |
| --- | --- | --- | --- |
| `CG26-SEC-001` | SECRET | `password` | литерал в параметре с ролью пароля |
| `CG26-SEC-002` | SECRET | `api-key` | литерал в параметре с ролью ключа/токена |
| `CG26-SEC-003` | SECRET | `configuration-token` | литерал токена в YAML/properties |
| `CG26-SEC-004` | SECRET | `secret-fallback` | литерал справа от `?:` после чтения окружения |
| `CG26-SEC-005` | SECRET | `recoverable-token` | credential, свёрнутый из нескольких локальных литералов, и поток до заголовка |
| `CG26-SCA-001` | SCA | `known-vulnerable-dependency` | объявленная версия (или lock-версия) в числовом диапазоне advisory |
| `CG26-SAST-001` | SAST | `sql-injection` | параметр запроса в интерполяции/конкатенации SQL-аргумента JDBC |
| `CG26-SAST-002` | SAST | `tls-verification` | безусловный `HostnameVerifier -> true` или пустой trust manager на используемом соединении |
| `CG26-SAST-003` | SAST | `credentialed-cors` | `*` origin и `allowCredentials = true` в одном `CorsConfiguration` |
| `CG26-SAST-004` | SAST | `route-authorization` | публичное правило открывает обработчик, возвращающий owner-колонку или нефильтрованные строки |
| `CG26-SAST-005` | SAST | `object-authorization` | идентификатор из `@PathVariable` доходит до чтения объекта без owner-предиката |

## Ключевые решения

* **Никаких имён файлов и номеров строк в правилах.** Лексер и структурный
  индекс работают с токенами; правило видит объявления, вызовы и поток значения.
* **Свёртка строк.** `val a = "x"`, `val b = "y"`, `val c = a + b` сворачиваются
  до `"xy"`; интерполяции `$x` и `${x}` разрешаются по локальным, файловым и
  классовым свойствам.
* **Плейсхолдеры и ссылки на окружение.** `YOUR_API_KEY`, `changeme`, `<token>`,
  `xxx` и `${ENV}` без встроенного значения не считаются секретами.
* **Секреты не раскрываются.** В отчёт попадают только маска и длина значения.
* **Версия из lock-файла приоритетна.** Если `gradle.lockfile` содержит
  координату, для экспорта и сравнения берётся его версия.
* **Один advisory — одна находка.** `advisoryId` — это `id` записи; CVE-алиасы
  остаются в `evidence.aliases`, но не создают вторую находку.
* **Неизвестная версия — диагностика.** Квалификаторы (`1.0-RC1`) и
  неразрешённые переменные не превращаются в находку.
* **Граф без декоративных стрелок.** У каждого ребра есть координата-доказательство
  и `status: established`; неразрешённые связи попадают в `unresolved` со
  статусом `unknown`. `routeReachable` показывает только статическую
  достижимость от маршрута и не означает «безопасно».

## Выгрузка в общий контракт

`analyzers/cg26/contract.py` переводит граф и находки в словарь
`CodeNode`/`CodeEdge` из `docs/data-contracts`, не добавляя фактов:

| Наш узел/ребро | В контракте |
| --- | --- |
| `route` | `route` |
| `authorization-rule` | `guard`; ребро `permits` для `permitAll`, `guards` для `denyAll`, `requires` для ролей и `authenticated` |
| `handler`, `function` | `function` |
| `parameter-source` | `source`; ребро `reads` от функции |
| `sink` sql/http | `sink`; ребро `calls`, а `flows_to` — только когда находка доказывает поток параметра в SQL |
| CORS-настройка | `config` |
| `handled-by` | `routes_to` |

Что сознательно **не** выгружается: `contains-sink` для TLS-настройки и CORS
(это не вызов), `registers-cors-for` (в словаре контракта нет подходящего
ребра). Узел остаётся, ребро не рисуется.

Координаты считаются по исходным байтам: `byteStart/byteEnd` и
`line/column/endLine/endColumn` в code points, как требует контракт. Файлы не в
UTF-8 получают диагностику `contract.unsupported-location`, а не выдуманные
позиции. `fingerprint` — `fp/v1`; у SECRET `secretHmac` обязателен, у остальных
`null`.

HMAC-ключ задаётся `CG26_HMAC_KEY` и версией `CG26_HMAC_KEY_VERSION`. Без них
выгрузка помечается `hmacKeyVersion: local-demo-1` и не считается сравнимой с
production-прогоном. Значения секретов в bundle не попадают: строковые
литералы в файлах с SECRET-находками маскируются, известные значения
заменяются звёздочками.

`artifactHash` локального прогона — sha256 от каталога-манифеста с префиксом
`local-directory/v1`, а не от принятого ZIP. При ingestion A передаёт настоящий
`artifactHash` и UUID через `--workspace-id/--run-id/--snapshot-id`.

## Проверка на учебном снимке

```text
findings: 15 ({'SECRET': 5, 'SCA': 5, 'SAST': 5})
expected: 15, correct: 15, missed: 0, false positives: 0
controls: 6, control hits: 0
SELFTEST PASSED: 74 проверки   # 24 анализатора + 50 RAG
```

`--rag-store` дополнительно индексирует выгрузку в файловое хранилище
`rag/`: элементы получают содержательный id, поэтому второй прогон того же
кода даёт `new: 0, reused: 148`. Детали и ограничения — в
[rag/README.md](../rag/README.md).

Сценарии self-test включают:

* перенос строки между `=` и литералом, комментарии и переименование файла;
* плейсхолдеры `YOUR_API_KEY` и `changeme`;
* небезопасный SQL рядом с параметризованным;
* отключённую проверку TLS рядом с обычным HTTPS-клиентом;
* `*` + credentials рядом с явным origin + credentials;
* публичный экспорт рядом с безопасным каталогом;
* чтение объекта без owner-предиката рядом с безопасным чтением по `customer_id`;
* локальную константу версии, интерполяцию, Gradle property, многострочный
  `add(...)`, приоритет lock-файла, версию выше границы и неизвестную версию;
* отсутствие значений секретов в JSON-отчётах;
* `AnalysisBundle`: три семейства, независимый пересчёт `fp/v1`, HMAC только у
  SECRET, совпадение bytes/line координат с исходными файлами и отсутствие
  значений секретов в выгрузке.

## Ограничения

* Это лексер и структурный индекс, а не компилятор Kotlin. Типы выводятся по
  конструкторам и явным аннотациям; сложные generic- и extension-вызовы могут
  остаться неразрешёнными.
* Межпроцедурный анализ ограничен одним переходом «обработчик → метод
  репозитория» по типу параметра конструктора. Глубокие цепочки сервисов
  отмечаются как `unknown`, а не угадываются.
* YAML поддержан в подмножестве: вложенные словари, скаляры, простые списки.
  Anchors, aliases и multi-document не разворачиваются.
* Gradle не запускается. Version catalogs (`libs.versions.toml`), BOM/platform и
  плагинные зависимости не разрешаются; для managed-версий нужен lock-файл,
  иначе выводится диагностика `sca.unknown-version`.
* Сравнение версий только числовое, как задано снимком. Квалификаторы и
  нечисловые схемы дают диагностику, а не догадку.
* Эвристики секретов консервативны: экзотические форматы ключей могут быть
  пропущены, а нестандартные литералы — не распознаны.
* Динамические и exploit-сценарии не входят в область: проверяется статический
  поток и конфигурация, а не поведение запущенного приложения.
