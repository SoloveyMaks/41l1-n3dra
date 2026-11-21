# Security map: отладка живого UI

Ветка `task/ai-vector-runtime`, UI-коммит `19a34d29` (после merge
`a006ae2`). Backend/API/App/deploy не редактировались.

## Что проверено

### Реальная страница App

Запускались локально backend на `127.0.0.1:8010` с PostgreSQL runtime и
frontend на `127.0.0.1:3005` (proxy `/api`). Workspace
`d3466e78-…`, run `e7279565-…`.

- Настоящая карта из API: 10 узлов, 9 связей, 26 evidence, 13 findings.
- Вкладка «Карта безопасности» занимает ровно `main` flex-область:
  desktop 1440×900 → `1440×844` под navbar; 850×900 → `850×844`;
  390×850 → `390×794`. Горизонтального overflow нет.
- Edge select → reason/producer → evidence → **реальный masked source**
  (`@GetMapping("/dump-database")`), state `ready`.
- Unknown edge select: `неизвестно`, причина
  «Нет применимого статического правила авторизации; runtime-конфигурация
  неизвестна», source открывается; 4 unknown edges видны amber dashed.
- React Flow paths/drag/zoom/pan/fit/reset и MiniMap присутствуют.

### Реальные 33/43 карты

Через временный live-preview реальные map JSON из codefacts (не mock):

| Карта | nodes | edges | evidence | findings | overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| default training | 33 | 32 | 39 | 6 | 0 |
| TeamB | 43 | 32 | 67 | 15 | 0 |

Layout слева-направо, cycles/shared nodes не дублируются, все handles
рендерятся. TeamB показывает `полная картина`; default — одну unknown
связь с amber dashed и warning banner.

### Сборка

```sh
npm run check --prefix ui/security-map   # tsc --noEmit
npm run build --prefix ui/security-map
npm run build --prefix frontend
```

Все три команды прошли. `npm run lint --prefix frontend` — только
существующие warnings других компонентов, без errors.

## Скриншоты

Временные browser screenshots (не входят в git):
`/tmp/security-map-shots/security-map-desktop-1440-final.png`,
`security-map-850-final.png`, `security-map-390-final.png`;
`/tmp/security-map-shots-3/security-map-default-1440.png`,
`security-map-teamb-1440.png`;
`/tmp/security-map-shots-3/app-map-1440.png`, `app-map-850.png`,
`app-map-390.png`.

## Что осталось интеграции

- В текущем `App.tsx` нет run-switcher: отображается latest run. Поэтому
  переключение run в реальном App не воспроизведено; `SecurityMapScreen`
  ремоунтится по `runId` и сбрасывает selection/source, это проверено в
  preview changed identity.
- App.tsx и остальные frontend components принадлежат deploy-worker;
  здесь не правились.
