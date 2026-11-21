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

## Поведение доставки

- По умолчанию `enabled=false`: ни одного внешнего POST, запись о завершении
  получает `delivery_status=skipped` и остаётся в журнале.
- `skipped` никогда не переписывается в `delivered`. Успех — только подтверждение
  2xx от приёмника.
- После завершения запуска строка outbox хранит снимок настроек. Если настройки
  выключили или URL изменили до отправки — `skipped`, запрос не уходит.
- Повтор при сетевой ошибке и HTTP 408/429/5xx — до 3 попыток, затем `failed`.
- `allow_local=true` разрешает только loopback (`127.0.0.1`, `::1`).
- `allow_external=true` разрешает только `https` и порт 443, публичные адреса.
  Private/reserved/link-local/metadata отклоняются.
- Query и fragment в URL запрещены: токены в ссылке не поддерживаются.
- Соединение идёт на проверенный IP с SNI/Host исходного hostname (анти-rebinding).

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