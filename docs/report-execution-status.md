# Report execution status consistency

Дата: 2026-11-21. Ветка `task/postgres-runtime`, local dev `9143178`.

## Проблема

`reports/builder.py` жёстко писал `metadata.executionStatus = "succeeded"` и не
показывал execution status в readable-отчёте. `Build Report` выполняется в
графе до поздних узлов, поэтому при последующем падении condition отчёт
показывал успешное выполнение, хотя run завершался `failed`.

Дополнительно condition-failure делал ранний `return` из executor: отчёт
оставался в состоянии `running`, финализация/publication не выполнялись, а
`security_verdict` принудительно менялся на `inconclusive`.

## Правило

- `executionStatus` — фактический статус выполнения run
  (`pending`/`running`/`succeeded`/`failed`), не security verdict.
- `verdict` и passport — отдельный security verdict policy/coverage.
- Пока Build Report выполняется в run, отчёт сохраняется с фактическим
  `running`; после финализации отчёт один раз пересобирается с терминальным
  статусом.
- Пересобираются читаемый markdown, official JSON, passport/verdict и
  policy-reasons; не только official metadata.
- Condition error больше не делает ранний `return`: run доходит до финализации,
  report получает `failed`, publication/SSE финализируются.
- Notify failure остаётся `DeliveryResult failed` и **не** считается analysis
  failure: execution status остаётся `succeeded`, отчёт не становится failed.

Изменены `backend/app/reports/builder.py`, `backend/app/engine/executor.py` и
этот документ. Storage, router, frontend и masking не менялись; completeness
score не перепридумывался.

## Живой проверочный сценарий

Temp PostgreSQL/runtime, clean Kotlin project:

| Сценарий | run execution | security verdict | report executionStatus | report verdict |
|---|---|---|---|---|
| normal clean | succeeded | inconclusive | succeeded | inconclusive |
| report → late failing condition | **failed** | inconclusive | **failed** | inconclusive |
| slow notify timeout | **succeeded** | inconclusive | **succeeded** | inconclusive |
| known findings (13: 8 critical, 4 high) | succeeded | **fail** | succeeded | **fail** |

Для late-failing condition order узлов был `node_report` → `node_late_fail`;
report уже существовал, condition затем падал. API run: `execution_status=failed`,
`security_verdict=inconclusive`; official metadata: `executionStatus=failed`;
readable: `Статус выполнения: failed`; passport/report verdict остались
`inconclusive`. В node_runs: `node_report=succeeded`, `node_late_fail=failed`.

Slow notify: во время отправки отчёт читался с `executionStatus=running`; после
таймаута delivery=`failed`, но run/report остались `succeeded`, потому что
notify не является analysis failure.

No report при падении до Build Report не создаётся искусственно: API отдаёт
404, как и раньше.