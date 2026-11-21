# RAG-хранилище элементов — локальный retrieval без реляционной БД

Пакет B/C-пограничный, но живёт отдельно от анализаторов: `rag/` не влияет на
решения SECRET/SCA/SAST. Он индексирует уже готовые факты (`AnalysisBundle`) и
проверенные примеры исправлений, чтобы **элемент одного запуска переиспользовался
в следующем**.

Решение пользователя: реляционной БД нет. Поэтому хранилище — файлы:

```
<store>/
  manifest.json    # workspace, embedding spec, specId, schema version
  events.jsonl     # append-only журнал элементов (источник истины)
  vectors.jsonl    # производные векторы: один ряд на элемент
```

`events.jsonl` — истина. `vectors.jsonl` можно удалить и пересобрать: он выводится
из элементов и версии embedding spec, ничего не теряется.

## Зачем переиспользование

У элемента **id, выведенный из содержания**, а не UUID запуска:

| kind | identity | откуда |
| --- | --- | --- |
| `finding` | `category\|fingerprint` | `fp/v1` из контракта |
| `node` | `kind\|symbolKey\|path` | `CodeFactsBundle` |
| `edge` | `kind\|fromSymbol\|toSymbol` | `CodeFactsBundle` |
| `evidence` | `kind\|path\|sourceHash\|line\|endLine` | evidence |
| `fix` | `exampleId` | `rag/examples/fixes.json` |

Поэтому:

* повторный скан того же кода даёт те же id → `new: 0, reused: N`;
* к id можно один раз привязать позицию на карте, объяснение или проверенную
  правку, и она останется привязанной в следующих запусках;
* переименование файла меняет `path` и fingerprint, поэтому это **новый**
  элемент — молча склеивать разные якоря нельзя (контракт этого не разрешает);
* `seen` и `runs` показывают, сколько раз элемент встретился и в каких запусках.

## Векторизация

`rag/embed.py` — hashed TF-IDF с BM25-весами, 4096 измерений, метрика cosine:

* текст приводится к нижнему регистру, `camelCase`/`snake_case` разбиваются,
  стоп-слова убираются;
* вес термина документа `idf * tf*(k1+1)/(tf + k1*(1-b+b*dl/avgdl))`,
  запроса — `idf * (1 + ln tf)`;
* обе стороны хешируются беззнаково в 4096 измерений и L2-нормируются;
* `distance = 1 - cos`, меньше — ближе. Это **не вероятность**: 0 — почти
  одинаковый словарь, 1 — общих терминов нет. Для совпадающего по смыслу, но
  по-другому сформулированного элемента типично 0.6–0.9.

Почему не нейросетевая модель: провайдера ещё нет, а переиспользование должно
быть воспроизводимым и проверяемым. Лексическая модель честно говорит, что она
умеет — ранжирует по общим терминам, а не «доказывает тот же баг». Adapter D
подключается заменой `Index` при сохранении версионированного spec.

`idf` зависит от корпуса, поэтому векторы **производные**: после любого ingest
`vectors.jsonl` пересобирается целиком. Id элементов от этого не меняются.
Добавление элемента меняет векторы всех элементов — это свойство bag-of-words,
а не ошибка; для нашего объёма (сотни элементов) пересборка занимает
миллисекунды.

Версии подготовки текста и spec фиксируются в `manifest.json`
(`provider`, `model`, `modelVersion`, `dimension`, `metric`,
`queryPreparationVersion`, `documentPreparationVersion`, `redactionVersion`).
Если код и хранилище расходятся по spec, ingest падает с `SpecMismatch`, а поиск
возвращает typed `unavailable` — смешивать векторные пространства нельзя.

## Что индексируется

* findings, nodes, edges, evidence из `AnalysisBundle` (`rag.cli index`);
* проверенные примеры исправлений (`rag.cli examples`).

В текст элемента никогда не попадает значение секрета: берётся только
`redactedSnippet` из bundle. Проверка `rag.selftest` ищет известные маркеры
секретов в `manifest.json`, `events.jsonl` и `vectors.jsonl`.

К каждому тексту добавляется **словарь понятий** (`CONCEPT_TAGS` в `store.py`):
`sql-injection` → «sql query string concatenation prepared statement parameter
binding jdbc». Это слова для поиска, а не правила детекции: анализаторы уже
решили, что является находкой. Своя формулировка, без сторонних rule set.

## CLI

```bash
# проиндексировать готовый bundle
python -X utf8 -m rag.cli index analyzers/examples/bundle.json --store .rag/store

# проиндексировать проверенные примеры исправлений
python -X utf8 -m rag.cli examples rag/examples/fixes.json \
    --store .rag/store --workspace 649d5c51-b4a5-5ad2-956f-bb8a4720bac0

# поиск по всем элементам
python -X utf8 -m rag.cli search "hostname verifier accepts every certificate" \
    --store .rag/store --kind finding --k 3

# контрактный SimilarFixesResult
python -X utf8 -m rag.cli similar-fixes "unsafe SQL from request parameter" \
    --store .rag/store --rule-family sql-injection --language kotlin --framework spring-boot

python -X utf8 -m rag.cli stats   --store .rag/store
python -X utf8 -m rag.cli get el:... --store .rag/store
python -X utf8 -m rag.cli reindex --store .rag/store
```

Один шаг вместе со сканом:

```bash
python -X utf8 -m analyzers.contract_export <snapshot>/training-project \
    --db <snapshot>/vulnerability-database/vulnerabilities.json \
    --out analyzers/examples/bundle.json --rag-store .rag/store
```

Все команды печатают JSON; ошибки хранилища — в stderr, код возврата 2.

## Контракт

`similar_fixes()` возвращает ровно `SimilarFixesResult` из
`docs/data-contracts/contracts.schema.json`:

* `status: available` → `reason: null`, непустые `items`;
* `status: unavailable` → непустой `reason`, `items: []`;
* `SimilarFix` содержит только поля контракта, `embeddingSpec` — `active` с
  заполненными provider/model/version/dimension/metric и версиями подготовки;
* `exampleId` — UUID (`uuid5` от внешнего id, если в фикстуре он не UUID);
* `distance` — точное cosine-расстояние, `metric: cosine`;
* `limitations` и `redactedFix` обязательны: пример без ограничений не
  считается проверенным.

Обязательные фильтры контракта — workspace (зашит в хранилище), `ruleFamily`,
`language`, `framework`, `status`. Чужой workspace — ошибка `WorkspaceMismatch`,
а не пустая выдача.

Результат проверен AJV (draft 2020-12) по `$defs/SimilarFixesResult`; в
репозитории зависимостей нет, поэтому проверка структуры повторена в
`rag.selftest` (точный набор полей, UUID, типы, согласованность `status`/`reason`).

## Проверка

```bash
python -X utf8 -m rag.selftest        # 49 проверок
python -X utf8 -m analyzers.selftest  # 73 проверки: анализаторы + RAG
```

`rag.selftest` работает на настоящем `analyzers/examples/bundle.json` и
`rag/examples/fixes.json`, а не на игрушечном payload. Он проверяет:

* первый ingest создаёт все элементы, 15 findings, векторы по одному на элемент;
* повторный запуск: `new: 0`, `reused: 148`, `reusedAcrossRuns` у всех;
* два независимых хранилища дают одинаковые id;
* изменение текста не создаёт новый элемент, но перевекторизует его;
* поиск находит нужное семейство (9 из 9 контрольных запросов, порог в тесте —
  8 из 9), повторный
  запрос даёт тот же порядок, пустой запрос — `unavailable`;
* `SimilarFix` совпадает с контрактом по полям и типам;
* в индексе нет значений секретов;
* чужой workspace и другой embedding spec отклоняются, неполный пример — ошибка;
* `reindex` восстанавливает векторы из журнала элементов.

## Ограничения

* Лексическая модель: перефразировки без общих терминов не находятся; это не
  семантический поиск. Точность — `9/9` на девяти контрольных запросах (порог
  теста 8/9), а не оценка на production-корпусе.
* `idf` пересчитывается на весь корпус: хранилище рассчитано на сотни-тысячи
  элементов, не на миллионы. ANN-индекса нет — поиск точный, полным перебором.
* Реальный embedding provider, его размерность и версии подготовки текста не
  выбраны: текущий spec — `local/bm25-hash-tfidf/2`, статус в контракте
  `active`, но это локальный adapter, а не согласованный D.
* `distance` не подтверждает применимость правки. `status: vetted` требует
  ручной проверки; сейчас все примеры имеют `documented`.
* Переименование сущности меняет fingerprint → это новый элемент. Перенос
  находки между запусками по «похожести» не делается: для сравнения есть
  `packages/comparison`, а не вектор.
* Хранилище не шифруется и не является источником правды о коде: это индекс
  redacted-фактов. Права доступа к каталогу — ответственность backend (A).
