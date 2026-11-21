# Telegram rebind: изоляция settings, jobs и outbox

## Корень

Старый `consume_binding_code` обновлял владельца строки `telegram.bindings`,
но `telegram.settings` вставлял через `ON CONFLICT DO NOTHING`: сценарий
прошлого владельца оставался. `list_jobs(chat_id)` фильтровал только по chat,
поэтому новый аккаунт видел старые job/run_id. `outbox` тоже был chat-only:
pending/sending строки прошлого владельца могли пережить rebind, а доставка
брала workspace из текущего binding.

Исправление — не косметические `if`, а fencing по владельцу и эпохе привязки.

## Модель

`db/migrations/011_telegram_rebind.up.sql` (аддитивно):

- `telegram.bindings.binding_epoch bigint NOT NULL DEFAULT 1`;
- `telegram.jobs.binding_epoch`;
- `telegram.outbox.user_id`, `workspace_id`, `binding_epoch`;
- статус outbox `cancelled`;
- индекс `(chat_id, user_id, workspace_id, binding_epoch, status)`.

Существующие unsent outbox-строки при миграции консервативно переводятся в
`cancelled`: истории владельца у них нет, угадывать нельзя. `sent/failed`
строки и `jobs` сохраняются; `runtime.runs`/reports не трогаются.

## Атомарный rebind

`TelegramRepository.consume_binding_code` в одной транзакции:

1. атомарно потребляет код;
2. блокирует строку binding (`SELECT ... FOR UPDATE`);
3. если chat принадлежит другому user/workspace:
   - `binding_epoch += 1`;
   - старые `jobs` в `queued/importing/running` → `cancelled` (строки остаются);
   - старые `outbox` в `pending/sending` → `cancelled`;
   - `settings` сбрасываются в `both` + пустой scenario;
   - binding переключается на нового владельца;
4. если это тот же user+workspace (повторный `/start`): epoch не меняется,
   settings/jobs/pending outbox сохраняются — rebind идемпотентен.

`binding_epoch` — fencing token, а не UI-поле.

## Jobs

- `create_job` вставляет строку только при совпадении текущего binding
  (chat/user/workspace/epoch); иначе `JobOwnershipLost`.
- `claim_job` делает `queued -> importing` только с join на текущий binding и
  совпадением epoch; старый queued job после rebind не клеймится.
- `list_jobs(chat, user, workspace, epoch)` фильтрует владельца, epoch и
  join на текущий binding. `/status` не видит jobs прошлого владельца.
- `job_is_current(job)` перепроверяется перед импортом, сохранением артефакта,
  созданием run, запуском executor и уведомлениями.
- `set_job_status` с epoch не перезаписывает уже `cancelled` job (кроме явной
  отмены); старый executor не воскрешает историю.
- Ошибка старого job больше не отправляет сообщение в chat: `_best_effort_message`
  вызывается только при `job_is_current`.

## Outbox и гонка claimed delivery

- `enqueue_outbox` пишет owner/workspace/epoch и вставляет только при текущем
  binding.
- `claim_outbox` берёт `pending` только с join на актуальный binding/epoch;
  старые `sending` при несовпадении переводятся в `cancelled`, stale sending
  того же владельца возвращается в `pending`.
- Payload (`alert`/`jsonc`) строится по `item.workspace_id`, а не по текущему
  binding: scope из current binding запрещён.
- `acquire_delivery_guard(item)` открывает транзакцию и делает
  `SELECT ... FOR UPDATE` на строке binding; `release_delivery_guard(conn)`
  отпускает её. Оба вызова идут через `asyncio.to_thread`, поэтому ожидание
  rebind-а не блокирует event loop. Rebind делает тот же lock, поэтому порядок
  линеаризуется:
  - sender взял lock первым → сообщение уходит, пока старый владелец ещё
    актуален, затем rebind;
  - rebind взял lock первым → sender видит новый epoch и отменяет доставку.
- `finish_outbox` принимает owner/epoch и не переводит `cancelled` в `sent`;
  старая claimed доставка не может ни отправиться под новым владельцем, ни
  «завершиться» после отмены.

## Event loop и DB waits

Sync psycopg может ждать row lock. В async-коде он теперь всегда уходит в
`asyncio.to_thread`:

- runtime: `_repo()`/`_db()` для binding/job/outbox/settings/run DB-вызовов;
- `_handle_start` вызывает `consume_binding_code` в worker thread — именно здесь
  раньше был deadlock: sender держал `FOR UPDATE` через `await send`, а rebind
  блокировал event loop;
- API `/telegram/binding`, `/telegram/me`, `/telegram/unbind`,
  `/telegram/settings`, `/telegram/scenarios` тоже не держат DB-вызовы на loop;
- delivery-guard acquisition/release выполняются в worker threads, сама
  транзакция с `FOR UPDATE` живёт через `await send`; rebind ждёт её в своём
  thread, heartbeat/recovery продолжают тикать.

Порядок остаётся fencing-корректным: если sender взял row lock первым, отправка
происходит, пока старый owner ещё актуален; если rebind взял lock первым,
sender видит новый epoch и отменяет доставку. Снять guard без замены нельзя —
это вернуло бы утечку прошлого владельца.

## Restart recovery (012)

`db/migrations/012_telegram_job_recovery.up.sql`:

- `telegram.jobs.lease_token`, `lease_until`, `heartbeat_at`,
  `recovery_attempts`;
- статус `interrupted`;
- индекс `(status, lease_until, recovery_attempts)`.

`TelegramRuntime.start()` теперь запускает `_recovery_loop` даже при
отсутствии bot token: recovery — это DB-работа, а не Telegram-транспорт.
Раз в 5 секунд bounded-проход `recover_jobs_once()`:

1. `queued` → `claim_job` (lease + token) → обычный `_run_job`;
2. stale `importing` с `run_id IS NULL` → retry с новым lease, пока
   `recovery_attempts < 3`; после исчерпания → terminal `interrupted`;
3. `importing/running` с существующим `run_id` → **никогда** не re-import:
   terminal run `succeeded/failed` переносится в job, иначе job становится
   `interrupted` с сохранённым `run_id` и error `runtime restarted; run preserved`;
4. expired lease старого worker-а не мешает, пока он жив: heartbeat продлевает
   lease; чужой token не может обновить job.

`claim_job` атомарен: только один runtime получает queued/stale-importing job.
`job_lease_valid` проверяет token, lease, owner generation и статус перед
импортом, сохранением артефакта, созданием run, executor-ом и уведомлением.
`_run_job` heartbeats lease каждые 40 секунд; при потере lease работа
прекращается без второго run. `set_job_status` с lease token не может
перезаписать terminal history чужим/просроченным token-ом.

`recover_jobs(limit)` — **общий** предел на проход: одним
`SELECT ... FOR UPDATE SKIP LOCKED LIMIT` выбираются до `limit` строк из всех
бакетов (exhausted, run-backed settle, retry), затем обновления/возврат идут в
той же транзакции. Поэтому 4 исчерпанных job-а при `limit=1` дают ровно один
`interrupted` за проход; следующие проходы добирают остальные. Два
конкурирующих recovery не превышают limit и не дублируют execution: строки
разведены row lock, а вернувшиеся retry-id всё равно проходят атомарный
`claim_job` (один claim, второй `None`).

## API/runtime contract

- `/api/telegram/me` под старым аккаунтом после rebind: `bound=false`.
- `/api/telegram/me` под новым: `chatId`, дефолтные `settings` (`both`, пустой
  scenario), без scenario прошлого владельца.
- `PUT /api/telegram/settings` под новым owner обновляет только свой binding;
  гонка с rebind → `409 TELEGRAM_REBOUND`.
- `/status` показывает только jobs текущего user+workspace+epoch; старые run_id
  не выводятся.
- Отправка в Telegram не является источником истины: непринятая/отменённая
  доставка остаётся историей, run/report не удаляются.

## Живая проверка

Изолированный PG17+ASGI, два обычных аккаунта, один chat:

```text
A->B rebind: epoch 2 settings reset, old jobs/outbox cancelled
ASGI /me and /settings scoped to B
stale claimed job guarded
stale claimed delivery guarded
same-owner rebind idempotent: epoch/settings/outbox preserved
own new job/outbox flow works; only B deliveries claimed
runtime /status clean: Статус аккаунта
TELEGRAM REBIND SMOKE OK
```

Проверено: A привязал chat, выбрал scenario A, создал jobs и pending/sending
outbox; B привязал тот же chat своим кодом — epoch 2, settings сброшены, старые
jobs/outbox в `cancelled`, строки сохранены. A `/me` → `bound=false`; B `/me` и
`/settings` видят только B. Старый claimed job не клеймится и не обновляется;
старая claimed delivery отменяется без отправки. Повторный bind B идемпотентен:
epoch/settings/pending B outbox сохраняются. Новый B job/outbox работает.

Миграция 011 на legacy-БД:

```text
binding_epoch=1
job=queued epoch=1
outbox=cancelled owner=<legacy> epoch=1
outbox=sent owner=<legacy> epoch=1
history_kept=1
sent_kept=1
```

## Recovery evidence

Живой PG17, без Telegram API (recovery не зависит от транспорта):

```text
telegram runtime disabled: token not configured
queued after start succeeded
stale importing retried 1
exhausted importing terminal interrupted
running with run_id interrupted, no second run
running with succeeded run settled
two runtimes claim once
lease fencing and heartbeat
cancelled old job ignored
TELEGRAM RECOVERY SMOKE OK
```

Проверено: новый runtime поднял queued job; stale importing без run
перезапустился ровно один раз и завершился; исчерпанные retry стали
`interrupted` без запуска executor; running с run_id не создал второй run и
стал terminal `interrupted` (а succeeded run дал succeeded job); два runtime
одновременно выполнили queued job ровно один раз; неверный lease token не
обновил job, heartbeat и верный token работают; отменённый прошлый job не
подхватывается. Миграция 012 на legacy-БД сохранила running job,
`recovery_attempts=0`, lease null и разрешила `interrupted`.

## Event-loop evidence

Живой PG17, локальный Bot API stub (без внешних sends):

```text
recovery012: queued start + stale importing retry
concurrent delivery/rebind: heartbeat ticks 41 old pending cancelled
new owner report sent, stale old pending not sent
API unbind completed off-loop, heartbeat ticks 21
TG ASYNC SMOKE OK
```

Сценарий: delivery держит binding `FOR UPDATE` и fake `send_message` уходит в
`asyncio.sleep(0.2)`; одновременно настоящий `_handle_start` с валидным кодом B
вызывает `consume_binding_code` в worker thread. Event-loop ticker продолжает
тикать (41 тик), rebind завершается после отправки, старый pending outbox
отменяется, новый B run доставляется только B. Отдельно `/api/telegram/unbind`
ждёт ту же строку в worker thread: 21 тик heartbeat, unbind 200, delivery
завершается. Recovery 012 после перевода DB-вызовов в threads не
регрессировал.

## Recovery limit evidence

Живой PG17:

```text
limit=1 exhaustive: one row per pass {'interrupted': 4}
mixed buckets share limit: changed 1
two concurrent recovery: two rows, no overrun {'importing': 2, 'interrupted': 2}
concurrent recovery same queued: single claim [id] [id]
TG LIMIT SMOKE OK
```

Проверено: 4 stale importing с `recovery_attempts=3` при `limit=1` — ровно по
одному `interrupted` за проход (4 прохода); смешанные exhausted/running бакеты
делят один total limit (изменена ровно одна строка); два параллельных recovery
с `limit=1` обработали по одной строке, не все четыре; два recovery вернули один
и тот же queued id, но `claim_job` выиграл только один.

## Ограничения

1. Перенос аккаунта в другой chat (не тот же) по-прежнему удаляет старое
   binding; существующие FK `ON DELETE CASCADE` уносят telegram.jobs/outbox
   старого chat. Runtime run/report history при этом не удаляется. Это
   отдельная модель «одного активного chat», не затронутая observed A→B
   rebind.
2. Внешний Bot API в тесте не вызывался: проверены binding/job/outbox/guard
   уровни; `acquire_delivery_guard`/`release_delivery_guard` сериализуют
   отправку и rebind по строке binding.
3. `binding_epoch` не отдаётся в публичный DTO; это внутренний fencing token.
