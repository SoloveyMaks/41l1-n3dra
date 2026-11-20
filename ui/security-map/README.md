# SecurityMap

Подключаемый React Flow компонент карты доступа и данных. Production API принимает настоящий `SecurityMapDto` из [`docs/data-contracts/contracts.ts`](../../docs/data-contracts/contracts.ts) и отдельный массив `Finding[]`; manual pickup fixture подключается только через `PickupPreview`.

```tsx
import { SecurityMap } from "@41l1-n3dra/security-map";
import "@41l1-n3dra/security-map/style.css";

<SecurityMap
  identity={{ workspaceId, runId, snapshotId }}
  data={mapDto}
  findings={scanFindings}
  loadState="ready"
  onOpenEvidence={({ workspaceId, runId, snapshotId, evidenceId }) =>
    api.maskedEvidence({ workspaceId, runId, snapshotId, evidenceId })
  }
  onSelectEdge={(edge) => setSelectedEdge(edge.id)}
  onSelectNode={(node) => setSelectedNode(node.id)}
  onSelectFinding={(finding) => setSelectedFinding(finding.id)}
  onLayoutChange={saveLayout}
  onRetry={reloadMap}
/>;
```

Peer dependencies: `react`, `react-dom`, `@xyflow/react`. Изменения layout хранятся отдельно от DTO и никогда не меняют facts. `onOpenEvidence` получает только `{workspaceId, runId, snapshotId, evidenceId}`; A проверяет session/workspace, связность run→snapshot→evidence, TTL, hash и redaction до чтения bytes. UI не строит URL и не обходит auth.

Компонент отказывает с явным кодом до показа карты при scope mismatch, broken refs, несогласованной identity/signature/policy, неверной primary location или stale source. При смене identity интеграция должна сбросить `selection` и `sourceState`; внутренний view также снимает локальный выбор. `loading`, `empty`, `partial`, `error` показываются текстом, retry доступен; empty не означает безопасный запуск. Unknown/partial связи остаются видимыми, unknown рисуется пунктиром и карточкой diagnostics. Edge detail содержит resolution/reason, producer, basis и policy; evidence — путь/координаты/hash и callback со scoped IDs.

## Первый preview

`src/preview.tsx` даёт отдельный типизированный `adaptPickupIllustration` и `PickupPreview`. Он принимает только `pickup-path.illustration.json`, проверяет manual provenance и scope каждого сохранённого evidence и показывает постоянную метку **«Ручной разбор исходника — не результат анализатора»**. Адаптер не создаёт UUID, `RunIdentity`, signature, scanner rule, fingerprint `fp/v1`, approved policy или live masked-source response. Фрагменты открываются как reviewed redactedSnippet с `сохранённый фрагмент`, path/coordinates/hash и producer `manual-source-review/1`.

## Локальная проверка

```sh
npm install --prefix ui/security-map
npm run check --prefix ui/security-map
npm run build --prefix ui/security-map
npm run dev --prefix ui/security-map -- --host 127.0.0.1 --port 4317
```

В текущем worktree живой browser pass через Orca подтвердил: React Flow 6 nodes/5 edges; клик edge → reason/producer → scoped evidence; unknown guards, Finding links и policy assumption; loading/empty/error/retry; workspace/run/snapshot mismatch; смена identity сбрасывает source/selection; responsive viewport 390px без горизонтального overflow. Сервер запускался только на `127.0.0.1`; backend, scanner и auth endpoint не запускались.
