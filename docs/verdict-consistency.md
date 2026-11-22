# Verdict consistency: policy PASS vs canonical coverage

Дата: 2026-11-21. Ветка `task/postgres-runtime`, local dev `8f5d523`.

## Что было

`codefacts/canonical.py::_build_coverage()` честно отдаёт `status=partial`:
пропущены `runtime-configuration`, `runtime-guards`, `binaries-and-archives`,
а `publish.py` ставит `expectedScopesVerified=false`. Это ограничение
статического анализа, а не баг, который надо «поднять» до `complete`.

При этом `engine/executor.py::POLICY_GATE` может вернуть `pass` по
scanner/runtime coverage, особенно при `strictCoverageRequired=false`. Canonical
SQL guard не допускает `pass` при partial coverage и отклоняет публикацию.
Получался разрыв: run `succeeded/pass`, отчёт `pass`, а canonical bundle — не
опубликован. Execution success путался с security verdict.

## Reproduce (temp PG/runtime)

Чистый Kotlin-проект (`README.md`, `src/main/kotlin/Main.kt`), default workflow,
policy `strictCoverageRequired=false`:

```text
before fix:
execution_status=succeeded, security_verdict=pass, findings=0
canonical coverage=partial (skipped runtime-configuration/runtime-guards/
  binaries-and-archives), expectedScopesVerified=false
publication={"published": false, "reason": "publish failed: pass requires
  published complete outcomes and nonempty checked coverage for every scanner family"}
report.verdict=pass
```

Строгий default policy на том же чистом проекте уже давал `inconclusive`
(SCA coverage gap), но это была случайная защита от scanner coverage, а не
гарантия canonical verdict.

## Что изменено

Изменены `engine/executor.py` и поле `details` у `PolicyResult` (документация —
этот файл). Canonical coverage, scanner statuses, SQL migrations, storage и
frontend не менялись.

Canonical artifact уже создаётся в `MERGE_FINDINGS` до `POLICY_GATE`, поэтому
проверка применяется **до** downstream condition/AI/report/notify:

1. Policy gate сначала считает findings-вердикт (`fail` по critical/high/medium
   сохраняется без изменений).
2. Если findings дают не `fail`, загружается canonical artifact:
   `coverage.status == "complete"` **и** `provenance.expectedScopesVerified is true`.
3. Если это не подтверждено, policy gate сразу возвращает `inconclusive` —
   включая `strictCoverageRequired=false`. `strictCoverageRequired` управляет
   только runtime COVERAGE-требованиями и не может отменить доказательность
   общего security verdict.
4. Downstream condition/AI/report/notify получают уже `inconclusive`: condition
   `equals pass` даёт `false`, webhook/report/SSE/API не расходятся.
5. Видимая причина на русском:
   `Полная проверка не подтверждена: покрытие частичное; обязательные scopes не
   верифицированы; пропущены: runtime-configuration, runtime-guards,
   binaries-and-archives`.
   Enum статуса и хэши лежат в `PolicyResult.details.canonicalCoverage`
   (`coverageStatus`, `expectedScopesVerified`, `skippedScopes`, `bundleHash`,
   `parserHash`, `rulesHash`, `artifactHash`).
6. `execution_status` остаётся честным: `succeeded` означает, что анализ
   выполнился, а не что security verdict `pass`.
7. Финальный guard после графа сохранён только как защита от обхода policy или
   повреждения данных. При нормальном policy-gate он не срабатывает и не
   пересоздаёт отчёт.

Публикация получает `inconclusive` и проходит canonical inconclusive-ветку;
`expectedScopesVerified` и `partial` не подделываются.

## Фактические результаты

| Сценарий | execution | security verdict | canonical coverage | report | publication |
|---|---|---|---:|---|---|
| relaxed clean, до fix | succeeded | **pass** | partial | pass | **false** (SQL guard) |
| relaxed clean, после fix | succeeded | **inconclusive** | partial | inconclusive | **true** |
| strict clean | succeeded | inconclusive | partial | inconclusive | true |
| known findings (13: 8 critical, 4 high) | succeeded | **fail** | partial | fail | fail run not downgraded |

Для clean relaxed condition `equals pass` проверен живьём: `decision=false`,
`actualValue=inconclusive`; webhook body, report, run API и SSE `run_status`
показывают `inconclusive`. Known fail (13 находок) остаётся `fail` и в policy,
и в condition/report/webhook.

SQL narrow proof не менялся; на несравнимой паре он по-прежнему отвечает
контролируемым `422 SQL_PATTERN_REJECTED`, не `500`. Published/unpublished
инварианты остаются в storage/SQL слое.

## Ограничение

Canonical coverage теперь проверяет runtime-конфигурацию, method security и
архивы/бинарники по факту (`config_static`, `guards_static`,
`archives_static`), а `expectedScopesVerified` выставляется publish-путём.
Пока `expectedScopesVerified` не подтверждён, полный `pass` невозможен.
Это намеренно: лучше честный `inconclusive`, чем ложный `complete`. Для
будущего `pass` нужно подтвердить expected scopes, не меняя этот guard на
«всегда true».