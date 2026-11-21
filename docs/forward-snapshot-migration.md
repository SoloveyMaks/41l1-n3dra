# Forward migration: 006 historical + 007 guards

## Контекст

`db/migrations/006_snapshot_origins.up.sql` уже применён в production с
ledger checksum:

```text
4956db3112a2a61e85fec353c25236859191f9f64dcb91319b45279b85e87d19
```

Этот файл — историческая миграция и больше не редактируется. Review DB
содержит более поздний finalized 006 с checksum:

```text
273cd694c6d1fc91e2520bb66cdc9217fdd8415a99833bcf403d4bdca7c068d7
```

Его ledger не переписывается. Обе схемы переводятся вперёд через отдельную
`007_snapshot_origin_guards.up.sql`.

## 006 (immutable)

Создаёт `snapshot_content_hashes`, `snapshot_origins`, FK `runs` на content
hash, row immutability guards. Marker 006 проверяет именно эту историческую
схему: таблицы, PK/FK, `runs_snapshot_content_hash_fkey`, row guards.

## 007 (forward guards)

Перед установкой guards 007 проверяет:

- обе таблицы и обязательные PK/FK;
- `runs_snapshot_content_hash_fkey`;
- включённые row guards;
- каждый content hash имеет origin с тем же hash;
- каждый origin ссылается на существующий artifact.

При нарушении 007 останавливается и не пишет ledger. Она никогда не вставляет
origin и не пытается «исправить» данные. Затем 007 создаёт/обновляет deferred
`check_snapshot_content_origin`, TRUNCATE guards и 007-only sentinel
`snapshot_origin_guards_v1()`.

Marker 007 проверяет полную схему guards, их `tgenabled='O'` и sentinel. Поэтому
review schema с finalized 006 не считается 007-applied, пока не пройдёт
forward validation.

## Checksum alias

`migrate.py` содержит узкую таблицу:

```text
006 + 273cd694... -> принимается только при полной фактической guard-схеме
```

Она не меняет ledger, checksum или `applied_at`. Unknown checksum для 006
падает; общий ignore checksum отсутствует. Исторический 006 с checksum
`4956...` принимается обычным точным сравнением.

## Пути

- fresh DB: 006 → 007;
- old 006 (4956): 006 ledger совпадает → 007;
- review finalized 006 (273c): известный alias + полная схема → 007;
- partial/invalid schema: fail до ledger-записи 007.

## Ограничения

007 — DDL-forward, не data migration. DB owner/superuser может отключить
триггеры или изменить схему; это не гарантия против владельца БД. Production
backup проверялся только на отдельной restored copy; production не трогался.
