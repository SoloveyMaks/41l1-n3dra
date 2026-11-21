# Release handoff — 41l1-n3dra / nedra

Единая текущая сводка. Публичный стенд обновлён и проверен; это проверенный
прототип для показа с явными границами, не полный security-аудит.

## Публичный стенд

- **URL:** https://nedra.soloveyka.bond — health 200, HTML `no-cache`.
- Exact public revision: `63ca8c129181e76a93d0000928db5f98b800f7c1`; image
  `d9507494f8c7`; asset `index-DA9FQkXU.js`.
- Public smoke прошёл: user/admin roles, login/403, immutable asset, missing
  asset 404, sample run, карта, SSE, JSON/Markdown, published comparison.
- Current `dev` source выше `63ca8c1` содержит только docs. **`main` merge ещё
  pending у root**; не заявлять, что main уже merged.
- Review `18082` остаётся healthy на `b6abde4` и не тронут.

## Как показать

1. Открыть https://nedra.soloveyka.bond и войти по пользовательскому коду.
2. Выбрать ZIP или «Попробовать пример» (Kotlin/Spring Boot).
3. Нажать «Запустить проверку» — используется сохранённый сценарий, без
   редактора и `PUT draft`.
4. Смотреть «Проблемы» → фрагмент → «Карта доступа» → «Сравнить проверки» →
   «Отчёт».

Админка — отдельный вход по `CG_ADMIN_ACCESS_CODE`: «Обзор», «Проекты и
проверки», «Сценарий», «Результаты», «Система». Пароли/коды не публикуются.

## Что проверено на public

- `user` login → `role:user`, admin overview 403; `admin` login →
  `role:admin`, overview 200.
- Immutable asset отдаётся, missing asset — 404.
- Sample run `0f9613c4-a83f-423b-90b2-fc05d9f02778`: 13 findings; map
  10 nodes / 9 edges / 26 evidence; SSE с корректным run ID.
- `official.json` 200; `readable.md` 200 без `100%`, с «Полнота проверки не
  установлена» и русскими причинами пропущенных scopes.
- Published comparison: general `comparable=false`, SQL
  `pattern_disappeared`.
- Root лично подтвердил public HTTPS/health 200, asset `index-DA9FQkXU.js` и
  `no-cache` HTML.

## Миграции и deploy

- `007_snapshot_origin_guards.up.sql` применена обычным backend startup; ledger
  содержит 6 миграций.
- Историческая `006` осталась неизменной: checksum и `applied_at` прежние;
  ledger/история не переписывались.
- DB backup mode 600; volumes `nedra-db-data`/`nedra-app-data` не тронуты;
  удалений и сброса не было.
- Forward-миграция: [forward-snapshot-migration](forward-snapshot-migration.md).
- История прошлой неудачной попытки сохранена в
  [deployment-final](deployment-final.md) и здесь не дублируется.

## Reviews

| Review | Итог |
| --- | --- |
| [Sol logic](reviews/sol-logic.md) | принят AFTER85: artifact A/B и failed-report закрыты |
| [Sol visual](reviews/sol-visual.md) | принят AFTER85: основные мобильные блокеры закрыты |
| [Sol structure](reviews/sol-structure.md) | принят FINAL: оба остатка AFTER85 закрыты |
| [Sol migration](reviews/sol-migration.md) | e3e259: фактический PostgreSQL 16 PASS, 007/alias проверены |

Все шесть исходных структурных препятствий закрыты в проверенных путях. Это не
полный аудит и не release-wide approval всех функций.

## Принятые решения

- `pass` — нет блокирующих проблем по политике при данном покрытии; не ноль
  проблем и не полная проверка.
- `executionStatus` отчёта — фактическое выполнение, отдельно от verdict.
- Полнота отчёта — canonical completeness; `100%` только при `complete` и
  подтверждённых scopes; parser ratio отдельно.
- Сравнение: synthetic origin не даёт `comparable=true`; общее сравнение
  остаётся `inconclusive`, SQL-проверка узкая.
- AI-объяснения отдельно от фактов; политика предметной области не подключена.
- Общий workspace без аккаунтов; роли `user`/`admin` ограничивают операторские
  действия.

## Перед main

1. Root выполняет PR merge и финальную приёмку; worker не push/merge main.
2. После merge не менять public revision без отдельного deploy/smoke.
3. Публичный smoke уже выполнен; полный UI/security-аудит не заявляется.

## Оставшиеся ограничения

- Shared workspace, без приватных аккаунтов и multi-tenant.
- Хост — локальный ПК; `Linger=no`, после reboot нужен login.
- Покрытие частичное; полный security-аудит и отсутствие уязвимостей не
  заявляются.
- Embeddings 403/disabled; retrieval выключен; vetted-примеров 0.
- Общее сравнение `inconclusive`; SQL proof узкий.
- Политика предметной области не подключена; допущения видны как допущения.
- Неблокирующие visual-остатки: короткие edge labels, часть comparison-текста
  на 390; конструктор на 390 не перепроверялся.
- Production readiness, независимый VPS и «все функции проверены» не
  заявляются.