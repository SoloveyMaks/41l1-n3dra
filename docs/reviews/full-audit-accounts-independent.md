# Независимое ревью: accounts / API / artifacts (второй пакет)

Дата: 2026-11-22. Worktree: `audit-review-accounts-1122`, ветка
`SoloveyMaks/audit-review-accounts-1122`. Ревью начато на
`9550ebe240c2e2fe2227430754e353d08431bf2d`; после приёмки отчёта по
разрешению координатора исправлена только регрессия R1 —
`backend/app/artifacts/intake.py`, коммит `a0412aa078f407bb09b45dc8cad0c923f7132c49`
(см. «Ремедиация R1»). Другие файлы кода не менялись.

Проверенный точный финал: `2ec64ff3071ab4f1099f949dcc7f91c47f93804d`
(ветка `SoloveyMaks/audit-all-accounts-1122`), проверенный SHA кода:
`d0d07efdcea6696d07a7ba1467bdde5d5f9f123d`. Delta просмотрена
`git diff 9550ebe240c2e2fe2227430754e353d08431bf2d 2ec64ff3071ab4f1099f949dcc7f91c47f93804d`
(3 коммита: `745fa476`, `d0d07efd`, `2ec64ff` — последний меняет только
`docs/reviews/full-audit-accounts.md`). Исходная exact база:
`35d27d30ae44fd955824001e18eff895197c933c`.

Дерево не переключалось: `2ec64ff` выгружен через
`git archive` в `/tmp/nedra-audit-review-accounts/src`, точные функции базы —
через `git show 35d27d3:...` в `/tmp/nedra-audit-review-accounts/probes/`.
Дифференциальные пробы сравнивают ровно базу и финал на одних и тех же
входах. Никаких reset/checkout/merge, чужих файлов, push, контейнеров,
служб, портов, production 18080/БД, credentials и внешней сети.

## Результат

Все заявленные исправления подтверждены в доступной области: cookie-подпись,
account → demo, ZIP-пути/заголовки/кодек/CRC, repository-relative пути,
strict hook URL/подписи, tenant-изоляция журнала webhook, account
Unicode/NUL, лимиты filesystem/sample, роль/workspace сессии. Полный
PostgreSQL lifecycle, middleware, tenant SQL, durable webhook и scheduler
**не выполнялись** (нет разрешённой disposable PG) и не объявляются
прошедшими.

Найдена **одна реальная регрессия** исправления ZIP-кодеков (R1, ниже):
на Python 3.14 валидный ZIP с `ZIP_ZSTANDARD` начал отклоняться, хотя точная
база его принимала. Регрессия исправлена в этой ветке (коммит `a0412aa`);
остальное — наблюдения о соседних границах, не регрессии.

## R1. ZIP с Zstandard ошибочно отклонялся (регрессия, Low/Medium; исправлено)

Точное место: `backend/app/artifacts/intake.py:351-354` (коммит `4580db1`):

```python
if info.compress_type not in {
    zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA,
}:
    raise SafeIntakeError("Неподдерживаемый метод сжатия ZIP")
```

Список кодеков захардкожен и не учитывает, что реально читает runtime. В
Python 3.14 `zipfile` поддерживает `ZIP_ZSTANDARD` (`zipfile.ZIP_ZSTANDARD ==
93`) через stdlib-модуль `compression.zstd`; в проверочном окружении это
Python 3.14.7.

Собственное воспроизведение (`/tmp/nedra-audit-review-accounts/probes/probe_zip.py`):

- `ZipInfo("src/A.kt")`, `compress_type=zipfile.ZIP_ZSTANDARD`;
- `zipfile.ZipFile(...).read("A.kt")` → `b"class A"` (runtime читает архив);
- точный `intake` из `35d27d3` → архив **принят**, snapshot paths
  `['src/A.kt']`;
- точный `intake` из `2ec64ff` → `SafeIntakeError("Неподдерживаемый метод
  сжатия ZIP")`.

Последствия:

1. `/api/artifacts` вернёт **400** на валидный пользовательский архив при
   запуске на Python ≥ 3.14 (раньше 200); это регрессия относительно exact
   базы.
2. Поведение стало несогласованным внутри одного коммита: provider-архив с
   тем же zstd проходит `_sanitize_archive` (он пережимает всё в
   `ZIP_DEFLATED`) и принимается repository-импортом. Проверено:
   `_sanitize_archive(zstd provider) -> codec 8`, затем
   `ingest_zip_file(..., strip_wrapper_root=False)` → `file_count: 1`.
3. `deploy/Dockerfile:5` фиксирует `python:3.12-slim`; на 3.12
   `ZIP_ZSTANDARD` не читается вообще, поэтому там поведение не ухудшилось
   (было 500-подобное, стало 400). Регрессия проявляется на runtime, который
   умеет zstd; именно такой runtime (3.14.7) использовался для проверок
   этого пакета.

Минимальная рекомендация: строить allowlist из фактической поддержки
runtime, например `{ZIP_STORED, ZIP_DEFLATED}` плюс `ZIP_BZIP2`/`ZIP_LZMA`/
`ZIP_ZSTANDARD` при наличии соответствующего stdlib-модуля и константы
(`getattr(zipfile, "ZIP_ZSTANDARD", None)`), либо явно документировать
ограничение и вернуть в сообщении список поддерживаемых методов. Потоковые
лимиты (`file_size`, `written_total`, ratio) уже ограничивают decompression
bomb и для zstd, поэтому расширять allowlist безопасно. Проверка: реальный
zstd ZIP в наборе проб.

### Ремедиация R1 (коммит `a0412aa078f407bb09b45dc8cad0c923f7132c49`)

`backend/app/artifacts/intake.py` взят точным содержимым
`git show 2ec64ff...:backend/app/artifacts/intake.py`; все upstream guards,
лимиты и cleanup сохранены. `git diff 2ec64ff -- backend/app/artifacts/intake.py`
показывает только один участок — allowlist:

```python
def _zstd_zip_supported() -> bool:
    """ZIP_ZSTANDARD читается только вместе с compression.zstd (Python 3.14+)."""
    if getattr(zipfile, "ZIP_ZSTANDARD", None) is None:
        return False
    try:
        from compression import zstd  # noqa: F401 - проверка наличия кодека
    except ImportError:
        return False
    return True


_SUPPORTED_COMPRESSION_METHODS = {
    zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA,
}
if _zstd_zip_supported():
    _SUPPORTED_COMPRESSION_METHODS.add(zipfile.ZIP_ZSTANDARD)
# ...
if info.compress_type not in _SUPPORTED_COMPRESSION_METHODS:
    raise SafeIntakeError("Неподдерживаемый метод сжатия ZIP")
```

Гейт по фактической поддержке, а не по наличию одной константы: на Python
3.12 константы `ZIP_ZSTANDARD` нет, на урезанной 3.14 без `compression.zstd`
модуль не импортируется — в обоих случаях кодек остаётся вне allowlist и
архив получает `SafeIntakeError` («Неподдерживаемый метод сжатия ZIP»), без
500 и без каталога распаковки. Если zstd исчезнет уже после импорта
(`zipfile` бросает `RuntimeError` из `_check_compression`), срабатывает
существующий маппинг ошибок чтения: `SafeIntakeError` + `shutil.rmtree`.

Пробы после фикса (те же одноразовые каталоги
`/tmp/nedra-audit-review-accounts`, вывод в `probes/*-after-fix.out` и
`probes/r1-fixed*.out`):

- `probe_r1_fixed.py main`: **16 PASS / 0 FAIL** — реальный zstd ZIP принят
  (`mobile/src/A.kt`, 7 байт), unknown codec 20 → `SafeIntakeError` без
  мусора, bad CRC → `SafeIntakeError` без мусора, stored/deflate/bzip2/lzma
  по-прежнему приняты, недоступный на чтении zstd → `SafeIntakeError` без
  мусора, гейт `True` на этом runtime и `False` при заблокированном
  `compression.zstd`.
- `probe_r1_fixed.py limits` (`CG_MAX_FILE_COUNT=3`,
  `CG_MAX_UNCOMPRESSED_BYTES=6`): **2 PASS** — count/total лимиты действуют.
- `probe_zip_after_fix.py` (полный ZIP-набор из ревью): **28 PASS / 0 FAIL**,
  zstd-строка теперь «accepted»; остальные guards/пути/cleanup без изменений.
- `py_compile` и `git diff --check` чисты.

## Наблюдения (не регрессии)

### O1. Параметр role override у account-сессии стал неработоспособным

`accounts.create_session(user, ttl, role)` (`backend/app/storage/accounts.py:241-247`)
и `issue_account_session(user, role)` (`backend/app/api/session.py:181`) умеют
записать в `account.sessions` роль, отличную от `account.users.role`. Новый
предикат `s.role = u.role` (`accounts.py:282-283`) делает такую сессию
невидимой для `load_session` сразу после создания. Сейчас оба вызова
(`backend/app/api/auth.py:120,150`) передают `role=None`, поэтому runtime-
дефекта нет; но параметр — ловушка для будущего вызывающего. Минимум:
удалить override или отклонять `role != user["role"]`.

### O2. `_code_matches` асимметричен для невалидного UTF-8 в конфиге

`backend/app/api/session.py:96-103`: кодирование присланного значения
защищено, `expected.encode("utf-8")` — нет. Если `CG_ACCESS_CODE` /
`CG_ADMIN_ACCESS_CODE` / `CG_REGISTRATION_CODE` попали в окружение
невалидными байтами (`os.environ` отдаёт их как surrogateescape), каждая
проверка кода бросает `UnicodeEncodeError` → 500. Проверено с
`CG_ACCESS_CODE=$'bad\xffcode'`: `verify_access_code("wrong")` →
`UnicodeEncodeError`. Это fail-closed (не обход), точная база ведёт себя так
же, т.е. не регрессия; но новый helper заявляет обработку недопустимого
UTF-8 только для входа. Минимум: валидировать коды при старте (503) или
закрыть `expected.encode` тем же `try`.

### O3. Кольцевой буфер журнала webhook общий для workspace

`received_webhooks` ограничен 200 записями глобально
(`backend/app/api/router.py:77-79,998-1000`). Чтение изолировано (проверено),
но активный tenant может вытеснить записи другого tenant. Утечки или записи
в чужой workspace нет. Если журнал важен для аудита — лимит на workspace.

### O4. Возврат роли/workspace реактивирует старую cookie

Инвалидация сессии — предикат, а не revoke. Если аккаунт вернуть к прежним
`role` и `workspace_id` в пределах TTL, старый (не отозванный) v3-cookie
снова проходит `load_session`. Это ожидаемое следствие «сессия обязана
совпадать с текущими правами», но при требовании «любое изменение прав
отзывает сессии» нужен явный revoke строк при изменении.

### O5. Маска payload не трогает identity-ключи и нестроковые значения

`mask_value_tree` (`backend/app/security/presentation.py:37-58`) сохраняет
`IDENTITY_KEYS` (включая `signature`, `runId`) и маскирует значения под
secret-маркерами только если это `str`. Для тестового приёмника это
администратору того же workspace; tenant-утечки нет. Числовой PIN под
ключом `password` останется видимым.

## Что проверено собственными прогонами

Все пробы — одноразовые, под `/tmp/nedra-audit-review-accounts`, реальные
crypto/filesystem/Starlette `Request`; БД не подменялась, сеть не
использовалась. Выводы сохранены в `probes/*.out` (PASS/FAIL, 140 проверок,
0 FAIL; R1 отмечен как `FINDING` — это поведение точного `2ec64ff`).
После ремедиации R1 отдельно прогнаны `probes/r1-fixed*.out` и
`probes/*-after-fix.out`: ещё 61 проверка, 0 FAIL.

| # | Заявленное исправление | Собственное доказательство | Итог |
|---|---|---|---|
| 1 | cookie bytes | `Request` с сырым байтом cookie `0xE9`: база — `TypeError`, финал — `None`; валидный токен проходит | подтверждено, достижимо через latin-1 заголовок |
| 2 | account → demo | точное тело `POST /api/session` (финал и база) с подписанной v3-cookie и без DSN: база ушла в БД (`DatabaseUnavailable`), финал БД не трогал, выдал проверяемый v2-токен; workspace ответа == workspace подписи == configured | подтверждено |
| 3 | ZIP 500 → безопасный отказ | реальный bad CRC → `SafeIntakeError`, каталог распаковки удалён; encrypted, declared oversize, parent/file-dir collision, `.`-сегмент, двойной слэш, traversal, absolute/drive, backslash, case-дубликат, `__MACOSX`, центральный NUL — отклонены; bzip2/lzma приняты; лимит файлов 3+1 в отдельном процессе | подтверждено; R1 исправлен в `a0412aa` |
| 4 | normalizer: central/orig, `__MACOSX` | центральный NUL → отказ; локальный NUL ловится собственным name-mismatch `zipfile` (`BadZipFile`) → `SafeIntakeError`; база принимала `src/./A.kt` и `__MACOSX/...` | подтверждено |
| 5 | repository paths | sanitizer убирает provider-wrapper и `__MACOSX`; `strip_wrapper_root=False` сохраняет `mobile/build.gradle.kts`; default intake на том же архиве срезает `mobile/` (старый баг воспроизведён) | подтверждено |
| 6 | hook URL/signature | 8 классов URL (credentials, port, query, fragment, лишний путь, `%`, http, чужой host) дают `None` там, где база канонизировала; нормальный URL/`.git`/регистр совпадают; HMAC принимает верное тело, отвергает изменённое и non-ASCII заголовок | подтверждено |
| 7 | webhook log | тело 64 KiB — 200, 64 KiB+1 и заявленный большой `Content-Length` — 413 до чтения; ключи/паттерны маскируются; запись помечена workspace; чтение фильтрует (чужой marker отсутствует), `workspace_id` не отдаётся; non-admin 403, anonymous 401; cap 200 | подтверждено |
| 8 | admin metadata | только чтение `backend/app/api/admin.py:157-165`: `shared`/`accessModel` по `session["mode"]`, `userAccounts=true` | исполнение не проводилось (нужен router/DB) |
| 9 | account Unicode/NUL | NUL/surrogate в email, пароле, displayName → `AccountError` до БД; валидная регистрация доходит до `DatabaseUnavailable`; scrypt принимает/отвергает | подтверждено |
| 10 | filesystem/sample limits | fallback: count+1, total+1, oversized file, symlink, control char, empty; 1 GiB sparse отвергнут без полного чтения (rss delta 0 KiB); sample: count/bytes, sparse тоже bounded | подтверждено |
| 11 | Unicode login codes | реальные `verify_registration_code`/`verify_access_code`/`verify_admin_code` отвергают surrogate и NUL, принимают верный код; точная база бросала `UnicodeEncodeError` | подтверждено |
| 12 | role/workspace при load_session | точный SELECT из `accounts.py:274-286`, исполненный на SQLite: current → 1 строка, demoted → 0, moved → 0, role-override legacy → 0, revoked/disabled отсекаются Python-кодом | семантика предиката подтверждена; PG/psycopg — нет |

Дополнительно: `git diff --check` чист; все изменённые Python-файлы
компилируются (`py_compile`). `import backend.app.api.router` без DSN падает
на инициализации `DatabaseRepository` (`DatabaseUnavailable: DATABASE_URL не
задан`) — фактическая граница недоступной интеграции.

## Что осталось непроверенным

1. Полный PostgreSQL lifecycle: register/login/logout/replay/disabled,
   `load_session` через psycopg на реальных строках, tenant SQL, конкурентные
   A/B, полный middleware, durable webhook inbox/replay, watch claims,
   scheduler. Разрешённой disposable PG нет; `podman start
   nedra-mini-integration-pg` ранее отклонён и не повторялся/не обходился.
2. `admin_overview` целиком (counts/vector/DB) не исполнялся: маршрут требует
   router-singleton и БД.
3. Полный upload endpoint через HTTP не запускался (после intake нужен
   `db_repo.save_artifact`); проверены прямые вызовы intake на реальных
   ZIP/файловых деревьях, а не HTTP-маршрут.
4. Удалённые GitHub/GitLab и provider API не вызывались.
5. R1 проверен на Python 3.14.7 (zstd принимается). На 3.12
   (`deploy/Dockerfile:5`) `ZIP_ZSTANDARD` отсутствует, поэтому гейт
   выключает кодек и архив получает `SafeIntakeError`; отдельного прогона на
   3.12 не было — вывод следует из кода и симуляции заблокированного импорта.

## Сверка с отчётом владельца

`docs/reviews/full-audit-accounts.md` (в `2ec64ff`) прочитан. Пункты 1–12 в
доступной области воспроизведены независимо (таблица выше); ложных
утверждений не найдено. Два уточнения:

- Утверждение о NUL в local header реализуется не через
  `orig_filename`, а через собственную проверку `zipfile` на несовпадение
  имён central/local: `BadZipFile` теперь мапится в `SafeIntakeError` (400).
  Результат совпадает с заявленным, механизм другой.
- Отчёт не отмечает сужение списка поддерживаемых кодеков (R1): до фикса
  zstd-архив на 3.14 принимался. Устранено в этой ветке коммитом `a0412aa`.

Временные скрипты владельца (`/tmp/nedra-audit-accounts/task-032c129a/`) как
доказательство не использовались; мои пробы независимы.

## Воспроизведение

```bash
cd /tmp/nedra-audit-review-accounts
E="env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/nedra-audit-review-accounts/src CG_DATA_DIR=/tmp/nedra-audit-review-accounts/data"
$E /tmp/nedra-venv/bin/python probes/probe_session.py
$E /tmp/nedra-venv/bin/python probes/probe_zip.py          # содержит FINDING R1
$E /tmp/nedra-venv/bin/python probes/probe_hooks.py
$E /tmp/nedra-venv/bin/python probes/probe_weblog.py
$E /tmp/nedra-venv/bin/python probes/probe_accounts.py
$E /tmp/nedra-venv/bin/python probes/probe_sql.py
$E /tmp/nedra-venv/bin/python probes/probe_route.py
$E CG_MAX_FILE_COUNT=3 CG_MAX_UNCOMPRESSED_BYTES=6 CG_MAX_FILE_BYTES=4 \
   CG_MAX_SAMPLE_FILES=2 CG_MAX_SAMPLE_BYTES=6 \
   /tmp/nedra-venv/bin/python probes/probe_limits.py fallback
$E CG_MAX_FILE_COUNT=3 /tmp/nedra-venv/bin/python probes/probe_limits.py intake-count
$E CG_MAX_SAMPLE_FILES=2 CG_MAX_SAMPLE_BYTES=6 /tmp/nedra-venv/bin/python probes/probe_limits.py sample
$E /tmp/nedra-venv/bin/python probes/probe_limits.py sparse
```

Проверки после ремедиации R1 (рабочее дерево ветки, а не архив `2ec64ff`):

```bash
cd /tmp/nedra-audit-review-accounts
E="env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/rodion/orca/workspaces/41l1-n3dra/audit-review-accounts-1122 CG_DATA_DIR=/tmp/nedra-audit-review-accounts/data NEDRA_SRC=/home/rodion/orca/workspaces/41l1-n3dra/audit-review-accounts-1122"
$E /tmp/nedra-venv/bin/python probes/probe_r1_fixed.py main
$E CG_MAX_FILE_COUNT=3 CG_MAX_UNCOMPRESSED_BYTES=6 \
   /tmp/nedra-venv/bin/python probes/probe_r1_fixed.py limits
$E /tmp/nedra-venv/bin/python probes/probe_zip_after_fix.py
```

## Вердикт

Точечные исправления второго пакета корректны в проверенной области. Найденная
регрессия R1 (отклонение валидного Zstandard ZIP на runtime, который его
читает) исправлена в этой ветке коммитом `a0412aa`: точный `2ec64ff` intake
сохранён, allowlist строится из фактической поддержки runtime, недоступный
кодек остаётся `SafeIntakeError` без 500 и мусора. Полная интеграционная
приёмка accounts/tenant/webhook/upload остаётся за разрешённым PG-прогоном;
этот отчёт не подтверждает её.