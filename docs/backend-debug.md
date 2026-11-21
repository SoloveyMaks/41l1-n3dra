# Backend debug audit

Дата: 2026-11-21. Ветка `task/postgres-runtime`, merge local `dev` `770f0a6`.
Кодовый fix: `a886868530` **«Проект: исправить CRUD, SSE и cron-расписания»**.
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
| Report | report JSON / official.json / readable.md | PASS: 200, valid JSON, `application/json`, `text/plain` |
| Report | отсутствующий report | PASS: 404 controlled |
| SSE | history order, resume after seq | PASS: seq возрастает, resume > after |
| SSE | live events + disconnect/reconnect | PASS: live seq ordered, reconnect работает |
| SSE | missing run | **FIXED** (был hang → 404) |
| Schedules | invalid cron / bad interval | **FIXED**: 400 |
| Schedules | unknown workflow | **FIXED**: 404 |
| Schedules | valid cron next fire | PASS: `croniter`, next minute%5=0 |
| Schedules | interval:1 firing | PASS: run `trigger_type=schedule`, succeeded |
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
| Artifacts | all-text ZIP | PASS: accepted, 2 files/0 Kotlin; run succeeded/pass/0 findings |
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