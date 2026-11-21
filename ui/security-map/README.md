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
dot grid и строгие вертикальные полосы по фактическим kinds:
`кто → проверка доступа → маршрут → обработчик → операция/вход → данные →
проблема`. X узла задаётся полосой, поэтому две точки одного вида всегда на
одной линии; Y раскладывается детерминированно по реальным предшественникам и
последователям (barycenter), без overlap. Заголовки полос и их границы
нарисованы в координатах canvas (ViewPortal), поэтому двигаются с картой.
Стрелки сохраняют direction source → target; cycles/shared nodes не
дублируются, обратные рёбра уходят по нижней полосе. Connections/reconnect/
delete отключены, drag/pan/zoom/fit/reset работают.

Каждая стрелка несёт подпись перехода и реальное число сохранённых фрагментов
(`N док.` / `нет док.`); `unknown/partial` — amber dashed с русской подписью.
Проблема — карточка в полосе «Проблема»; она привязана к реальным
`findingNodeLinks`, а не к выдуманному ребру, и пишет, к скольким узлам
привязана. Проблемы без привязки не рисуются как конец цепочки: они остаются в
списке «Проблемы», а над картой стоит явное предупреждение. Узлы без
доказательств и связи без фрагмента-основания помечаются явно, а не нулём.

Режим «Дерево» — отдельная tidy-tree раскладка: корень сверху, ветки вниз,
вертикальные handles, рёбра только между видимыми узлами, crosslink/cycle
пунктиром. В панели — раскрытие/сворачивание, счётчики корней/сирот и
объяснение: это дерево связей доступа, а не структура файлов. Выбор
узла/стрелки/проблемы подсвечивает соседей; детали открывают reason, producer,
masked source и технические id. Смена run сбрасывает selection/source, включая
StrictMode-двойной запуск эффектов.

## Локальная проверка

```sh
npm install --prefix ui/security-map
npm run check --prefix ui/security-map
npm run build --prefix ui/security-map
npm run dev --prefix ui/security-map -- --host 127.0.0.1 --port 4317
# только компонент на весь viewport, без demo-панели:
# http://127.0.0.1:4317/?fullscreen=1
```

В этом worktree живой browser pass (Orca + Playwright) на 1440×900 и 390×850
подтвердил:

- preview и реальный запуск: строгие полосы `0/312/624/936/1248/1560/1872`,
  0 пересечений карточек, 0 попаданий рёбер внутрь карточек;
- реальная карта 10 узлов + 5 привязанных проблем: 15 карточек, 9 связей,
  каждая связь с `N док.`; 4 unknown-связи дают amber dashed + баннер;
- детали unknown-связи показывают reason, «это пробел, а не безопасность» и
  открывают masked source (`cg-redaction/2`);
- 8 проблем без `findingNodeLinks` не рисуются карточками, над картой стоит
  явное предупреждение; авто-выбор primary finding не сбрасывается StrictMode;
- «Дерево»: 10 карточек, 10 строк, 0 overlap, 0 полос, вертикальные рёбра,
  кнопки «Раскрыть всё / Только корни», панель объясняет разницу с файлами;
- `npm run build` (frontend) и `npm run build` (ui/security-map) проходят;
  консоль после полной перезагрузки — без ошибок и warnings;
- backend запускался только на `127.0.0.1` с изолированной PostgreSQL в
  docker; production и внешние auth не затрагивались.
