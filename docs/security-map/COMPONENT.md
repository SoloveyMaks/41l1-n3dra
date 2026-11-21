# SecurityMap component — integration contract

`ui/security-map/src/SecurityMap.tsx` — подключаемый production-компонент на React + `@xyflow/react`. Он не владеет fetching, router, auth, scanner или backend. A создаёт активный `{workspaceId, runId, snapshotId}`, получает `SecurityMapDto` и отдельные `Finding[]`, а затем сбрасывает selection/source cache при смене identity.

## Import and peer dependencies

```tsx
import { SecurityMap } from "@41l1-n3dra/security-map";
import "@41l1-n3dra/security-map/style.css";
```

Peers: `react`, `react-dom`, `@xyflow/react` (React Flow 12). Локальный harness находится в `ui/security-map/dev`; это не основной frontend и не router.

## Props

- `identity: RunScope` — активный workspace/run/snapshot от A.
- `data: SecurityMapDto | null` — production bundle, где карта содержит refs на findings, но не сами Finding.
- `findings: readonly Finding[]` — отдельные findings из scan result того же scope.
- `loadState: loading | ready | empty | partial | error`, `error?: { code }`, `onRetry()`.
- `selection?: ScopedSelection | null` — внешний selection с тем же scope; без него компонент хранит view selection отдельно.
- `sourceState?: SourceState` — ответ A для выбранного scoped evidence (`ready`, `loading`, `error`, `unavailable`).
- `onSelectFinding`, `onSelectNode`, `onSelectEdge` — выбор факта; `onOpenEvidence({workspaceId, runId, snapshotId, evidenceId})` — единственный переход к source.
- `layout?: SecurityMapLayout`, `onLayoutChange(layout)` — только позиции/viewport; layout scope проверяется.
- `onRetrySource` — повтор masked-source запроса.

До render выполняется scope gate для всех records, refs и source state; чужой run/snapshot/workspace получает `SCOPE_MISMATCH` и не показывает snippet. Также проверяются production identity/signature hashes, duplicate IDs, endpoints/evidence/finding links, primary location, policy signature и source hash/location/redaction. Detail панели не превращают layout или Finding link в code fact.

## Integration A

A должен реализовать `maskedEvidence` с авторизацией и scoped ID lookup. Клиент передаёт только IDs из DTO; он не принимает arbitrary path/range/file URL, не читает ZIP и не обходит session. Перед выдачей A проверяет workspace ownership, run→snapshot→evidence, containment, TTL, `sourceHash` и redaction coverage. Безопасные исходы: `evidence_not_available`, `source_changed`, `source_unavailable`, `source_redaction_incomplete`; UI не выдаёт их за пустой успешный source.

## Хост-экран и фактическое состояние загрузки

`frontend/src/components/SecurityMapScreen.tsx` сам загружает карту выбранного run и отдаёт наружу необязательный `onLoadStateChange(state: LoadState)`; тип экспортируется как `SecurityMapLoadStateChange`.

```ts
// frontend/src/components/SecurityMapScreen.tsx
export type SecurityMapLoadStateChange = (state: LoadState) => void;
export interface SecurityMapScreenProps {
  runId: string | null;
  onLoadStateChange?: SecurityMapLoadStateChange;
}
```

Семантика уровней: `loading` — запрос по выбранному run пошёл (выбор run или повтор); затем ровно один терминал — `ready` (coverage complete), `partial`, `empty` (нет узлов, связей и `findingRefs`) или `error`. Запоздалый ответ отменённого run, устаревший reload и размонтированный экран не эмитят; при `runId === null` эмиссий нет, потому что грузить нечего. Значение — уровень, а не событие: повтор того же значения (StrictMode) допустим, строить переходы на счётчике вызовов нельзя. Экран монтируется по ключу `runId`, поэтому владелец (App → LiveRunView) сбрасывает своё состояние сам при смене run.

## Manual pickup preview

`ui/security-map/src/preview.tsx` — отдельный `PickupIllustration` adapter. Он сохраняет только ручные reviewed snippets, path/coordinates/hash, scope и `manual-source-review/1`; рисованные Finding/edge `supports_finding` остаются карточкой Finding/links, unknown guard — diagnostics без выдуманного endpoint. Preview всегда видим с меткой «Ручной разбор исходника — не результат анализатора» и не проходит production `SecurityMap` DTO gate.

## Проверено живым запуском

Команды: `npm install --prefix ui/security-map`, `npm run check --prefix ui/security-map`, `npm run build --prefix ui/security-map`; dev server только `127.0.0.1:4317`, Orca browser snapshot/click/eval/screenshot. Наблюдения: 6 React Flow nodes, 5 edges; edge `calls` → resolution/reason/producer → `ev-pickup-query` сохранённый snippet; `ev-pickup-permit` и policy evidence; unknown guards; Finding links; loading/empty/error/retry; scope mismatch по workspace/run/snapshot; identity change даёт 0 source panel/0 selected edge; 390px viewport имеет `scrollWidth=390`, grid 368px, unknown border dashed.

Остаётся подключить реальные `SecurityMapDto`/Finding payloads A/B и живой masked-source endpoint. Harness намеренно не заявляет production scanner/auth/backend и не создаёт synthetic production signature.

## Source state и жизненный цикл

`SourceState` — discriminated union в `src/types.ts`: `idle`; `loading/error/unavailable` с `request: EvidenceRequest` (для ошибок ещё `code`); `ready` с тем же request, `sourceHash`, `location`, `maskedFragment`, `redactionVersion`. A нормализует свой безопасный ответ в эту форму. Компонент показывает `maskedFragment` только после клика по соответствующему evidence и совпадения всех IDs/hash/location; production `redactedSnippet` сам по себе не выдаётся за живой ответ A. Ошибка одного source оставляет граф; scope/hash mismatch закрывает весь несогласованный ответ с кодом отказа. `onRetrySource` необязателен: без него повторяется `onOpenEvidence`.

В `findings` передаётся точный набор `data.findingRefs`, не все находки чужих запусков. `selection` передаётся как `{ workspaceId, runId, snapshotId, value: { kind: "edge" | "node" | "finding", id } | null }`; `undefined` включает внутренний выбор, `null` — управляемое отсутствие выбора. Scope/layout/selection/source старой identity дают отказ; ключ identity+bundle сбрасывает внутренний view. A отменяет запросы и игнорирует запоздалые ответы, сохраняет layout по scope+viewId; перемещение узла и viewport вызывают `onLayoutChange`. Нет изменения facts или публикации из UI. На узком экране выбор прокручивает к детали/фрагменту; resize подгоняет граф.

Реальные версии проверки: Node 22.23.3, npm 12.2.2, React/React DOM 18.3.1, React Flow 12.12.1, TypeScript 5.9.3, Vite 7.3.8 (точные зависимости в локальном lockfile). Build: 59 modules, ~389 kB JS / ~124 kB gzip; предупреждение Vite об игнорировании `use client` в React Flow не помешало сборке и browser pass. Одноразовая проверка через TypeScript transpile дополнительно отклонила пять намеренно неполных отрицательных входов (чужой scope у map/node/evidence/finding/source) до доступа к signature; положительный production DTO не выдумывался.

## Воспроизведение для A

```sh
npm ci --prefix ui/security-map --ignore-scripts
npm run check --prefix ui/security-map
npm run build --prefix ui/security-map
npm run dev --prefix ui/security-map -- --port 4317 --strictPort
```

Открыть `http://127.0.0.1:4317/`. Нажать стрелку `calls`, затем `ev-pickup-query`; открыть Finding и policy evidence; unknown evidence — `ev-security-scope`. В селекторе harness доступны loading, empty, error/retry, три отрицательных scope и changed identity. Для последних используются только ручной view или заведомо неполный отрицательный envelope: он не становится production DTO и не получает invented hashes. Весь pickup path и 10 trace evidence сохранены; все 21 reviewed evidence остаются в preview view model. Ticket/catalog пока только outlines исходного документа и не заявлены выполненными сценариями.

Скриншоты в `ui/security-map/review/` получены настоящим Orca browser screenshot. Это визуальная проверка локального preview; действующий A endpoint, auth/TTL/hash/redaction end-to-end и положительный production run ещё требуют интеграции A/B. Полная JSON Schema/semantic validation выполняется при загрузке A; client gate не заменяет серверную проверку или авторизацию.

Явные `edge.resolution=partial/unknown` имеют приоритет над `coverage.status=complete`: badge остаётся «Частичная картина», появляется предупреждение с числом незавершённых связей; сами стрелки пунктирные и подписаны resolution. Полная summary не скрывает причины/диагностику и не означает «безопасно».

Дополнительный browser probe `Unknown edge / complete summary` — явно ручная проверка представления, не анализатор: `loadState=ready`, coverage summary complete и одна unknown edge. Реальный DOM показал badge «Частичная картина», предупреждение с числом 1, `flows_to · unknown`, `strokeDasharray="6, 5"`. Снимок `review/unknown-summary.png` сохраняет этот отрицательный визуальный сценарий.

## Независимое ревью после интеграции

Read-only проверка приняла положительный синтетический DTO через production gate/model/render и проверила отрицательные scope/source/ref случаи. Чужой workspace/run/snapshot, устаревший source hash/location и сломанные graph/finding refs отклоняются; до клика по evidence source не раскрывается. Strict TypeScript дал 0 diagnostics, production entry собрался отдельно от dev harness. Новых блокеров не найдено. Это contract scenario, не настоящий scanner run; браузерные клики и 390px описаны выше, backend/auth/TTL/redaction остаются интеграцией A/B.
