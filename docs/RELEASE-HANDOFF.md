# Release handoff — NEDRA

Этот документ описывает исходники и условия следующей выкладки. Аудит
2026-11-22 не менял production, сервисы, образы, теги, контейнеры или volumes.
Публичный адрес: https://nedra.soloveyka.bond; текущий image/SHA определяется
на хосте, а не по идентификаторам старых релизов из документации.

## Проверенная база

Release-аудит начат на exact `35d27d30ae44fd955824001e18eff895197c933c`.
Правки и результаты: [full-audit-release.md](reviews/full-audit-release.md).
Полный SHA результата берётся из git commit; после объединения независимых
веток требуется новый exact SHA и новый полный образ.

Изолированная сборка frontend этой базы прошла: 2 223 модуля, JS 963,76 kB,
CSS 152,88 kB; предупреждение о размере bundle. Это проверка compiled frontend,
а не образа и не пользовательского пути с PostgreSQL/Telegram. Compile Python,
Bash syntax и проверки manifest/env/cleanup описаны с границами в отчёте.

## Что изменилось в release-пути

- Build использует чистый git snapshot и полный revision SHA; рабочие и
  untracked файлы не подмешиваются в image под старым label.
- Runtime включает rag, deployment requirements и manifest с SHA-256 полного
  app/frontend состава; Dockerfile проверяет читаемость и manifest от uid10001.
- Локальная установка backend включает python-docx для DOCX-экспорта.
- Runtime env должен быть каноническим Podman KEY=value; bootstrap проверяет
  результат перед заменой файла. Quoted source values допускают комментарий
  после закрывающей кавычки без порчи значения.
- Lifespan выполняет все зарегистрированные cleanup даже при ошибке startup
  или Telegram stop; app systemd unit останавливает контейнер также после
  failed ExecStart. Graceful stop ждёт 45 секунд вместо стандартных десяти.
- Up использует уже разрешённый image ID и проверяет другие labelled app с
  включённым Telegram до старта. Это локальная защита, не распределённая
  блокировка poller: другие хосты/процессы оператор ограждает отдельно.

## До выкладки

1. Зафиксировать итоговый чистый SHA, собрать полный образ, записать полный
   image ID и revision; проверить release-manifest внутри образа. Успешное
   чтение Dockerfile не является сборкой.
2. Проверить read-only rootfs, uid10001, writable app-data и /tmp в отдельном
   окружении. Не исправлять существующий production volume вслепую.
3. Выполнить rehearsal startup/migrations на свежей и исторической БД и
   restore согласованной резервной копии в изоляции. Автоматический reviewer
   ранее не одобрил `podman start nedra-mini-integration-pg`; действие нельзя
   повторять или обходить другой формой/контейнером. В этом аудите БД не запускалась.
4. Проверить установленные systemd app Wants/After для db/proxy. Шаблон
   содержит порядок, но это не доказывает состояние уже установленного unit.
5. Перед cutover остановить все writers/pollers, сохранить согласованные DB
   custom dump + globals + app-data и старый image/container. Хранить backup
   вне /tmp с ограниченным доступом; успешный TOC не равен успешному restore.
6. После отдельно разрешённой выкладки проверить health, Mini App HTML/assets,
   вход/привязку в реальном Telegram WebView, schema/checksum, команды/menu и
   публичную доступность. Второй getUpdates для проверки не запускать.

## Восстановление

`nedra-up.sh --recreate-app` удаляет старый app container, `nedra-down.sh`
удаляет оба собственных контейнера; это не сохранение rollback-точки. Volumes
не удаляются, но от неудачной миграции они не защищают. Общего проверенного
backup/restore/cutover скрипта в репозитории нет.

Текущий source runtime — v5; старый backend v3/v4 отвергает v5. Откат только
образа допускается после проверки совместимости схемы **и форматов данных**.
Иначе нужен отдельный restore-план DB+app-data с сохранением нового состояния
и учётом записей после cutover. Применённые SQL/checksum не переписывать;
изменение схемы делается forward migration.

Telegram startup может опубликовать global commands/menu через Bot API.
Откат image не отменяет это изменение автоматически; health 200 не доказывает
успешную публикацию. Реальная внешняя проверка остаётся отдельным шагом.

Подробные команды локального окружения и границы — в
[DEPLOYMENT.md](DEPLOYMENT.md). Старые независимые отчёты полезны как история,
но не являются доказательством поведения итогового объединённого SHA.
