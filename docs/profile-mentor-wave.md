# Профиль и история запусков — сессионный фундамент

Волна 24h, backend-only. Цель — минимальный, честный фундамент профиля:
вошедший пользователь видит сводку своего профиля и свою историю запусков,
привязанную к идентичности подписанной сессии. Полноценного аккаунта,
регистрации и пароля здесь нет, и API это не изображает.

## Что добавлено

| Файл | Роль |
|---|---|
| `db/migrations/008_profile_history.up.sql` | новая схема `profile`: профили и закладки запусков |
| `backend/app/storage/profile.py` | `ProfileStore`: чтение/запись в PostgreSQL |
| `backend/app/api/profile.py` | `create_profile_router()`: три эндпоинта |
| `docs/profile-mentor-wave.md` | этот контракт и ограничения |

Существующие `backend/app/api/session.py`, `backend/app/api/router.py`,
`backend/app/main.py` и frontend не изменялись. Admin-guards не ослаблены:
профильные эндпоинты используют только `session_from_request`, а `require_admin`
остаётся нетронутым.

## Идентичность: почему это не аккаунт

Текущий токен `cg_session` (v1/v2) содержит workspace, роль, issued и nonce.
Постоянного `user_id` в нём нет. Поэтому профиль — это **анонимный сессионный
псевдоним**:

```text
profile_id = "p_" + HMAC-SHA256(CG_SESSION_SECRET, "profile:" + nonce)[:32]
```

Свойства:

- один и тот же cookie даёт один и тот же `profile_id` на всех запросах;
- nonce уже подписан сервером; `HMAC` необратим, наружу уходит только digest,
  сам nonce и `CG_SESSION_SECRET` не раскрываются;
- повторный вход выдаёт новый nonce → новый `profile_id`; история старого
  профиля остаётся в БД, но этим токеном недоступна;
- при незаданном `CG_SESSION_SECRET` работает эфемерный секрет процесса:
  после рестарта `profile_id` меняется, и старая история становится недоступной.
  Для настоящей истории секрет обязан быть постоянным;
- запрос без валидной сессии → `401`, подделанная подпись → `401`;
- **изоляция аккаунтов не заявляется**: пользователи публичного demo делят
  workspace, а закладка запуска не доказывает авторство запуска.

Последний пункт важен. В общем workspace любой вошедший, знающий `run_id`,
может добавить его в свою историю. Читать чужую историю нельзя: каждый запрос
фильтруется по `(workspace_id, profile_id)`. Но это изоляция сессий, а не
доказанное владение аккаунтом.

### Почему не правили `session.py`

Задача запрещает правки `session.py`/`router.py`. Псевдоним выводится из уже
подписанного nonce, поэтому формат токена обратно совместим: старые v1/v2
cookie продолжают работать без миграции и без нового серверного секрета.
Если позже появится `user_id` в токене, `profile_id` можно заменить на него —
схема БД уже готова к строковому идентификатору (`p_...` — просто текущий
формат).

## API-контракт

Все три маршрута требуют валидную сессию и монтируются под `/api/profile`.

| Метод | Путь | Смысл |
|---|---|---|
| `GET` | `/api/profile` | сводка своего профиля |
| `GET` | `/api/profile/history?limit=20` | свои закладки запусков, `1..100` |
| `POST` | `/api/profile/runs/{run_id}` | добавить существующий запуск своего workspace в свою историю |

### `GET /api/profile`

```json
{
  "profileId": "p_25a8c3b521e2a9deefc73e1f41523dea",
  "scope": {
    "kind": "anonymous_session",
    "workspaceId": "11111111-2222-4333-8444-555555555555",
    "role": "user",
    "issuedAt": "2026-11-21T12:02:51+00:00",
    "expiresAt": "2026-11-22T00:02:51+00:00",
    "accountIsolation": false,
    "note": "Профиль привязан к подписанной сессии, а не к аккаунту..."
  },
  "createdAt": "2026-11-21T12:02:51.201000+00:00",
  "lastSeenAt": "2026-11-21T12:02:51.201000+00:00",
  "history": {
    "runs": 1,
    "lastRunAt": "2026-11-21T12:02:51.189541+00:00",
    "lastBookmarkedAt": "2026-11-21T12:02:51.231225+00:00"
  }
}
```

`history.runs` считает только закладки, для которых запуск ещё существует в
`runtime.runs`. Удалённый запуск не выдаётся как живой.

### `GET /api/profile/history`

```json
{
  "profileId": "p_25a8c3b521e2a9deefc73e1f41523dea",
  "scope": {"kind": "anonymous_session", "accountIsolation": false},
  "limit": 20,
  "items": [
    {
      "id": "run_smoke",
      "workflow_id": "wf_smoke",
      "revision_id": "rev_smoke",
      "artifact_id": null,
      "trigger_type": "manual",
      "execution_status": "succeeded",
      "security_verdict": "pass",
      "total_findings": 2,
      "critical_count": 0,
      "high_count": 0,
      "medium_count": 0,
      "low_count": 0,
      "info_count": 0,
      "started_at": null,
      "finished_at": null,
      "created_at": "2026-11-21T12:02:51.189541+00:00",
      "bookmarked_at": "2026-11-21T12:02:51.231225+00:00"
    }
  ]
}
```

`workspace_id` наружу не отдаётся. Данные запуска читаются из `runtime.runs`
в момент запроса, поэтому статус и счётчики всегда актуальны.

### `POST /api/profile/runs/{run_id}`

Идемпотентно. Запуск должен существовать в `runtime.runs` того же workspace.

```json
{
  "profileId": "p_25a8c3b521e2a9deefc73e1f41523dea",
  "runId": "run_smoke",
  "recorded": true,
  "created": true,
  "createdAt": "2026-11-21T12:02:51.231225+00:00"
}
```

Повторный вызов возвращает `200`, `"created": false`. Чужой/несуществующий
запуск → `404 RUN_NOT_FOUND` без различия между «нет такого» и «есть в другом
workspace». Запрос с чужого `Origin` → `403` (дополнительно к middleware
`main.py`). Хранилище недоступно → `503 PROFILE_STORAGE_UNAVAILABLE` без DSN в
тексте.

### Автозапись истории пока не включена

`trigger_run` в `router.py` не знает профиль, а править его в этой волне
запрещено. Поэтому запуск попадает в историю только явным `POST` после
создания запуска. Root может либо вызвать этот `POST` из frontend после
`POST /api/workflows/{id}/runs`, либо позже добавить executor-hook, который
записывает закладку в той же транзакции/после создания run. Полурабочих
автозаписей здесь нет.

## Схема БД

`008_profile_history.up.sql` создаёт отдельную схему `profile`, а не таблицы
в `data_contract`: канонические факты анализа не должны мутировать, а записи
профиля можно удалить без потери истории запусков.

```sql
profile.session_profiles (
  workspace_id uuid REFERENCES data_contract.workspaces,
  profile_id   text CHECK (profile_id ~ '^p_[0-9a-f]{32}$'),
  created_at, last_seen_at,
  PRIMARY KEY (workspace_id, profile_id)
);

profile.run_bookmarks (
  workspace_id, profile_id, run_id, created_at,
  PRIMARY KEY (workspace_id, profile_id, run_id),
  FOREIGN KEY (workspace_id, profile_id)
    REFERENCES profile.session_profiles ON DELETE CASCADE
);
```

Индексы: `(workspace_id, profile_id, created_at DESC)`. Миграция идемпотентна
по DDL (`IF NOT EXISTS`), транзакционная, без down/destructive SQL. Ledger
`public.schema_migrations` фиксирует её автоматически при старте backend или
через `python -m backend.app.storage.migrate`.

`run_bookmarks` намеренно не имеет FK на `runtime.runs`: `runtime` создаётся
после контрактных миграций и живёт в compatibility-слое. Связь проверяется
при вставке и join-ом при чтении; если run удалён, закладка просто перестаёт
попадать в выдачу.

## Патч интеграции для root

`router.py` не менялся. В конец `backend/app/api/router.py`, рядом с другими
`include_router`:

```python
from backend.app.api.profile import create_profile_router

api_router.include_router(create_profile_router())
```

Больше ничего не нужно: `main.py` уже применяет `/api`-middleware (same-origin,
session gate) ко всем маршрутам `api_router`. Проверено на реальном `app`:
без cookie — `401`, с подписанной cookie — `200`.

Если root предпочитает монтировать в `main.py`, эквивалент:

```python
from backend.app.api.profile import create_profile_router
app.include_router(create_profile_router(), prefix="/api")
```

## Живая проверка (изолированный PostgreSQL)

Прод не трогался. Поднят одноразовый контейнер
`docker.io/library/postgres:17-alpine` на свободном localhost-порту с
`--tmpfs /var/lib/postgresql/data`, пустая БД `profile_verify`.

```bash
DATABASE_URL=postgresql://verify:verifypass@127.0.0.1:<port>/profile_verify \
CG_WORKSPACE_ID=11111111-2222-4333-8444-555555555555 \
CG_SESSION_SECRET=verify-secret-not-production \
python3 -m backend.app.storage.migrate
```

Результат:

```text
Применены миграции: 001_core.up.sql, 002_integrity_publish.up.sql,
004_bundle_envelopes.up.sql, 005_canonical_envelope.up.sql,
006_snapshot_origins.up.sql, 007_snapshot_origin_guards.up.sql,
008_profile_history.up.sql
```

Затем полный `DatabaseRepository()` (contract + runtime schema), два разных
подписанных токена и реальные HTTP-запросы через `TestClient`:

```text
profile ids differ: p_25a8c3b5... p_370b56da...
no session: 401 AUTH_REQUIRED
A summary before: {"runs": 0, ...}
A bookmark: True 2026-11-21T12:02:51.231225+00:00
A bookmark repeat idempotent: created=False
A history item: run_smoke succeeded 2026-11-21T12:02:51.231225+00:00
A summary after: {"runs": 1, ...}
missing run: 404 RUN_NOT_FOUND
foreign workspace run: 404 RUN_NOT_FOUND
B isolated: items=0 runs=0
DB bookmarks: [{'profile_id': 'p_25a8...', 'run_id': 'run_smoke',
                'workspace_id': '11111111-...'}]
forged cookie: 401
SMOKE OK
```

Проверено отдельно:

- монтирование ровно предложенным патчем на реальном `backend.app.main:app`:
  `unauthenticated through main middleware: 401`,
  `mounted GET /api/profile: 200`, `mounted GET /api/profile/history: 200`;
- повторный `python -m backend.app.storage.migrate`:
  `Миграции уже применены, ledger актуален`;
- запись в БД видна через SQL (`profile.run_bookmarks`), то есть это не
  in-memory заглушка;
- второй токен не видит закладку первого.

Одноразовая БД и временное окружение после проверки удаляются; в репозиторий
не попадают.

## Ограничения и следующий шаг

1. **Нет регистрации/пароля.** Профиль не является завершённой аутентификацией
   и не должен так называться в UI. `scope.accountIsolation=false` — явный
   маркер для frontend.
2. **История только по явной закладке.** Автозапись из `trigger_run` требует
   правки `router.py`/executor — отдельная интеграция root.
3. **Смена сессии = новый профиль.** Для постоянной истории нужен `user_id` в
   токене. Предложение: session v3 с полем `userId` (7 сегментов либо
   опциональный claim), `verify_session` возвращает его, `profile_id`
   становится `u_<userId>`; старые v1/v2 остаются валидными с текущим
   сессионным псевдонимом. Регистрация, пароль и постоянный user id — вне
   этой волны.
4. **Нет удаления истории.** Осознанно не добавлял: сначала нужно решить,
   удаляет ли пользователь закладку или всю историю, и как это сочетается с
   audit-требованиями.
5. **Нет frontend-страницы.** Только backend-контракт; UI-владелец может
   рисовать по этому документу.

Эксплуатационное требование: для переживания рестартов задать постоянный
`CG_SESSION_SECRET`. Без него профиль работает, но псевдоним меняется вместе
с процессом.