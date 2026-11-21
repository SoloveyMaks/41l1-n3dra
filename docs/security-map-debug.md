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
- Unknown и partial различаются: «связь не установлена» / «связь
  подтверждена частично»; health и banner считают их отдельно. Покрытие:
  «Проверенный объём завершён» / «Проверено неполностью», не «проект
  безопасен».
- Пробелы `runtime-configuration`, `runtime-guards`,
  `binaries-and-archives` получили человеческое пояснение; raw-строки и
  коды остались в «Технические подробности покрытия».
- Первый экран: строка «Откуда приходит запрос, как проверяется доступ и
  какие данные затрагиваются» + «Нажмите на стрелку, чтобы увидеть
  основание».
- Без внешней selection карта сама выбирает фактическую проблему с
  node-refs (severity/status/confidence) и её цепочку; desktop показывает
  читаемый `route/guard/function/sink` (scale≈0.86), 390 — route-узел этой
  проблемы при scale≈0.9. Вся карта и unknown остаются доступны.
- Если у findings нет node-refs, выбирается сама проблема, показывается
  пояснение «нет привязки к узлам», а viewport показывает общую карту;
  случайный route как problem path не выбирается.
- Source error возвращает человеческую причину в alert и `Код ошибки` +
  исходный код в «Технические данные»; secrets не выводятся.
- `focusViewport` не срабатывает повторно на resize/drag после первичного
  фокуса; reset/смена run применяют фокус и очищают старую selection/source.
- Scope/подпись переведены: «Технический контекст и подпись», «Рабочая
  область», «Запуск», «Снимок», «Ревизия».
- MiniMap скрыт на узком по умолчанию, кнопка «Мини» включает вручную.

## Проверка

- default 33: `33 узла / 32 связи / 6 проблем / 39 доказательств`,
  1440 и 390, overlap `0`, overflow нет.
- TeamB 43: `43 / 32 / 15 / 67`, overlap `0`, health
  «Проверенный объём завершён»; primary problem — «Можно запросить чужой
  объект» (high/confirmed).
- Реальный App: 10 узлов, 13 проблем, masked source `загружен`
  (`@GetMapping("/dump-database")`), 4 unknown-связи.
- Отдельно проверены: source error (`source_unavailable`) → человеческая
  причина + `Код ошибки` в details; no-node-refs → note и общий viewport,
  route не выбран; partial+unknown → отдельные подписи и счётчики.
- `npm run check --prefix ui/security-map`, build ui/security-map и
  build frontend прошли.

## Скриншоты (human labels)

`/tmp/security-map-shots-7/problem-default-33-1440.png`
sha256 `b05080afab7725fb483e37c250fc5bf2bb884133834c15bcb5d1344621821c24`;
`/tmp/security-map-shots-7/problem-default-33-390.png`
sha256 `7deaf3f3b7147cd1ee0d8f5936a2fdd96eeb417c9d729b7a3b0bdfe45bd6bf50`;
`/tmp/security-map-shots-7/problem-teamb-43-1440.png`
sha256 `ef48bae349642c105ed725bc08986417b96cff9a9b6cc6b1f8b5b8877f69188a`;
`/tmp/security-map-shots-7/problem-teamb-43-390.png`
sha256 `2b2b2f6033f1ff4089ecb5eeaf228b10335fe4d82b7a6cbcdd8f9cc70e038581`.

Ранние скриншоты: `/tmp/security-map-shots-6/`, `/tmp/security-map-shots-5/`.

## Что осталось интеграции

- В текущем `App.tsx` нет run-switcher: отображается latest run. Поэтому
  переключение run в реальном App не воспроизведено; `SecurityMapScreen`
  ремоунтится по `runId` и сбрасывает selection/source, это проверено в
  preview changed identity.
- App.tsx и остальные frontend components принадлежат deploy-worker;
  здесь не правились.
