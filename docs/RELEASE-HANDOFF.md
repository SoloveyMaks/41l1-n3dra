# Release handoff — NEDRA

Публичный адрес: https://nedra.soloveyka.bond. Опубликованный код `bde900c`,
image `dd25e853d46938c79093b034503210bd86e8b49c747c0d219175068214c1d784`,
frontend `index-DabRgDEM.js`. Последующие изменения документации не меняют
код опубликованного приложения. PR №3 переносит обновление из dev в main.

## Изменения

- Прежняя панель пользователя: редактор, палитра, инспектор, вкладки и история.
- Создание именованного сценария, сохранение и запуск актуальной ревизии.
- ZIP и импорт публичного GitHub/GitLab на точном SHA.
- Административный опрос обновлений репозитория: baseline, interval/cron,
  дедупликация коммита, пауза и последний запуск.
- Исправлены потеря queued-запуска, повторное исполнение после lease,
  malformed URL500, отсутствующий источник фонового запуска, мобильное
  обрезание настроек и подстановка чужого результата после reload.
- Системные настройки остаются в отдельной админке; workspace общий.

## Проверка

[Независимое PG/ASGI ревью](reviews/sol-repositories.md) и
[независимое живое ревью редактора](reviews/sol-user-editor.md) приняты.
Build/typecheck и git diff --check проходят.

Публичный API-путь: импорт GitLab200 → пользовательский сценарий → сохранение
rev2 → запуск `ca6a4beb-c12a-48da-a77d-7a077db7bb0d` на
`art_11b1e2a00b58` → succeeded/inconclusive → report200. Привязка к workflow,
revision и artifact проверена. Проверочное наблюдение запомнило baseline,
check-now вернул unchanged без запуска; наблюдение оставлено paused.
User/admin login200, аноним401, admin API для user403, malformed watchURL400.
Это не полный security-аудит.

## Выкладка и восстановление

- Runtime v3→v4 применён обычным startup; контрактные миграции не изменены.
- БД не сбрасывалась, persistent volumes сохранены; production API healthy.
- Перед выкладкой `/tmp/nedra-db-pre-bde900c.sql` (159417716 bytes, mode600).
- Предыдущий образ сохранён как `localhost/nedra-app:rollback-pre-repository`
  (`d9507494f8c7`, код63ca8c1). Для отката учитывать новую runtime version4:
  старый backend нельзя запускать против v4 без проверки совместимости;
  восстановление БД требует отдельного решения, без потери новых данных.
- На остановке старый процесс не завершился за10 секунд и был SIGKILL;
  уже начатые прогоны могут остаться running. Автоматический replay running
  не обещается. Новые публичные проверки завершились штатно.
- Review18082 и Cloudflare/DNS не менялись. Хост — локальный ПК, Linger=no;
  после reboot нужен login.

## Для защиты

[Понимание кейса и сильные фичи](mentor-defense.md),
[путь показа](JURY-WALKTHROUGH.md),
[репозиторий и наблюдение](repository-import.md),
[структура интерфейса](interface-structure.md).

## Ограничения

Общий workspace, публичные github.com/gitlab.com, периодический опрос без
webhook, неполное покрытие и общий inconclusive, узкое SQL-сравнение,
выключенные embeddings/retrieval и 0 vetted-примеров. Private repo auth,
полный CI/CD status и промышленные гарантии не заявляются.
