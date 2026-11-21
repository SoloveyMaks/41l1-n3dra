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

Только `engine/executor.py` (документация — этот файл). Canonical coverage,
scanner statuses, SQL migrations, storage и public DTO не менялись.

Перед финализацией run:

1. Policy gate и condition продолжают работать как раньше; при relaxed policy
   результат policy может остаться `pass`.
2. Если policy verdict `pass`, проверяется canonical artifact:
   `coverage.status == "complete"` **и** `provenance.expectedScopesVerified is true`.
3. Если это не подтверждено, итоговый `security_verdict` становится
   `inconclusive`; в reasons отчёта добавляется:
   `Canonical coverage неполное: status=partial, expectedScopesVerified=false;
   PASS не подтверждён`.
4. Сохранённый report (если report-node был) пересобирается с финальным
   verdict, чтобы `run`, `report`, passport и official JSON не расходились.
5. `execution_status` остаётся честным: `succeeded` означает, что анализ
   выполнился, а не что security verdict `pass`.
6. `fail` не понижается: известные критичные/высокие находки остаются `fail`.

Публикация теперь получает финальный `inconclusive` и проходит canonical
inconclusive-ветку; `expectedScopesVerified` и `partial` не подделываются.

## Фактические результаты

| Сценарий | execution | security verdict | canonical coverage | report | publication |
|---|---|---|---:|---|---|
| relaxed clean, до fix | succeeded | **pass** | partial | pass | **false** (SQL guard) |
| relaxed clean, после fix | succeeded | **inconclusive** | partial | inconclusive | **true** |
| strict clean | succeeded | inconclusive | partial | inconclusive | true |
| known findings (13: 8 critical, 4 high) | succeeded | **fail** | partial | fail | fail run not downgraded |

Policy-event внутри run может показывать `pass`: это отдельный результат
policy gate, а не финальный security verdict. Пользовательские run/report/API
показывают `inconclusive`.

SQL narrow proof не менялся; на несравнимой паре он по-прежнему отвечает
контролируемым `422 SQL_PATTERN_REJECTED`, не `500`. Published/unpublished
инварианты остаются в storage/SQL слое.

## Ограничение

Пока canonical coverage не покрывает runtime-конфигурацию, runtime-guards и
бинарники, а `expectedScopesVerified` не выставлен доверенным publish-путём,
полный `pass` невозможен. Это намеренно: лучше честный `inconclusive`, чем
ложный `complete`. Для будущего `pass` нужно реально закрыть эти scopes и
подтвердить expected scopes, не меняя этот guard на «всегда true».