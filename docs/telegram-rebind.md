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
- `delivery_guard(item)` открывает транзакцию и делает
  `SELECT ... FOR UPDATE` на строке binding. Rebind делает тот же lock, поэтому
  порядок линеаризуется:
  - sender взял lock первым → сообщение уходит, пока старый владелец ещё
    актуален, затем rebind;
  - rebind взял lock первым → sender видит новый epoch и отменяет доставку.
- `finish_outbox` принимает owner/epoch и не переводит `cancelled` в `sent`;
  старая claimed доставка не может ни отправиться под новым владельцем, ни
  «завершиться» после отмены.

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

## Ограничения

1. Перенос аккаунта в другой chat (не тот же) по-прежнему удаляет старое
   binding; существующие FK `ON DELETE CASCADE` уносят telegram.jobs/outbox
   старого chat. Runtime run/report history при этом не удаляется. Это
   отдельная модель «одного активного chat», не затронутая observed A→B
   rebind.
2. Внешний Bot API в тесте не вызывался: проверены binding/job/outbox/guard
   уровни; `delivery_guard` сериализует отправку и rebind по строке binding.
3. `binding_epoch` не отдаётся в публичный DTO; это внутренний fencing token.
