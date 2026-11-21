# Локальный деплой nedra

Локальный компьютер — единственный хост приложения: если он выключен или спит,
`nedra.soloveyka.bond` и loopback-приложение недоступны. Публичный вход —
`https://nedra.soloveyka.bond` через отдельный Cloudflare tunnel `nedra`;
backend auth (access code + server session secret) включён.

## Что собирается

| Слой | Где живёт | Доступ |
|---|---|---|
| React + Vite build | внутри образа `localhost/nedra-app:local` | статика из FastAPI |
| FastAPI | контейнер `nedra-app` | только `127.0.0.1:18080` на хосте |
| PostgreSQL 16 + pgvector | контейнер `nedra-db` | только приватная сеть `nedra`, host-портов нет |
| Данные | volumes `nedra-db-data`, `nedra-app-data` | переживают перезапуск и смену контейнера |

Браузер никогда не подключается к БД напрямую: у `nedra-db` нет публикации
портов. Снаружи (после публикации) будет доступен только HTTPS-туннель на
backend.

## Образ

`deploy/Dockerfile` — multi-stage:

1. `frontend-build`: закреплённый Node image, `npm ci` по
   `frontend/package-lock.json`, копирование sibling-пакетов `ui/security-map` и
   `docs` (карте нужны production-исходники и контрактные типы), `npm run build`.
2. `python-wheels`: закреплённый Python 3.12 image, wheel-кэш по
   `deploy/requirements.txt`.
3. `runtime`: установка только из локальных wheel, копирование `backend`, `db`
   (миграции ledger), `docs` (canonical `contracts.schema.json`), `packages/comparison`,
   healthcheck, `frontend/dist`; non-root `nedra` (uid 10001), read-only rootfs,
   writable `/app/data` и tmpfs `/tmp`.

### Node 22 для comparison

`packages/comparison` вызывает Node-модули, поэтому в runtime добавлен отдельный
stage `node-runtime` из того же pinned Node image; в финальный образ копируется
только `/usr/local/bin/node` (без npm/npx и без runtime `npm install`).
`packages/comparison` копируется в `/app/packages/comparison`, а
`docs/data-contracts/validate-bundle.mjs` уже лежит в `/app/docs`.

Python slim (Debian 13) содержит нужные shared libs (`libstdc++`, `libgcc_s`,
`libm`, glibc); сборка проверяет это через `RUN node --version`. Backend вызывает
CLI как bounded child process без shell (`subprocess.run` с timeout 60), temp JSON
создаёт сам backend; image задаёт `CG_NODE_BIN=/usr/local/bin/node`.

`CG_DATA_DIR=/app/data` задан и в образе, и в `runtime.env`. Intake и codefacts
используют этот корень (`data_root()`), поэтому артефакты, распаковка, snapshot
manifest и SQLite fallback лежат на volume `nedra-app-data`, а не в слое
контейнера.

Закреплены digest'ы базовых образов и все Python-пакеты. Обновление зависимостей —
осознанная правка `deploy/requirements.txt` и digest'ов, а не `latest`.

`deploy/requirements.txt` — deployment lock по смыслу
`backend/requirements.txt` из `task/postgres-runtime`. Когда backend requirements
меняются, lock нужно сверить.

## Секреты и окружение

Источник — `~/.omp/agent/.env`. Читается **только** `REFORMBOSS_API_KEY`;
WikiVibe GPT key не читается и не копируется. Парсер
`scripts/nedra_env.py` — единственный парсер dotenv: поддерживает
`export KEY=value`, одинарные/двойные кавычки и full-line комментарии, не
запускает shell и не печатает значения. Путь источника можно переопределить
через `NEDRA_SECRET_SOURCE`. `load_runtime_env` не делает `source`: Python
отдаёт NUL-delimited `KEY/value`, Bash валидирует имя и делает `export` без eval.

Канонический `runtime.env` — только строки `KEY=value` без `export`, кавычек и
inline-комментариев: ровно то, что читает `podman --env-file`. Существующие
quoted/export значения парсятся и переписываются в этот вид с сохранением
семантики (пробелы и `#` внутри значения остаются частью значения).

`scripts/nedra-bootstrap.sh --write` создаёт/дополняет
`~/.config/nedra/runtime.env`:

- каталог `700`, файл `600`;
- существующие custom `CG_*`/`VECTOR_*` значения сохраняются семантически;
- `REFORMBOSS_API_KEY`, `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`,
  `DATABASE_URL` — обязательные; без `--refresh-key` существующий
  `REFORMBOSS_API_KEY` сохраняется, источник читается только если ключа нет;
- `CG_SESSION_SECRET`, `CG_ANCHOR_HMAC_KEY`, `CG_ACCESS_CODE` генерируются
  только если отсутствуют и дальше сохраняются;
- `CG_DATA_DIR=/app/data`, `CG_PUBLIC_DEMO=1`,
  `CG_ALLOWED_ORIGINS=https://nedra.soloveyka.bond`, `CG_TRUST_PROXY=1`
  (cloudflared передаёт `X-Forwarded-Proto`, поэтому Secure cookie),
  `AI_PROVIDER=reformboss`, `AI_MODEL=deepseek-v4.1-flash-1017`,
  `VECTOR_RETRIEVAL_ENABLED=0`;
- значения не печатаются, access code не выводится ни в `--check`, ни в
  `--write`; model/key передаются только через env-file, не argv.

Секреты не коммитятся. Посмотреть можно только имена ключей:

```bash
sed -n 's/^\([A-Z_]*\)=.*/\1/p' ~/.config/nedra/runtime.env
```

Ротация access code: удалить строку `CG_ACCESS_CODE` из файла и запустить
`--write` (остальные секреты сохранятся), затем пересоздать backend.
Ротация ключа: `--write --refresh-key`, затем `nedra-down.sh && nedra-up.sh`;
`podman restart` не перечитывает `--env-file`.

## Команды

```bash
# 1. Проверка без записи секретов и запуска
scripts/nedra-bootstrap.sh --check

# 2. Создать/дополнить runtime.env (сохраняет custom CG_*/VECTOR_*)
scripts/nedra-bootstrap.sh --write

# 3. Собрать образ
scripts/nedra-build.sh

# 4. Запустить БД и backend (идемпотентно)
scripts/nedra-up.sh

# 5. Статус/health
scripts/nedra-status.sh

# 6. Остановить без удаления volumes
scripts/nedra-stop.sh      # только stop
scripts/nedra-down.sh      # stop + rm только своих контейнеров
```

`nedra-up.sh` понимает `--db-only`, `--app-only`, `--build` и
`--recreate-app`. Если `runtime.env`/образ изменились, а backend запущен, обычный
`up` откажется его трогать; `--recreate-app` сначала штатно остановит, затем
удалит и пересоздаст **только** контейнер с label `io.nedra.owner=nedra`.
Volumes и данные не удаляются. `nedra-stop.sh` и `nedra-down.sh` тоже проверяют
owner label и не используют `podman rm -f`; чужие контейнеры не трогаются.

## Health и статус

- `/api/health` — строгий публичный health без session-cookie. Fallback на
  `/api/runs` удалён: отсутствие endpoint или auth-ошибка дают `unhealthy`, а не
  ложный успех.
- `scripts/nedra-status.sh` показывает контейнеры, `/api/health`,
  Docker healthcheck, `pg_isready`, версию pgvector, `CG_DATA_DIR`,
  `CG_PUBLIC_DEMO`, `VECTOR_RETRIEVAL_ENABLED`, `AI_MODEL` и отсутствие
  host-портов у БД.
- `HEALTHCHECK` встроен в образ (`deploy/healthcheck.py`); образ собирается в
  формате `docker`, иначе Podman игнорирует healthcheck.
- Логи: `podman logs -f nedra-app`, `podman logs -f nedra-db`.

## PostgreSQL и pgvector

БД создаётся из `docker.io/pgvector/pgvector:pg16` (digest закреплён).
`deploy/db/init/001-extensions.sql` включает `vector` при первой инициализации
volume. Порт 5432 не публикуется: backend ходит по `nedra-db:5432` внутри сети
`nedra`.

`backend` при старте применяет `db/migrations/*.up.sql` через ledger
`public.schema_migrations` (для этого `db/` теперь есть в образе). Проверено:
001, 002 и 004 применились, `vector 0.8.7` доступен.

Optional `db/optional/003_exact_vector.up.sql` — зона PG worker, не backend и не
deploy. Deploy не выполняет ручной SQL и не создаёт `example_embeddings` сам;
`CREATE EXTENSION vector` уже идемпотентен, а 003 применится из ledger после
изменения storage-владельцем.

## Systemd user units (rootless)

`scripts/nedra-systemd-install.sh` рендерит
`~/.config/systemd/user/nedra-{db,app,tunnel}.service` из `deploy/systemd/*.in`,
делает `daemon-reload`. Юниты вызывают те же скрипты; owner labels и volumes
соблюдаются.

```bash
scripts/nedra-systemd-install.sh            # установить, не включать
scripts/nedra-systemd-install.sh --enable   # включить db/app на login/boot
scripts/nedra-systemd-install.sh --enable --start
```

`nedra-tunnel.service` **не включается и не запускается** установщиком. Для
старта без интерактивного логина нужен `loginctl enable-linger $USER`; это
глобальная настройка пользователя, поэтому она не включается автоматически.
`podman-restart.service` сам по себе не поднимает rootless-контейнеры без
user-manager/linger.

## Cloudflare: опубликовано

Публичный вход: **https://nedra.soloveyka.bond** (ingress только на
`http://127.0.0.1:18080`, остальное — 404). Используется отдельный туннель
`nedra` `7e72751b-bc39-4e7d-b0fc-6ef1e67e7a2b`; credentials
`~/.cloudflared/7e72751b-....json` и config
`~/.config/nedra/cloudflared-nedra.yml` имеют mode `600`.

Системный unit `nedra-tunnel.service` установлен из `integration-dev`, включён и
active. DNS опубликован только для `nedra.soloveyka.bond`. Существующий туннель
`brigada` (`e5999ace-...`), его config и root DNS не изменялись. `route` теперь
вызывает `cloudflared tunnel route dns <tunnel-id> <hostname>` без
`--overwrite-dns`, чтобы чужой route не перезаписывался.

Фактическая доступность: туннель поднят по `http2` (precheck: QUIC PASS, TCP
HTTP/2 PASS; в config выбран `http2` как primary). Внешние проверки:
`GET /api/health` 200; `GET /api/runs` без cookie 401; `GET /api/session` без
`Set-Cookie`; неверный код 401; верный код 200 с
`Secure; HttpOnly; SameSite=Lax`; авторизованные runs/map/evidence и SQL proof
200. Cloudflare может отдавать 403 на дефолтный `Python-urllib` User-Agent;
браузерный/curl UA проходит, это не app-auth.

PC-хост должен быть включён. `Linger=no`: после reboot db/app/tunnel поднимутся
после входа пользователя (units enabled), либо нужно один раз выполнить
`loginctl enable-linger $USER`. Туннель без PC/интернета недоступен.

## Проверено реальной сборкой

Текущий loopback/публичный образ собран из `integration-dev` HEAD `c01c96d`
(worktree не изменялся):

- `localhost/nedra-app:local` = `c322a7f6d1e3...`, revision `c01c96d`,
  Node `v22.23.5`, `db/`, `docs/`, `packages/comparison`, healthcheck,
  Python wheels и `frontend/dist` внутри.
- Backend стартовал, применил ledger 001/002/004/005; `vector 0.8.7`,
  `published_bundles=1`.
- Auth: `GET /api/health` 200; `GET /api/runs` без сессии 401;
  `GET /api/session` 200 без `Set-Cookie`; `POST /api/session` с access code 200
  и cookie; неверный код 401.
- Реальный multipart upload: `art_989a260ea1d5` (8 файлов, 5 Kotlin), run
  `15dc34ba-...` succeeded/verdict fail/13 findings; `security-map` 200 schema
  0.1 (10 nodes, 9 edges, 26 evidence, 13 findings), evidence ready.
- Systemd user units переустановлены из `integration-dev`; app unit cycle прошёл,
  artifact в volume `nedra-app-data` сохранился. Tunnel unit enabled/active,
  DNS опубликован только для `nedra.soloveyka.bond`.
- Node-образ `node-verify` (`7874a386f36b...`, 20 шагов) содержит
  `node v22.23.5`; committed
  `examples/demo/sql-before-after/{baseline,current}.published.json` прогнаны
  через `/app/packages/comparison/compare-cli.mjs` внутри образа. Результат
  равен committed `comparison.json` (`comparable=true`, 1 item
  `resolved/unique_anchor`, `deletedFiles=[]`). Это реальный CLI-прогон, не fake
  envelope.

## Ограничения

- Локальный хост должен быть включён; после reboot db/app поднимутся через
  systemd user manager (при linger) или после логина.
- Сборка требует сеть для первого `podman build` (digest pull) и `npm ci`.
- `CG_PUBLIC_DEMO=1` требует access code; без него backend не стартует. Код
  хранится только в `runtime.env` и никогда не печатается.
- Optional 003 — зона PG worker; deploy не выполняет его SQL.
- Публичный образ `c322a7f6d1e3` (HEAD c01c96d) содержит Node и comparison;
  новые remote analyzers add-only из `dev66fec77` в runtime не включены и cg26
  не заявляется.
- Volumes удаляются только вручную и осознанно; скрипты этого не делают.