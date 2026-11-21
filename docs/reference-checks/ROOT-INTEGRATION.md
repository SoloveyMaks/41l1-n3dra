# Root integration: reference checks

Своя фича живёт отдельно. Root не переписывается: ниже два куска, которые
владелец корня вставляет сам.

## Backend

`backend/app/api/router.py` уже содержит `api_router = APIRouter(prefix="/api")`,
поэтому достаточно после auth/workspace middleware:

```python
from backend.app.api.reference_checks import create_reference_checks_router

api_router.include_router(create_reference_checks_router())
```

Получится `POST /api/reference-checks/compare`. Тело запроса:

```json
{
  "manifest": { "schemaVersion": "reference-suite/v1", "...": "..." },
  "bundle": { "schemaVersion": "0.1", "...": "..." },
  "targetSha": "<40-char commit sha>",
  "targetProjectId": "reference-checks/orders-service",
  "targetBranch": "fixtures/target-compatible"
}
```

Ответ — `reference-report/v1` со `status` `pass | fail | inconclusive`.
Несовместимая версия анализаторов и потеря покрытия дают `inconclusive`,
а не `pass`. Код `422` остаётся только для битого входа; сам несовместимый
bundle — это вердикт в отчёте.

## Frontend

Компонент standalone, лежит в
`frontend/src/components/reference-checks/ReferenceChecksPanel.tsx` и требует
только пропсы. `App.tsx` и чужие экраны не трогаются.

```tsx
import { ReferenceChecksPanel } from './components/reference-checks/ReferenceChecksPanel';
import type { PinnedReference, PinnedTarget } from './components/reference-checks/types';

const references: PinnedReference[] = [
  {
    id: 'orders-v1',
    label: 'Orders reference suite',
    projectId: 'reference-checks/orders-service',
    branch: 'SoloveyMaks/reference-branch-checks',
    sha: '<suite commit sha>',
    manifest: referenceSuiteManifest, // docs/reference-checks/fixtures/reference-suite.json
  },
];

const targets: PinnedTarget[] = artifacts.map((artifact) => ({
  id: artifact.id,
  label: artifact.filename,
  projectId: 'reference-checks/orders-service',
  sha: artifact.repository?.commit_sha ?? '',
  branch: artifact.repository?.branch,
  // Root владеет published envelope; компонент не выдумывает bundle.
  loadBundle: () => loadPublishedAnalysisBundle(artifact.id),
}));

<ReferenceChecksPanel references={references} targets={targets} />
```

`loadPublishedAnalysisBundle` — существующий путь корня к published immutable
bundle (например, `get_published_envelope(...)["bundle"]`). Если bundle нет,
компонент честно показывает ошибку, а не рисует `pass`.

`compareUrl` можно переопределить, если сравнение монтируется под другим
префиксом; по умолчанию `${API_BASE}/reference-checks/compare`.