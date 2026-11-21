# Telegram runtime в production: причина молчания и готовый route

Проверка 2026-11-21 в worktree `telegram-live-fix` от `d23a68d`. Production
read-only: контейнеры, env, сеть, firewall, общий proxy не менялись; polling,
`getUpdates`, `sendMessage` не запускались. Токен читался только внутри
процесса/контейнера и не попадал в argv, логи или чат.

## Итог

Бот не отвечал по двум независимым причинам:

1. **Runtime вообще не запускался.** В production env нет `TELEGRAM_ENABLED`,
   а `backend/app/main.py` создаёт задачу `telegram_runtime.start()` только
   при `TELEGRAM_ENABLED=1`. Ни `getMe`, ни polling, ни ответов не было.
2. **У контейнера нет маршрута к Bot API.** Токен в контейнере есть, но
   `TELEGRAM_PROXY_URL` не задан; прямой egress к `api.telegram.org` из
   контейнера не работает, а host-прокси `127.0.0.1:14600` слушает только
   host loopback и из контейнера недоступен.

Готовый минимальный route: **host-релей на UNIX-сокете → один upstream
`127.0.0.1:14600`; каталог с сокетом монтируется в app-контейнер; приложение
поднимает loopback TCP→UNIX bridge внутри своего netns.** Каталог, а не inode
сокета: релей при рестарте удаляет и создаёт сокет заново, и mount файла
навсегда прибивал бы мёртвый inode. Ни host network, ни публичного порта, ни
изменения `zen-proxy` и firewall не требуется.

## Измеренные факты

Production-контейнер `nedra-app`, образ `localhost/nedra-app:incident-d23a68d`:

```text
TELEGRAM_BOT_TOKEN     present
TELEGRAM_ENABLED       absent
TELEGRAM_PROXY_URL     absent
network                nedra (10.89.1.0/24), gw 10.89.1.1
```

`podman logs nedra-app` не содержит ни одной строки Telegram: runtime не
стартовал. Host-прокси `127.0.0.1:14600` отвечает (HTTP 302 на
`https://api.telegram.org/`), поддерживает CONNECT.

Проверенные варианты маршрута:

| Вариант | Результат |
| --- | --- |
| `host.containers.internal:14600` (netavark) | недоступен (ConnectError/timeout): прокси слушает только loopback |
| `pasta:--map-host-loopback,169.254.1.2` | TCP до `169.254.1.2:14600` — **ok** |
| `--network nedra --network pasta:...` | podman: `selected mode pasta conflicts with bridge` — неприменимо вместе с БД |
| `slirp4netns` | не установлен на хосте |
| UNIX-сокет-релей + `--group-add keep-groups` | CONNECT 200, TLS 1.3, `getMe` — **ok** |

Живая проверка нового кода в контейнере (образ production, backend смонтирован
из worktree, токен через `--env-file`):

```text
settings proxy_mode: socket
getMe ok: chilli_nedra_bot id: 9239629524
webhook url: '' pending: 5 last_error: None
runtime proxyMode: socket botUsername: chilli_nedra_bot
```

`getWebhookInfo` безвреден: webhook не установлен (`url=""`), long polling
корректен. `pending_update_count=5` — у Telegram уже лежат недоставленные
апдейты, включая сообщение пользователя; они не съедены, пока не вызван
`getUpdates`.

Проверка ошибки без релея:

```text
blocked: telegram proxy socket unavailable: /run/nedra/missing.sock
```

## Исправления после ревью

- **Гонка `_require_client`/`close`.** `close()` теперь берёт тот же
  `_client_lock`, выставляет `_closed` и забирает client/bridge под локом.
  Живой прогон 400 гонок в production-образе: 200× `acquired+closed`,
  200× `closed+TelegramUnavailable`, после каждого раунда `_client`/`_bridge`
  `None`, все 200 поднятых listener'ов закрыты (connect refused), повторный
  `_require_client` после close даёт `TelegramUnavailable`. Отмена задачи во
  время `bridge.start()` тоже закрывает listener (`CancelledError`,
  `listener_closed True`).
- **Bind mount inode.** Монтируется каталог
  `/run/user/1000/nedra/telegram-proxy` → `/run/nedra/telegram-proxy:ro`.
  Живой прогон: `FIRST_OK` через релей (2.7s), релей остановлен и запущен
  заново (inode `236146` → `236170`), тот же API/bridge в контейнере uid 10001 +
  `keep-groups` дал `SECOND_OK` (0.6s) без перезапуска приложения.
- **Строгий upstream.** У релея больше нет `--upstream`: host и порт
  зашиты (`127.0.0.1:14600`); `--upstream 127.0.0.1:14601` отклоняется
  argparse с exit 2. Другие host-порты сконфигурировать нельзя.
- **Токен в httpx INFO.** Фильтр сначала рендерит запись, затем редактит
  `record.msg` и сбрасывает `record.args`. Живая проверка с фейковым токеном и
  `httpx.URL` в args: `token_leaked: False`,
  строка `HTTP Request: GET https://api.telegram.org/*** "HTTP/1.1" 200 OK`;
  обычные `%d`-записи форматируются как раньше.

Важно про scope: релей — обычный HTTP proxy hop, а не фильтр целей. Клиент с
доступом к сокету может отправить CONNECT на любой хост, который разрешает
upstream. Граница — один upstream `127.0.0.1:14600` плюс права UNIX-сокета;
target allowlist здесь не заявлен и не реализован.

## Что реализовано

| Файл | Назначение |
| --- | --- |
| `scripts/deploy/nedra-telegram-proxy.py` | host-релей: UNIX-сокет → зашитый loopback `127.0.0.1:14600`; upstream не настраивается |
| `deploy/systemd/nedra-telegram-proxy.service.in` | user unit, каталог `%t/nedra/telegram-proxy/`, сокет `proxy.sock` |
| `backend/app/telegram/proxy_bridge.py` | в контейнере: `127.0.0.1:<ephemeral>` → смонтированный UNIX-сокет, splice байтов |
| `backend/app/telegram/bot_api.py` | `TELEGRAM_PROXY_SOCKET`, ленивый httpx-клиент, lock-fenced close, redaction rendered logs |
| `scripts/nedra-up.sh` | preflight сокета, mount каталога сокета, `--group-add keep-groups` в rootless |
| `deploy/systemd/nedra-app.service.in` | app unit поднимает релей (`Wants/After`) |
| `scripts/nedra-systemd-install.sh` | рендерит и (при `--enable`) включает unit релея |

Правила конфигурации:

- `TELEGRAM_PROXY_URL` и `TELEGRAM_PROXY_SOCKET` взаимоисключающие; оба
  заданные — `TelegramConfigError`, runtime не стартует.
- `TELEGRAM_PROXY_SOCKET` — абсолютный путь **внутри контейнера**.
- `TELEGRAM_PROXY_SOCKET_HOST` — абсолютный путь сокета на хосте; по умолчанию
  `${XDG_RUNTIME_DIR}/nedra/telegram-proxy/proxy.sock`. Монтируется только его
  каталог; имена файла host/container обязаны совпадать.
- Сокет: режим `0660`, выделенный каталог `0750`; контейнер получает группу
  владельца через `--group-add keep-groups` (только rootless). Другие
  host-пути не монтируются, host-порты не публикуются.

## План root (после одобрения)

1. Установить/включить релей (user systemd, без root-firewall):

   ```bash
   scripts/nedra-systemd-install.sh
   systemctl --user enable --now nedra-telegram-proxy.service
   ls -l "${XDG_RUNTIME_DIR}/nedra/telegram-proxy/proxy.sock"   # srw-rw----
   ```

2. В `~/.config/nedra/runtime.env` (0600) добавить, не трогая токен:

   ```dotenv
   TELEGRAM_ENABLED=1
   TELEGRAM_PROXY_SOCKET=/run/nedra/telegram-proxy/proxy.sock
   TELEGRAM_PROXY_SOCKET_HOST=/run/user/1000/nedra/telegram-proxy/proxy.sock
   ```

   `TELEGRAM_PROXY_URL` убрать, если он появился: два маршрута одновременно
   запрещены. `TELEGRAM_PROXY_SOCKET_HOST` подставить реальный
   `XDG_RUNTIME_DIR` пользователя, от которого работает rootless podman.
   `TELEGRAM_ENABLED=1` включать только на одном контейнере с этим токеном:
   второй poller получит 409 Conflict от Telegram.

3. Пересобрать образ (в нём новый bridge) и пересоздать только app:

   ```bash
   scripts/nedra-build.sh
   scripts/nedra-up.sh --app-only --recreate-app
   ```

4. Проверить без send/polling:

   ```bash
   podman exec nedra-app python -c \
     "import asyncio; from backend.app.telegram.bot_api import TelegramBotAPI; \
      print(asyncio.run(TelegramBotAPI.from_runtime().get_me())['username'])"
   podman logs --tail 20 nedra-app | grep -i telegram
   ```

   Ожидается `chilli_nedra_bot` и `telegram runtime started as @chilli_nedra_bot`.
   `GET /api/telegram/me` под account-сессией должен вернуть
   `externalStatus="ready"`, `proxyMode="socket"`.

5. Пользователю: старый код привязки живёт 600 секунд. Если он истёк, пока
   runtime не работал, создать новый код в веб-кабинете и отправить
   `/start <код>` **или просто `<код>`** — оба варианта поддержаны.

## Откат

```bash
systemctl --user disable --now nedra-telegram-proxy.service
# убрать TELEGRAM_ENABLED/TELEGRAM_PROXY_SOCKET* из runtime.env
scripts/nedra-up.sh --app-only --recreate-app
```

Никаких изменений DNS, firewall, публичных портов или общего `zen-proxy` нет,
поэтому откат — только env + app-контейнер + unit релея.

## Ограничения

- `zen-proxy` крутит upstream-выходы; доступность Telegram зависит от него.
  Runtime честно уходит в `blocked` и ретраит, но не подменяет ошибку успехом.
- Релей не фильтрует цели CONNECT: это обычный HTTP proxy hop к
  `127.0.0.1:14600`. Клиент с доступом к сокету может ходить на любой хост,
  который разрешает upstream; allowlist целей не реализован.
- Верификация делала только `getMe`/`getWebhookInfo`; реальные `sendMessage`/
  `sendDocument` и `getUpdates` не запускались, чтобы не съесть сообщения.
- Старый код привязки мог истечь за время простоя; это UX-случай, не баг
  маршрута.
- ZIP/файлы боту не поддержаны: теперь бот явно отвечает, что нужна
  HTTPS-ссылка на GitHub/GitLab, вместо молчания.