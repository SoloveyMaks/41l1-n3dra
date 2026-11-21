# Полный аудит NEDRA: Telegram Mini UI

Дата: 2026-11-22. Область владельца: только `frontend/src/miniapp/`; backend прочитан, не изменён.

## Ревизии и итог

- Исходный exact SHA: `35d27d30ae44fd955824001e18eff895197c933c`.
- Проверенный код после правок: `5bfd61187012b15b0e91b8afcb3b22676e3b3086`.
- Коммиты: `9c858b2` (API и счётчики), `553477a` (изоляция сессий), `0360344` (SDK и привязка), `5bfd611` (детали и отчёты).
- Сборка клиента проходит; реальные границы mini router без credentials проверены через ASGI. Компоненты показаны в браузере на 390 px без API mocks. Полный Telegram → auth/bind → БД → запуск → скачивание **не проверен**.

## Охват

Прочитаны все файлы miniapp, входной `main.tsx`, общий `API_BASE`, фактические `miniapp.py`, `miniapp_auth.py`, `runtime.py`, схема `RunSummary`, импорт репозиториев, сохранение/экспорт отчётов. Проверены пути `/api/telegram/mini/{auth,bind,bootstrap,settings,runs}` и detail/report, signed initData, хранение bearer, refresh/rebind, повтор, отмена при смене экрана/сессии, WebView chrome/close, mobile CSS, открытие кабинета и blob download.

## Найденное и исправления

### 1. Терялись критические и высокие находки в сводке

**Воспроизведение/причина:** backend `_run_summary` возвращает `critical_findings` и `high_findings`, mini UI ожидает `critical_count`/`high_count`. Реальная Python-проекция локальной строки с 2 критическими и 3 высокими дала `total_findings=5, critical_findings=2, high_findings=3`; исходная `findingsSummary` вывела только `5 находок`.

**Исправление:** нормализация обоих ответов bootstrap/detail в mini API, без изменений общего frontend контракта. Medium/low/info сервер этим маршрутом не отдаёт; mini UI не выдумывает их разбивку.

**После:** compiled function probe вывела `5 находок: 2 критич. · 3 высок.`; такой же текст получен в браузерном Runs/Home компоненте. Ограничение: передавалась локальная строка через настоящую серверную проекцию, не получены данные рабочей БД.

### 2. Refresh мог смешивать новую привязку со старым экраном

**Воспроизведение/причина:** старый таймер повторно вызывал `/auth` и заменял только token. Этот endpoint заново разрешает текущую привязку; bootstrap, выбранный run и settings оставались от предыдущего аккаунта. Кроме того, `withSession` принимал завершившийся ответ уже закрытой сессии, а ошибка старого bootstrap могла инвалидировать новую.

**Исправление:** refresh запускает полный auth effect: старый bearer очищается, controller отменяется, bootstrap/detail/вкладка сбрасываются, затем загружается свежий bootstrap. `withSession` проверяет актуальность сессии после успеха и ошибки; bootstrap имеет поколение запроса. Контроллер сохраняется ещё до ожидания auth/bind, поэтому cleanup отменяет и запрос входа. `TELEGRAM_REBOUND` 409 обрабатывается как утрата сессии. Ошибка после успешного bind переводит к повтору входа, не требует повторно расходовать код.

**После:** одноразовая проба исполнила извлечённый настоящий callback до/после с отложенным результатом и настоящим AbortController: `before=accepted stale result`, `after=AbortError`. Настоящий refresh timer с минимальной задержкой 5 секунд увеличил `retryTick` ровно до 1. Source review подтверждает: ошибки входа не заводят таймер; disabled bot/account ведут на error/unbound/invalid, а не в бесконечный retry.

**Ограничение:** успешная смена привязки на сервере и bind→bootstrap network failure не воспроизведены с БД. Локальная callback-проба доказывает отбрасывание устаревшего результата, не интеграцию Telegram.

### 3. Неверный источник кода привязки

**Воспроизведение/причина:** подсказка обещала код после Start от бота, actual backend требует код из веб-кабинета; `/start` объясняет, где его создать.

**Исправление:** подсказка ведёт в веб-кабинет → Telegram → создание кода, затем ввод здесь.

**После:** браузерный GateScreen `unbound` показал новый текст, поле и disabled кнопку без кода. 390 px: document scrollWidth=390; секреты/реальные коды не вводились.

### 4. Сбой загрузки SDK сохранялся навсегда в пределах вкладки

**Воспроизведение/причина:** resolved-null `sdkPromise` кэшировался; повтор не мог увидеть позже появившийся SDK, а outside-sdk вообще не имел повтора.

**Исправление:** null результат освобождает кэш, outside-sdk получает кнопку повтора. Успешный SDK по-прежнему кэшируется.

**После:** compiled function probe: `SDK absent: true`, затем `SDK retry recovered: true` при добавлении локального SDK-объекта. В браузере настоящий MiniApp без SDK показал outside gate с повтором и **0 resource requests**.

**Ограничение:** SDK-объект в function probe — только локальная проверка кэша; официальный SDK из Telegram WebView не загружался и не считается проверенным.

### 5. Нельзя было забрать существующий отчёт failed/interrupted запуска

**Воспроизведение/причина:** `reportAvailable` разрешал только succeeded, хотя backend report route проверяет наличие сохранённого отчёта, а executor может сохранить его до последующей ошибки.

**Исправление:** для завершённого неактивного запуска доступна попытка скачивания. Отсутствие отчёта продолжает честно показывать 404 «ещё не готов».

**После:** compiled probe для failed+finished_at дала `failedReport=true`. Ограничение: реальный защищённый blob не скачивался.

### 6. В деталях скрывались усечение списка и возможность повтора

**Воспроизведение/причина:** backend отдаёт максимум 200 findings; UI показывал длину ответа без полного total. При staleError уже загруженного завершённого run кнопки повтора не было.

**Исправление:** при усечении показано `N из total`, у staleError есть повтор. Сообщение после blob click теперь «Файл передан браузеру для скачивания»: browser click не подтверждает сохранение файла на устройстве.

**После:** TypeScript/Vite build прошли, source diff проверен. Ограничение: detail polling/stale-error/download не прожиты с настоящим authenticated сервером и мобильным Telegram.

### 7. Неполная очистка имени файла и ответа сессии

**Причина:** filename очищал path-разделители, но сохранял управляющие и bidi-символы. Session exchange принимал пустой token/нефинитный expiresAt.

**Исправление:** управляющие/bidi удаляются заменой, пустой bearer и нефинитный expiry отклоняются. Время сервера остаётся авторитетным, сравнения expiry с локальными часами клиента нет.

**После:** compiled probe для RFC5987 имени с `/`, newline, NUL и bidi вернула `.._report_ _txt.jsonc`; разделители/управляющие исчезли. Build прошёл. Ограничение: текущий backend генерирует безопасное `nedra-{run_id}.jsonc`, это дополнительная защита обработки файла; повреждённые ответы сессии по HTTP не генерировались.

## Команды и фактические результаты

Все локальные пробы, копии и результаты: `/tmp/nedra-audit-miniui/`. Установки зависимостей по сети не было. Сборка использовала доступные node_modules integration-dev **только на чтение**, исходники/запись артефактов — временная копия этого checkout. Для tsc buildinfo путь в копии перенесён в `/tmp`.

```sh
git rev-parse HEAD
git status --short
# На старте: 35d27d30ae44fd955824001e18eff895197c933c; clean.

cd /tmp/nedra-audit-miniui/frontend
npm run build -- --configLoader runner --base ./
# tsc -b && vite build: exit 0, Vite 8.3.8, 2223 modules.
# dist JS 964.83 kB (gzip 268.39), CSS 152.88 kB (gzip 26.75).
# Предупреждение bundle >500 kB осталось; массовое разделение bundle вне задачи.

PYTHONPATH=. /tmp/pg-runtime-venv/bin/python /tmp/nedra-audit-miniui/router-probe.py
# Настоящий FastAPI router + httpx.ASGITransport, без mocked HTTP/auth.
# POST /auth и /bind с {}: 400 INVALID_INIT_DATA.
# GET bootstrap, PUT settings, POST runs, GET detail/report без bearer:
# каждый 401 AUTH_REQUIRED; БД/бот/импорт/исполнитель не вызваны.

node /tmp/nedra-audit-miniui/function-probe.mjs
# Настоящие compiled helpers: счётчики, filename, combined AbortSignal,
# failed report gate, retry SDK. Результаты указаны выше.
node /tmp/nedra-audit-miniui/lifecycle-run.mjs
# before accepted stale result; after AbortError.
node /tmp/nedra-audit-miniui/expiry-probe.mjs
# retryTick=1 после настоящего timer; без сетевых ответов/credentials.

git diff 35d27d30ae44fd955824001e18eff895197c933c --check
# exit 0.
```

Browser: существующая Orca вкладка `935fea27-30e6-4576-a6ab-262e5eb33e3c`, iPhone 12 390×844. Файловая навигация Orca не загрузила содержимое сама, поэтому compiled IIFE MiniApp и production CSS переданы в пустую локальную вкладку через `orca eval`; entrypoint в репозитории не изменялся. Отдельный временный entry показывает GateScreen/RunsTab/HomeTab/SettingsTab с явно локальными props; это **компонентная проверка**, не end-to-end и не authenticated MiniApp.

Фактические результаты: outside gate и unbound/runs/home/settings имеют scrollWidth=390 при innerWidth=390. Дополнительный замер полей, кнопок, длинного displayName Home/Settings не нашёл элементов за краями или переполнения (`clipped=[]`). Backend ответы в браузере не подменялись, никаких успешных auth/run/settings/download там не изображалось.

Первичные неуспехи проверки: неполная временная копия не разрешала sibling docs/ui imports — скопированы текущие docs/ui; tsc пытался писать buildinfo в read-only borrowed node_modules — перенесён только путь временного tsconfig; default Playwright executable отсутствовал. Доступный локальный Chromium не стартовал в sandbox (`setsockopt Operation not permitted`, SIGTRAP); запуск не повторялся. Standalone library build сначала оставил process.env.NODE_ENV — production define добавлен в **временный harness**, после этого compiled MiniApp реально запустился в уже существующем Orca browser.

## Безопасность и ограничения

- Bearer остаётся приватным полем MiniApi в памяти, теперь явно очищается при invalidation/restart/unmount. InitData и bind code передаются только JSON body; cookies omitted, cache no-store. Нет localStorage/sessionStorage/URL bearer и нет использования initDataUnsafe как identity.
- Сообщённый координатором переход backend на v2/payload2 не требует парсинга на клиенте: bearer opaque. Старый bearer очищается, повтор входа использует текущий `WebApp.initData`. Проверка reauth с реальным v2 backend остаётся открытой; в проверяемой базе находится исходный серверный контракт.
- Cabinet URL строится из собственного origin; находки/evidence — React text, HTML не исполняется. Report URL кодирует runId, blob не открывается через Telegram openLink с bearer в URL.
- У mini backend нет cancel-run endpoint. Отмена локальных detail/download запросов при уходе реализована; это не обещание отменить уже созданный серверный запуск.
- Download через blob anchor может зависеть от поддержки Telegram Android/iOS; фактическое сохранение файла не проверено. UI больше не заявляет его подтверждённым.
- Не использованы реальные Telegram credentials, production БД, изменение сервиса 18080, меню бота, контейнеров, volumes, служб или сети. Новые серверы/порты не запускались. `podman start nedra-mini-integration-pg` и отказ 403 не повторялись.
- Открытый интеграционный блокер: в разрешённых границах нет изолированного доступного Telegram+backend+БД для положительного auth/bind/bootstrap/settings/run/detail/report. Об этом отправлено координатору `msg_02842fa03497`; браузерный компонентный прогон не выдаётся за закрытие этого пробела.
