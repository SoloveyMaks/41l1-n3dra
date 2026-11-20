# Локальный деплой nedra

Локальный компьютер — единственный хост приложения: если он выключен или спит,
`nedra.soloveyka.bond` и loopback-приложение недоступны. Публикация DNS и запуск
туннеля выполняются координатором отдельно, после готовности backend auth.
Сейчас ничего наружу не открыто.

## Что собирается

| Слой | Где живёт | Доступ |
|---|---|---|
| React + Vite build | внутри образа `localhost/nedra-app:local` | статика из FastAPI |
| FastAPI | контейнер `nedra-app` | только `127.0.0.1:18080` на хосте |
| PostgreSQL 16 + pgvector | контейнер `nedra-db` | только приватная сеть `nedra`, host-портов нет |
| Данные | volumes `nedra-db-data`, `nedra-app-data` | переживают перезапуск контейнеров |

Браузер никогда не подключается к БД напрямую: у `nedra-db` нет публикации
портов. Снаружи (после публикации) будет доступен только HTTPS-туннель на
backend.

## Образ

`deploy/Dockerfile` — multi-stage:

1. `frontend-build` на закреплённом Node image: `npm ci` по
   `frontend/package-lock.json`, копирование sibling-пакетов `ui/security-map` и
   `docs` (карте нужны production-исходники и контрактные типы), `npm run build`.
2. `python-wheels` на закреплённом Python 3.12 image: сборка wheel-кэша по
   `deploy/requirements.txt`.
3. `runtime`: установка только из локальных wheel, копирование `backend` и
   `frontend/dist`, non-root пользователь `nedra` (uid 10001), read-only rootfs
   при запуске, writable только `/app/data` и tmpfs `/tmp`.

Закреплены digest'ы базовых образов и все Python-пакеты. Обновление зависимостей —
осознанная правка `deploy/requirements.txt` и digest'ов, а не `latest`.

`deploy/requirements.txt` — deployment lock, собранный по смыслу
`backend/requirements.txt` из `task/postgres-runtime` (FastAPI, uvicorn,
pydantic, httpx, multipart, croniter, tree-sitter, psycopg). Когда backend
requirements вольются в ветку, lock нужно сверить; backend-код этим пакетом не
меняется.

## Секреты

Источник — `~/.omp/agent/.env`, оттуда читается **только**
`REFORMBOSS_API_KEY`. WikiVibe GPT key не читается и не копируется.

`scripts/nedra-bootstrap.sh --write` создаёт `~/.config/nedra/runtime.env`:

- каталог `700`, файл `600`;
- `REFORMBOSS_API_KEY`, `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`,
  `DATABASE_URL`;
- пароль БД генерируется локально и сохраняется при повторном запуске;
- значения не печатаются, не попадают в git, argv или логи;
- backend получает файл через `--env-file`, пароль БД — через `--env NAME`
  (значение не в argv).

Секреты не коммитятся. Проверить можно только имена ключей:

```bash
sed -n 's/^\([A-Z_]*\)=.*/\1/p' ~/.config/nedra/runtime.env
```

Ротация ключа: `scripts/nedra-bootstrap.sh --write --refresh-key`, затем
`scripts/nedra-down.sh && scripts/nedra-up.sh`. `podman restart` не перечитывает
`--env-file`, поэтому backend нужно пересоздать.

## Команды

```bash
# 1. Проверка без записи секретов и без запуска контейнеров
scripts/nedra-bootstrap.sh --check

# 2. Запись runtime.env (один раз; повторный запуск сохраняет пароль БД)
scripts/nedra-bootstrap.sh --write

# 3. Сборка образа
scripts/nedra-build.sh

# 4. Запуск БД и backend (идемпотентно)
scripts/nedra-up.sh

# 5. Проверка
scripts/nedra-status.sh

# 6. Остановка без удаления данных
scripts/nedra-down.sh
```

`nedra-up.sh --build` пересоберёт образ перед запуском. Скрипты трогают только
контейнеры/сеть/volumes с префиксом `nedra-`: чужие контейнеры, `supabase_*`,
туннель `brigada` и `~/.cloudflared/config.yml` не изменяются.

## Health и статус

- `nedra-status.sh` показывает контейнеры, HTTP `/api/runs`, Docker healthcheck
  backend, `pg_isready`, версию pgvector и отсутствие host-портов у БД.
- В образе есть `HEALTHCHECK` на `http://127.0.0.1:18080/api/runs`; образ
  собирается в формате `docker`, иначе Podman игнорирует healthcheck.
- Логи: `podman logs -f nedra-app`, `podman logs -f nedra-db`.

## PostgreSQL и pgvector

БД создаётся из `docker.io/pgvector/pgvector:pg16` (digest закреплён).
`deploy/db/init/001-extensions.sql` включает `vector` при первой инициализации
volume. Порт 5432 не публикуется: backend ходит по адресу `nedra-db:5432` внутри
сети `nedra`.

Миграции контрактной схемы и runtime-слоя применяет backend при старте после
merge `task/postgres-runtime` (`python -m backend.app.storage.migrate` также
доступен). В текущей ветке `app-map-integration` backend ещё на SQLite и, кроме
того, не содержит `backend/app/artifacts/intake.py`; `backend/app/api/router.py`
импортирует этот модуль, поэтому контейнер падает с
`ModuleNotFoundError: No module named 'backend.app.artifacts'`. Это дефект
backend-ветки, а не deploy-скриптов: образ собирается, Python-зависимости и
frontend проходят проверку, БД здорова, но приложение не стартует до merge
backend-изменений из `dev`/`task/postgres-runtime`. `DATABASE_URL` уже
передаётся, поэтому после merge отдельный deploy-конфиг не потребуется.

## Что проверено в этой ветке

- `scripts/nedra-bootstrap.sh --check` — dry-run без записи секретов и запуска:
  ключ найден, порт 18080 свободен, образы/сеть/volumes проверены, cloudflared и
  cert.pem доступны.
- `scripts/nedra-bootstrap.sh --write` — создан
  `~/.config/nedra/runtime.env` (каталог 700, файл 600); повторный запуск
  сохранил пароль БД; WikiVibe key не читался.
- `scripts/nedra-build.sh` — образ `localhost/nedra-app:local` собран
  (`4ebc429296cd...`), три стадии; внутри: `fastapi 0.165.0`,
  `pydantic 2.14.4`, `psycopg`, `tree-sitter`, `frontend/dist`, uid 10001,
  healthcheck в формате docker.
- `nedra-db` из закреплённого pgvector-образа: healthy, `pgvector 0.8.7`,
  нулевые host-порты, volume переживает restart (маркер и extension на месте).
- `scripts/nedra-tunnel.sh status` — read-only: туннеля `nedra` и DNS
  `nedra.soloveyka.bond` нет, `brigada` не тронут, публикация не выполнялась.
- `scripts/nedra-up.sh` доходит до запуска containers и останавливается на
  health backend из-за отсутствующего `backend.app.artifacts` (см. выше);
  контейнер БД при этом исправен.

## Туннель Cloudflare (не опубликован)

Домен `soloveyka.bond` подтверждён, cert лежит в `~/.cloudflared/cert.pem`.
Существующий туннель `brigada` (`e5999ace-...`) не трогается; его конфиг
`~/.cloudflared/config.yml` не изменяется.

Для nedra готов отдельный конфиг-шаблон
`deploy/cloudflared/nedra.yml.template` и скрипт `scripts/nedra-tunnel.sh`.
Публикация **не выполнялась**:

```bash
# read-only осмотр
scripts/nedra-tunnel.sh status

# далее — только когда координатор разрешит публикацию:
scripts/nedra-tunnel.sh create --yes     # создаёт tunnel nedra в Cloudflare
scripts/nedra-tunnel.sh config           # пишет ~/.config/nedra/cloudflared-nedra.yml (600)
scripts/nedra-tunnel.sh route --yes      # публикует DNS nedra.soloveyka.bond
scripts/nedra-tunnel.sh run --yes        # запускает connector
```

Ingress: `nedra.soloveyka.bond -> http://127.0.0.1:18080`, остальное — 404.
Перед `route`/`run` должен быть готов backend auth и live check; иначе наружу
уйдёт неаутентифицированный API.

## Ограничения и границы

- Локальный хост должен быть включён; после перезагрузки контейнеры с
  `--restart=unless-stopped` поднимутся сами, туннель нужно запускать заново
  (systemd-юнит не добавлялся, чтобы не менять системные сервисы).
- Сборка требует сеть для первого `podman build` (digest pull) и `npm ci`;
  дальше слои кэшируются.
- Backend в этой ветке не использует PostgreSQL; это не ошибка deploy-скриптов,
  а ожидание merge `task/postgres-runtime`.
- Секреты и БД не входят в git; volume'ы удаляются только вручную и осознанно
  (`podman volume rm`), скрипты этого не делают.