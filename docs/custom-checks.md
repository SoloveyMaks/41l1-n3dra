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
- Executor запускает проверку в отдельном потоке (`asyncio.to_thread`), пишет
  `custom_checks.runs` с `workflow_run_id` и **не** добавляет находки в
  `FindingsBundle`, policy, report и `runtime.findings`.
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

## Изоляция

Для каждого запуска:

- `bwrap --unshare-all --die-with-parent`: новые namespaces, включая network;
  сеть проверена попыткой TCP-подключения — `OSError`; `/etc/passwd` не виден;
- `--clearenv` и фиксированные `PATH/LANG/LC_ALL/PYTHONIOENCODING`; host env
  и секреты не наследуются. В проверке env содержит только эти служебные
  значения плюс `LD_LIBRARY_PATH` для distro python;
- read-only bind: `/usr`, `/usr/local`, `/lib`, `/lib64`, `/bin`, `/sbin`
  (только существующие пути), `/input`, `/project`; `--dev /dev` — минимальный
  devtmpfs. `/proc` намеренно не монтируется: внутри rootless podman он
  недоступен, а проверке не нужен;
- `/work` — tmpfs на 1 MiB, cwd; после запуска удаляется;
- `prlimit`: address space, CPU seconds, file size, file descriptors;
- wall-clock timeout убивает process group;
- stdout/stderr читаются порциями с жёстким лимитом; превышение убивает
  process group и даёт `output_limit`;
- symlink в snapshot отклоняется до запуска;
- скрипт ограничен 64 KiB, NUL запрещён; путь скрипта фиксированный, не из
  пользовательского имени файла;
- `shell=True` нигде не используется; argv — allowlist, без пользовательских
  аргументов.

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

- probe bwrap: `available=true` внутри такого контейнера;
- env внутри sandbox: только `PATH/LANG/LC_ALL/LD_LIBRARY_PATH/PWD/
  PYTHONIOENCODING/PYTHONDONTWRITEBYTECODE`; `/etc/passwd` → отсутствует;
  TCP 1.1.1.1:53 → `OSError`; запись в `/project` → denied; cwd `/work`;
- symlink в snapshot → отказ до запуска;
- `validate` шаблона `todo_scan` → `valid=true`, 1 finding на fixture;
- `create` + `run` на `art_cc_live` → `succeeded`, 1 finding с
  `path=src/main/kotlin/Sample.kt`, `countsAsFindings=false`;
- timeout 1s → `timeout` (`valid=false`); `print('not json')` →
  `contract_error`; 100000 байт при лимите 1024 → `output_limit`;
- executor: `manual_trigger → project_input → custom_check` → node
  `succeeded`, `custom_checks.runs.workflow_run_id` заполнен,
  `GET /runs/{id}/findings` отдаёт 1 custom-факт, `runtime.findings` = 0;
- аварийный probe (bwrap «отсутствует») → `GET /status available=false`,
  `validate`/`run` → `503 CUSTOM_SANDBOX_UNAVAILABLE`, код не исполнялся;
- router смонтирован в реальном `api_router`: `GET /api/custom-checks/status`
  → 200;
- `npm run build` (tsc + vite) проходит.

## Ограничения

- Только Python + bwrap allowlist; нет произвольных executable/языков.
- Процессный лимит `RLIMIT_NPROC` не выставляется: в текущем окружении он
  ломает создание user namespace у bwrap. CPU/time/memory/output/file-descriptor
  лимиты выставлены; PID namespace и timeout ограничивают lifetime.
- Встроенный fixture — минимальный Kotlin-проект; он проверяет контракт, а не
  качество анализа.
- `last_validated_at` фиксируется при create; изменение source через прямой SQL
  потребует повторной validate.
- Custom-факты не отображаются в `FindingsView`/security map: их читает
  InspectorPanel/API отдельным потоком. Это осознанно, чтобы не смешивать с
  canonical findings.
- Custom-нода не участвует в verdict, но её провал (timeout/contract_error/
  sandbox unavailable) помечает execution status run как `failed` — молча
  пропускать сломанную проверку нельзя.