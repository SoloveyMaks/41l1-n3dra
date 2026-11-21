# Отчёт по собственным анализаторам

Пакет B. Проверяемый проект — учебный снимок
`TBagirov/training@fe1fcbe` (`training-project/`), в этот репозиторий он не
входит. Эталон `ground-truth/training-findings.json` читается только скриптом
сверки; модули анализаторов его не импортируют.

## Итог

| Категория | Ожидалось | Верно | Пропущено | Ложных срабатываний | Контролей | Задето контролей |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SECRET | 5 | 5 | 0 | 0 | 2 | 0 |
| SCA | 5 | 5 | 0 | 0 | 1 | 0 |
| SAST | 5 | 5 | 0 | 0 | 3 | 0 |
| **Всего** | **15** | **15** | **0** | **0** | **6** | **0** |

Команды воспроизведения (путь к снимку подставьте свой):

```powershell
python -m analyzers.scan <snapshot>/training-project `
  --db <snapshot>/vulnerability-database/vulnerabilities.json `
  --out analyzers/examples/submission.json --full artifacts/scan-full.json
python -m analyzers.compare analyzers/examples/submission.json `
  --truth <snapshot>/ground-truth/training-findings.json `
  --out analyzers/examples/comparison.json
python -m analyzers.selftest
```

В репозитории лежат `analyzers/examples/submission.json` и
`analyzers/examples/comparison.json` — результат прогона на снимке.
`artifacts/` (полный отчёт с графом) в git не попадает.

## Находки

| ID | Класс | Файл:строки | Доказательство |
| --- | --- | --- | --- |
| A-SEC-01 | `password` | `integration/PartsImportSettings.kt:4` | литерал в `legacyImportPassword`; sink `mapOf("X-Import-Password", ...)` |
| A-SEC-02 | `api-key` | `integration/QuoteRequest.kt:7` | литерал в `pricingApiKey`; sink `.header("X-Api-Key", ...)` на строке 10 |
| A-SEC-03 | `configuration-token` | `application.yml:13` | `pickup.callback.authorization` содержит `Bearer …`; значение маскировано |
| A-SEC-04 | `secret-fallback` | `integration/DepotStatusClient.kt:8` | правая часть `System.getenv(...) ?: "…"` |
| A-SEC-05 | `recoverable-token` | `integration/WarrantyHeaders.kt:7-10` | фрагменты строк 7 и 8, итог на строке 9, sink `setBearerAuth` на строке 10 |
| A-SCA-01 | `known-vulnerable-dependency` | `build.gradle.kts:31` | `org.apache.commons:commons-text:1.9`, advisory `GHSA-599f-7c49-w659`, lock-строка 16 |
| A-SCA-02 | `known-vulnerable-dependency` | `build.gradle.kts:32` | `org.jsoup:jsoup:1.14.1`, advisory `GHSA-m72m-mhq2-9p6c` |
| A-SCA-03 | `known-vulnerable-dependency` | `build.gradle.kts:33` | `commons-compress:1.25.0` через `$archiveFormatVersion`, lock-строка 14 |
| A-SCA-04 | `known-vulnerable-dependency` | `build.gradle.kts:36` | многострочный `add("implementation", "commons-net:commons-net:3.8.0")` |
| A-SCA-05 | `known-vulnerable-dependency` | `build.gradle.kts:38` | `org.json:json:20230227` через `providers.gradleProperty("legacyManifestJsonVersion").get()`, `gradle.properties:1` |
| A-SAST-01 | `sql-injection` | `repair/RepairLookupController.kt:13-14` | `@RequestParam frameTag` интерполируется в SQL и передаётся в `queryForList` |
| A-SAST-02 | `tls-verification` | `integration/SupplierHttpsClient.kt:13` | `hostnameVerifier = HostnameVerifier { _, _ -> true }` на используемом соединении |
| A-SAST-03 | `credentialed-cors` | `config/RepairCors.kt:13-14` | `allowedOriginPatterns = listOf("*")` и `allowCredentials = true`; маршрут `/api/customer/summary` |
| A-SAST-04 | `route-authorization` | `config/RepairSecurity.kt:18` | `permitAll()` на `GET /api/pickup/export`; обработчик возвращает `customer_id` и `counter_note` |
| A-SAST-05 | `object-authorization` | `repair/RepairTicketReader.kt:10-11` | чтение по `ticket_id = ?` выбирает `customer_id`, owner-предиката нет; маршрут `GET /api/customer/tickets/{ticketId}` |

## Контроли

| ID | Класс | Файл:строка | Почему не сработало |
| --- | --- | --- | --- |
| A-CTRL-01 | `api-key` | `README.md:9` | документационный плейсхолдер `YOUR_API_KEY` |
| A-CTRL-02 | `configuration-token` | `application.yml:17` | `${INVENTORY_TOKEN}` — ссылка на окружение без значения |
| A-CTRL-03 | CVE-2025-48924 | `build.gradle.kts:39` | `commons-lang3:3.20.0` выше границы `3.18.0` |
| A-CTRL-04 | `sql-injection` | `RepairLookupController.kt:17-18` | SQL использует `?`, значение передаётся отдельным аргументом |
| A-CTRL-05 | `route-authorization` | `RepairSecurity.kt:17` | публичный каталог возвращает статический список услуг |
| A-CTRL-06 | `tls-verification` | `ManualsHttpsClient.kt:7-8` | штатный `HttpsURLConnection` без переопределения верификатора |

## Граф доказательств

`artifacts/scan-full.json` содержит 43 узла и 32 ребра: 6 маршрутов, 5
упорядоченных security-правил, 6 обработчиков, 4 источника параметров, 11
функций и 11 sink. У всех рёбер есть координата-доказательство и
`status: established`; неразрешённых связей в учебном проекте нет. Для sink
дополнительно указано `routeReachable`: например, HTTP-заголовки интеграций
статически не достижимы из маршрута, и это отражено явно, а не стрелкой.

Порядок правил сохранён: `permitAll` для каталога → `permitAll` для экспорта →
`hasRole("MECHANIC")` для `/api/pickup/**` → `authenticated()` для
`/api/customer/**` → `denyAll()`. Маршрут `GET /api/pickup/export` получает
первое совпавшее правило (строка 18), а не более позднее `hasRole`.

## Выгрузка в общий контракт

`python -m analyzers.contract_export ...` выдаёт `AnalysisBundle` из
`docs/data-contracts`: 3 результата семейств, 43 узла, 32 ребра и 67 evidence
refs. Выгрузка проверена двумя способами:

```text
node analyzers/validate_bundle.mjs analyzers/examples/bundle.json <snapshot>/training-project
{"findings":15,"evidence":67,"nodes":43,"edges":32}

ajv (draft 2020-12) по docs/data-contracts/contracts.schema.json
schema ok
```

Покрытие в контракте: `SECRET` — complete, `SAST` — complete, `SCA` — partial,
потому что managed-версии без lock-файла остаются диагностикой
`sca.unknown-version`. `filesTotal` считается по области семейства (Kotlin для
SAST, Gradle-файлы для SCA, все текстовые файлы для SECRET), а не по всему
репозиторию.

Что именно доказывают рёбра контракта:

* `routes_to` — аннотация маршрута на функции-обработчике;
* `permits`/`requires`/`guards` — первое совпавшее правило и его эффект;
* `reads` — параметр объявлен в сигнатуре обработчика;
* `calls` — вызов разрешён по типу конструктора;
* `flows_to` — только там, где находка доказывает поток параметра в SQL.

`contains-sink` для TLS-настройки и CORS, а также `registers-cors-for` в
контракт не переводятся: в словаре нет подходящего ребра, и рисовать похожую
стрелку было бы неверно. Узлы при этом остаются с координатами. Значения
секретов не попадают в bundle: литералы в файлах с SECRET-находками
маскируются, а у SECRET-находок есть только `secretHmac`.

## Самотест

`python -m analyzers.selftest` — 73 проверки: 24 по анализаторам и 49 по
RAG-хранилищу, на фиксированных копиях:

* перенос строки, комментарии и переименование файла не меняют результат;
* `YOUR_API_KEY` и `changeme` не считаются секретами;
* небезопасный SQL рядом с параметризованным;
* отключённый TLS рядом с обычным HTTPS;
* `*` + credentials рядом с явным origin + credentials;
* публичный экспорт рядом с безопасным каталогом;
* чтение объекта без owner-предиката рядом с чтением по `customer_id`;
* локальная константа, интерполяция, Gradle property, многострочный `add(...)`,
  приоритет lock-файла, версия выше границы, неизвестная версия;
* отсутствие значений секретов в JSON-отчётах;
* `AnalysisBundle`: три семейства, независимый пересчёт `fp/v1`, HMAC только у
  SECRET, совпадение bytes/line координат с файлами, отсутствие значений
  секретов в выгрузке.

## Переиспользование элементов (RAG)

Реляционной БД нет, поэтому память фактов и правок лежит в `rag/`:
append-only журнал элементов `events.jsonl` + производные векторы
`vectors.jsonl` + `manifest.json` с embedding spec. Id элемента выводится из
содержания (`category|fingerprint`, `kind|symbolKey|path`, evidence hash,
`exampleId`), поэтому повторный скан не создаёт дублей: `new: 0`,
`reused: 148` на втором прогоне того же bundle.

Векторизация — hashed TF-IDF с BM25-весами (4096 измерений, cosine,
`local/bm25-hash-tfidf/2`). Она воспроизводима и не требует сети; это
лексическая модель, а не семантическая, и adapter D подключается заменой
`Index` при сохранении версии spec. `similar_fixes()` возвращает ровно
`SimilarFixesResult` из контракта (`status/reason/items`, `SimilarFix` с
`embeddingSpec`, `distance`, `metric`, `limitations`, `redactedFix`); результат
проверен AJV (draft 2020-12) и структурными проверками самотеста.

```text
python -X utf8 -m rag.selftest            # 49 проверок
python -X utf8 -m analyzers.selftest      # 73 проверки
python -X utf8 -m rag.cli stats --store .rag/store
```

Ограничения retrieval вынесены в [rag/README.md](../rag/README.md): нет ANN,
`idf` пересчитывается на корпус, `distance` не подтверждает применимость
правки, переименование сущности даёт новый элемент.

## Честные ограничения

* Лексер и структурный индекс, а не компилятор Kotlin: сложные generic-,
  extension- и delegate-вызовы могут остаться неразрешёнными.
* Межпроцедурный поток ограничен переходом «обработчик → метод репозитория» по
  типу параметра конструктора. Более глубокие цепочки не угадываются.
* YAML поддержан в подмножестве; anchors/aliases и multi-document не
  разворачиваются.
* Gradle не исполняется: version catalogs, BOM/platform и плагинные зависимости
  не разрешаются. Без lock-файла managed-версия даёт диагностику
  `sca.unknown-version`.
* Сравнение версий только числовое; квалификаторы дают диагностику.
* Эвристики секретов консервативны и могут пропускать экзотические форматы.
* Контракт `docs/data-contracts` — предложение команды, а не утверждённый
  интерфейс. Выгрузка проверена их schema и чекером, но словарь и правила
  matching ещё согласуются A/B/C.
* `artifactHash` локального прогона — digest каталога с префиксом
  `local-directory/v1`, не hash принятого ZIP. Без `CG26_HMAC_KEY` SECRET-HMAC
  помечен как `local-demo-1` и не сравним с production-прогоном.
* Динамические и exploit-сценарии не строились: проверяется статический поток и
  конфигурация.
