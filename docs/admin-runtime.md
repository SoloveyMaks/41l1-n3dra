# Admin runtime: отдельный код доступа

Документ фиксирует env-контракт для отдельного admin-кода публичного demo и
безопасный порядок его появления на хосте. Полный контракт ролей, токена и
матрицы доступа — в [docs/admin-access.md](admin-access.md); здесь — только
`runtime.env`, bootstrap и ограничения деплоя.

## Переменная

`CG_ADMIN_ACCESS_CODE` — отдельный секрет для входа с ролью `admin`.

- Хранится только в `~/.config/nedra/runtime.env` (каталог `700`, файл `600`).
- Не коммитится, не печатается скриптами, не передаётся в argv внешних команд
  и в отчёты/чат. Backend получает его через `podman --env-file`, значения
  передаются по имени.
- Генерируется `scripts/nedra-bootstrap.sh --write` только при отсутствии:
  8 байт из `/dev/urandom` (16 hex-символов), с повтором, если совпал с
  `CG_ACCESS_CODE`.
- Повторный `--write` сохраняет существующий код без изменений: файл остаётся
  байт-в-байт тем же.
- Если существующий `CG_ADMIN_ACCESS_CODE` совпадает с `CG_ACCESS_CODE`,
  `--write` отказывается писать и не трогает `runtime.env`: коды нужно развести
  вручную (удалить admin-строку и повторить `--write`).
- Отсутствие ключа или равенство с `CG_ACCESS_CODE` — валидное, но нерабочее
  для админки состояние: backend отдаёт `adminAvailable: false`, а
  `POST /api/session {role:"admin"}` — `503`; обычный вход по `CG_ACCESS_CODE`
  продолжает работать. `nedra-lib.sh` не требует этот ключ при загрузке env.

`CG_ACCESS_CODE` остаётся общим кодом для роли `user`; для действующей админки
оба кода обязательны и различны.

## Контракт (реализован в dev)

Полная версия — [docs/admin-access.md](admin-access.md). Ключевое для env:

- `POST /api/session` принимает `{accessCode, role?}`; `role` — `user` или
  `admin`, по умолчанию `user`.
- `role=user` сверяется с `CG_ACCESS_CODE`, `role=admin` — с
  `CG_ADMIN_ACCESS_CODE`. Сравнение постоянного времени, значения не
  возвращаются в ошибках.
- `GET /api/session` отдаёт `role` для аутентифицированной сессии и
  `adminAvailable` для любой.
- `adminAvailable=true` только если `CG_ADMIN_ACCESS_CODE` непустой и не равен
  `CG_ACCESS_CODE`. Иначе admin-вход выключен: `POST {role:"admin"}` → `503`,
  user-вход не меняется. Код user'а в теле с `role:"admin"` даёт `401`.
- Один workspace и одна сессия: `admin` — роль в подписанном токене в рамках
  demo, не отдельный tenant и не приватный кабинет.

## Точное безопасное действие для будущего деплоя

Выполнять на хосте деплоя, не в общем Orca-worktree. Нужен доступ к реальному
`~/.config/nedra/runtime.env` и возможность пересоздать backend.

1. Проверить состояние без записи и без вывода значений:

   ```bash
   scripts/nedra-bootstrap.sh --check
   ```

   Ожидаемо: `CG_ADMIN_ACCESS_CODE: отсутствует, будет сгенерирован...` или
   `... есть, сохраняется`. Если есть предупреждение о совпадении кодов —
   сначала развести коды вручную, `--write` откажется.

2. Добавить недостающий код, сохранив остальные настройки:

   ```bash
   scripts/nedra-bootstrap.sh --write
   ```

   Скрипт пишет `runtime.env` атомарно (`mktemp` + `mv`), ставит `600`,
   каталогу — `700`. Существующие `CG26_*`, кастомные `CG_*`/`VECTOR_*` и
   остальные не-managed строки сохраняются. `AI_PROVIDER`, `AI_MODEL`,
   `VECTOR_RETRIEVAL_ENABLED` по-прежнему фиксируются bootstrap'ом.

3. Проверить только имена ключей:

   ```bash
   sed -n 's/^\([A-Z_]*\)=.*/\1/p' ~/.config/nedra/runtime.env
   ```

   В списке должен быть `CG_ADMIN_ACCESS_CODE`; значения не выводить.

4. Пересоздать backend, потому что изменился env-файл:

   ```bash
   scripts/nedra-up.sh --recreate-app
   ```

   Пересоздаётся только контейнер с label `io.nedra.owner=nedra`; volumes и
   данные сохраняются. Если backend ещё не запущен, достаточно
   `scripts/nedra-up.sh`.

5. Smoke-проверка контракта. Код читается без эха и не попадает в argv:

   ```bash
   curl -sS http://127.0.0.1:18080/api/session          # adminAvailable
   read -rs CG_CODE
   printf '{"accessCode":"%s","role":"admin"}' "$CG_CODE" \
     | curl -sS -c /tmp/nedra-admin.jar -X POST --data-binary @- \
         http://127.0.0.1:18080/api/session
   unset CG_CODE
   curl -sS -b /tmp/nedra-admin.jar http://127.0.0.1:18080/api/session
   ```

   Ожидаемо: `adminAvailable=true`; admin-вход — `200` и `role=admin`; user-код
   с `role:"user"` — `role=user`; user-код с `role:"admin"` — `401`. Если
   `CG_ADMIN_ACCESS_CODE` отсутствует или равен user-коду, `adminAvailable`
   будет `false`, а admin-вход вернёт `503` — это штатное выключение админки,
   user-вход продолжает работать. Cookie-файл после проверки удалить.

6. Ротация admin-кода: удалить строку `CG_ADMIN_ACCESS_CODE` из
   `runtime.env`, выполнить `--write`, затем `--recreate-app`; bootstrap создаст
   новый отдельный код. Если цель — штатно выключить админку, удалить строку и
   пересоздать backend без `--write`: `adminAvailable` станет `false`, admin-вход
   будет отдавать `503`. Новый код передать администратору вне git/чата/логов.

## Ограничения общего workspace

- Этот worktree используется несколькими worker'ами. Реальный
  `~/.config/nedra/runtime.env` не читать и не менять; `--write` здесь не
  запускать.
- Любые проверки bootstrap — только на временном каталоге:

  ```bash
  export NEDRA_RUNTIME_DIR=/tmp/nedra-check/rt
  export NEDRA_SECRET_SOURCE=/tmp/nedra-check/secret.env   # синтетический ключ
  ```

  После проверки временный каталог удалить. Реальные ключи в тестовый источник
  не копировать.
- Не пересобирать образ, не перезапускать контейнеры, не публиковать туннель,
  не пушить и не мержить `main`/`integration-dev`.
- Не выводить значения кодов в stdout/stderr, orchestration-сообщения, отчёты
  и git. Для диагностики допустимы только имена ключей, режимы файлов,
  булевы результаты сравнения и хэши файлов.

## Живая проверка

Проверено одноразовыми скриптами на копиях `runtime.env` в `/tmp`; реальный
файл не использовался. Итог: 20/20 проверок PASS при 7 запусках bootstrap до
merge и 14/14 PASS при 5 повторных запусках после merge local dev (4012fb5).
Bootstrap после merge не менялся; backend-контракт `503`/`adminAvailable`
сверен с `backend/app/api/session.py` и `router.py`.

| Проверка | Результат |
|---|---|
| Первый `--check` | файл не создан, генерация admin-кода объявлена |
| Первый `--write` | `runtime.env` 600, каталог 700, оба кода есть |
| Формат admin-кода | 16 hex, криптостойкий источник `/dev/urandom` |
| Различие кодов | admin ≠ user на всех 5 чистых установках |
| Повторный `--write` | файл байт-в-байт идентичен, режим 600 |
| Custom env | `CG26_*`, кастомный `CG_*` сохранены; AI/vector — канонические |
| Совпадение кодов | `--write` отказал, файл не изменён, temp-файлов нет |
| Утечка значений | кодов нет в stdout/stderr ни на одном запуске |
| Backend при equal/absent | `adminAvailable=false`, admin login `503` (по коду dev) |