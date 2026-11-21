# Telegram deployment: Bot API route из `nedra-app`

Проверка read-only: контейнеры/сеть/прокси не менялись, Telegram polling и
send не запускались. Токен читался только внутри скрипта из 0600-файла и не
печатался/не передавался в argv; выполнялся только `getMe`.

## Итог

Существующего безопасного route из контейнера `nedra-app` к Bot API через
`127.0.0.1:14600` **нет**. Host-side прокси исправен, но слушает только host
loopback; rootless-контейнер до него не достаёт. В контейнере также нет
Telegram env вообще, а его config hash отличается от текущего `runtime.env`.

## Измеренные факты

Host (baseline, токен не выводился):

```text
host-proxy-14600 status 200 ok True
```

Контейнер `nedra-app` (без внешних send/polling):

```text
container-local-127     error ConnectError
host-gw-10.89.1.1       error ConnectError
host.containers.internal error ConnectTimeout
container-direct        error ConnectError
```

Сеть:

- rootless podman, backend `netavark`;
- сеть `nedra`: `10.89.1.0/24`, gateway `10.89.1.1`,
  `nedra-app` = `10.89.1.138`;
- `/etc/hosts` контейнера: `169.254.1.2 host.containers.internal`, но TCP до
  `169.254.1.2:14600` — timeout;
- `zen-proxy` слушает `127.0.0.1:14600` (только loopback) и supports CONNECT;
- контейнерный direct egress к `api.telegram.org` — ConnectError.

Env:

- `podman inspect nedra-app`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_PROXY_URL`,
  `TELEGRAM_ENABLED` отсутствуют;
- `~/.config/nedra/runtime.env` (0600) содержит `TELEGRAM_BOT_TOKEN`, но не
  `TELEGRAM_PROXY_URL`; `TELEGRAM_ENABLED` тоже нет;
- config hash контейнера не совпадает с текущим `runtime.env`, то есть
  `nedra-up.sh` без `--recreate-app` не применит новые env.

## Скрипты

- `scripts/nedra-up.sh` передаёт app `--env-file runtime.env`, публикует только
  `127.0.0.1:18080:18080`; Telegram proxy не публикует и не настраивает.
- `scripts/nedra-lib.sh` загружает runtime.env парами KEY/VALUE, обязательными
  считает только core-ключи (`DATABASE_URL`, `CG_*`); Telegram-ключи не
  валидирует.
- `scripts/nedra_env.py` умеет читать один ключ, но в `nedra-up.sh` для
  Telegram не используется.
- Вывод: route/proxy wiring в deployment-скриптах отсутствует; всё, что нужно
  для Bot API, сейчас должно приходить из env и host-прокси.

## Минимальные варианты (не применены)

### A. Rootless pasta host-loopback (предпочтительный по изоляции)

Оставить app на сети `nedra` и добавить pasta-интерфейс с пробросом host
loopback-порта, например:

```text
--network nedra --network pasta:-T,14600
TELEGRAM_PROXY_URL=http://127.0.0.1:14600
```

`pasta:-T,14600` по man podman-run предназначен для forwarding TCP-порта из
контейнера к host через loopback. Плюс: никаких публичных портов, host-прокси
остаётся на `127.0.0.1`. Минус/риск: надо проверить в throwaway-контейнере, что
multi-network `nedra`+pasta сохраняет DNS/маршрут к `nedra-db` и health backend;
при неудаче — не применять. Требуется `--recreate-app`, т.к. меняется network
и env.

### B. Bind proxy на host IP + source-filter

Разрешить `zen-proxy` слушать дополнительный адрес (`0.0.0.0` или host LAN IP)
и добавить nftables-правило: порт 14600 только из `10.89.1.0/24`, drop для
остальных. Затем `TELEGRAM_PROXY_URL=http://10.89.1.1:14600` (если rootless
bridge доставляет до host listener). Плюс: app network/DB не меняется. Минус:
меняется общий zen-proxy и firewall, порт виден локальной сети до фильтра;
root должен подтвердить, что rootless netavark gateway действительно доставляет
до host listener.

### C. Host network для app

`--network=host` даст доступ к `127.0.0.1:14600` напрямую, но убирает сетевую
изоляцию контейнера и меняет DB-маршрут; для production не рекомендуется.

## Что нужно root

1. Выбрать A или B (A предпочтительнее, если pasta multi-network проходит
   проверку).
2. Добавить в `runtime.env` `TELEGRAM_PROXY_URL` и `TELEGRAM_ENABLED=1`
   (токен там уже есть; при использовании файла — `TELEGRAM_BOT_TOKEN_FILE`).
3. Пересоздать только `nedra-app` по штатному пути `scripts/nedra-up.sh
   --recreate-app` после теста сети.
4. Проверить тем же безопасным `getMe` (не polling/send) и `/api/telegram/me`.

Никаких публичных портов/туннелей/Cloudflare/Brigada не требуется.
