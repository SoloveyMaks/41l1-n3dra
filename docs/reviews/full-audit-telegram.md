# Полный аудит: Telegram-бот, привязка и доставка

## Ревизия и охват

- Worktree: `SoloveyMaks/audit-all-telegram-1122`, HEAD на начало работы —
  `b46823bb413fb1b92699096945cbca812b8b0fbf`.
- База: `35d27d30ae44fd955824001e18eff895197c933c` (dev), проверена
  `git merge-base --is-ancestor`.
- Собственные коммиты задачи:
  - `31ef9cb` — fencing `binding_epoch` через durable sequence, owner/account
    guard для чтений привязки, n2 callbacks, отказ n1, границы bigint;
  - `80a0b81` — owner/lease guard в `claim_job` и heartbeat;
  - `fc372ba` — таймауты Bot API, скрытие секретов в ошибках, закрытие
    loopback-соединений proxy bridge;
  - `b46823b` — чтение `telegram.settings` только в поколении владельца;
  - `12457fc` — тесты: ожидаемый n2, подписанный legacy n1, scoped
    `get_settings` в in-memory фикстуре.
- Владею: `backend/app/telegram/` кроме `miniapp.py`, `miniapp_auth.py`,
  `report.py`; `backend/app/storage/telegram.py`; разрешённая правка
  `backend/tests/test_telegram_buttons.py`.
- Читал без изменений: `miniapp.py`, `miniapp_auth.py`, `report.py`,
  `storage/accounts.py`, миграции, `engine` только как контекст.

## Подтверждённые дефекты и исправления

### 1. High: unbind + повторная привязка сбрасывала поколение

**Исходная причина.** В базе `35d27d3` (`storage/telegram.py`) при отсутствии
строки binding новый владелец получал `epoch = 1`, при смене владельца —
`old_epoch + 1`; `delete_binding*` удалял строку. Поэтому после
unbind → привязка заново поколение снова становилось `1`: старый
Mini App bearer и старые подписанные кнопки с epoch 1 проходили проверку для
нового владельца. Callback-версия была `n1`, а epoch в callback ограничивался
10 цифрами, что не покрывало весь bigint.

**Исправление.**

- `consume_binding_code` выдаёт новое поколение только
  `nextval('telegram.binding_epoch_seq')`; sequence не привязана к строке
  binding и переживает `DELETE`. Fallback на `1` удалён.
- `issue_binding_code`, `consume_binding_code`, `delete_binding`,
  `delete_binding_for_user` берут общий advisory lock `741205917`, поэтому
  bind/unbind/move для отсутствующей строки тоже сериализованы.
- Callback-формат переведён на `n2`; `n1` отклоняется до проверки подписи.
  Epoch в callback принимается в диапазоне `0..2^63-1` (0 — только меню
  без привязки), тело ограничено 64 байтами.
- `decode_callback` принимает epoch до 19 цифр и явно отвергает значения
  больше `2^63-1`.

**Проверка.** Чистая проба фактического `menus.py`:

```text
mode  42 bytes  n2.mode.2.9223372036854775807.<sig>
scen  52 bytes  n2.scen.9999-abcdef.9223372036854775807.<sig>
page  43 bytes  n2.page.99.9223372036854775807.<sig>
checks 42 bytes n2.checks.9223372036854775807.<sig>
legacy n1.mode.2.1.<валидная HMAC-подпись> -> decode None
epoch 2^63 и -1 -> encode ValueError
CALLBACK_VERSION = n2
```

`test_telegram_buttons.py` дополнительно прогоняет подписанный n1 через
реальный `TelegramRuntime.handle_update` и проверяет, что запись настроек не
происходит.

**Ограничение.** Живая проверка delete/recreate на PostgreSQL не выполнена
(см. блокеры). Уникальность поколения следует из sequence и подтверждена
кодом, но не SQL-прогоном в этом checkout.

### 2. High: отключённый аккаунт оставался авторизован в боте

**Исходная причина.** В базе `35d27d3` `get_binding`, `get_binding_by_user`,
чтения settings/jobs/outbox и recovery не проверяли
`account.users.disabled_at`. Mini App отклонял отключённый аккаунт через
`accounts.get_user`, а бот продолжал отдавать `/status` и принимать кнопки.

**Исправление.** Все авторизующие чтения и записи теперь соединяются с
`account.users` по `user_id + workspace_id` и требуют
`disabled_at IS NULL`:

- `get_binding`, `get_binding_by_user`, idempotent-ветка `consume_binding_code`,
  финальное чтение binding;
- `get_settings`, `set_settings`, `create_job`, `claim_job`, `heartbeat_job`,
  `job_lease_valid`, `job_is_current`, `recover_jobs`, `list_jobs`,
  `enqueue_outbox`, `claim_outbox`, `delivery_is_current`,
  `acquire_delivery_guard`, `finish_outbox`;
- `issue_binding_code` и потребление кода проверяют активный аккаунт до
  выдачи/приёма кода.

После отключения аккаунта `get_binding` возвращает `None`: команды показывают
инструкцию привязки, callback не пишет настройки, фоновые job/outbox не
клеймятся и не доставляются.

**Проверка.** SQL-инвариант проверен чтением всех перечисленных запросов;
ветка «binding исчез между чтением и записью» покрыта
`test_telegram_buttons.py` (`rebind old message writes nothing/refreshes`,
`stale epoch writes nothing`) и реальными обработчиками runtime с
записывающим транспортом. Отдельный PG-прогон с `disabled_at` не выполнен.

### 3. High: настройки читались вне поколения владельца

**Исходная причина.** `_main_view`, `_settings_view`, `_status_view`,
`_start_repo_job`, `_enqueue_notifications` вызывали
`get_settings(chat_id)` без владельца; `/telegram/me` — тоже. При гонке с
rebind можно было прочитать настройки нового владельца и отрисовать их
старому.

**Исправление.** `_binding_settings(binding)` всегда передаёт
`user_id/workspace_id/binding_epoch`. `get_settings` принимает либо все три
поля, либо ни одного, и при неполной идентичности бросает `ValueError`.
`_current_view` после построения текста повторно читает binding и при
несовпадении owner/workspace/epoch отдаёт только экран привязки.
`/telegram/me` читает settings с полной идентичностью и повторно сверяет
binding.

### 4. Регрессия тестового двойника после scoped-чтения

`FakeRepo.get_settings` не принимал `user_id/workspace_id/binding_epoch`,
поэтому `test_telegram_buttons.py` падал с `TypeError` ещё до проверок.
Фикстура обновлена до фактической сигнатуры и при несовпадении epoch
возвращает дефолтные настройки, как production-запрос. Проверок не убрано:
`95/95` без БД; с disposable PG добавляются 4 прежние DB-проверки (`99/99` у
предыдущего ревью).

## Остальной аудит области

Проверено чтением и прогонами, подтверждённых дефектов не найдено:

- **Команды и callback.** Только `private`, `sender == chat`, не бот; update
  клеймится один раз; чужие/групповые/битые/просроченные кнопки не пишут;
  `help` не требует epoch, но не читает данные workspace; stale epoch
  обновляет меню; duplicate `update_id` не пишет дважды.
- **Bind/unbind/rebind.** Один живой код на аккаунт; код хранится как sha256;
  повторное использование кода идемпотентно только в том же chat; смена
  владельца отменяет старые jobs/outbox, сбрасывает settings, увеличивает
  поколение; same-owner `/start` сохраняет настройки и работу; отсутствие
  sequence останавливает привязку без fallback.
- **Фоновые задачи.** `MAX_ACTIVE_JOB_TASKS=20` ограничивает локальные задачи,
  recovery учитывает свободную ёмкость; claim атомарен по lease token;
  heartbeat отменяет owner при потере lease/ошибке; каждый side effect
  (`import`, `save_artifact`, `create_run`, `execute_run`, финальный статус,
  уведомления) повторно проверяет lease/owner; terminal-статус не воскрешает
  `cancelled`.
- **Отмена и ресурсы.** `_acquire_delivery_guard`/`_release_delivery_guard_safe`
  дожидаются worker-потока при отмене и не оставляют connection/row lock;
  `stop()` сначала останавливает циклы, затем ждёт job-задачи 15 секунд;
  `send_message`/`send_document` ограничены 75 секундами; proxy bridge
  закрывает loopback writer и UNIX writer на всех путях, включая отмену
  bind/соединения.
- **Маскирование.** Token не попадает в `repr`, URL/описания ошибок Bot API,
  логи httpx/httpcore и тексты `TelegramAPIError`; `_safe_error` применяет
  `redact`/`mask_sensitive_text`; proxy URL и bot-token заменяются.
- **Workspace.** `telegram_workspace_scope` не подменяет чужой workspace;
  `_scenarios`, `_workflow`, `_recent_runs`, delivery payloads и `miniapp`
  работают в workspace владельца; job/outbox фильтруются по
  `user+workspace+epoch`.
- **HTTP.** `/binding`, `/me`, `/unbind`, `/scenarios`, `/settings` не держат
  sync DB-вызовы на event loop; `/binding` возвращает 409 для недоступного
  аккаунта; `/settings` требует существующий в workspace сценарий и даёт
  409 при rebind.

## Команды и фактические результаты

```text
$ /tmp/nedra-venv/bin/python backend/tests/test_telegram_buttons.py
SKIP DB probe: TELEGRAM_TEST_DATABASE_URL is not set
95/95 checks passed

$ PYTHONPATH=. /tmp/nedra-venv/bin/python backend/tests/test_telegram_miniapp.py
Ran 25 tests in 0.001s
OK (skipped=14)          # 14 integration-тестов требуют disposable PG

$ /tmp/nedra-venv/bin/python -m compileall -q backend/app/telegram \
    backend/app/storage/telegram.py backend/tests/test_telegram_buttons.py
COMPILE_OK
```

Проба callback-контракта и границ (фактический `menus.py`, без БД) дала
размеры `42/52/43/42` байта для максимального epoch, отказ подписанного n1 и
`ValueError` для `2^63`/`-1`.

Доступного PostgreSQL в окружении нет: `initdb`, `pg_ctl`, `postgres`, `psql`
и `/usr/lib/postgresql` отсутствуют. Запуск `podman start
nedra-mini-integration-pg` ранее был отклонён approval-слоем; эта команда не
повторялась и не обходилась. Поэтому SQL-прогон storage/миграций и живой
disabled/unbind сценарий остаются непроверенными.

## Контракт с miniapi (`miniapp_auth.py`, владелец miniapi)

Требуемый интерфейс, уже реализованный в storage:

1. Источник поколения — `telegram.binding_epoch_seq` (`bigint`, increment 1,
   NO CYCLE, seed выше максимума `bindings/jobs/outbox`). `delete/recreate`
   получает новое значение; sequence не удаляется вместе с binding.
2. Токен v2 должен нести это поколение как `ep`; `authorize`/`assert_live`
   обязаны заново читать активный binding и `account.users` и сверять
   `chat_id/user_id/workspace_id/binding_epoch`. Кэшировать identity между
   запросами нельзя — иначе unbind снова не отзовёт bearer.
3. `get_settings` вызывать только с полной тройкой
   `user_id/workspace_id/binding_epoch`; `set_settings` уже требует её и
   бросает `SettingsOwnershipLost`. В `miniapp.py:390` и `:460` пока остались
   чтения `get_settings(identity.chat_id)` без идентичности — это остаточная
   гонка чтения настроек при rebind, её должен закрыть владелец miniapi.
4. v1-токены отклоняются вместе с n1-кнопками; старые клиенты должны получить
   свежий auth и обновлённое меню.
5. Фактическое состояние ветки miniapi на момент аудита (`69beddd`): токен всё
   ещё `v1`, оба `get_settings` без идентичности. До cutover владельцу miniapi
   нужно выпустить v2 и scoped-чтения; иначе отзыв старых bearer после
   delete/recreate не гарантирован.

Callback-контракт: `n2.<action>[.<arg>].<epoch>.<12 hex>`,
HMAC-SHA256 по `<body>|<user_id>|<chat_id>` с ключом из bot token, только
`private` chat, `sender == chat`, не более 64 байт. n1 не принимается.

## Зависимость от данных и открытые блокеры

- **013 отсутствует в этой ветке.** Код storage использует
  `telegram.binding_epoch_seq`, но forward-миграция
  `013_telegram_binding_generation.up.sql` создана владельцем данных
  (`5c3ca78`, ветка `SoloveyMaks/audit-all-data-1122`). Без неё привязка
  останавливается (`nextval` падает), fallback на 1 намеренно нет. Интеграция
  обязана включить 013 с seed выше persisted max, `NO CYCLE`, advisory lock
  `741205917`; в этом checkout миграция не добавлялась, чтобы не дублировать
  чужой файл.
- **Реальный PostgreSQL acceptance pending.** Не проверены: sequence на
  legacy/fresh БД, delete/recreate старого token/callback, `disabled_at` через
  реальные handlers, конкурентный bind/unbind. Это блокер окружения, не
  зелёный статус.
- **Реальный Telegram transport не запускался** (нет credentials и запрещены
  внешние side effects); проверены только recording transport и чистые
  payload-функции.
- **Остаточная гонка в `miniapp.py`**: unscoped `get_settings` (см. контракт);
  вне моих файлов.
- **FK `ON DELETE CASCADE`**: unbind удаляет binding, а с ним jobs/outbox
  history этого chat; run/report в `runtime` не удаляются. Это модель «одного
  активного chat», не регрессия текущей задачи.
- `docs/telegram-rebind.md` и `docs/telegram-miniapp.md` описывают старую
  модель (`epoch = old+1`, token `v1`); их должен обновить владелец
  документации/miniapi.