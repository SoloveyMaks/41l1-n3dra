# Runtime на PostgreSQL

Слой A: `DatabaseRepository` и таблицы API. Контрактная схема C (`data_contract`)
не меняется и не ослабляется — она приходит из `db/migrations/001..002`.

## Настройки

| Переменная | Обязательна | Смысл |
|---|---|---|
| `DATABASE_URL` | да | `postgresql://user:password@host:5432/db` |
| `RUNTIME_WORKSPACE_ID` | нет | UUID workspace; по умолчанию `93486ede-5bf9-58ff-87fe-062e7b47d748` (`uuid5(NAMESPACE_URL, "41l1-n3dra/default-workspace")`) |

API пока не принимает workspace в запросах: процесс работает в одном workspace,
и чтение по id из чужого workspace возвращает `None`, а не чужие данные.
Секреты — только в локальном окружении, не в репозитории.

## Что делает старт

1. Применяет миграции C через ledger `public.schema_migrations` (filename + sha256,
   advisory lock); повторный запуск их не повторяет, изменённый файл — ошибка.
2. Проверяет `runtime.schema_meta`. Нет версии и есть старые таблицы — понятная
   ошибка: runtime-слой до workspace isolation не переносится автоматически.
3. Создаёт/проверяет schema `runtime` версии 2.
4. Заводит identity-строку workspace в `data_contract.workspaces`
   (`runtime:<uuid>`), если её нет. Других записей в C-схему runtime не делает.

## Границы runtime-слоя

- Все таблицы лежат в schema `runtime`, не в `data_contract`.
- PK всегда начинается с `workspace_id`, поэтому одинаковые текстовые id в разных
  workspace не конфликтуют.
- `findings`, `node_runs`, `reports`, `run_events` scoped по
  `(workspace_id, run_id)`. Старый scanner finding id может повториться в
  следующем прогоне: это отдельная строка, первый прогон не перетирается.
- Ни одного `ON DELETE CASCADE`: удалить run/workspace/workflow с историей нельзя,
  FK не уносит факты прогонов.
- Никакого `INSERT OR REPLACE`: только точечный `ON CONFLICT ... DO UPDATE` в
  пределах своего ключа (workspace + run).
- Интерфейс `DatabaseRepository` и ключи возвращаемых словарей прежние;
  `workspace_id` наружу не отдаётся.

## Чего здесь нет

- C published facts не подключены к API: `analysis_bundles`, `publish_bundle`,
  `scan_outcomes`, `coverage`, `graph_coverage`, `published_*` runtime не пишет и
  не читает. Никакой подделки publish/coverage/verdict нет: отчёт строится по
  своим findings, а не по опубликованному bundle C.
- `ai_explanations`, `comparisons`, `remediation_examples` не затрагиваются.
- `croniter` заявлен в зависимостях для расписаний A; текущий scheduler пока
  разбирает `interval:`/cron вручную и этот слой не меняет.

## Локальная проверка

```bash
# временная БД (пример; креды — локальные, в репозиторий не попадают)
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/runtime_check
python -m backend.app.storage.migrate     # применить миграции C
python -c "from backend.app.storage.database import DatabaseRepository as R; R()"
```

Прогон без временной БД: `backend/test_e2e.py` проверяет полный путь
artifact → run → findings → report. Проверка изоляции: заведите строку в другом
workspace и убедитесь, что `get_run`/`get_artifact` по её id возвращают `None`.

## Обновление схемы

Версия runtime-слоя — `runtime.schema_meta`. Меняете таблицы — поднимайте
`RUNTIME_SCHEMA_VERSION` в `storage/database.py` и добавляйте явный путь
обновления. Молча работать на незнакомой версии нельзя.