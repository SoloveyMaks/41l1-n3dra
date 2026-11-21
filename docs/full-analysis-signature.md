# Подпись full-analysis

`AnalysisSignature` в канонической карте отвечает на один вопрос: можно ли
сравнивать два прогона. Если реализация движков, правила, парсеры или выбранный
advisory-каталог отличаются, сравнение становится неполным и не должно выдавать
«исправлено» / «не исправлено».

Подпись собирается в `backend/app/codefacts/canonical.py::CanonicalMapBuilder._build_signature`.
Поля DTO не менялись: `docs/data-contracts/contracts.schema.json#/$defs/AnalysisSignature`.

## Что попадает в подпись

| Поле | Источник |
| --- | --- |
| `parserVersion` | `EXTRACTOR_VERSION` — версия backend-парсера codefacts |
| `rulesVersion` | `RULES_VERSION` + `cg-full-analysis/1` — версия правил и композиции движков |
| `advisoryVersion` | версия backend-каталога + версия выбранного Team B advisory snapshot |
| `configVersion` | `cg-config/2` — схема конфигурации подписи |
| `hmacKeyVersion` | версия серверного HMAC-ключа (`CG26_HMAC_KEY_VERSION`), не ключ |
| `parserHash` | backend `extractor.py` / `source.py` / `canonical.py` + Team B parser-модули |
| `rulesHash` | определения backend-правил, код backend-анализаторов, весь `analyzers/cg26`, композиция full-analysis |
| `advisoryHash` | байты `backend/app/analyzers/advisories.json` + байты выбранного доверенного каталога Team B |
| `scanConfigHash` | расширения, лимит графа, состав движков `backend + team_b` для SECRET/SCA/SAST |
| `domainPolicy*` | `null`: в этой композиции доменная политика не задана |

В `rulesHash` входят `backend/app/integrations/full_analysis.py`,
`backend/app/integrations/team_b.py` и `backend/app/analyzers/advisory_catalog.py`.
Поэтому изменение состава объединения движков или логики выбора каталога меняет
подпись, даже если сами правила не тронуты.

## Выбор доверенного каталога

Подпись не дублирует резолюцию: при наличии адаптера вызывается
`backend.app.integrations.team_b._trusted_advisory_path`, а не упрощённый
`trusted_catalog_path`. Локальная копия используется только в standalone-сборке
codefacts, где адаптера нет или он старого контракта, и повторяет ту же семантику:

1. `CG26_ADVISORY_DB` пуст → встроенный `analyzers/advisory-snapshot/trusted-advisories.json`.
2. URL запрещён → встроенный снапшот.
3. Задан `CG26_ADVISORY_ROOT`, а файл вне него → встроенный снапшот.
4. Файл не найден или больше 10 MiB → каталог не выбран, SCA работает без него.

Загруженный репозиторий никогда не выбирает каталог. В подписи нет абсолютных
путей: только sha256 байтов и версия из метаданных (`snapshotVersion`, иначе
`snapshot.date`, иначе `schemaVersion`).

## Консервативное поведение

- Каталог не выбран или не читается → `advisoryVersion` содержит
  `trusted-advisories/unavailable`, `advisoryHash` учитывает `null` и в
  `diagnostics` добавляется `CF-SIGNATURE-CATALOG-UNAVAILABLE`.
- Невалидный JSON каталога → версия `invalid`, но байтовый digest остаётся в
  подписи.
- Отсутствующий модуль реализации хэшируется маркером `missing`; появление
  файла меняет хэш. Это защищает сравнение от «тихого» расширения движка.
- HMAC-секрет и содержимое ключей в подпись не попадают, только версия ключа.

## Детерминированность

Хэши считаются по относительным POSIX-именам и байтам файлов в отсортированном
порядке. Копия дерева в другом каталоге даёт те же значения. Повторный вызов на
тех же входах даёт ту же подпись.

## Совместимость

Исторические bundle не переписываются. Новая подпись отличается от старой
(`configVersion` `cg-config/2`, `rulesVersion` с `cg-full-analysis/1`), поэтому
старые прогоны честно становятся несравнимыми с новыми, а не «совместимыми по
ошибке». При изменении выбранного каталога или кода правил сравнение обязано
перейти в `inconclusive`.