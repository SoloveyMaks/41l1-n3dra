# Reference checks: серверный эталон против опубликованного снимка

Фича сравнивает **server-owned reference suite** с **опубликованным
`AnalysisBundle` 0.1** другого запуска того же workspace. Клиент не присылает
ни bundle, ни манифест, ни commit SHA: только id серверного манифеста и id
запуска.

Runner не делает checkout, не вызывает тесты и не исполняет репозиторий. Он
читает уже опубликованный immutable envelope из canonical storage.

## Граница доверия

Запрос:

```json
{
  "referenceManifestId": "reference-suite",
  "targetRunId": "run_ab12cd34",
  "targetArtifactId": "art_9f00"
}
```

- `referenceManifestId` — ключ в серверном реестре
  (`CG_REFERENCE_SUITES_DIR`, по умолчанию `docs/reference-checks/fixtures`).
- `targetRunId` — запуск из текущего authenticated workspace. Сервер сам
  достаёт run → artifact → snapshot → canonical published envelope.
- `targetArtifactId` необязателен; если передан, обязан совпадать с артефактом
  запуска.
- `manifest`, `bundle`, `targetSha`, `targetBranch`, `targetProjectId` и любые
  другие поля отклоняются (`422 extra_forbidden`).

Workspace берётся из существующей сессии (`require_workspace_actor`), а не из
тела запроса. Чужой run виден как `404 TARGET_RUN_UNKNOWN`.

## Что фиксируется в манифесте

`reference-suite/v1`:

- `reference.branch`, `reference.sha` — ветка и 40-символьный commit эталона;
- `reference.snapshotManifestHash`, `reference.artifactHash` — хэши снимка и
  артефакта эталона;
- `reference.bundleContentSha256` — sha256 канонического JSON **разобранного**
  reference bundle. Это проверяемый хэш содержимого; `bundleSha256` остаётся
  хэшем исходного экспортированного файла и проверяется только если сервер
  отдал source-export digest;
- `reference.runId` + `reference.snapshotId` — необязательная пара для
  live-загрузки эталонного опубликованного envelope из canonical storage;
- `reference.runnerVersion` — `producerVersion` bundle;
- `reference.signature` — полная статическая подпись анализаторов (12 полей);
- `expectedFindings` / `expectedControls` — что именно должно быть в эталоне.

Серверный реестр:

```text
$CG_REFERENCE_SUITES_DIR/
  reference-suite.json            # сам манифест
  reference-suite.reference.json  # опциональный frozen snapshot
```

`*.reference.json`:

```json
{
  "sourceCommit": "ae1fd4805c3ce8cfc533be99a601b0e2d3aa95a1",
  "sourceBranch": "SoloveyMaks/reference-branch-checks",
  "sourceExportSha256": "72bb792c…",
  "bundle": { "schemaVersion": "0.1", "...": "..." }
}
```

Сервер считает `bundleContentSha256` из `bundle` сам. Если frozen snapshot нет,
используется `reference.runId`/`snapshotId` и тот же canonical loader. Если нет
ни того, ни другого — вердикт `inconclusive` с явной причиной, а не `pass`.

## Проверка provenance

Перед сравнением сервер обязан подтвердить:

- `reference.sha` = source commit из server-owned repository provenance
  (`runtime.repository_watch_commits` / `repository_hook_deliveries`) или из
  frozen snapshot;
- `reference.snapshotManifestHash` = manifest hash загруженного snapshots row;
- `reference.artifactHash` = `bundle.identity.artifactHash`;
- `reference.bundleContentSha256` = sha256 канонического JSON reference bundle;
- ожидаемые находки манифеста реально присутствуют в reference bundle с тем же
  fingerprint и `status=confirmed`, а безопасные контроли не срабатывают.

Для цели:

- bundle загружен сервером по `targetRunId`; `identity.workspaceId/runId/
  snapshotId/artifactHash/snapshotManifestHash` совпадают с runtime-строками;
- `target.sha` — source commit из server-owned provenance. Клиент не может
  заявить произвольный commit;
- если commit/branch/artifact hash в canonical provenance отсутствует —
  `inconclusive` с причиной. Ничего не додумывается.

## Правила вердикта

| Ситуация | Вердикт |
| --- | --- |
| Provenance эталона и цели подтверждён, все ожидаемые находки совпали в `checked` scope, контроли чисты, сканеры не failed | `pass` |
| Подпись/runner/схема/fingerprintVersion несовместимы | `inconclusive` |
| Сканер `failed`/`timeout`/`unsupported` или coverage `unavailable` | `inconclusive` |
| У ожидаемой пары path/rule нет `checked` scope | `inconclusive` по строке |
| Ожидаемая находка пропала в `checked` scope, fingerprint/severity изменились | `fail` |
| Безопасный контроль дал подтверждённую находку | `fail` |
| Находка со статусом `inconclusive`/`potential` вместо `confirmed` | `inconclusive` |
| Есть находка вне манифеста | `inconclusive` |
| Сервер не подтвердил SHA/хэш/provenance | `inconclusive` |

Один actual finding может закрыть только одно ожидание. Два ожидания с разными
fingerprint не могут «потребить» одну и ту же находку: второе станет honest
`missing` → `fail`.

`partial` scanner/coverage сам по себе не блокирует `pass`, если конкретные
ожидаемые scopes `checked`; потеря ожидаемого scope остаётся `inconclusive`.

## CLI: raw fixture, не аттестация

```bash
python3 -m analyzers.contract_export docs/reference-checks/fixtures/project-compatible \
  --db analyzers/fixtures/vulnerabilities.json --out /tmp/compatible-bundle.json

python3 -m backend.app.reference_checks \
  docs/reference-checks/fixtures/reference-suite.json \
  /tmp/compatible-bundle.json --out /tmp/compatible.json
```

CLI сравнивает локальные файлы и **не принимает** `--target-sha`. Отчёт всегда
помечен:

```json
"provenance": {
  "mode": "raw-fixture",
  "attestation": "none",
  "verified": false,
  "reasons": ["raw-fixture mode: …"]
}
```

`target.sha` в raw-режиме всегда `null`; identity бандла лежит в
`untrusted.bundleIdentity`. Коды выхода: `0` pass, `1` fail, `2` inconclusive,
`3` битый вход. Это вердикт сравнения файлов, а не подтверждение коммита.

## Fixtures и отчёты

- Манифест: `docs/reference-checks/fixtures/reference-suite.json`.
- Исходный снимок эталона: `fixtures/project-reference/`, содержимое
  зафиксировано коммитом `ae1fd4805c3ce8cfc533be99a601b0e2d3aa95a1`.
  Его `snapshotManifestHash`/`artifactHash` совпадают с манифестом:
  `6be987bf…5772` и `fd93a4cb…3608`.
- Совместимый снимок: `fixtures/project-compatible/`; исправленный —
  `fixtures/project-fixed/`.
- `docs/reference-checks/reference-bundle.json` — raw export эталонного
  bundle, пересобранный из `project-reference` актуальным анализатором
  (код `49f4025`). Его sha256 файла — `72bb792c…13ed`, канонического
  разобранного JSON — `a5868546…fd7c`; оба значения закреплены в манифесте.
- `fixtures/reference-suite.reference.json` — frozen sidecar с тем же bundle,
  `sourceCommit: ae1fd48…`, `sourceExportSha256: 72bb792c…13ed`. Серверный
  registry читает его без БД; `verify_reference_snapshot` на нём даёт
  `verified: true` и `expectedFindingsPresent: true`.
- Старая подпись манифеста была снята анализатором на дереве `ae1fd48`
  (`parserHash 45a0c139…02ab`, `rulesHash 8731ddbc…e41ae`). После правок
  `analyzers/cg26/*` актуальный код даёт `parserHash fc9747f7…9496`,
  `rulesHash 25598be9…a9e`; сравнение без пересбора эталона честно
  возвращало `incompatible` и `0/3`, а не pass.
- `docs/reference-checks/reports/` — raw-fixture отчёты (`reference-report/v2`),
  пересобранные под новый эталон. У них `attestation: none`; статус
  `compatible.json` остаётся `inconclusive` из-за SCA coverage `unavailable`.

| Отчёт | Статус | Итог |
| --- | --- | --- |
| `compatible.json` | `inconclusive` | 3/3 находки, 2/2 контроля, но SCA coverage `unavailable` — честный inconclusive, не pass |
| `failure.json` | `fail` | 3 находки пропали в `checked` scope, контроли чисты |
| `incompatible.json` | `inconclusive` | `rulesVersion` не совпал, сравнение не начато |
| `coverage-loss.json` | `inconclusive` | 2 совпали, 1 без scope покрытия; лишних нет |

Пересборка эталона и отчётов (без сети и БД):

```bash
python -m analyzers.contract_export docs/reference-checks/fixtures/project-reference \
  --db analyzers/fixtures/vulnerabilities.json \
  --workspace-id 649d5c51-b4a5-5ad2-956f-bb8a4720bac0 \
  --run-id 5b31f17c-c905-5e02-ab60-cbd78e1f8656 \
  --snapshot-id 7c72dc11-b407-5b80-a0ed-ce246e5d587f \
  --workflow-revision-id dc5978d2-8e5a-5840-b913-6f9a88c5eac9 \
  --out docs/reference-checks/reference-bundle.json
node analyzers/validate_bundle.mjs docs/reference-checks/reference-bundle.json \
  docs/reference-checks/fixtures/project-reference

python -m analyzers.contract_export docs/reference-checks/fixtures/project-compatible \
  --db analyzers/fixtures/vulnerabilities.json \
  --workspace-id 649d5c51-b4a5-5ad2-956f-bb8a4720bac0 \
  --run-id c8d00859-32ed-5096-9a09-ab11ee09f0e3 \
  --snapshot-id db54c28e-67c2-562d-8121-eff6680cddc5 \
  --workflow-revision-id 603ee9ca-d486-5e7a-a885-5e935fb36e2a \
  --out /tmp/nedra-compatible-bundle.json
python -m backend.app.reference_checks docs/reference-checks/fixtures/reference-suite.json \
  /tmp/nedra-compatible-bundle.json --out docs/reference-checks/reports/compatible.json
```

`failure.json` строится так же из `project-fixed` с run id
`42c6cfa9-af8a-5136-8b00-e14c191e1fa6`, snapshot id
`112c24a2-2e28-5cdf-8277-92aeb6c7b3bf`. `incompatible.json` — копия
compatible bundle с `signature.rulesVersion = "cg26-rules/0.2.0"` во всех
частях; `coverage-loss.json` — копия без двух SAST scopes
`OrderLookupController.kt`/`SafeOrderLookupController.kt` для
`CG26-SAST-001`. Оба отчёта остаются `untrusted` raw-fixture, не аттестацией.

## Где код

- `backend/app/reference_checks/manifest.py` — манифест и его provenance-поля.
- `backend/app/reference_checks/bundle.py` — строгая проверка верхнего уровня,
  вложенных identity/signature, статусов сканеров/находок и `fp/v1`.
- `backend/app/reference_checks/provenance.py` — сверка pinned SHA/хэшей с
  server snapshot и проверка target identity.
- `backend/app/reference_checks/compare.py` — one-to-one сравнение и вердикт.
- `backend/app/reference_checks/canonical.py` — server loaders
  (`get_published_envelope`, repository provenance).
- `backend/app/reference_checks/registry.py` — серверный реестр манифестов.
- `backend/app/api/reference_checks.py` — DI router factory.
- `backend/app/api/reference_checks_mount.py` — точный root mount hook.
- `frontend/src/components/reference-checks/ReferenceChecksPanel.tsx` — UI с
  epoch/abort guard; запрос содержит только id.
- `docs/reference-checks/ROOT-INTEGRATION.md` — как root это монтирует.
