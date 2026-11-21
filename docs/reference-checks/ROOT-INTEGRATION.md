# Root integration: reference checks

Фича смонтирована в глобальный `api_router` через
`backend/app/api/reference_checks_mount.py`; `main.py` не менялся. Ниже — точный
контракт, который владелец корня уже применяет и который можно заменить
инъекцией своих loaders.

## Backend mount

`backend/app/api/router.py` содержит ровно этот вызов после остальных
`include_router`:

```python
from backend.app.api.reference_checks_mount import mount_reference_checks

# Defaults: серверный реестр, canonical loaders, require_workspace_actor.
mount_reference_checks(api_router)
```

Эквивалент без helper:

```python
from backend.app.api.reference_checks import create_reference_checks_router

api_router.include_router(create_reference_checks_router())
```

Получится `POST /api/reference-checks/compare` и
`GET /api/reference-checks/references`.

Тело запроса — только серверные id:

```json
{
  "referenceManifestId": "reference-suite",
  "targetRunId": "run_ab12cd34",
  "targetArtifactId": "art_9f00"
}
```

`targetArtifactId` необязателен; если передан, обязан совпадать с артефактом
запуска. Поля `manifest`, `bundle`, `targetSha`, `targetBranch`,
`targetProjectId` отклоняются с `422` (`extra_forbidden`).

Workspace по умолчанию берётся из `require_workspace_actor(request)`, то есть из
уже существующей сессии. Root может заменить резолвер:

```python
mount_reference_checks(
    api_router,
    resolve_workspace=lambda request: my_session(request)["workspaceId"],
)
```

## GET /api/reference-checks/references

Список эталонов для UI. Тот же workspace-guard, что у compare. Ответ — массив
дескрипторов:

```json
[
  {
    "id": "reference-suite",
    "label": "orders-reference-suite",
    "projectId": "reference-checks/orders-service",
    "branch": "SoloveyMaks/reference-branch-checks",
    "sha": "ae1fd4805c3ce8cfc533be99a601b0e2d3aa95a1"
  }
]
```

`id` — это `referenceManifestId` для compare; `label` — `suiteId` из манифеста;
`projectId`/`branch`/`sha` — только из валидированного серверного манифеста
(`parse_manifest`), SHA не выдумывается. Bundle, provenance, fixture paths и
любые файловые пути наружу не отдаются.

Перечисление безопасное: только строгие `<id>.json` с id по `_ID_RE`,
`*.reference.json` (sidecar) пропускается, каталоги и мусорные имена — тоже.
Битый/нечитаемый манифест — честная `503 REFERENCE_UNREADABLE`, а не молча
укороченный список; невалидный манифест — `422`. Ветка без манифестов даёт
пустой массив.

## Loader hooks

Все зависимости инжектируются. Сигнатуры — часть контракта:

```python
from backend.app.reference_checks import (
    ReferenceSuite, ReferenceSuiteRegistry, ServerSnapshot,
)
from backend.app.reference_checks.canonical import (
    load_canonical_snapshot, load_reference_snapshot,
)

# 1. target loaders
def my_target_loader(
    workspace: str, run_id: str, artifact_id: str | None
) -> ServerSnapshot: ...

# 2. reference loader
def my_reference_loader(
    workspace: str,
    suite: ReferenceSuite,
    registry: ReferenceSuiteRegistry,
    manifest_id: str,
) -> ServerSnapshot | None: ...

mount_reference_checks(
    api_router,
    registry=ReferenceSuiteRegistry("/srv/reference-suites"),
    load_target_snapshot=my_target_loader,
    load_reference_snapshot=my_reference_loader,
)
```

Defaults (если hook не передан):

- `load_target_snapshot` → `backend.app.reference_checks.canonical.load_canonical_snapshot`
  - workspace-scoped `DatabaseRepository.get_run/get_artifact`;
  - `CodeFactsStore` run/artifact meta для snapshot id;
  - `get_published_envelope(...)` из canonical storage;
  - сверка `identity.workspaceId/runId/snapshotId/artifactHash/snapshotManifestHash`;
  - source commit/branch из `runtime.repository_watch_commits` и
    `runtime.repository_hook_deliveries`, плюс artifact meta.
- `load_reference_snapshot` → `...canonical.load_reference_snapshot`
  - сначала `<manifestId>.reference.json` из реестра;
  - иначе `reference.runId` + `reference.snapshotId` через тот же canonical loader.
- `registry` → `ReferenceSuiteRegistry()` читает `CG_REFERENCE_SUITES_DIR`,
  по умолчанию `docs/reference-checks/fixtures`.

Если canonical provenance не содержит commit/branch/хэш, hook возвращает
`ServerSnapshot` с `None` в этом поле. Компаратор сделает `inconclusive` с
причиной; фабриковать commit нельзя.

## Формат registry

```text
$CG_REFERENCE_SUITES_DIR/
  reference-suite.json
  reference-suite.reference.json   # optional frozen reference snapshot
```

`reference-suite.json` — `reference-suite/v1` (см. README).

`reference-suite.reference.json`:

```json
{
  "sourceCommit": "<40-char commit>",
  "sourceBranch": "<branch>",
  "sourceExportSha256": "<optional sha256 of the raw exported bundle file>",
  "bundle": { "schemaVersion": "0.1", "...": "..." }
}
```

`bundleContentSha256` сервер считает из `bundle`; манифест должен
закреплять то же значение.

## Ответ

`reference-report/v2`. Важные поля:

- `status`: `pass | fail | inconclusive`;
- `provenance.mode`: `server-canonical`;
- `provenance.attestation`: `canonical-published` только если подтверждены и
  reference, и target; иначе `none`;
- `provenance.reasons`: почему provenance не подтверждён;
- `provenance.reference/target`: server-owned source, commitSha, branch, hashes;
- `target.sha`: source commit из provenance, не из запроса;
- `compatibility.coverageReasons`: failed/unavailable сканеры;
- `compatibility.coverageWarnings`: partial, не блокирующий проверенные scopes.

`422` остаётся для невалидного id/манифеста, `404` — для чужого/неизвестного
run/artifact, `409` — для неопубликованного или неполного canonical envelope.
Несовместимая подпись и потеря покрытия — это вердикт `inconclusive`, а не
HTTP-ошибка.

## Frontend

Компонент standalone: `frontend/src/components/reference-checks/ReferenceChecksPanel.tsx`.
Он не грузит bundle сам и не отправляет SHA. Root передаёт только серверные
дескрипторы:

```tsx
import { ReferenceChecksPanel } from './components/reference-checks/ReferenceChecksPanel';
import type { PinnedReference, PinnedTarget } from './components/reference-checks/types';

const references: PinnedReference[] = [
  {
    id: 'reference-suite',          // referenceManifestId для сервера
    label: 'Orders reference suite',
    projectId: 'reference-checks/orders-service',
    branch: 'SoloveyMaks/reference-branch-checks',
    sha: '<suite commit sha>',      // только подпись в UI; сервер проверит сам
  },
];

const targets: PinnedTarget[] = runs.map((run) => ({
  id: run.id,
  label: run.id,
  runId: run.id,                    // серверный id запуска
  artifactId: run.artifact_id ?? undefined,
}));

<ReferenceChecksPanel references={references} targets={targets} />
```

`compareUrl` можно переопределить, если роутер смонтирован под другим префиксом;
по умолчанию `${API_BASE}/reference-checks/compare`.

Компонент:
- отправляет `{ referenceManifestId, targetRunId, targetArtifactId }`;
- при смене эталона/цели инкрементит epoch, абортит in-flight запрос и сразу
  выбрасывает старый отчёт;
- поздний ответ A после выбора B не применяется (`epoch !== current`);
- показывает `pass` только из ответа сервера и отдельно рисует
  `attestation: none`, если provenance не подтверждён.
