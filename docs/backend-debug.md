# Backend debug audit

Дата: 2026-11-21. Ветка `task/postgres-runtime`, merge local `dev` `770f0a6`.
Кодовые fix-коммиты: `a886868530` (CRUD/SSE/cron), `084c90a196`
(next-fire/bound/pause-resume) и `1eb6721b92` (bounded executor,
mixed-engine terminal, no schedule overlap).
Проверка — read-only относительно исходников, на изолированной temporary
PostgreSQL и temporary workspace/data; ключи и DSN не выводились, реальные demo
данные не использовались. Все probes — `/tmp`, в repo тестовых файлов нет.

## Что исправлено

| # | Было | Стало | Где |
|---|---|---|---|
| 1 | `PUT /api/workflows/wf_absent/draft` → 200 и создавал workflow | 404 `{"detail":"Сценарий не найден"}` | `backend/app/api/router.py` (`update_workflow_draft`) |
| 2 | `GET /api/runs/<missing>/events` → 200 и вечный пустой SSE | 404 `{"detail":"Запуск не найден"}` | `backend/app/api/router.py` (`stream_run_events`) |
| 3 | `POST /api/schedules` принимал `not-a-cron` → 200; неизвестный workflow мог пройти/дать DB error | 400 `cron должен состоять из 5 полей`; 404 `Сценарий не найден`; проверка артефакта | `backend/app/api/router.py` (`create_schedule`) |
| 4 | Cron считался приблизительным `*/N`-парсером; прочие cron → 60s | `croniter` вычисляет next fire; `interval:N` — точные секунды | `backend/app/engine/scheduler.py` |
| 5 | Impossible cron `0 0 31 2 *` и огромный `interval` принимались; затем падали на `get_next`/`timedelta` | API проверяет фактический next fire; cron без даты → 400, interval вне `1..31536000` → 400, ничего не сохраняется | `backend/app/engine/scheduler.py`, `backend/app/api/router.py` |
| 6 | Не было способа остановить случайный interval-schedule | `PATCH /api/schedules/{id} {"enabled":bool}`: scoped pause/resume, unknown/foreign → 404; resume ставит `next_fire_at=NULL`; runs/history не удаляются | `backend/app/api/router.py`, `backend/app/storage/database.py` |
| 7 | Persisted mixed-engine draft: POST run 202, `_engine_for_draft` падал до validator/status running, run навсегда `queued` | engine resolution после validator; run терминально `failed/inconclusive`; wrapper `finally` освобождает team_b/cache | `backend/app/engine/executor.py` |
| 8 | `CG_MAX_CONCURRENT_RUNS` действовал только в router; scheduler вызывал executor напрямую и обходил лимит | один bounded entrypoint в executor для manual+schedule; router `_RUN_SEM` убран; scheduler не создаёт run того же schedule, пока активен предыдущий | `backend/app/engine/executor.py`, `backend/app/engine/scheduler.py`, `backend/app/api/router.py` |

Точный repro:

```text
PUT /api/workflows/wf_absent/draft  (валидный draft)
before: 200 {"workflow_id":"wf_missing","latest_revision_id":"rev_wf_missing_1"}
after:  404 {"detail":"Сценарий не найден"}

GET /api/runs/<random-uuid>/events
before: 200, stream висит без событий (httpx ReadTimeout)
after:  404 {"detail":"Запуск не найден"}

POST /api/schedules {"workflowId":..., "artifactId":..., "cronOrInterval":"not-a-cron"}
before: 200 {"status":"created"}
after:  400 {"detail":"cron должен состоять из 5 полей"}

POST /api/schedules ... "*/5 * * * *"
after:  200; scheduler next_fire_at = 2026-11-21T01:35:00+00:00 (minute%5=0)
POST /api/schedules ... "interval:1"
after:  200; run с trigger_type=schedule создан, execution_status=succeeded

POST /api/schedules ... "0 0 31 2 *"
after:  400 {"detail":"cron не имеет следующего срабатывания или некорректен"}

POST /api/schedules ... "interval:999999999999"
after:  400 {"detail":"interval должен быть от 1 до 31536000 секунд"}
# обе bad-записи в runtime.schedules отсутствуют

PATCH /api/schedules/<id> {"enabled": false}
after:  200, enabled=0; за 7 секунд новые schedule-runs не появились
PATCH /api/schedules/<id> {"enabled": true}
after:  200, enabled=1, next_fire_at=null; после тика scheduler новый run появился
PATCH /api/schedules/sch_absent {"enabled":false}
after:  404 Расписание не найдено

POST /api/workflows/<mixed>/runs (draft с team_b + backend scanners)
before: 202, run остаётся queued (background ValueError)
after:  202 -> run failed/inconclusive, не queued

CG_MAX_CONCURRENT_RUNS=1, slow local notify (4с):
schedule run: running; manual run: queued; оба terminal succeeded;
одновременно running >1 не наблюдался.
Долгий schedule interval:1: nonterminal runs одного schedule ≤1;
pause останавливает новые запуски.
```

## Матрица checked / fixed / unverified

| Область | Проверка | Результат |
|---|---|---|
| Workflow CRUD | create → get → save draft → revision меняется | PASS |
| Workflow CRUD | PUT unknown workflow | **FIXED** (404) |
| Validation | пустой граф | PASS: `valid=false`, «Граф пуст» |
| Validation | цикл | PASS: `valid=false`, «замкнутый цикл» |
| Validation | несуществующий порт | PASS: `valid=false`, порт отсутствует |
| Validation | unknown engine | PASS: `valid=false`, допустимы backend/team_b |
| Validation | mixed engine | PASS: `valid=false`, «не может смешивать engine» |
| Validation | duplicate node id / unknown NodeKind | PASS: duplicate error; unknown kind → 422 |
| Run | несуществующий artifact | PASS: 404 «Артефакт не найден» |
| Run | валидный run sample artifact | PASS: succeeded, fail, 13 findings |
| Run | повторный run того же artifact | PASS: отдельные runs, evidence 26/26 disjoint |
| Run | persisted mixed-engine draft | **FIXED**: 202 → failed/inconclusive, не queued |
| Run | bounded manual+schedule при `CG_MAX_CONCURRENT_RUNS=1` | PASS: schedule running, manual queued; >1 running не наблюдалось |
| Report | report JSON / official.json / readable.md | PASS: 200, valid JSON, `application/json`, `text/plain` |
| Report | отсутствующий report | PASS: 404 controlled |
| SSE | history order, resume after seq | PASS: seq возрастает, resume > after |
| SSE | live events + disconnect/reconnect | PASS: live seq ordered, reconnect работает |
| SSE | missing run | **FIXED** (был hang → 404) |
| Schedules | invalid cron / bad interval | **FIXED**: 400 |
| Schedules | impossible cron `0 0 31 2 *` | **FIXED**: 400, строка не создана |
| Schedules | huge interval `999999999999` | **FIXED**: 400 bound, строка не создана |
| Schedules | unknown workflow | **FIXED**: 404 |
| Schedules | valid cron next fire | PASS: `croniter`, next minute%5=0 |
| Schedules | interval:1 firing | PASS: run `trigger_type=schedule`, succeeded |
| Schedules | PATCH pause | PASS: enabled=0; 7с без новых runs |
| Schedules | PATCH resume | PASS: next_fire_at=null, следующий run после тика |
| Schedules | PATCH unknown/foreign | PASS: 404, чужие workspace не затронуты |
| Schedules | history on pause/resume | PASS: существующие runs/report остаются |
| Schedules | overlap/backlog одного slow schedule | PASS: active schedule run не дублируется; nonterminal ≤1 |
| Schedules | direct scheduler bypass лимита | **FIXED**: schedule run проходит через executor semaphore |
| Conditions | true (`equals fail`) | PASS: true branch, merge/report/notify |
| Conditions | false (`equals pass`) | PASS: false branch; AI node `skipped` |
| Conditions | regex `f.*` | PASS: true branch |
| Conditions | missing field (error) | PASS: condition failed, run failed/inconclusive |
| Branch | branch_merge при активной одной ветке | PASS: merge succeeded, inactive AI skipped |
| Notify | default disabled | PASS: delivery `skipped`, HTTP не отправлялся |
| Notify | unsafe metadata URL | PASS: delivery failed «запрещён», target пустой |
| Notify | local safe receiver (`127.0.0.1`, allowLocal) | PASS: delivery succeeded, receiver получил POST |
| AI | provider недоступен (local base URL) | PASS: report `ai_status=fallback`, внешних вызовов нет |
| Artifacts | bad ZIP / `../` / absolute / backslash traversal | PASS: 400 controlled, no rows/dirs outside data |
| Artifacts | all-text ZIP | PASS: accepted, 2 files/0 Kotlin; **strictCoverageRequired=true → succeeded/inconclusive/0**, strict=false → succeeded/pass/0. Ранее pass/0 был только strict=false; coverage bug не найден |
| Artifacts | >50 MB upload | PASS: 400 «Архив больше 50 МБ», new artifact row нет |
| Auth | public GET health/session без cookie | PASS |
| Auth | неверный login | PASS: 401 без cookie |
| Auth | logout без сессии | 200 idempotent — принято как допустимая семантика, не менялось |
| Finding IDs | одинаковый scanner `id` в двух runs | Не менялось: PK runtime scoped `(workspace,run,id)`; mixed UI state не наблюдал |
| Team B | adapter/engine execution `473234f` | UNVERIFIED: отдельный worker; в этом audit не запускался |
| Training | 6-finding training run | UNVERIFIED: не повторялся по указанию (уже проверен) |
| SQL proof | before/after demo | UNVERIFIED: не повторялся (дорого, уже проверен) |
| Frontend/main | static cache/белый экран | UNVERIFIED: другая зона/другой агент |

## Ограничения

- Изолированная БД/workspace удалены после проверки; секреты/DSN не печатались.
- Внешние webhook-отправки не выполнялись; проверялся только loopback-приёмник и
  блокировка unsafe URL.
- Публичное расписание/домен не включались.
- `docs/DEPLOYMENT.md`, frontend, `main.py` static cache, `integrations/team_b.py`
  и codefacts не менялись.