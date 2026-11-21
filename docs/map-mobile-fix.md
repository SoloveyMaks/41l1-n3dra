# Mobile map: карта/детали и читаемость

Ограниченная фиксация двух visual-дефектов из
[docs/reviews/sol-visual.md](reviews/sol-visual.md), пункты 1 и 3. Это не новый
дизайн: desktop-раскладка, pan/zoom, layout графа и контракт workspace не
менялись.

## Причина

На ширине до 1100 px `.sm-workspace` становился колонкой: canvas получал
`min-height: 52vh` (48vh до 700 px), а `.sm-details` — `max-height` и
сжимался flex-контейнером. В просмотре 390×844 деталям оставалось меньше
250–300 px, поэтому проблема и код читались только через узкую щель.

## Что изменено

- `MapView.tsx`: на mobile/tablet (`max-width: 1100px`) появился переключатель
  **Карта / Детали** (по умолчанию «Карта»).
- Выбор проблемы, связи, узла или фрагмента кода на mobile переключает на
  «Детали». Автовыбор основной проблемы при загрузке карту не скрывает.
- «Детали» — панель поверх полотна, а не сжатая треть колонки: она занимает всю
  высоту workspace и прокручивается внутри. Canvas остаётся смонтированным под
  ней, поэтому pan/zoom и viewport не сбрасываются при переключении.
- Возврат к карте — вкладка «Карта» в том же переключателе.
- При смене выбранного элемента панель деталей прокручивается к началу; переход
  к фрагменту кода оставляет источник в видимой области.
- Подписи рёбер: непрозрачная подложка (`fill-opacity: 1`), тонкая обводка,
  padding 7×4, скругление 5 и `interactionWidth` 36 вместо 32. Раскладка графа
  не менялась.

Desktop 1440 px: переключатель скрыт, `.sm-workspace` остаётся grid
`canvas + 390px details`, поведение прежнее. Сбросы `selection`/`sourceState`
при смене run/scope не затрагивались.

## Измерения

Изолированный preview `?fullscreen=1`, реальные viewport 390×844 и 1440×1000.
Снимки — в локальном `.playwright-mcp/` (не коммитятся): `sm-fixed-390-*` и
`sm-fixed-1440-*` для initial → problem → source → back map.

| Проверка | 390×844 |
|---|---|
| До фикса (review, приложение) | details 50–67 px при scrollHeight 681–883 |
| До фикса (изолированный стенд) | details 230 px / scrollHeight 1231, canvas 405 px |
| Переключатель | 46 px, обе вкладки по 182×34 |
| Карта | canvas 589 px, details скрыты |
| Проблема/связь | details 589 px, scrollHeight 1231, код доступен |
| Клик по центру подписи ребра | открывает «Связь: Подходит под правило», details 589 px |
| Источник | блок 187 px, `pre` 50 px, верх блока в viewport (top 297) |
| Возврат к карте | canvas 589 px, details 0 px |
| Pan/zoom | transform до/после возврата одинаковый; zoom 0.9 → 1.08 |
| Горизонтальный overflow | document 390/390, details scrollWidth 390/390 |
| 320×568 | details 313 px (>300) |

| Проверка | 1440×1000 |
|---|---|
| Переключатель | `display: none` |
| Раскладка | grid: canvas 1050×885, details 389×885 |
| Details scrollHeight | 1231, внутренний scroll как раньше |
| Горизонтальный overflow | document 1440/1440 |
| Edge label | fill-opacity 1, stroke 0.8, ширины подложек 42–110 px |

## Проверки

```sh
npm ci --prefix ui/security-map
npm run check --prefix ui/security-map
npm run build --prefix ui/security-map
npm run dev --prefix ui/security-map -- --host 127.0.0.1 --port 4317
```

`check` (tsc) и `build` (tsc + vite) прошли. Стенд 18082, backend, frontendApp,
`runtime.env` и deploy не запускались и не менялись.

## Ограничения

- Роль admin/user, redaction backend и общий каркас frontend — вне этой правки.
- На 1100 px и уже details открываются поверх полотна; на 1101 px и шире
  действует прежняя двухколоночная раскладка.
- Ширина/раскладка графа и `workspaceId`/`runId`/`snapshotId` не менялись;
  изменилась только подложка и hit-area подписей рёбер.
- Файлы изменения: `ui/security-map/src/MapView.tsx`,
  `ui/security-map/src/style.css`, этот отчёт.