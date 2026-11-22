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
3. `runtime`: установка только из локальных wheel, копирование `backend`,
   `analyzers`, `rag` (локальные Python-пакеты), `db` (миграции
   ledger), `docs` (canonical `contracts.schema.json`), `packages/comparison`,
   healthcheck, `frontend/dist`; non-root `nedra` (uid 10001), read-only rootfs,
   writable `/app/data` и tmpfs `/tmp`. Пакет `analyzers` копируется рядом с
   `backend` для будущего optional `config.engine=team_b`; default engine не
   меняется, adapter не считается активным, пока это не подтверждено отдельной
   сборкой.

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
`backend/requirements.txt`. Когда backend requirements
меняются, lock нужно сверить.

## Секреты и окружение

Источник — `~/.omp/agent/.env`. Bootstrap ищет AI-ключ в порядке
`OPENCODE_API_KEY`, `AI_API_KEY`, `REFORMBOSS_API_KEY`; Go и generic ключи
читаются только из окружения/источника, продукт не открывает credential DB.
Парсер `scripts/nedra_env.py` — единственный парсер dotenv: поддерживает
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
- один выбранный AI-ключ (`OPENCODE_API_KEY`, `AI_API_KEY` или legacy
  `REFORMBOSS_API_KEY`), `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`,
  `DATABASE_URL` — обязательные; без `--refresh-key` существующий AI-ключ
  сохраняется, источник читается только если ключа нет;
- `CG_SESSION_SECRET`, `CG_ANCHOR_HMAC_KEY`, `CG_ACCESS_CODE` генерируются
  только если отсутствуют и дальше сохраняются;
- `CG_DATA_DIR=/app/data`, `CG_PUBLIC_DEMO=1`,
  `CG_ALLOWED_ORIGINS=https://nedra.soloveyka.bond`, `CG_TRUST_PROXY=1`
  (cloudflared передаёт `X-Forwarded-Proto`, поэтому Secure cookie),
  `VECTOR_RETRIEVAL_ENABLED=0`; `AI_PROVIDER` выбирается по типу ключа и
  конфигу (`opencode-go` для Go/generic без base, `openai-compatible` для
  generic с `AI_BASE_URL`, `reformboss` для legacy), `AI_MODEL` —
  `deepseek-v4.1-flash` для Go и `deepseek-v4.1-flash-1017` для legacy;
  `AI_BASE_URL` для Go не переносится (чтобы Go не взял legacy endpoint);
  для local `openai-compatible` и legacy он сохраняется как явный override;
  resolver также игнорирует ReformBoss-host в `AI_BASE_URL`, если выбран Go;
- optional Team B: `CG26_HMAC_KEY` (отдельный persistent random secret);
  `CG26_HMAC_KEY_VERSION`, `CG26_ADVISORY_ROOT`, `CG26_ADVISORY_DB` получают
  дефолты только при отсутствии, существующие custom значения сохраняются при
  повторном `--write`. Advisory — доверенный server-side каталог в volume, не
  uploaded snapshot; значения не печатаются;
- значения не печатаются, access code не выводится ни в `--check`, ни в
  `--write`; model/key передаются только через env-file, не argv.

Секреты не коммитятся. Посмотреть можно только имена ключей:

```bash
sed -n 's/^\([A-Z_]*\)=.*/\1/p' ~/.config/nedra/runtime.env
```

Ротация access code: удалить строку `CG_ACCESS_CODE` из файла и запустить
`--write` (остальные секреты сохранятся), затем пересоздать backend.
Ротация ключа: `--write --refresh-key`, затем согласованное пересоздание только app;
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

### Изолированный e2e-прогон

`scripts/nedra-e2e.sh` — самодостаточный smoke/end-to-end прогон на своих
ресурсах: контейнеры/volume/сеть с префиксом `nedra-rel-e2e-*` и label
`io.nedra.owner=nedra-rel-e2e`, свободный loopback-порт, свои одноразовые
секреты (без чтения `runtime.env`). Прогон собирает образ из рабочего дерева,
поднимает свежую БД, проверяет ledger-миграции, регистрирует аккаунт, импортирует
ZIP из `examples/demo`, запускает `wf_secure_pr_gate` до `succeeded` и проверяет
`official.json`, `export.jsonc`, `export.md`, `export.docx` (включая открытие
файла `python-docx` из образа) и `security-map`. Прод `nedra-app`/`nedra-db`,
сеть `nedra` и volumes `nedra-*-data` скрипт не упоминает; очистка по `trap`
работает только по своим owner-label. Подробности и живые результаты — в
[release hardening](RELEASE-HARDENING.md).

## Health и статус

- `/api/health` — строгий публичный health без session-cookie. Fallback на
  `/api/runs` удалён: отсутствие endpoint или auth-ошибка дают `unhealthy`, а не
  ложный успех.
- `scripts/nedra-status.sh` показывает контейнеры, `/api/health`,
  Docker healthcheck, `pg_isready`, версию pgvector, `CG_DATA_DIR`,
  `CG_PUBLIC_DEMO`, `VECTOR_RETRIEVAL_ENABLED`, `AI_PROVIDER`, `AI_MODEL` и
  отсутствие host-портов у БД.
- `HEALTHCHECK` встроен в образ (`deploy/healthcheck.py`); образ собирается в
  формате `docker`, иначе Podman игнорирует healthcheck.
- Логи: `podman logs -f nedra-app`, `podman logs -f nedra-db`.

## PostgreSQL и pgvector

БД создаётся из `docker.io/pgvector/pgvector:pg16` (digest закреплён).
`deploy/db/init/001-extensions.sql` включает `vector` при первой инициализации
volume. Порт 5432 не публикуется: backend ходит по `nedra-db:5432` внутри сети
`nedra`.

`backend` при старте применяет `db/migrations/*.up.sql` через ledger
`public.schema_migrations`; затем инициализирует runtime schema (в текущем
исходнике v5). Это запись в БД уже при импорте `backend.app.main`: проверять
импорт против production нельзя. История и checksum должны совпадать с каждым
применённым SQL-файлом; старые миграции не переписываются.

Optional `db/optional/003_exact_vector.up.sql` — зона PG worker, не backend и не
deploy. Deploy не выполняет ручной SQL и не создаёт `example_embeddings` сам;
`CREATE EXTENSION vector` идемпотентен. `DB_ENABLE_VECTOR=1` включает
optional 003/014/015; 015 содержит synthetic draft fixture, а не production
выдачу. Режим и миграции проверяются отдельно на изолированной БД.

## Systemd user units (rootless)

`scripts/nedra-systemd-install.sh` рендерит
`~/.config/systemd/user/nedra-{db,app,telegram-proxy,tunnel}.service` из `deploy/systemd/*.in`,
делает `daemon-reload`. Юниты вызывают те же скрипты; owner labels и volumes
соблюдаются.

```bash
scripts/nedra-systemd-install.sh            # установить, не включать
scripts/nedra-systemd-install.sh --enable   # включить db/app/proxy на login/boot
scripts/nedra-systemd-install.sh --enable --start
```

App unit имеет `Wants/After` для db и telegram-proxy. Установленные юниты могут
отличаться от шаблонов: перед выкладкой нужно проверить `systemctl --user cat`
для app. Установщик включает db/app/proxy, tunnel **не включает и не запускает**.
`ExecStopPost` app вызывает безопасный stop и после неудачного `ExecStart`: иначе
oneshot unit мог остаться failed при всё ещё работающем poller. Остановка
контейнера ждёт 45 секунд (Telegram job grace — 15), затем Podman может послать
SIGKILL; завершение всех прогонов этим не гарантируется.

Для старта без интерактивного логина нужен `loginctl enable-linger $USER`; это
глобальная настройка пользователя, поэтому она не включается автоматически.
`podman-restart.service` сам по себе не поднимает rootless-контейнеры без
user-manager/linger.

## Публичный вход и текущий релиз

Конфигурация tunnel направляет `nedra.soloveyka.bond` целиком на
`http://127.0.0.1:18080`; отдельный маршрут для `/miniapp` не требуется.
Credentials и runtime.env не входят в git. Команды tunnel и systemd меняют
окружение пользователя и выполняются только как отдельно разрешённая выкладка.

Записанные в старых handoff SHA, image ID и HTTP 200 — исторические наблюдения.
Они не устанавливают, что запущено сейчас. Текущий релиз сверяют по полному
`org.opencontainers.image.revision`, immutable image ID, manifest и фактическому
состоянию DB; актуальные границы проверки — в
[release handoff](RELEASE-HANDOFF.md).

## Воспроизводимый состав сборки

`scripts/nedra-build.sh` отказывается собирать изменённые tracked-файлы,
извлекает `git archive` полного HEAD во временный каталог и использует только
этот snapshot. Untracked/ignored файлы, локальные secrets и node_modules не
попадают в контекст. Revision label содержит все 40 символов SHA. Сборка
по-прежнему требует доступные base images и пакеты для `npm ci`/`pip wheel`;
успешный frontend build отдельно не доказывает сборку образа.

`/app/release-manifest.json` содержит SHA исходного коммита и SHA-256 файлов
backend/analyzers/rag/db/docs/comparison, compiled frontend и deployment
requirements/healthcheck. В Dockerfile проверка manifest выполняется также от
uid 10001: приложение должно читать исходники даже при приватных host modes.
Manifest фиксирует состав, но не заменяет проверку поведения. Прямой
`podman build` требует `--build-arg NEDRA_REVISION=<полный SHA>`; предпочтителен
проектный build script. После сборки зафиксируйте полный image ID из вывода;
`nedra-up.sh` разрешает tag в ID один раз и запускает этот ID.

## Смена релиза и восстановление

1. Подготовить чистый SHA и собрать полный образ; проверить revision, manifest,
   Mini App assets и Python/Node зависимости в образе.
2. На отдельной БД и app-data проверить startup, миграции свежей и исторической
   схемы и restore защищённой резервной копии. Отклонённый запуск
   `nedra-mini-integration-pg` не повторять и не обходить: эта часть требует
   отдельного решения координатора.
3. До остановки writer/poller проверить все token-enabled процессы и стенды.
   Up отказывается запускать Telegram при другом работающем labelled NEDRA app
   с `TELEGRAM_ENABLED=1/true/yes`; это локальная защита от ошибки, не общий lock.
   Не запускайте второй `getUpdates` для проверки бота. Время между проверкой и
   запуском не защищено distributed lock; cutover выполняет один оператор.
4. Остановить все writers, записать старый SHA/image ID и metadata схемы,
   сохранить согласованные DB custom dump + globals + app-data archive в
   защищённое долговременное хранилище. `/tmp` не является таким хранилищем.
   Проверить restore в изоляции до опоры на backup; один `pg_restore --list`
   подтверждает читаемость TOC, но не восстановление.
5. Сохранить старый контейнер/образ до cutover. `--recreate-app` удаляет старый
   app container, а `down` ещё и DB container: это не безопасная release/rollback
   процедура. Volumes остаются, но этого недостаточно для восстановления.
6. Запустить единственный app/poller с reviewed image ID; проверить local
   health, `/miniapp`, API/auth, schema version/checksum и доступность снаружи.
   Health 200 не подтверждает команды/menu Telegram: startup публикует их
   best-effort, это отдельное внешнее изменение.
7. Image rollback допустим только при совместимых schema и форматах данных.
   Старый runtime v3/v4 отвергает v5. При несовместимости нужен отдельный план
   restore DB+app-data с учётом всех записей после cutover; backup перед restore
   сохраняет и новое состояние. Автоматический downgrade схемы не обещается.

В этом репозитории нет общей проверенной backup/restore/cutover автоматики.
Подготовленные вне репозитория скрипты нельзя считать исполненными по факту
чтения или Python compile. Любая выкладка остаётся отдельным действием.

## Cache после redeploy

`backend/app/main.py` ставит `Cache-Control: no-cache, must-revalidate` на HTML
SPA, `public, max-age=31536000, immutable` на существующие `/assets/*` и
`no-store` на missing assets. Поэтому старый `index.html` не закрепляет
удалённый JS после пересборки. У клиента, который уже держал старый HTML в
кэше до этого заголовка, возможен один blank screen — нужен hard reload;
после него браузер ревалидирует index без повторного залипания.

## Ограничения

- Хост должен быть включён; старт без входа пользователя требует отдельно
  настроенного linger. Установщик сам linger не меняет.
- Первый build требует загрузки base images/npm/wheels; lock без package hashes
  фиксирует версии, но не удостоверяет байты артефактов из реестра.
- `CG_PUBLIC_DEMO=1` требует session secret и access code. Секреты передаются
  через env-file; существующий container не перечитывает его при restart.
- Запуск использует read-only rootfs, volume `/app/data` и tmpfs `/tmp`.
  Владение уже существующим volume uid 10001 требует отдельной проверки;
  source chmod не исправляет чужие volume permissions.
- Новый build, restore, запуск с PostgreSQL и реальная доставка Telegram в
  аудите 2026-11-22 не выполнялись; подробности в отчёте release-аудита.
