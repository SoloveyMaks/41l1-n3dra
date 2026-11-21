# SQL rule comparison: узкий source-pattern proof

`packages/comparison/sql-rule.mjs` — отдельный модуль для одного конкретного
SQL-правила `KOTLIN-SQL-INJECTION`. Он **не** вызывает `compareBundles`, не
меняет global `comparable`, не делает весь run сопоставимым и не доказывает
runtime-исправление или отсутствие других уязвимостей.

Вход — два published scanner envelopes (`bundle`, `manifest`, `provenance`) из
canonical storage. Модуль проверяет опубликованные метаданные и scoped
coverage, затем отвечает только про выбранный source pattern.

## API

```js
import { compareSqlRuleScopes } from '../../packages/comparison/sql-rule.mjs';

const result = compareSqlRuleScopes(baselineEnvelope, currentEnvelope, {
  path: 'src/main/kotlin/demo/RepairLookupController.kt', // optional
  id: '33333333-3333-4333-8333-333333333333',           // optional UUID
});
```

CLI:

```sh
node packages/comparison/sql-rule-cli.mjs \
  baseline.published.json current.published.json \
  [--path <path>] [--id <uuid>]
```

CLI печатает только JSON DTO. Ошибка входного контракта — stderr/exit 1.

Если `path` не передан, модуль допускает ровно один `checked` scope
`SAST/path/KOTLIN-SQL-INJECTION` в baseline. Несколько путей → `inconclusive` с
`candidatePaths`, без выбора «первого».

## Что валидируется

- `AnalysisBundle` schema `0.1`, три scanner outcomes, fingerprint/scopes и
  evidence references через `validateBundle` (без source bytes).
- Manifest: `complete=true`, workspace/snapshot совпадают с identity,
  `manifestHash(files)` совпадает с `manifest.hash` и
  `identity.snapshotManifestHash`; evidence sourceHash/byteLength и coverage
  paths входят в manifest.
- Provenance: `published=true`, `origin=scanner`, `bundleHash` — 64 hex.
  `envelopeHash/canonicalHash/documentHash/publishedAt` проверяются, если
  присутствуют. `bundleHash` берётся из доверенного SQL publication; модуль
  проверяет форму, а не пересчитывает persistence hash.
- Один workspace; разные run **и** snapshot.
- Совпадают `parserVersion/Hash`, `rulesVersion/Hash`,
  `advisoryVersion/Hash`, `configVersion/scanConfigHash`,
  `domainPolicyRevisionId/Version/Hash`, `hmacKeyVersion` и
  `normalizationVersion`.
- `expectedScopesVerified` **не** требуется true и никогда не переписывается.
  Он только сообщается в DTO. Scoped proof опирается на фактический
  `coverage.scopes` выбранного файла/правила.
- Выбранный scope должен иметь `category=SAST`,
  `ruleId=KOTLIN-SQL-INJECTION`, `path` и `status=checked` с обеих сторон.
  Parser errors, limitsReached, отсутствие/не checked scope, `unknown`
  policy applicability или не-scopper provenance → `inconclusive`.
- Global graph/runtime coverage здесь не проверяется и не объявляется complete.
  Runtime unknown остаётся в обычном `compareBundles`.

## Статусы

| Статус | Условие |
| --- | --- |
| `persistent` | Обе стороны checked; baseline/current finding имеют один уникальный stable anchor и одинаковый fingerprint |
| `changed` | Обе стороны checked; один уникальный stable anchor, но fingerprint/context изменился |
| `pattern_disappeared` | Файл есть в обоих manifests; обе стороны checked; baseline finding уникален по stable anchor; current SQL finding/anchor в этом path отсутствует |
| `inconclusive` | Несовместимые signatures/provenance/policy, parser error, limit, нет или не checked scope, baseline finding отсутствует/неоднозначен, current anchor неоднозначен или отличается (возможен rename) |
| `file_deleted` | Выбранный path отсутствует в полном current manifest. Это не `resolved`; `deletedFile` содержит baseline source hash и предупреждение о rename |

`pattern_disappeared` — статическое исчезновение source pattern, не runtime fix
и не доказательство безопасности приложения.

## DTO

```json
{
  "schemaVersion": "sql-rule-comparison/1",
  "id": "uuid",
  "scope": "source_pattern",
  "ruleId": "KOTLIN-SQL-INJECTION",
  "workspaceId": "uuid",
  "baselineRunId": "uuid",
  "baselineSnapshotId": "uuid",
  "currentRunId": "uuid",
  "currentSnapshotId": "uuid",
  "path": "src/.../RepairLookupController.kt",
  "status": "pattern_disappeared",
  "matchingBasis": "unique_anchor",
  "reason": "one-line reason",
  "globalComparable": false,
  "signaturesCompatible": true,
  "reasons": ["..."],
  "baselineFindingId": "uuid|null",
  "currentFindingId": "uuid|null",
  "baselineFingerprint": "sha256|null",
  "currentFingerprint": "sha256|null",
  "baselineRuleVersion": "1.0.0|null",
  "currentRuleVersion": "1.0.0|null",
  "baselineAnchor": {"path": "...", "symbolKey": "...", "role": "sql_sink", "normalizedContextHash": "..."} ,
  "currentAnchor": null,
  "baselineCheckedScope": {"path": "...", "ruleId": "KOTLIN-SQL-INJECTION", "category": "SAST", "status": "checked", "reason": "..."},
  "currentCheckedScope": {"path": "...", "ruleId": "KOTLIN-SQL-INJECTION", "category": "SAST", "status": "checked", "reason": "..."},
  "candidatePaths": [],
  "deletedFile": null,
  "expectedScopesVerified": {"baseline": false, "current": false},
  "provenance": {
    "baselineBundleHash": "sha256",
    "currentBundleHash": "sha256",
    "normalizationVersion": "cg-codefacts/1.0.0",
    "origin": "scanner"
  },
  "manifestHashes": {"baseline": "sha256", "current": "sha256"}
}
```

Поля `scope`, `status`, `reason`, `path` — стабильная поверхность для UI.
`globalComparable` всегда `false`: узкий SQL proof не заменяет общий
`ComparisonDto` и не снимает его guards.

## Live probe

На реальном SAST из `examples/demo/sql-before-after` (before: 1 finding,
after: 0; оба selected scope checked):

```sh
node packages/comparison/sql-rule-cli.mjs \
  examples/demo/sql-before-after/baseline.published.json \
  examples/demo/sql-before-after/current.published.json
```

Результат: `pattern_disappeared`, `globalComparable=false`, scopes `checked` с
обеих сторон. Тот же результат сохраняется, если у обоих envelopes
`expectedScopesVerified=false`, а scan/coverage имеют `partial` со
skipped-другими-rules: доказывает именно checked scope, а не глобальный
attestation. Потеря checked scope → `inconclusive`; несовместимый `rulesHash` →
`inconclusive`; удаление controller из полного current manifest → `file_deleted`.

## Границы

- Нет SQL publication внутри модуля, нет `ComparisonStore`, нет vetted/embeddings.
- Нет runtime, Kotlin/Gradle, exploit или owner-policy proof.
- Нет synthetic source graph complete и нет подмены global `expectedScopesVerified`.
- Несколько SQL scopes без `path` не разрешаются эвристикой.