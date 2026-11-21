# Release preflight: push-релиз `a253a8d`

21.11.2026, срез 08:52–08:55 UTC. Проверка только чтением: живые контейнеры,
Podman-метаданные, состояние БД, публичный HTTPS и кандидат `a253a8d`.
Мутаций production, перезапусков, сборок и деплоя в этом worktree не было.

## Вывод

Кандидат `a253a8d` — прямой потомок `db45c68` (текущий `main`), меняет только
`backend/app` и не трогает `deploy/`. Runtime-схема БД переходит `v4 → v5`
аддитивно. Публичный чек-лист и скрипт готовы.

Главное ограничение отката: после первого успешного старта v5 старый образ v4
**не запустится** против этой БД. Откат только образом — не работает; нужен
снимок БД до старта v5 и восстановление БД. Снимок ещё не снят.

Дополнительно: тег `localhost/nedra-app:local` уже переехал на чужую сборку
`20bf98327d70` (`bbb5ebf`). Работающий production — `dd25e853d469` (`bde900c`),
но у него сейчас нет тега. Перед сборкой root обязан пометить именно ID
работающего контейнера, а не текущий `:local`.

## Живое состояние (read-only)

| Объект | Факт |
| --- | --- |
| `nedra-app` | container `e2e5adfb3123`, image `dd25e853d46938c79093b034503210bd86e8b49c747c0d219175068214c1d784`, revision `bde900c`, healthy, restart `unless-stopped` |
| Backend | `127.0.0.1:18080 -> 18080/tcp`; сеть `nedra`; user `nedra`; rootfs read-only; volume `nedra-app-data:/app/data` |
| `nedra-db` | container `0bb25236b625`, pgvector digest `018b4a92…`, healthy; host-портов нет; volume `nedra-db-data:/var/lib/postgresql/data` |
| Runtime schema | `runtime.schema_meta = 4`; ledger `public.schema_migrations` — 6 строк (001,002,004,005,006,007); размер БД 103 MB |
| Счётчики на 08:52 UTC | artifacts 18, runs 417, findings 5286, workflows 4, watches 1; значения живые и меняются |
| `:local` | `20bf98327d70`, revision `bbb5ebf`, создан 08:52:49 UTC; к production не относится |
| `nedra-sol-api-18082` | `localhost/nedra-app:sol-review-b6abde4`, revision `b6abde4d92d8`, healthy; не трогать |
| UI `18099` | отдельный `uvicorn` на `127.0.0.1:18099`; не трогать |
| Cloudflare | процесс `cloudflared tunnel --config …/cloudflared-nedra.yml run nedra`; не перезапускать, DNS/туннель не менять |
| Чужие push-контейнеры | `nedra-push-db` (`127.0.0.1:55434`) и `nedra-push-ui` уже запущены другим worker'ом; скрипты `nedra-*` их не владеют и не должны касаться |

Публичный HTTPS на момент проверки:

| Проверка | Результат |
| --- | --- |
| `/api/health` | 200 |
| `/` | 200, `cache-control: no-cache, must-revalidate` |
| `/assets/index-DabRgDEM.js` | 200, 640202 bytes |
| `/assets/index-D7Y9tnQV.css` | 200 |
| несуществующий asset | 404 |
| аноним `/api/workflows` | 401 |
| аноним `/api/admin/overview`, `/api/repository-watches`, `/api/schedules` | 401 |
| user на admin/watch/schedule | 403 |
| admin `/api/admin/overview` | 200 |

`/tmp/nedra-release-public-check.py` прогнан в read-only режиме: user/admin
login 200, роли совпали, `adminAvailable=true`, все guards ожидаемые, training
artifact и workflow доступны. Секреты и cookie не печатались.

## Кандидат `a253a8d`

- Родитель: `db45c68` — ровно текущий `main`; кандидат не требует merge-конфликта.
- Diff: 7 файлов, `+827/−42`, только `backend/app`.
  `deploy/Dockerfile` уже копирует `backend`, поэтому новый
  `runtime_repository_hooks.sql` попадает в образ без правки deploy.
- Runtime v5:
  - `runtime.repository_watches`: `trigger_mode` (`poll`/`webhook`),
    `webhook_nonce`, `last_delivery_at/status/error`;
  - новая durable-таблица `runtime.repository_hook_deliveries` с PK/unique/FK
    и двумя индексами;
  - `RUNTIME_SCHEMA_VERSION = 5`; upgrade `v2/v3/v4 → v5` выполняется при
    штатном старте backend под advisory DDL lock.
- Новых обязательных env нет:
  - `CG_REPOSITORY_WEBHOOK_KEY` опционален; fallback — существующий
    `CG_SESSION_SECRET`;
  - `CG_RATE_HOOK` опционален, default 120.
- Новый публичный маршрут без cookie/Origin:
  `POST /api/repository-hooks/{rw_…}`. Он проверяет HMAC по сырому телу до
  разбора JSON. Админский setup/rotate:
  `POST /api/repository-watches/{id}/webhook/setup`; секрет отдаётся только
  этому admin-вызову и не хранится в БД в открытом виде.
- Миграция аддитивная: существующие watches получают `trigger_mode='poll'`,
  polling-выборка явно фильтрует `trigger_mode='poll'`. Данные не
  переписываются, старые строки не удаляются.

### Trusted catalog

Каталог уже лежит в app-volume и виден контейнеру:

- файл: `nedra-app-data/team-b-advisories/vulnerabilities.json`, 34K,
  sha256 начинается с `1f58bd08feae5938`;
- в контейнере: `/app/data/team-b-advisories/vulnerabilities.json` присутствует;
- `CG26_ADVISORY_DB` и `CG26_ADVISORY_ROOT` в `runtime.env` точно указывают на
  этот каталог (проверено сравнением строк, значения не выводились).

Но `a253a8d` **не включает** Team B по умолчанию и не меняет анализаторы:
у текущего `wf_secure_pr_gate` scanner-ноды имеют пустой `config` (нет
`engine=team_b`). Последний training-run `acc5879f…` на
`art_ccbfe0b31d8d`/`rev_wf_secure_pr_gate_11` дал `SECRET=3`, `SAST=3`,
`SCA=0`, evidence 39/39, coverage `partial`. Если релиз обещает 15 находок
Team B или SCA из каталога, это отдельное изменение другого владельца, а не
этот кандидат. Не выдавать его за результат `a253a8d`.

## v4 → v5: снимок и откат

Что требуется до первого старта v5:

1. Снять logical dump БД и сохранить режим 600.
2. Сохранить образ работающего production по его **image ID**, не по `:local`.
3. Зафиксировать `runtime.schema_meta`, число строк ledger и счётчики.
4. Остановить только `nedra-app` на время снимка app-volume, чтобы не писать
   новые артефакты; `nedra-db` можно оставить запущенной — `pg_dump` даёт
   согласованный снимок.
5. После старта проверить `runtime.schema_meta = 5`.

Почему откат образом не работает: `init_db()` при
`version != RUNTIME_SCHEMA_VERSION` выбрасывает `DatabaseUnavailable`. Старый
образ v4 против базы v5 штатно откажется стартовать. Это защита, а не баг.
Поэтому:

- если кандидат упал **до** записи v5 (версия осталась 4) — достаточно
  вернуть старый образ;
- если версия стала 5 — нужен restore БД из снимка; данные, созданные после
  снимка, при откате теряются.

## Точные безопасные команды

`$ROOT` — integration-worktree, в котором root собирает кандидата. Все команды
запускает root; этот worktree ничего из перечисленного не выполнял.

### 1. Read-only preflight

```bash
cd "$ROOT"
scripts/nedra-bootstrap.sh --check
scripts/nedra-status.sh

podman inspect nedra-app --format \
  'image={{.Image}} rev={{index .Config.Labels "org.opencontainers.image.revision"}} health={{.State.Health.Status}}'
podman exec nedra-db psql -U nedra -d nedra -tAc \
  'select version from runtime.schema_meta'
podman exec nedra-db psql -U nedra -d nedra -tAc \
  'select count(*) from public.schema_migrations'
```

Ожидаемо сейчас: image `dd25e853…`, rev `bde900c`, health `healthy`,
schema `4`, ledger `6`. Если image/rev уже другие — остановиться и разобрать,
кто пересобрал `:local`.

### 2. Снимок и rollback-тег (до сборки и старта v5)

```bash
cd "$ROOT"
umask 077

# app-volume: остановить только app, БД не трогать
scripts/nedra-stop.sh --app
podman volume export nedra-app-data \
  --output /tmp/nedra-app-data-pre-a253a8d.tar
chmod 600 /tmp/nedra-app-data-pre-a253a8d.tar

# logical DB snapshot
podman exec nedra-db pg_dump -U nedra -d nedra -Fc \
  > /tmp/nedra-db-pre-a253a8d.dump
chmod 600 /tmp/nedra-db-pre-a253a8d.dump
test -s /tmp/nedra-db-pre-a253a8d.dump
podman exec -i nedra-db pg_restore -l \
  < /tmp/nedra-db-pre-a253a8d.dump >/tmp/nedra-db-pre-a253a8d.list

# тег именно работающего образа, не текущего :local
prod_image=$(podman inspect -f '{{.Image}}' nedra-app)
podman tag "$prod_image" localhost/nedra-app:rollback-pre-a253a8d
podman image inspect localhost/nedra-app:rollback-pre-a253a8d \
  --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'

podman exec nedra-db psql -U nedra -d nedra -tAc \
  'select version from runtime.schema_meta'
```

`pg_restore -l` должен перечислить объекты без ошибки. Снимок БД — основной
артефакт отката; tar app-volume нужен для точного восстановления файлов
артефактов, если root решит откатывать и их.

### 3. Сборка и старт кандидата

```bash
cd "$ROOT"
scripts/nedra-build.sh
podman image inspect localhost/nedra-app:local --format \
  'id={{.Id}} rev={{index .Config.Labels "org.opencontainers.image.revision"}}'
scripts/nedra-up.sh --recreate-app
```

`nedra-up.sh --recreate-app` останавливает и удаляет только контейнер с
`io.nedra.owner=nedra`, volumes сохраняет. Порт, сеть, env-file, owner-label,
read-only rootfs и tmpfs задаются скриптом; вручную `podman run` не нужен.

### 4. Проверка после старта

```bash
cd "$ROOT"
scripts/nedra-status.sh
curl -fsS http://127.0.0.1:18080/api/health
podman inspect nedra-app --format \
  'image={{.Image}} rev={{index .Config.Labels "org.opencontainers.image.revision"}} health={{.State.Health.Status}}'
podman exec nedra-db psql -U nedra -d nedra -tAc \
  'select version from runtime.schema_meta'          # ожидается 5
podman logs --tail 80 nedra-app

python3 /tmp/nedra-release-public-check.py            # только чтение
python3 /tmp/nedra-release-public-check.py --run-training  # опционально, одна мутация
```

`--run-training` создаёт только один run на существующем immutable artifact
`art_ccbfe0b31d8d` в существующем `wf_secure_pr_gate`. Он не создаёт артефакт
и не меняет workflow. В выводе — `categoryCounts` и `evidenceAvailability`, без
секретов и cookie. По умолчанию скрипт read-only.

### 5. Откат

Только если schema осталась `4` (кандидат не дошёл до записи v5):

```bash
cd "$ROOT"
podman tag localhost/nedra-app:rollback-pre-a253a8d localhost/nedra-app:local
scripts/nedra-up.sh --recreate-app
```

После v5 — полный откат через боковую БД, без разрушения текущей:

```bash
cd "$ROOT"
scripts/nedra-stop.sh --app

podman exec nedra-db createdb -U nedra nedra_restore
podman exec -i nedra-db pg_restore -U nedra -d nedra_restore --no-owner \
  < /tmp/nedra-db-pre-a253a8d.dump
podman exec nedra-db psql -U nedra -d nedra_restore -tAc \
  'select version from runtime.schema_meta'          # ожидается 4

podman exec nedra-db psql -U nedra -d postgres -c \
  'ALTER DATABASE nedra RENAME TO nedra_after_v5;'
podman exec nedra-db psql -U nedra -d postgres -c \
  'ALTER DATABASE nedra_restore RENAME TO nedra;'

podman tag localhost/nedra-app:rollback-pre-a253a8d localhost/nedra-app:local
scripts/nedra-up.sh --recreate-app
```

`nedra_after_v5` не удалять до подтверждения отката: это копия данных,
созданных после снимка. `DROP DATABASE` — отдельное осознанное решение root,
не автоматический шаг. Если восстановление боковой БД не проходит, не
переписывать ledger и не запускать v4 против v5 «на удачу».

## Блокеры и риски

| Риск | Статус / что сделать |
| --- | --- |
| `:local` переехал на `bbb5ebf` (`20bf9832`) | Блокер сборки, если не перепроверить. Тегировать rollback по `podman inspect -f '{{.Image}}' nedra-app`, затем собирать кандидата. |
| Откат образа после v5 | Не поддерживается схемой. Обязателен dump до старта; иначе откат только вперёд. |
| Снимок ещё не снят | Сделать шаг 2 до `nedra-up.sh`. |
| Чужие `nedra-push-*` и review/UI | Не трогать: `nedra-up/down/stop` работают только с owner-label `io.nedra.owner=nedra`. |
| Trusted catalog не меняет default | `a253a8d` не включает Team B/SCA. Текущий default: `SECRET=3`, `SAST=3`, `SCA=0`. Если ожидаются 15 находок — нужен отдельный кандидат другого владельца. |
| Секрет user-кода на loopback `18081` | Найден чужой одноразовый HTTP-сервер, отдающий `CG_ACCESS_CODE` по `GET /code` без auth. Это не production-порт и не мой процесс; root должен решить, гасить ли его до релиза. Значение не выводилось. |

## Что не делалось

- Не собирался образ, не перезапускался `nedra-app`, не выполнялся `--run-training`.
- Не менялись `runtime.env`, volumes, сеть, Cloudflare, review `18082`, UI `18099`.
- Не пушилось, не мержилось в `main`, не трогались чужие контейнеры.
- `a253a8d` в этом worktree не применён: ветка остаётся docs-only от `db45c68`.