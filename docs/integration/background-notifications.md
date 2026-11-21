# Фоновые уведомления: точки интеграции

Фича живёт в `backend/app/notifications/*` и `frontend/src/components/NotificationCenter.tsx`.
Общие файлы в этой ветке не тронуты: применить патч может владелец `main.py`/`App.tsx`.

```bash
git apply docs/integration/background-notifications-hooks.patch
```

## Что делает патч

`backend/app/main.py`:

1. импортирует `NotificationRuntime` и `create_notification_router`;
2. в lifespan до `yield` вызывает `await notifications_runtime.start()` — это
   идемпотентно ставит SQL-триггер на `runtime.runs` и запускает фоновый воркер
   исходящей доставки;
3. в `finally` останавливает воркер;
4. подключает роутер с префиксом `/api` до `StaticFiles`, иначе SPA-монтаж
   перехватит `/api/notifications`.

`frontend/src/App.tsx`: монтирует `<NotificationCenter>` в пользовательский и
админский layout. Клик по уведомлению открывает соответствующий запуск.

## Контракт API

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/api/notifications` | журнал завершённых запусков + `unread` |
| `POST` | `/api/notifications/{runId}/read` | отметить одно прочитанным |
| `POST` | `/api/notifications/read-all` | отметить всё прочитанным |
| `GET` | `/api/notifications/settings` | opt-in настройки отправки |
| `PUT` | `/api/notifications/settings` | сохранить opt-in настройки |

`GET/PUT /settings` требуют подтверждённого аккаунта или demo-admin
(`require_workspace_actor`). Остальные методы scoped по workspace из сессии.

`GET/PUT /settings` никогда не возвращают полный URL: только
`webhook_url: ""`, `webhook_configured`, `webhook_masked` (`https://host/…`) и
`webhook_ref`. Полный адрес (включая path-токен) хранится на сервере. Пустое
поле `webhook_url` в `PUT` означает «оставить сохранённый адрес без изменений»;
это позволяет редактировать галочки, не зная секрет.

## Поведение доставки

- По умолчанию `enabled=false`: ни одного внешнего POST, запись о завершении
  получает `delivery_status=skipped` и остаётся в журнале.
- `skipped` никогда не переписывается в `delivered`. Успех — только подтверждение
  2xx от приёмника.
- После завершения запуска строка outbox хранит снимок настроек.

### Граница отмены (важно)

Сохранение настроек и разрешение отправки сериализуются одним
per-workspace advisory lock (`store.save_settings` / `store.authorize`):

1. воркер берёт lease и проверяет, что настройки всё ещё включены и URL не
   изменился;
2. под тем же lock ставится `authorized_at` — только после этого разрешён POST;
3. disable/смена URL, закоммиченные **до** шага 2, переводят строку в `skipped`,
   запрос не начинается;
4. disable, закоммиченный **после** шага 2, не отменяет уже начатый HTTP-запрос.
   Он доходит до конца, а журнал пишет реальный `delivered`/`failed`, а не
   фиктивный `skipped`.

Отменить уже отправленный HTTP-запрос нельзя; код этого и не обещает. Поздний
воркер со старым lease не может ни авторизовать отправку, ни переписать журнал:
`finish` требует совпадения lease-token и `authorized_at IS NOT NULL`.

- Повтор при сетевой ошибке и HTTP 408/429/5xx — до 3 попыток, затем `failed`.
- `allow_local=true` разрешает только loopback (`127.0.0.1`, `::1`).
- `allow_external=true` разрешает только `https` и порт 443. Любой resolved
  non-global адрес отклоняется, включая CGNAT `100.64.0.0/10`, private,
  link-local, metadata, reserved, unspecified и multicast.
- Query и fragment в URL запрещены: токены в ссылке не поддерживаются.
- DNS-проверка возвращает IP, соединение идёт на этот pinned IP с SNI/Host
  исходного hostname; redirects выключены, 3xx считается ошибкой.
- Некорректный hostname/IDNA-лейбл — контролируемый 422, не 500.
- Ошибки резолвера, HTTP-тело и полный URL не попадают ни в API, ни в журнал,
  ни в логи.

## Состояние журнала в UI

`NotificationCenter` не прячет ошибки фонового polling. Если сервер вернул 503 или
сеть недоступна, панель показывает «Сервис уведомлений временно недоступен» и
пометку «данные могут быть устаревшими». При наличии последних успешных данных
список сохраняется, а при пустом кэше явно пишется «Журнал недоступен», а не
«Завершённых запусков пока нет». Кнопка «Повторить» делает явный refresh.

## Интеграция root-файлов

`backend/app/main.py` уже подключён в этой ветке: `NotificationRuntime`
стартует в lifespan внутри `try` (сбой инициализации всё равно останавливает
scheduler/telegram и закрывает пул БД), останавливается в `finally`, роутер
смонтирован как `include_router(create_notification_router(), prefix="/api")` до
`StaticFiles`.

`frontend/src/App.tsx` ещё не изменён; его владелец применяет только frontend-hunk
патча:

```bash
git apply --exclude=backend/app/main.py docs/integration/background-notifications-hooks.patch
```

Патч обновлён под текущие API (`NotificationRuntime`,
`create_notification_router`, `handleSelectRun`, `setActiveTab`,
`setAdminSection`); перед применением — `git apply --check` и code review.

## Проверка без сторонних адресатов

Локальный приёмник:

```bash
python - <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        print(self.path, self.headers.get("idempotency-key"), self.rfile.read(n).decode())
        self.send_response(204); self.end_headers()
HTTPServer(("127.0.0.1", 9099), H).serve_forever()
PY
```

В настройках уведомлений: включить отправку, URL
`http://127.0.0.1:9099/notify`, галочка «Разрешить локальный приёмник».