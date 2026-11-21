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

## Человеческая подача

- Категории: «Кто обращается», «Адрес запроса», «Правило доступа»,
  «Какие запросы охватывает правило», «Проверка доступа»,
  «Обработчик запроса», «Входные данные», «Операция», «Данные», «Настройка».
- Главная карточка: человеческая категория, label/endpoint из кода и
  «N доказательств / N проблем». `symbolKey`, producer, версии и ID
  убраны в «Технические данные»; на карточке их больше нет.
- Стрелки: «Обращается», «Подходит под правило», «Разрешает», «Требует»,
  «Вызывает», «Передаёт данные», «Читает», «Изменяет»; source/target
  остались фактическими, направление не переворачивалось.
- Находки: человеческие заголовки вместо ruleId, status/severity/confidence
  переведены; выбранная проблема показывает «Почему так» с реальными
  reason из DTO, без AI-догадок.
- Unknown: «связь не установлена», жёлтый пунктир, banner и source
  сохраняются. Покрытие: «Проверенный объём завершён» /
  «Проверено неполностью», не «проект безопасен».
- Пробелы `runtime-configuration`, `runtime-guards`,
  `binaries-and-archives` получили человеческое пояснение; raw-строки и
  коды остались в «Технические подробности покрытия».
- Первый экран: строка «Откуда приходит запрос, как проверяется доступ и
  какие данные затрагиваются» + «Нажмите на стрелку, чтобы увидеть
  основание».
- Initial focus — читаемый участок `route/guard/function/sink` (на 1440
  scale≈0.86, cards≈214px); на узком canvas — один route-узел при scale≈0.9.
  Полная карта и unknown не скрываются: «Вся карта» / «Показать путь».
- MiniMap скрыт на узком по умолчанию, кнопка «Мини» включает вручную.

## Проверка

- default 33: `33 узла / 32 связи / 6 проблем / 39 доказательств`,
  1440 и 390, overlap `0`, overflow нет.
- TeamB 43: `43 / 32 / 15 / 67`, overlap `0`, health
  «Проверенный объём завершён».
- Реальный App: 10 узлов, 13 проблем, masked source `загружен`
  (`@GetMapping("/dump-database")`), 4 unknown-связи.
- `npm run check --prefix ui/security-map`, build ui/security-map и
  build frontend прошли.

## Скриншоты (human labels)

`/tmp/security-map-shots-6/human-default-33-1440-final.png`
sha256 `09724aa46162f412370e1186379a26341428223ec1c73074886e0fb8445ccaea`;
`/tmp/security-map-shots-6/human-default-33-390-final.png`
sha256 `d88577b8ed5ff39cbb4fb7d6bb4fdf7ca14fa7438478105a22dc055117e003ad`;
`/tmp/security-map-shots-6/human-teamb-43-1440-final.png`
sha256 `caa1564b3f0da2314d221480e123eab4b72e4b61b17400408b83589e4d85f2e8`;
`/tmp/security-map-shots-6/human-teamb-43-390-final.png`
sha256 `f6191815fba5198655da3bbe3f2561e87f4fb89be4c7115328b9db758cb8238c`.

Ранние скриншоты: `/tmp/security-map-shots-5/`, `/tmp/security-map-shots-4/`.

## Что осталось интеграции

- В текущем `App.tsx` нет run-switcher: отображается latest run. Поэтому
  переключение run в реальном App не воспроизведено; `SecurityMapScreen`
  ремоунтится по `runId` и сбрасывает selection/source, это проверено в
  preview changed identity.
- App.tsx и остальные frontend components принадлежат deploy-worker;
  здесь не правились.
