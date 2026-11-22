# SQL before/after: реальный scanner run и честный comparison

Это воспроизводимый прогон двух минимальных source snapshots. Он не доказывает
runtime-исправление и не является production publication. Цель — проверить, что
реальный SAST находит SQL-интерполяцию до правки, не находит её после
параметризации, и что comparison даёт `resolved` только при наличии checked
scope с обеих сторон и полного source graph. Удаление файла и потеря покрытия
остаются отдельными `file_deleted` / `inconclusive`.

## Что лежит в примере

```
examples/demo/sql-before-after/
  before/src/main/kotlin/demo/RepairLookupController.kt
  before/src/main/kotlin/demo/SecurityConfig.kt
  after/src/main/kotlin/demo/RepairLookupController.kt
  after/src/main/kotlin/demo/SecurityConfig.kt
  prepare_snapshots.py
  run_real_scan.py
  build_envelopes.mjs
  negative_probes.mjs
  before.zip / after.zip
  snapshots.json
  raw-before.json / raw-after.json
  baseline.published.json / current.published.json
  comparison.json
  negative-probes.json
  sast-observation-probes.json
```

`SecurityConfig.kt` даёт маршруту статическое `hasRole("STAFF")`, чтобы
`actor -> route` не оставался `unknown` и не блокировал сравнение посторонним
graph gap. SQL-дефект и его исправление находятся только в
`RepairLookupController.kt`.

## Что именно запускалось

Использовался `/tmp/pg-runtime-venv/bin/python` (там есть `tree-sitter`,
`tree-sitter-kotlin`, `pydantic`) и Node 22. Команды:

```sh
/tmp/pg-runtime-venv/bin/python examples/demo/sql-before-after/prepare_snapshots.py
/tmp/pg-runtime-venv/bin/python examples/demo/sql-before-after/run_real_scan.py
node examples/demo/sql-before-after/build_envelopes.mjs
node examples/demo/sql-before-after/negative_probes.mjs
```

`run_real_scan.py` запускает настоящие `SastAnalyzer`, `SecretAnalyzer`,
`ScaAnalyzer`, `extract_facts` и `CanonicalMapBuilder`. ZIP-файлы
детерминированные: повторный `prepare_snapshots.py` даёт те же SHA-256.

### Реальные результаты

| Сторона | SAST findings | SQL rule observed | SECRET | SCA | Manifest |
| --- | ---: | --- | ---: | ---: | --- |
| before | 1 × `KOTLIN-SQL-INJECTION` | controller: `invoked=1, findings=1` | 0 | 0 | 2 файла |
| after | 0 | controller: `invoked=1, findings=0` | 0 | 0 | 2 файла |

Before finding: `src/main/kotlin/demo/RepairLookupController.kt:12`,
`KOTLIN-SQL-INJECTION`, anchor
`function:demo.RepairLookupController.findByFrameTag`, role `sql_sink`.
Оба ZIP-а имеют полный manifest с source hashes; before/after run и snapshot
различаются, workspace и static signature совпадают.

`comparison.json`:

```json
{
  "comparable": true,
  "items": [
    {
      "status": "resolved",
      "matchingBasis": "unique_anchor",
      "reason": "Файл сохранился; оба file/rule scope полностью checked, текущий паттерн отсутствует"
    }
  ],
  "deletedFiles": []
}
```

Это статическое исчезновение паттерна, не доказательство отсутствия всех
уязвимостей и не runtime-проверка.

## Узкое SQL-сравнение

Для конкретного правила есть отдельный модуль
[`packages/comparison/sql-rule.mjs`](../packages/comparison/sql-rule.mjs) и
[`sql-rule-cli.mjs`](../packages/comparison/sql-rule-cli.mjs). Полный контракт —
[SQL rule comparison](data-contracts/sql-rule-comparison.md).

```sh
node packages/comparison/sql-rule-cli.mjs \
  examples/demo/sql-before-after/baseline.published.json \
  examples/demo/sql-before-after/current.published.json
```

На этих реальных envelopes результат:

```json
{
  "scope": "source_pattern",
  "status": "pattern_disappeared",
  "matchingBasis": "unique_anchor",
  "globalComparable": false,
  "expectedScopesVerified": {"baseline": true, "current": true}
}
```

Тот же `pattern_disappeared` сохраняется, если принудительно поставить
`expectedScopesVerified=false` и `partial` scan/coverage с пропуском других
rules: proof опирается на фактический checked scope
`SAST/path/KOTLIN-SQL-INJECTION`, а не на глобальный attestation. Потеря
checked scope → `inconclusive`; несовместимый `rulesHash` → `inconclusive`;
удаление path из полного current manifest → `file_deleted`. Этот модуль не
вызывает `compareBundles`, не делает run comparable и не проверяет graph
runtime; обычное сравнение и его guards остаются отдельными.

## Откуда взялся checked scope

`SastAnalyzer` теперь сам пишет typed наблюдения в
`Coverage.rule_observations`; demo больше не использует subclass. Для
`KOTLIN-SQL-INJECTION` на каждый `.kt` файл создаётся одна запись:

| Поле | Смысл |
| --- | --- |
| `rule_id` | `KOTLIN-SQL-INJECTION` |
| `path` | файл внутри snapshot |
| `status` | `checked`, `not_applicable`, `skipped` или `error` |
| `invocations` | сколько раз реально вызван `_check_sql_injection` |
| `findings` | сколько новых findings дало правило |
| `reason` | причина статуса |

`checked` ставится только после фактического вызова и успешного завершения
правила. `findings=0` при `checked` — проверенный отрицательный scope.

Реальные наблюдения на демо:

- before: controller — `checked`, `invocations=1`, `findings=1`;
- after: controller — `checked`, `invocations=1`, `findings=0`;
- `SecurityConfig.kt` в обоих — `not_applicable`, `invocations=0`.

`not_applicable`, `skipped` и `error` не являются checked. Parser error,
непрочитанный файл и exception внутри правила дают `skipped`/`error`, а не
отрицательный результат. Неинструментированные rules не получают запись и не
считаются checked. Состояние наблюдений локально для каждого `analyze`, без
class-level mutable state; одна запись на файл, поэтому размер ограничен
количеством `.kt` файлов.

`run_real_scan.py` просто читает `sast_result.coverage.rule_observations` и
строит SAST scope только из `checked` записей. Наблюдение живёт в обычном
`SastAnalyzer`; отдельного subclass или demo-обёртки больше нет.

## Code facts coverage: граница примера

`run_real_scan.py` запускает настоящий codefacts extractor и берёт его
nodes/edges. Для demo coverage считается complete только для source graph:

- все файлы manifest прочитаны;
- `unresolved_symbols` и `limits` пусты;
- у каждого edge `resolution=resolved`;
- scopes построены по реально прочитанным source paths.

Остаётся отдельная граница adapter/А: до этапа замены заглушек
`CanonicalMapBuilder._build_coverage` добавлял в `skippedScopes`
`runtime-configuration`, `runtime-guards` и `binaries-and-archives` руками.
Сейчас эти три строки не вписываются: `runtime-configuration` и
`runtime-guards` проверяются статически (`config_static`, `guards_static`),
`binaries-and-archives` — чтением архивов (`archives_static`), а
`skippedScopes` собирается только по фактическим причинам (encrypted /
too-large / unsupported archive, unresolved security-relevant placeholder).
Если такой причины нет, graph coverage может стать `complete`; comparison
проверяет это по scopes, а не по наличию трёх строк. Это не влияет на сам
факт per-file SQL observation.

`expectedScopesVerified=true` в envelope — attestation demo-сборки: ожидаемый
SQL scope (файл с `@RequestParam`) совпал с `checked` наблюдением, manifest
прочитан полностью. В production это проверяет доверенное A/B, а не
self-asserted JSON. Наблюдение уже приходит из обычного `SastAnalyzer`, поэтому
adapter может использовать его напрямую.

## Отрицательные проверки

`sast-observation-probes.json` — реальные одноразовые прогоны обычного
`SastAnalyzer`:

| Probe | Наблюдение |
| --- | --- |
| Синтаксическая ошибка в `.kt` | `skipped`, `checked=false`, parserErrors=1 |
| Нечитаемый `.kt` | `skipped`, `checked=false`, parserErrors=1 |
| Файл без user source | `not_applicable`, `checked=false` |

`negative-probes.json`:

| Probe | Результат |
| --- | --- |
| Убрать SAST checked scope / сделать coverage partial | `inconclusive` |
| Сделать codeFacts coverage partial | `inconclusive` |
| Убрать controller из полного current manifest | `file_deleted` + `deletedFiles` с path |

Удаление файла — manifest mutation probe, не второй scanner run. Он проверяет
именно comparison guard: отсутствие пути не превращается в `resolved`.
Coverage-loss probes тоже синтетические: они меняют coverage уже собранного
real bundle, а не изображают новый scanner result.

## Чего здесь нет

- Нет SQL publication, production `published_bundles` и A-attestation:
  `baseline.published.json`/`current.published.json` — локальные envelopes с
  корректным manifest hash, а не записи в БД.
- Нет Kotlin/Gradle execution, runtime/exploit, owner-policy proof.
- Нет embeddings, vetted remediation examples или AI-совета.
- Нет утверждения, что параметризация сохранила функциональность приложения.

Итог: обычный `SastAnalyzer` даёт per-file SQL observation; demo global
comparison показывает `resolved`, а узкий SQL DTO — `pattern_disappeared` при
checked scope с обеих сторон. Production adapter может использовать эти scopes
без synthetic source graph; обычный comparison по-прежнему отдельно зависит
от graph/runtime coverage и может остаться `inconclusive`.