# Sol review: finding responsibility enrichment

Проверен локальный `dev` после merge, исходный source HEAD `8f35d301a863b3b066f88bea24e24b52bc0a6090`. Область: `backend/app/responsibility/**`, `backend/app/api/responsibility.py`, интеграция и `ResponsibilityPanel`; baseline/auth/TG и уже подтверждённые root ASGI tenant/build proof не повторялись. Проверки делались на disposable PG и synthetic ZIP, без внешних provider-запросов, токенов или отправок.

## Targeted recheck: 864273d — частично принято

Точный fix SHA `864273d4ac6ad514897ef8c9acf573db8afdd7a4`, локальный review merge HEAD `1021a70b5e666ce7343596646be44d57b339b90e`. Проверены только streaming/history/PR/review изменения. Ниже текущий статус; последующие разделы сохраняют результаты прежних ревизий, а не утверждают, что уже исправленные дефекты остаются в 864273d. **Общая release acceptance остаётся открытой:** CODEOWNERS и синхронный DB/filesystem work этим commit не исправлялись; подтверждён также остаточный decompression resource gap.

### Medium — decoded body ограничен после выделения большого decompressor chunk

`backend/app/responsibility/history.py:187-189`: `response.aiter_bytes()` сначала распаковывает входной кусок, а затем код проверяет его размер. Это исправляет прежнюю полную загрузку обычного тела, но не обеспечивает заявленный потолок распакованного тела до выделения памяти. На настоящем `httpx.AsyncHTTPTransport(uds=...)` и локальном HTTP server через временный Unix socket: gzip JSON из 16 MiB повторяющихся символов дал **22 502 738 bytes** пиковых Python allocations (`tracemalloc`) уже после первых **8192 wire bytes**, до возврата корректного `failed` DTO. Сжатое тело генерировалось до начала измерения; оно не включено в этот peak. Накопительный `bytearray` действительно не превышает 256 KiB, но сам decoded chunk может значительно превышать этот предел. Это локальное доказательство поведения транспорта, не утверждение о фактическом злонамеренном ответе GitHub/GitLab.

Для строгого decompressed ceiling нужен bounded decoder до выделения выходного куска либо отказ от закодированных ответов; один `aiter_bytes(chunk_size=...)` не доказывает ограничение внутреннего decoder allocation. Внешние provider запросы не выполнялись. Одноразовый proof: `/tmp/sol-accounts-review/responsibility-864-proof.py`, вывод рядом в `.log` (временные артефакты, не часть commit).

### Что исправлено и подтверждено

* Прямой вызов owning `_review_decisions` на событиях прежнего reproduction: `APPROVED → COMMENTED` вернул `state=APPROVED, opinionState=COMMENTED, appliesToMerge=true`; `CHANGES_REQUESTED → COMMENTED` сохраняет CHANGES_REQUESTED. Поздний DISMISSED заменяет решение. Wrong-head и unmerged approval дают false, review после merge исключён.
* Неполная история из 100 событий одного reviewer с `incomplete=true` возвращает одну строку с `appliesToMerge=false` и явной причиной неполноты. 21 уникальный reviewer ограничен **20**, `truncated=true`. Реальный локальный HTTP response сохраняет Link header, `_has_next_page` распознаёт `rel="next"`; source wiring `history.py:380-390` включает incomplete при Link-next **или** full page >=100 и передаёт флаг reducer. Целый provider PR exchange не подменялся моками и не запускался наружу.
* На том же живом Unix-socket транспорте complete JSON размером ровно **262144 bytes** принимается; plain тело 2 MiB отклоняется, server успел отправить **270336 bytes** (порог плюс один 8192-byte chunk), а не всё тело. HTTP 429 с объявленным огромным Content-Length и без тела немедленно даёт `rate_limited`. Ошибка oversized не теряет Link header. Это подтверждает прекращение чтения и status/header handling отдельно от открытого gzip allocation gap.
* Source-read `history.py:580-590`: `asyncio.wait_for` накрывает весь file-history + PR enrichment общим **9 s** deadline; HTTP timeout остаётся 4 s/connect 2 s. Реальный 9-секундный provider timeout не запускался; это проверка wiring, не измеренный hard CPU deadline. Синхронное декодирование также не может быть прервано asyncio посередине chunk.
* Source-read: максимум 3 PR, detail только первых 2 и reviews только первых 2; последующие честно `not_loaded`. Совокупный GitHub cache miss по-прежнему до **6 requests** (history + association + 2 detail + 2 reviews), а не число всех PR. Новые fan-out лимиты не заменяют глобальный budget.
* `history.py:558-565` передаёт **last-file C** в PR association; frozen **S** остаётся в `frozenCommitSha`, C — в `associationCommitSha`. DTO и раскрытый блок UI (`ResponsibilityPanel.tsx:354`) показывают обе основы явно. Это source/DTO/UI-read подтверждение, без внешнего provider association proof.
* GitLab commit name больше не выдаётся за login (`history.py:543-544`): прямой identity вызов дал `{displayName: 'Full Name', login: null}`. Backend URL sanitizer принимает HTTPS github.com для GitHub и отклоняет чужой host, HTTP, userinfo, явный port и GitLab URL вместо GitHub; commit/PR ссылки проходят этот sanitizer. UI показывает решение отдельно от последнего отзыва и явно предупреждает о неполных reviews. Browser/live UI повторно не запускался, frontend build предоставлен root ранее.

### Что этим SHA не закрыто

* Ранее доказанные CODEOWNERS ReDoS, root/ownerless/`**`/comment semantics, обход MAX_ENTRIES и event-loop stall от синхронного DB/filesystem work остаются открытыми до отдельного owner SHA и targeted recheck. Их опасные/длительные proof повторно не запускались.
* Source-read concern: cache только completed, 256 entries; нет global network concurrency budget, singleflight или provider-wide cooldown. Одновременные одинаковые miss и разные path могут повторять те же PR reads. Последствия под настоящей массовой нагрузкой здесь не измерены; это конкретный открытый resource concern, а не доказанная production авария.
* Baseline/auth80/Telegram, tenant ASGI и frontend build не повторялись. Исходники не менялись, внешней сети, sends, credentials и production-портов не было.

## PR/reviewers targeted review: 5f0b549

Точный source commit `5f0b5490543927433844eec3f48d2fdd762a6b65`, review merge HEAD `0d17a4f4c03127fb4951effbc9d6ddbb20f6b070`. Предыдущие resource blockers не исправлены этим commit и повторно не запускались. **Release acceptance остаётся открытой до owner fix SHA и targeted recheck.** Внешние provider запросы не выполнялись; ниже прямой вызов owning reducer на synthetic review events, без mock network/pytest.

### Medium — COMMENTED скрывает прежнее решение APPROVED/CHANGES_REQUESTED

`backend/app/responsibility/history.py:180-197` выбирает последний review event по времени, а не последнее решение. Реальный `_review_last_decisions` с `APPROVED` в 01:00 и последующим `COMMENTED` в 02:00 от того же reviewer, для совпадающего head SHA и merge в 03:00, вернул только `COMMENTED, appliesToMerge=true`. Комментарий не отзывает approval/changes-requested решение, но действовавшее решение исчезает из DTO/UI. Нужны независимые decision state и comments/history либо корректное свёртывание только decision transitions.

### Medium — скрытая pagination допускает неполную review историю как полную

`history.py:284-295` получает только первую страницу `reviews?per_page=100`, `_fetch_json` выбрасывает response pagination headers, а `_review_last_decisions` ставит truncated только по числу уникальных reviewer (`:198-201`). Прямой reducer probe со 100 reviews одного reviewer вернул одну строку и `truncated=false`; при существующей второй странице позднее decision будет пропущено, а UI не покажет ограничение и может пометить старое решение «учтено к merge». Подтверждена потеря сигнала неполноты; реальный PR с >100 reviews в этой проверке не запрашивался. Нужен bounded page budget, явный `reviewsTruncated` при недочитанной pagination и запрет выдавать неполную историю за окончательное merged decision.

### Source-read: точная association, bounds и ограничения identity

* Вызов `_fetch_pull_requests(provider, project, commit_sha)` (`history.py:463-466`) использует **snapshot S**, тогда как `history.commit.sha` — **last-file C**. Код association не подменяет S на C. Но DTO рядом с PR не несёт `associationCommitSha`, а UI раскрывает PR под «История изменения» внутри карточки last-file C; явный basis SHA рядом с PR нужен для исключения неверного прочтения. Это source/UI clarity limitation, не доказанная ошибочная provider association.
* Максимум **3 PR**, detail/reviews только первых **2**, reviews page **100**, output unique reviewers **24** (`MAX_REVIEWERS`), что не равно запрошенному критерию 20. Остальные PR честно `not_loaded`; `pullRequestsTruncated` включается при четвёртом PR. За один cache miss GitHub делает до **6** read requests (file history, PR list, 2 details, 2 reviews); 256 completed cache entries не ограничивают in-flight число запросов, нет singleflight/global semaphore/provider cooldown. Cache key включает path, поэтому PR lookup S повторяется для разных файлов.
* Wrong-head APPROVED → `appliesToMerge=false`; review после merge исключён; unmerged PR → false — подтверждено прямыми reducer вызовами. UI явно показывает историческое/не доказанное состояние, author/committer не названы виновными.
* DTO identity хранит только displayName/login, email field не копируется. GitLab author/committer name ошибочно передаётся и как login (`history.py:449-450`), поэтому UI может показать вымышленное `@Full Name`; name из commit не доказывает публичный GitLab username. GitLab reviews честно `unsupported`.
* Уже подтверждённые post-download byte cap и синхронный async-service work остаются в 5f0b549; новые PR read requests увеличивают цену cache miss. Большой внешний HTTP response/нагрузка provider здесь не запускались.

## Подтверждённые проблемы

### High — CODEOWNERS matcher может блокировать event loop на коротком файле

`backend/app/responsibility/codeowners.py:22-52` превращает `*` в `[^/]*`, `**` в `.*` и запускает обычный backtracking regex синхронно. На реальном `SafeArtifactIntake` → frozen snapshot → `load_and_match_codeowners` synthetic CODEOWNERS размером 60 bytes (точная генерация: `"*" + "a*" * 25 + "b @owner\n"`) и безопасном path из 60 `a` за 2 секунды matcher не завершился; child process пришлось остановить. Вызов идёт из `async build_responsibility` через `service.py:118-125`, поэтому один специально сформированный CODEOWNERS может занять общий ASGI loop. Нужен линейный glob matcher или жёсткий лимит сложности/времени в worker thread.

### Medium — CODEOWNERS даёт неверную атрибуцию для root `**` и inline comments

Тот же живой snapshot proof показал:

* `**/A.kt @all-levels` не совпал с root path `A.kt`, хотя CODEOWNERS wildcard `**/A.kt` должен включать корень и вложенные каталоги; fallback `* @fallback` был возвращён.
* Строка `*.kt @backend # explanation` вернула owners `[@backend, #, explanation]`, то есть текст inline comment стал псевдовладельцами. UI затем показывает их как владельцев (`ResponsibilityPanel.tsx`), а `_suggest_role` добавляет их в basis (`service.py:68-87`).

Также фактический вывод предыдущего proof требует исправления первоначального текста отчёта: `/src/ @root-team` ошибочно совпал с `nested/src/A.kt` (ведущий `/` потерян), а строка `/src/` без owners не сняла прежнее `* @fallback`. Ранее эти два результата были ошибочно названы ожидаемыми; это неверно для GitHub CODEOWNERS root anchoring и явного снятия ownership. Это дополнительные воспроизведённые неправильные ownership hints, не доказательство доступа к чужим данным.

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
