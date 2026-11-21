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

## Откуда взялся checked scope

Реальный `SastAnalyzer` возвращает только агрегированный `Coverage`
(`files_scanned`, `rules_executed`, `parser_errors`), без per-file/per-rule
отрицательного outcome. Поэтому `run_real_scan.py` использует локальную
обёртку `ObservedSastAnalyzer`: она вызывает настоящий
`SastAnalyzer._check_sql_injection` и записывает, для какого файла правило
реально исполнялось и сколько findings оно дало. Это даёт настоящий
`checked` scope:

- before: `KOTLIN-SQL-INJECTION`, controller, `checked`, findings=1;
- after: `KOTLIN-SQL-INJECTION`, controller, `checked`, findings=0.

Без этой обёртки после-сторона не имеет подтверждённого checked-отрицательного
результата: comparison вернул бы `inconclusive: нет checked для ... / KOTLIN-SQL-INJECTION`.
Это и есть минимальный будущий scope для B: per-file `SAST / path /
KOTLIN-SQL-INJECTION / checked` с явным `findings=0` (или типизированный
inspected-sink outcome). Просто `files_scanned=1` и пустой findings массив
таким доказательством не являются.

Обёртка — demo adapter в `examples/`, backend-файлы не изменены.

## Code facts coverage: граница примера

`run_real_scan.py` запускает настоящий codefacts extractor и берёт его
nodes/edges. Для demo coverage считается complete только для source graph:

- все файлы manifest прочитаны;
- `unresolved_symbols` и `limits` пусты;
- у каждого edge `resolution=resolved`;
- scopes построены по реально прочитанным source paths.

Production `CanonicalMapBuilder._build_coverage` добавляет в `skippedScopes`
`runtime-configuration`, `runtime-guards`, `binaries-and-archives`, поэтому
production bundle с этим coverage даст `inconclusive` по graph completeness,
даже если SQL scope checked. В примере эти runtime scopes явно вынесены за
границу source-graph proof, а не объявлены проверенными. Для production
нужен один из вариантов:

1. B/A подтверждают source-graph-only coverage и applicability для
   comparison, не смешивая runtime scopes с SQL disappearance; либо
2. comparison получает явную category/rule applicability map; либо
3. B выдаёт дополнительный typed inspected-sink outcome, а graph gap остаётся
   видимым и блокирует только зависимые выводы.

До этого production comparison для SQL-паттерна обязан оставаться
`inconclusive`, а не `resolved`.

`expectedScopesVerified=true` в envelope — attestation этого demo-адаптера:
ожидаемый SQL scope (файл с `@RequestParam`) совпал с фактически
наблюдённым вызовом правила, manifest прочитан полностью. В production это
должно проверять доверенное A/B, а не self-asserted JSON.

## Отрицательные проверки

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

Итог: реальный scanner и real code facts дают воспроизводимое основание для
`resolved` в границах demo-adapter; production-путь честно требует
per-file SQL checked outcome и согласованного source-graph coverage, иначе
сравнение остаётся `inconclusive`.