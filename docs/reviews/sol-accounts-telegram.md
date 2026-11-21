# Sol review: accounts, tenant isolation and Telegram

Дата проверки: 2026-11-21. Проверял отдельной веткой после fast-forward merge локальной `dev`; исходный проверенный HEAD: `75c2f09aaa9d2fd49c3de408084be8b8688638ac` (до последующего owner fix). Использовались отдельные PostgreSQL 16/pgvector контейнер и ASGI-приложение; production-порты не трогались. Все приведённые итоговые Telegram proofs повторены с синтетическим токеном и локальным либо отключённым транспортом.

## Findings

### High — повторная Telegram-привязка переносит только owner, но оставляет состояние старого owner

В `backend/app/storage/telegram.py:227-239` `consume_binding_code` делает `ON CONFLICT (chat_id) DO UPDATE` для binding, но `telegram.settings` вставляет с `ON CONFLICT (chat_id) DO NOTHING`. Поэтому новый аккаунт, привязавший уже занятый chat, получает старый `scenario` и delivery mode. В `backend/app/storage/telegram.py:419-426` `list_jobs` фильтрует только по `chat_id`; `backend/app/telegram/runtime.py:342-369` показывает эти задачи и их `run_id` в `/status`.

Воспроизведение на реальной PG: аккаунт A привязал chat `80001`, выбрал свой workflow и создал job; аккаунт B привязал тот же chat своим одноразовым кодом. После этого `/api/telegram/me` под сессией B вернул scenario A, а `list_jobs(80001)` вернул job A (`user_id` A). Это утечка метаданных и неправильный выбор сценария; доступ к чужому report этим запуском не доказан. Отдельно проверка показала, что claims server-wide сами по себе атомарны, поэтому проблема именно в rebind cleanup/фильтрации.

### Medium — queued/importing Telegram jobs не восстанавливаются после перезапуска runtime

`backend/app/telegram/runtime.py:77-105` при `start()` запускает polling и outbox, но не читает незавершённые строки `telegram.jobs`; `_spawn_job` вызывается только из текущего процесса (`:407-415`), а `claim_job` в `backend/app/storage/telegram.py:361-377` принимает только `status='queued'`. После аварийного завершения процесса уже claimed job остаётся `importing`, и следующий runtime не может его claim-нуть. Штатная отмена внутри `_run_job` отдельно переводит job в `cancelled` (`runtime.py:469-474`); это не доказательство crash recovery.

Воспроизведение: в отдельной PG сохранены реальные строки со статусами `queued` и `importing`; создан новый `TelegramRuntime` с настоящим `TelegramBotAPI`, синтетическим токеном и локальным недоступным HTTPS endpoint. После `start()` через 1.2 секунды статусы остались `['queued', 'importing']`, `spawned=0`, внешний статус `blocked`. Это подтверждает потерю фоновой работы после crash/restart; внешняя доставка и скачивание репозитория не запускались.

## Что подтверждено рабочим

* Регистрация двух обычных `role=user` аккаунтов дала разные workspace, cookie v3 и scrypt-хэши `scrypt$16384$8$1$...` с уникальными солями; logout сделал старый cookie непригодным. Неверный пароль дал 401, правильный login 200; после `disabled_at` текущая account-сессия получила 401.
* Реальный ASGI/PG workflow с SECRET_SCAN и недоступным AI provider завершился детерминированными findings и report; AI стал `degraded`, гипотезы не попали в deterministic findings.
* Чужие workflow/artifact/run/nodes/findings/security-map/evidence/report/events возвращали 404; чужой artifact нельзя было использовать для собственного запуска. Параллельные 80 list/me запросов не смешали tenant и ContextVar сбросился.
* Обычный пользователь мог менять собственные schedule/watch (200), чужие давали 404; demo user получал 403; account admin/webhook logs закрыты 403. Server-wide schedule/watch/hook claims на двух workspace отдали по одной задаче без двойного claim, а чужое завершение hook lease не сработало.
* Подписанный webhook принимался 202, replay становился `duplicate`, неверная подпись 401; доставка сохранялась в workspace watch. Binding code нельзя было повторить в другом chat, а demo binding возвращал 401.
* Переход миграций со схемы 001–007 на 008/009/010 применил ровно новые три файла, сохранил sentinel workspace; повторный `ensure_contract_schema` вернул `[]`. Повторная инициализация repository успешна.
* Локальный ephemeral режим (`CG_PUBLIC_DEMO=0` без session secret) не подмешал account workspace в demo: account видел свой workflow, локальная сессия — только настроенный env workspace.

## Ограничения проверки

Успешная реальная Telegram-доставка не доказана; GitHub/GitLab download и Kotlin/Gradle execution в этом review не запускались. Проверялись локальные ASGI маршруты, SQL claims и lifecycle на синтетических данных; ограничения importer проверены чтением owning source, без повторения предыдущего baseline-аудита. При первом черновом smoke env-токен был ошибочно удалён, что позволяло factory подхватить сохранённую конфигурацию; подтверждённой отправки нет, секреты в вывод не попали. Следующий запуск отклонил automatic approval review, скрипт исправлен, итоговые proofs выше получены отдельным безопасным повтором. UI accounts screen в этот review не входил. Rebind finding передан владельцу; до его cleanup fix выпускать результат как полностью безопасный нельзя.
