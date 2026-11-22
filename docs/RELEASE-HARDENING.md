# Release hardening: DOCX-зависимость и изолированный e2e

**Ветка:** `SoloveyMaks/nedra-release-hardening`.
Зона: `deploy/**`, `scripts/nedra-e2e.sh`, `.env.example`, этот файл,
`docs/DEPLOYMENT.md`. Backend-код не менялся.

## 1. DOCX-зависимость закрыта (проверка, без правок)

| Звено | Состояние |
|---|---|
| `backend/requirements.txt` | `python-docx>=1.2,<2` — источник намерения |
| `deploy/requirements.txt` | `python-docx==1.2.0` + `lxml==6.1.3` (lock образа) |
| `deploy/Dockerfile` | stage `python-wheels` собирает wheels из `deploy/requirements.txt`, runtime ставит их `--no-index` → `python-docx` гарантированно в образе |
| `GET /api/runs/{id}/report/export.docx` | 200 + DOCX при установленной зависимости |
| Без зависимости | `build_docx` бросает `ReportExportUnavailable`, роутер отвечает **503** `REPORT_EXPORT_UNAVAILABLE` (`backend/app/api/report_exports.py`); JSONC/MD при этом продолжают работать |

Правка зависимостей не потребовалась: цепочка уже целая. Живое подтверждение
(прогон e2e, см. п. 3): `podman exec … python -c 'import docx'` → `1.2.0` в
образе, endpoint → 200, файл открывается `python-docx` того же образа.
Ветка 503 живьём не воспроизводилась (для этого нужен образ без зависимости);
путь «импорт падает → 503» проверен по коду.

## 2. `scripts/nedra-e2e.sh` — изолированный прогон

Запуск: `scripts/nedra-e2e.sh` (аргументов нет, `--help` печатает шапку).
Требования: `podman`, `curl`, `python3`, `git`, сеть для сборки.

Что делает, по шагам:

1. preflight: очистка только собственных остатков предыдущего прогона
   (проверка label `io.nedra.owner=nedra-rel-e2e`, чужие ресурсы не трогает);
2. своя сеть, два volume и свободный loopback-порт (выбирается bind'ом на
   `127.0.0.1:0`), генерация одноразовых паролей/session secret во временный
   каталог (режим 600, пароли не попадают в argv);
3. сборка образа `localhost/nedra-rel-e2e:local` из рабочего дерева
   (`deploy/Dockerfile`, `--build-arg NEDRA_REVISION=$(git rev-parse HEAD)`);
4. своя БД `nedra-rel-e2e-db` (pgvector, тот же digest, что и прод, host-портов
   нет — скрипт это проверяет) и свой backend `nedra-rel-e2e-app`
   (только `127.0.0.1:<свободный порт>:18080`, read-only rootfs, cap-drop=ALL);
5. миграции: сверка `count(*) public.schema_migrations` с числом файлов
   `db/migrations/*.up.sql` (13/13) и наличие `data_contract.runs`;
6. `POST /api/auth/register` (аккаунт + cookie `cg_session`);
7. импорт `examples/demo/sql-before-after/before.zip` через
   `POST /api/artifacts` (путь переопределяется `NEDRA_E2E_ZIP`);
8. `POST /api/workflows/wf_secure_pr_gate/runs` и опрос
   `GET /api/runs/{id}` до terminal-статуса (timeout 600s, переопределяется
   `NEDRA_E2E_RUN_TIMEOUT`); зелёный прогон требует `succeeded`;
9. проверки: `report/official.json` (JSON валиден), `report/export.jsonc`
   (schemaVersion `nedra-report-export/1`, `reportAvailable=true`),
   `report/export.md` (заголовок отчёта), `report/export.docx`
   (200, Content-Type Word, ZIP-целостность, открывается `python-docx` из
   образа), `security-map` (200, `identity.runId` и `map`);
10. по `trap EXIT/INT/TERM` — stop/rm обоих своих контейнеров, удаление своих
    volumes и сети (каждый шаг только после сверки owner-label), удаление
    временного каталога; при падении дополнительно печатаются хвосты логов.

Изоляция: скрипт упоминает только имена `nedra-rel-e2e-*`. Прод-контейнеры
`nedra-app`/`nedra-db`, сеть `nedra`, volumes `nedra-*-data`, `runtime.env`
и секреты хоста не читаются и не изменяются. Скрипт не вызывает
`nedra-up.sh`/`nedra-down.sh`. Порт БД не публикуется, публикация backend —
только loopback, поэтому после прогона не остаётся ни контейнеров, ни портов.
Остаётся только тег образа `localhost/nedra-rel-e2e:local` (кэш сборки).

## 3. Живой прогон (2026-11-22, эта машина)

Команда: `cd <worktree> && ./scripts/nedra-e2e.sh`.

| Прогон | Длительность | Результат |
|---|---|---|
| 1 | 22s | **упал** на старте БД: `Database is uninitialized and superuser password is not specified` — `podman run --env POSTGRES_PASSWORD` передаёт значение из окружения скрипта, а скрипт его не экспортировал. Исправлено: сгенерированные `POSTGRES_*` экспортируются (пароль по-прежнему не в argv). Cleanup отработал: свои контейнеры/volumes/сеть удалены |
| 2 | 24.5s | **зелёный** |
| 3 (финальный, на закоммиченном дереве `d1f5f7f69c`) | 38.2s | **зелёный**; после прогона контейнеров/volumes/сетей `nedra-rel-e2e-*` нет, порт свободен, `nedra-app`/`nedra-db` в исходном состоянии |

Детали зелёных прогонов: сборка образа 9–23s, БД healthy 6s, backend
healthy 2–3s, миграции 13/13, регистрация 0s, импорт `before.zip` 0s,
сценарий `wf_secure_pr_gate`, run завершается `succeeded` за 3s,
official.json/export.jsonc/export.md/export.docx — 200, DOCX (39 абзацев)
открыт `python-docx 1.2.0` из образа, security-map — 200. После прогона:
контейнеров/volumes/сетей `nedra-rel-e2e-*` нет, портов нет,
`nedra-app`/`nedra-db` продолжают работать в исходном состоянии
(`Up 5 hours (healthy)` / `Up 26 hours (healthy)`).

## 4. Границы проверки

- AI-провайдер в e2e не настроен: `ai_scan` уходит в `degraded`,
  `ai_explain` — в детерминированный fallback (штатное поведение без ключа).
  Реальные вызовы LLM, Telegram и tunnel e2e не проверяет.
- Прогон без `python-docx` (ветка 503) и restore/cutover — вне этого скрипта.
