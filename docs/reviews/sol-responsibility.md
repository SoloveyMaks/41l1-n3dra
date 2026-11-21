# Sol review: finding responsibility enrichment

Проверен локальный `dev` после merge, исходный source HEAD `8f35d301a863b3b066f88bea24e24b52bc0a6090`. Область: `backend/app/responsibility/**`, `backend/app/api/responsibility.py`, интеграция и `ResponsibilityPanel`; baseline/auth/TG и уже подтверждённые root ASGI tenant/build proof не повторялись. Проверки делались на disposable PG и synthetic ZIP, без внешних provider-запросов, токенов или отправок.

## Подтверждённые проблемы

### High — CODEOWNERS matcher может блокировать event loop на коротком файле

`backend/app/responsibility/codeowners.py:22-52` превращает `*` в `[^/]*`, `**` в `.*` и запускает обычный backtracking regex синхронно. На реальном `SafeArtifactIntake` → frozen snapshot → `load_and_match_codeowners` synthetic CODEOWNERS размером 60 bytes (`*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*a*b @owner`) и безопасном path из 60 `a` за 2 секунды matcher не завершился; child process пришлось остановить. Вызов идёт из `async build_responsibility` через `service.py:118-125`, поэтому один специально сформированный CODEOWNERS может занять общий ASGI loop. Нужен линейный glob matcher или жёсткий лимит сложности/времени в worker thread.

### Medium — CODEOWNERS даёт неверную атрибуцию для root `**` и inline comments

Тот же живой snapshot proof показал:

* `**/A.kt @all-levels` не совпал с root path `A.kt`, хотя CODEOWNERS wildcard `**/A.kt` должен включать корень и вложенные каталоги; fallback `* @fallback` был возвращён.
* Строка `*.kt @backend # explanation` вернула owners `[@backend, #, explanation]`, то есть текст inline comment стал псевдовладельцами. UI затем показывает их как владельцев (`ResponsibilityPanel.tsx`), а `_suggest_role` добавляет их в basis (`service.py:68-87`).

Это конкретные ложные ownership hints, не доказательство доступа к чужим данным. Проверенный matcher также подтвердил, что `/src/` сопоставляется только с `src`-деревом, а пустая ownerless строка не отменяет более раннее `*` — эти два поведения ожидаемы для принятой семантики.

### Medium — лимит CODEOWNERS entries обходится второй перечиткой

`read_codeowners` останавливает сбор на `MAX_ENTRIES=400` (`codeowners.py:143-156`), но `load_and_match_codeowners` перечитывает файл без этого лимита (`:230-240`). Synthetic file с 399 нерелевантными правилами и матчем на строке 401 вернул `matchedPattern` из строки 401. Это нарушает заявленный resource bound и позволяет большому числу regex-правил проходить второй фазой; полный размер файла всё ещё ограничен 128 KiB.

### Medium — синхронные DB/filesystem операции блокируют async endpoint

`build_responsibility` выполняет `db.get_run`, `get_findings`, `get_artifact`, `store.load_artifact_meta`, CODEOWNERS reading/matching синхронно внутри `async` функции (`service.py:97-125`). На настоящем PG, когда все 10 pool connections были заняты, запрос к отсутствующему run занял 0,351 секунды; отдельный 10 ms ticker получил максимальный gap 0,361 секунды. Это подтверждённая остановка event loop при исчерпанном pool, даже без внешнего provider. DB/filesystem work нужно вынести в worker thread или перейти на async storage.

### Medium — размер provider response проверяется после полной загрузки

`history.py:144-147` вызывает `client.get(...)`, а затем `await response.aread()` и только после этого проверяет `len(raw) > MAX_RESPONSE_BYTES`. Фактическая `httpx.AsyncClient.send` при `stream=False` сама вызывает `await response.aread()`. Значит 256 KiB — лимит обработки результата, но не лимит сетевой буферизации/памяти: provider может полностью прислать произвольное тело до проверки. Redirects и `trust_env=False` настроены правильно, timeout 4 s/connect 2 s, path и frozen SHA валидируются до URL. Нужен streaming response с накоплением максимум `MAX_RESPONSE_BYTES+1`.

## Что проверено рабочим

* `ResponsibilityResponse` отделён от Finding; роль помечена как подсказка, `last_file_commit` явно не выдаётся за blame, UI показывает confidence/basis/limitations и безопасную HTTPS ссылку только на GitHub/GitLab.
* `parse_repository_target` принимает только HTTPS GitHub/GitLab и 40-hex frozen commit SHA; provider/path/branch не берутся из клиента. ZIP без repository/commit возвращает bounded history `unsupported`, роль и snapshot CODEOWNERS остаются доступны.
* CODEOWNERS выбирается только из `.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS`; файл перечитывается из artifact root и сверяется с manifest SHA-256, traversal path отклоняется, отсутствующий файл даёт `absent`.
* `fetch_file_history` кэширует завершённые ответы (15 минут, ошибки 90 секунд, максимум 256 записей), ограничивает API page size 1, обрабатывает 429/rate-limit/timeout/redirect как bounded DTO и не использует credentials.
* `ResponsibilityPanel` передаёт только encodeURIComponent(runId/findingId), отменяет устаревший fetch через AbortController, различает 404/503/rate-limit/failed и не меняет Finding facts. Root-проверка own200/foreign404/missing404 и frontend build предоставлена координатором и здесь не повторялась.

## Ограничения

Provider live traffic намеренно не запускался: oversized-body, 429 и timeout подтверждены чтением кода и bounded DTO branches, но не внешним HTTP. CODEOWNERS и PG proof были локальными и read-only для исходников; implementation не менялся. Отдельных новых внешних writes, Telegram sends или production-портов не было.
