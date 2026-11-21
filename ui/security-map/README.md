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

## Визуальный язык

Карта — полноэкранный тёмный React Flow canvas в стиле workflow-редактора:
dot grid, слоистый слева-направо layout по фактическим kinds/edges
(`actor → route → access → handler → operation/data`), кастомные узлы с
semantic accent/icon, evidence/finding counts, полными путями и handles
слева/справа. Cycles/shared nodes не дублируются; несколько узлов слоя
раскладываются без overlap. MiniMap, controls, fit/reset, drag, pan и zoom
работают; connections/reconnect/delete отключены.

Выбор узла/стрелки/находки подсвечивает соседей и приглушает остальное.
Стрелки `unknown/partial` — amber dashed с русской подписью; источник каждой
связи открывается из тёмной панели деталей. Большие UUID, hash и scope
свёрнуты в «Технические данные». Смена run сбрасывает selection/source.

## Локальная проверка

```sh
npm install --prefix ui/security-map
npm run check --prefix ui/security-map
npm run build --prefix ui/security-map
npm run dev --prefix ui/security-map -- --host 127.0.0.1 --port 4317
# только компонент на весь viewport, без demo-панели:
# http://127.0.0.1:4317/?fullscreen=1
```

В текущем worktree живой browser pass через Orca подтвердил: 1440×900,
850×900 и 390×850 без горизонтального overflow; React Flow 6 nodes/5 edges;
edge select → reason/producer → scoped masked source; node drag, zoom, pan,
fit и reset; unknown edge amber dashed + русская подпись + warning banner;
смена identity сбрасывает selection/source; Minimap и Controls видны.
Сервер запускался только на `127.0.0.1`; backend, scanner и auth endpoint не
запускались.
