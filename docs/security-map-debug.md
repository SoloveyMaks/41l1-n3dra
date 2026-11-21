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

## Initial focus и узкий экран

Поверх UI-коммита `19a34d2`:

- `initialFocusNodeIds()` выбирает preferred finding по severity/status,
  берёт sink/data/function seed и строит реальный путь от actor (или route,
  если actor нет) до sink. Никаких hardcoded synthetic node.
- `focusViewport()` после измерения нод ставит meaningful subset в левый верх
  canvas, а не `DEFAULT_VIEWPORT`. Reset снова применяет этот focus.
- Layout получил barycenter-проход: слой выравнивается по строкам входящих
  рёбер, поэтому actor/route/handler не разъезжаются по вертикали.
- «Вся карта» делает fit всех нод; «Показать путь» центрирует выбранный
  узел/связь/находку или initial focus; unknown-связи не скрываются.
- На узком (<700px canvas) focus показывает route → function → sink,
  а MiniMap скрыт по умолчанию; кнопка «Мини» включает его вручную.
- Тексты заменены: «жёлтый пунктир», «источник анализа»,
  «Загружаем фрагмент», «Расположение блоков можно менять»,
  «КАРТА ДОСТУПА / СТАТИЧЕСКИЙ АНАЛИЗ»; технические поля — в details.

Live default 33 на 1440: actor внутри canvas на 24px, route не обрезан,
правый край focus `1026 < 1050`, scale≈0.422. На 390: route/function/sink
видны, MiniMap скрыт, scroll `390×850`, scale≈0.40. TeamB 43: overlap `0`,
actor в графе отсутствует — focus корректно падает на route.

## Скриншоты (после focus-правки)

`/tmp/security-map-shots-5/default-33-initial-1440.png`
sha256 `35cc7d101802b2c4f884966c592bc6bda097cc8d90ceb2975d8538841b1a39ad`;
`/tmp/security-map-shots-5/default-33-initial-390.png`
sha256 `8bc0b9d13dfd6109f86171036c3cdfa0bc462e65ee4f746819b434d76a1b8bea`;
`/tmp/security-map-shots-5/teamb-43-initial-1440.png`
sha256 `a4759902231eaa4623e7d0fd5353ef48c3ddbc4e945eeb20de5f8d0d9e2b2ace`.

Ранние скриншоты: `/tmp/security-map-shots/`, `/tmp/security-map-shots-3/`,
`/tmp/security-map-shots-4/`.

## Что осталось интеграции

- В текущем `App.tsx` нет run-switcher: отображается latest run. Поэтому
  переключение run в реальном App не воспроизведено; `SecurityMapScreen`
  ремоунтится по `runId` и сбрасывает selection/source, это проверено в
  preview changed identity.
- App.tsx и остальные frontend components принадлежат deploy-worker;
  здесь не правились.
