# Пользовательские проверки (custom checks)

Отдельный поток пользовательских проверок для workspace. Пользователь создаёт
проверку с именем, версией, Python-скриптом (или встроенным шаблоном) и
лимитами. Скрипт запускается только против принятого snapshot проекта, в
изолированном `bwrap`-процессе, и возвращает отдельные custom-факты. Он **не**
меняет canonical findings и не участвует в verdict.

## Что добавлено

- `backend/app/custom_checks/` — каталог шаблонов, DTO, sandbox, validation,
  PostgreSQL store.
- `backend/app/api/custom_checks.py` — DI-роутер `create_custom_checks_router`,
  смонтирован в `backend/app/api/router.py` (`/api/custom-checks`).
- `backend/app/contracts/{schemas,node_registry}.py` — `NodeKind.CUSTOM_CHECK` и
  `PortType.CUSTOM_FINDINGS`.
- `backend/app/engine/{validator,executor}.py` — исполнение custom-ноды в run и
  отдельная запись результата (`custom_checks.runs.workflow_run_id`).
- `db/migrations/013_custom_checks.up.sql` — schema `custom_checks` с
  definitions и runs.
- `frontend/src/api/customChecks.ts`, `frontend/src/components/CustomCheckModal.tsx`
  — типы, клиент и форма создания/проверки.
- `frontend/src/components/{CustomNode,InspectorPanel}.tsx` — отрисовка блока и
  выбор сохранённой проверки. `NodePalette.tsx` не менялся (у него отдельный
  владелец): элемент палитры лежит готовым в
  `frontend/src/components/customCheckPaletteItem.ts`.
- `deploy/Dockerfile` — `bubblewrap` + `util-linux` (`prlimit`) в runtime-образе.
- `docs/custom-checks.md` — этот контракт.

`App.tsx`, `ComparisonView`, `WorkflowCanvas`, `RepositorySourceView` и security
map не менялись: корневой мерж подключает модалку.

## Root wiring

Роутер уже смонтирован после auth/workspace middleware:

```python
from backend.app.api.custom_checks import create_custom_checks_router

api_router.include_router(create_custom_checks_router(db_repo))
```

Модалку можно смонтировать в основном приложении:

```tsx
<CustomCheckModal
  isOpen={customCheckOpen}
  onClose={() => setCustomCheckOpen(false)}
  onSaved={(definition) => {
    // Проверка уже сохранена и видна в InspectorPanel custom-ноды.
  }}
/>
```

`onSaved` вызывается только после успешного server-side create, который сам
повторно запускает validate на fixture. Автозапуска на проекте при сохранении
нет: run — отдельное ручное действие.

## Блок Custom Check в графе

Палитра → «Собственные анализаторы» → **Custom Check**. У блока один вход
`artifact` и один выход `custom_findings`. `config.definitionId` выбирается в
InspectorPanel из сохранённых проверок workspace; лимиты берутся из definition,
а не из графа.

- Валидатор требует подключённый артефакт и непустой `definitionId`.
- Executor запускает проверку в отдельном потоке (`run_in_executor` с
  `cancel_event` и копией contextvars), пишет `custom_checks.runs` с
  `workflow_run_id` и **не** добавляет находки в `FindingsBundle`, policy,
  report и `runtime.findings`.
- Успешная нода отдаёт выход `custom_findings` со `status`, `summary`,
  `findings`, `diagnostics`. Статус кроме `succeeded` (timeout, output_limit,
  contract_error, failed) — это провал ноды с явной причиной, а не «пустой
  success».
- Если sandbox недоступен, нода падает с `Custom Check: sandbox недоступен`.
  Host-fallback отсутствует.

Custom-факты run доступны отдельно:

```
GET /api/custom-checks/runs/{workflow_run_id}/findings
```

## Поддерживаемый runner

Сейчас поддержан ровно один вариант:

- язык: **Python 3**;
- runner: **`bwrap-python3`**;
- исполняемый файл: `/usr/bin/python3` или `/usr/local/bin/python3` (первый
  существующий), фиксированный argv;
- пользовательский executable не принимается; произвольный runner закрыт
  allowlist-ом.

`GET /api/custom-checks/status` возвращает `available` и причину. Проверка
доступности — один кэшированный probe: `bwrap` + `prlimit` + интерпретатор
поднимают изолированный Python. Если probe не проходит, `validate`/`create`/`run`
отвечают `503 CUSTOM_SANDBOX_UNAVAILABLE`; код пользователя не запускается.

## Контракт входа

Скрипт получает read-only `/input/input.json`:

```json
{
  "schemaVersion": "custom-check-input/1",
  "projectRoot": "/project",
  "artifact": {
    "id": "art_...",
    "filename": "project.zip",
    "fileCount": 42,
    "hashSha256": "..."
  },
  "limits": {
    "timeoutSeconds": 3,
    "memoryMb": 256,
    "maxOutputBytes": 16384
  }
}
```

`/project` — read-only mount принятого snapshot. Писать можно только в
`/work` (tmpfs 1 MiB), и этот каталог уничтожается после запуска.

## Контракт выхода

stdout обязан быть одним JSON-объектом:

```json
{
  "schemaVersion": "custom-check-result/1",
  "summary": "Короткая сводка",
  "findings": [
    {
      "ruleId": "MY-RULE-1",
      "severity": "info",
      "message": "Что найдено",
      "path": "src/main/kotlin/A.kt",
      "startLine": 10,
      "endLine": 10
    }
  ]
}
```

Правила:

- `findings` — список, не больше 50;
- `path` — относительный путь внутри snapshot, без `..`, абсолютных путей,
  symlink и выхода за проект;
- `startLine`/`endLine` — положительные целые, в пределах файла;
- `severity` — `info|low|medium|high|critical`;
- `ruleId` — `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`;
- `message` — до 500 символов; вывод дополнительно проходит redaction.

Любое нарушение контракта — `contract_error`. Nonzero exit — `failed`.
Timeout — `timeout`. Превышение вывода — `output_limit`. Ни один из этих
случаев не становится `succeeded`/пустым success.

## Изоляция и лимиты

Для каждого запуска:

- `bwrap --unshare-all --die-with-parent`: новые namespaces, включая network и
  PID; сеть проверена попыткой TCP-подключения — `OSError`; `/etc/passwd` не
  виден;
- `--clearenv` и фиксированные `PATH/LANG/LC_ALL/PYTHONIOENCODING`; host env
  и секреты не наследуются. В проверке env содержит только эти служебные
  значения плюс `LD_LIBRARY_PATH` для distro python;
- read-only bind: `/usr`, `/usr/local`, `/lib`, `/lib64`, `/bin`, `/sbin`
  (только существующие пути), `/input`, `/project`. `/proc` намеренно не
  монтируется: внутри rootless podman он недоступен, а проверке не нужен;
- `/dev` — tmpfs на 64 KiB с `ro-bind` только `/dev/null` и `/dev/urandom`;
  `/dev/shm` и `/tmp` отсутствуют. `/work` — tmpfs на 1 MiB, cwd; после
  запуска удаляется. Общий объём writable scratch ≤ 1 MiB + 64 KiB;
- внутренний `prlimit --nproc=1` делает дерево процессов ровно одной задачей:
  `os.fork`, потоки и `subprocess` падают с `EAGAIN`. Поэтому
  `--as/--cpu/--fsize` относятся ко всему дереву, а не к каждому ребёнку;
- `--nofile=16` и `--fsize=1MiB` ограничивают page cache файлов; `memfd_create`
  и SysV shared memory (`shmget/shmat/shmctl/shmdt`) запрещены classic-BPF
  seccomp-фильтром (иначе shmem шёл бы мимо `RLIMIT_AS` и `RLIMIT_FSIZE`);
- `prlimit --as=<memoryMb> --cpu=<timeout+1> --fsize=1MiB --nofile=16` стоит
  двумя слоями: на bwrap и на самом интерпретаторе;
- wall-clock timeout и отмена владельцем убивают process group (`killpg`);
  PID namespace добивает оставшихся детей при teardown bwrap; поток чтения
  дожидается `wait()`;
- stdout и stderr делят один бюджет `maxOutputBytes`; превышение убивает
  process group и даёт `output_limit`;
- symlink в snapshot отклоняется до запуска;
- скрипт ограничен 64 KiB, NUL запрещён; путь скрипта фиксированный, не из
  пользовательского имени файла;
- `shell=True` нигде не используется; argv — allowlist, без пользовательских
  аргументов.

Итоговый потолок одного check: `memoryMb` на единственную задачу (AS), плюс
ограниченный page cache (≤ ~15 файлов × 1 MiB) и ≤ 1.06 MiB tmpfs-скретча.
cgroup-лимитов внутри контейнера нет: `/sys/fs/cgroup` смонтирован read-only.
Поэтому агрегат держится структурно — одним процессом и запретом shmem, а не
cgroup-ом. Если проверке нужны дочерние процессы или shared memory, она не
поддерживается: это осознанное ограничение, а не скрытый fallback.

Доступность раннера — probe, который реально проверяет каждую границу:
`fork`/threads/subprocess → `EAGAIN`, `memfd_create` → `EPERM`, `shmget` → -1,
`/dev/shm` и `/tmp` отсутствуют, `/work` writable, env чистый, 80 MiB при
64 MiB AS → `MemoryError`. Если probe не проходит, `validate`/`create`/`run`
отвечают `503 CUSTOM_SANDBOX_UNAVAILABLE`; код не запускается.

### Отмена

Executor запускает check в отдельном потоке и при отмене задачи (shutdown,
потеря lease, явный stop) делает по порядку: выставляет `cancel_event` →
помечает run `cancelled` → ждёт завершения потока (≤5 c) → пробрасывает
`CancelledError`. Sandbox по событию убивает process group, reader делает
`wait()`. Узел получает terminal-запись `cancelled`, run — `cancelled`.

Поздняя запись успеха после отмены запрещена дважды: сервис проверяет
`cancel_event` перед сохранением, а `custom_checks.runs` для workflow-run
вставляется только если `runtime.runs.execution_status = 'running'`. Иначе
строка не пишется и сервис бросает `SandboxCancelled`. Manual API-run
(`workflow_run_id = NULL`) этим guard-ом не ограничен.

Snapshot проверяется через CodeFacts `manifestHash` и `artifactHash`; если
каталог изменился после приёма, run отклоняется (`409`, snapshot changed).
Первый run для старого артефакта может зафиксировать snapshot через
`ensure_artifact_meta`; последующие изменения уже ловятся.

### Docker/подовый runtime

`deploy/Dockerfile` ставит `bubblewrap` и `util-linux`. Проверено на реальном
runtime (rootless podman, `--read-only`, `--tmpfs /tmp`, `--cap-drop=ALL`,
`--security-opt no-new-privileges`, non-root `nedra`): bwrap поднимает
вложенные namespaces, сеть закрыта, `/etc/passwd` отсутствует, проект read-only,
`/work` — tmpfs; probe availability = `true`. Дистрибутивный python в
`/usr/local` требует `LD_LIBRARY_PATH=/usr/local/lib` (host ld cache внутрь не
пробрасывается) — sandbox выставляет его сам.

Если в другом окружении (например, Docker с seccomp, режущим `unshare`) probe
падает, API честно отвечает `503 CUSTOM_SANDBOX_UNAVAILABLE` и ничего не
исполняет. Притворяться, что проверка отработала, запрещено.

## API

Все маршруты требуют сессию; workspace берётся из неё.

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/custom-checks` | список включённых проверок workspace |
| `GET` | `/custom-checks/templates` | allowlist встроенных шаблонов |
| `GET` | `/custom-checks/status` | доступность bwrap-раннера и причина |
| `POST` | `/custom-checks/preview` | статическая нормализация, без запуска и записи |
| `POST` | `/custom-checks/validate` | запуск на серверной fixture; возвращает `valid`, stdout/status/limits |
| `POST` | `/custom-checks` | validate + сохранение; невалидная проверка не сохраняется |
| `POST` | `/custom-checks/{id}/run` | ручной запуск на `artifactId` текущего workspace |
| `GET` | `/custom-checks/runs/{run_id}/findings` | custom-факты одного workflow run |

`preview` не сохраняет и не запускает. `validate` не сохраняет. `create`
запускает fixture и сохраняет только при успешном контракте. `run` сохраняет
результат в `custom_checks.runs`, но не пишет `runtime.findings`.

Тело create/validate/preview:

```json
{
  "name": "TODO в Kotlin",
  "version": "1.0.0",
  "language": "python",
  "runner": "bwrap-python3",
  "sourceKind": "template",
  "templateId": "todo_scan",
  "script": "",
  "timeoutSeconds": 3,
  "memoryMb": 256,
  "maxOutputBytes": 16384
}
```

Для своего скрипта: `sourceKind="script"`, `templateId=null`, `script` — текст.
Лимиты: timeout `1..10`, memory `64..512` MB, output `1024..65536` bytes.

`run` тело: `{"artifactId": "..."}`. Результат содержит `status`, `exitCode`,
`durationMs`, `stdout`/`stderr` (redacted), `findings`, `diagnostics`, `limits`,
`workflowRunId`, `countsAsFindings=false`, `origin="custom_check"`.

## UI

`CustomCheckModal`: поля имени/версии/языка/runner/шаблона или скрипта/лимитов;
«Проверить описание» — preview; «Проверить контракт в sandbox» — validate на
fixture; «Добавить в палитру после проверки» — disabled, пока `valid !== true`.
Показывает статус, exit code, длительность, лимиты, redacted stdout/stderr,
diagnostics и число custom-фактов. Автозапуска на проекте нет.

`NodePalette` получает элемент **Custom Check** из
`customCheckPaletteItem.ts` (root вставляет `CUSTOM_CHECK_PALETTE_ITEM` в
категорию «Собственные анализаторы» после merge, файл палитры не конфликтует с
другим worker-ом). `InspectorPanel` грузит список сохранённых проверок и
показывает предупреждение, если `GET /custom-checks/status` вернул
`available=false`.

## Live proof

Проверено в контейнере, повторяющем production-flags
(`--read-only --tmpfs /tmp --cap-drop=ALL --security-opt no-new-privileges`,
non-root, disposable PostgreSQL, миграции 001..013):

- probe bwrap: `available=true` внутри такого контейнера; probe сам проверяет
  fork/threads/subprocess, `memfd_create`, `shmget`, отсутствие `/dev/shm`,
  `/work`, env и memory-лимит;
- env внутри sandbox: только `PATH/LANG/LC_ALL/LD_LIBRARY_PATH/PWD/
  PYTHONIOENCODING/PYTHONDONTWRITEBYTECODE`; host env canary не виден;
  `/etc/passwd` → отсутствует; TCP на listener в host-loopback → `OSError`;
  запись в `/project` → denied; cwd `/work`;
- `os.fork` в цикле на 3 ребёнка с `bytearray(40 MiB)` → первый же fork
  `EAGAIN` (11), детей нет, 120 MiB не выделены; `bytearray(256 MiB)` при
  `memoryMb=64` → `MemoryError`;
- `memfd_create` → `EPERM`; `/dev/shm` и `/tmp` отсутствуют; 2 MiB в `/dev` →
  `ENOSPC`; 2 MiB в `/work` → `EFBIG` (RLIMIT_FSIZE); stdout 800 B + stderr
  800 B при лимите 1024 → `output_limit` с общим бюджетом;
- symlink в snapshot → отказ до запуска;
- `validate` шаблона `todo_scan` → `valid=true`, 1 finding на fixture;
- `run` на артефакте → `succeeded`, 1 finding, `limits` содержит
  `maxTasks=1`, `workScratchBytes=1048576`, `devScratchBytes=65536`;
- `severity=[]`, NUL в `path`, `startLine=true` → `contract_error` и на
  validate, и на реальном run; строка run сохраняется, HTTP отвечает 200, не
  500;
- отмена: во время sandbox-run (3 процесса) `task.cancel()` → process tree
  исчезает, отмена укладывается в 0.05 c вместо 6 c скрипта, run становится
  `cancelled`, custom-нода получает terminal `cancelled`, поздних
  `custom_checks.runs` нет, прямой `save_run` с отменённым run возвращает
  `False`;
- `GET /custom-checks/runs/{id}/findings` для отменённого run → `[]`;
  manual `POST /{id}/run` после этого по-прежнему `succeeded`;
- аварийный probe (bwrap «отсутствует») → `GET /status available=false`,
  `validate`/`run` → `503 CUSTOM_SANDBOX_UNAVAILABLE`;
- router смонтирован в реальном `api_router`: `GET /api/custom-checks/status`
  → 200;
- `npm run build` (tsc + vite) проходит.

## Ограничения

- Только Python + bwrap allowlist; нет произвольных executable/языков.
- Check обязан быть однопроцессным: `fork`, потоки, `subprocess` и shared
  memory запрещены. Многопоточные/многопроцессные проверки не поддерживаются.
- Агрегат памяти держится `nproc=1` + `RLIMIT_AS`, а не cgroup-ом: cgroup fs в
  контейнере read-only. Потолок честный для одной задачи, плюс ограниченный
  page cache и tmpfs; без `nproc=1` per-process лимиты агрегатом не были бы.
- Если probe не может подтвердить seccomp/лимиты/mounts (например, чужой
  seccomp-профиль режет `unshare`), раннер честно `unavailable`; host-fallback
  отсутствует.
- Встроенный fixture — минимальный Kotlin-проект; он проверяет контракт, а не
  качество анализа.
- `last_validated_at` фиксируется при create; изменение source через прямой SQL
  потребует повторной validate.
- Custom-факты не отображаются в `FindingsView`/security map: их читает
  InspectorPanel/API отдельным потоком. Это осознанно, чтобы не смешивать с
  canonical findings.
- Custom-нода не участвует в verdict, но её провал (timeout/contract_error/
  sandbox unavailable) помечает execution status run как `failed` — молча
  пропускать сломанную проверку нельзя. Отмена помечает и run, и ноду
  `cancelled`.


## Подключение в приложении (22.11.2026)

Реализация объединена с текущим приложением. Кнопка «Создать свой блок»
смонтирована в редакторах пользователя и администратора. После проверки
контракта и сохранения новая versioned definition появляется в «Мои проверки»
и сразу добавляется к проекту в текущем draft с `definitionId`; сохранение
самого сценария остаётся явным действием. После reload definition и граф
сохраняются. Изменение формы отменяет предыдущий validate; поздние ответы
после закрытия/смены сценария не добавляют блок.

`LiveRunView` показывает свою проверку как отдельный этап; «Проблемы» содержит
отдельную секцию результатов своих проверок. Они не смешаны с 15 штатными
findings, AI-гипотезами или security verdict. Проверено в браузере на реальном
run, desktop и 390×844: ширина страницы 390, диалог 366, page errors 0.

Временная PostgreSQL: два account workspace, custom definition create 201,
validate=true и 1 факт на встроенной fixture; настоящий пользовательский ZIP
с TODO → succeeded и 1 custom-факт. Чужая definition/run → 404 и пустой список.
Невалидный stdout → contract_error, бесконечный цикл → timeout, без ложного
успеха. Full workflow из 13 этапов отработал, включая три скриптовых сканера,
AI_SCAN и custom_check. Production не менялся; требуется runtime с bwrap и
миграцией 013 из объединённой версии.
