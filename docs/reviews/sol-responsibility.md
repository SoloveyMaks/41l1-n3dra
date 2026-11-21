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

## Targeted recheck: 2af8c391 — частично принято

Точный owner SHA `2af8c39183755270e0ad33a60bbdd9619dd2b6aa`, локальный merge HEAD на момент этой проверки `5543d2c19d5e6e4f57c369e48fdf5f70ccb3edb5`. Этот раздел не переоткрывает уже принятый review-state/PR fix и не закрывает 864273d streaming allocation gap.

### Подтверждено живыми локальными прогонами

* Вредный 60-byte CODEOWNERS pattern (`"*" + "a*" * 25 + "b @owner\n"`) завершился за **0.006 s** через реальный `SafeArtifactIntake` → immutable snapshot → matcher; event-loop blocking ReDoS из предыдущего review больше не воспроизвёлся.
* На том же intake доказаны `/src/` root anchoring (nested path получает fallback), ownerless `/src/` снимает fallback, `**/A.kt` совпадает с root `A.kt`, inline `# comment` не становится owner, а файл с правилами после 400 возвращает `status=incomplete` и пустых owners вместо поздней атрибуции.
* Реальный disposable PG proof с десятью занятыми pool connections: ожидание отсутствующего run заняло **0.351 s**, ticker max gap **0.011 s**, все connections возвращены (`all_returned=true`). `service.py` теперь переносит синхронный DB/filesystem/CODEOWNERS context в `asyncio.to_thread`.
* Source-read `service.py:217-240`: active semaphore ограничен **4**, inflight key включает `workspace_id`, `run_id`, `finding_id`, а `asyncio.shield` и done-callback отделяют отмену одного awaiter от общей task. Runtime cancellation/tenant proof ниже подтверждает это на данном SHA; будущая замена to_thread требует повторной проверки передачи ContextVar.

### Medium — nested PR 429 не включает provider cooldown

`backend/app/responsibility/history.py:505-510` включает provider cooldown только когда **первичный file-history** запрос вернул `rate_limited`; `:570-574` делает то же после aggregate `pr_status`. Но `_github_pull_requests` возвращает `"available"` из `:419` даже если один из detail/reviews запросов внутри `review_by_number` вернул `rate_limited` (`:352-358`, `:376-393`); этот статус теряется. Поэтому 429 на PR detail/reviews не включает `PROVIDER_COOLDOWN_SECONDS=60`, и следующий cache-miss может немедленно повторить provider запрос. Это source-confirmed finding, внешние запросы не выполнялись; нужен явный propagation rate-limited из nested calls.

### Medium — bounded DP всё ещё даёт ложное совпадение по префиксу имени

`backend/app/responsibility/codeowners.py:89-96` обрабатывает `**/` так, что wildcard может поглотить символы до первого slash, включая часть последнего filename. Новый live intake proof (immutable ZIP snapshot, без host files) вернул владельца `@owner` для правил `A.kt @owner` и пути `BA.kt`, а также для `**/src/A.kt @owner` и пути `notsrc/A.kt`; оба случая должны были остаться на fallback/без совпадения. Это уже не ReDoS, а неправильная ownership attribution: безопасный путь получает чужую подсказку владельца. Нужна проверка directory boundary при `**/` и обычном непути pattern; не считать matcher полностью закрытым до fix.

### Не закрыто

* 864273d gzip/decompressor allocation gap остаётся: `aiter_bytes()` может выделить большой decoded chunk до проверки 256 KiB (см. живой UDS proof выше).
* Service inflight key включает tenant/run/finding, но разные findings одного public repo/path не объединяют history network requests. Provider-wide cooldown не покрывает потерянный nested 429 до owner fix. Кроме того, 10 s wait_for начинается после входа в semaphore, поэтому это бюджет активной обработки, а не жёсткий общий wall-clock deadline с очередью; при очереди elapsed может быть выше. Отмена coroutine не останавливает уже работающий `to_thread` DB вызов; это известная граница API, а не доказанная утечка connections.
* Дополнительный real-PG concurrency proof: два одинаковых awaiter в одном tenant и один в другом дали независимые inflight keys; отмена одного awaiter не отменила другого, результаты сохранили пути `one/A.kt` и `two/A.kt`, cleanup оставил 0 inflight. Восемь разных запросов упёрлись ровно в semaphore=4 (четыре ожидали pool), после освобождения все завершились и pool вернулся. Это подтверждает cancellation/tenant scope для service layer; внешний provider не вызывался.
* Production/provider traffic, external Telegram, baseline/auth80, tenant ASGI and frontend build не повторялись.

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


## Следующий candidate: admission b3f7240 — не принят

Координатор предоставил `b3f72400bf2c76629a9278acb631e73ef40771c1` и сразу указал найденную им регрессию: `run_in_executor(None, _load_context, ...)` не переносит request ContextVar, в отличие от `asyncio.to_thread`. Этот SHA здесь не объявлен tenant-safe и не включён в доказательства 2af8c391. Для совместного targeted admission/cancellation/ContextVar acceptance ожидается точный исправленный SHA; gzip allocation и новые matcher/cooldown findings также остаются открытыми. Базовые auth/TG прогоны не нужны.

## Gzip targeted recheck: bb863df4 — allocation defect закрыт

Точный fix SHA `bb863df4c024a25d4a3fed24b4e974259f580086`, review merge HEAD `e4022b35fdbc25cfcb58555c9e5ca2903a69b60f`. Влит только для targeted transport proof; включённый ancestor b3f7240 **не принят** по tenant ContextVar до отдельного fix.

* Исходный реальный Unix-socket HTTP proof повторён без изменения gzip16MiB payload: `_fetch_json_page` вернул `failed` на size ceiling с peak **504278 bytes** вместо **22502738 bytes** на 864273d. Первые 8192 wire bytes больше не разворачиваются целиком до проверки. Small valid gzip принят; обычный exact 262144-byte JSON остаётся `ok`, oversized plain остановлен после 270336 bytes. Тем самым исходный gzip allocation defect закрыт на этом SHA; это не обещание лимита всех allocations до 256 KiB.
* Source-read `_read_limited_json_body`: `aiter_raw`, compressed ceiling 256 KiB, zlib `max_length` для decoded chunks, отказ от unsupported Content-Encoding; `_client` просит identity. На живом локальном drip-feed (byte каждые 0.6 s, соединение активно) запрос вернул `failed` по общему request deadline через **4.023 s**; это больше не только per-read timeout.
* Minor boundary limitation: valid JSON ровно **262144 decoded bytes**, сжатый gzip в 290 bytes, отклонён как oversized, хотя тот же plain JSON принят. Причина — `remaining <= 0` перед `decoder.flush()`/eof даже когда decoder уже полностью завершён. Это воспроизведённая консервативная ошибка на точном верхнем пределе, не обход memory bound и сама по себе не блокирует feature acceptance.
* Повторно подтвердились COMMENTED-preserves-decision, incomplete false, 20-reviewer cap и URL sanitizer из прежнего прямого reducer proof; новых provider calls не было. Temporary scripts/logs: `responsibility-bb-proof.log`, `responsibility-bb-boundaries.py/.log` под `/tmp/sol-accounts-review/`.
* Общая feature acceptance всё ещё открыта: ContextVar regression b3f7240, nested PR429 cooldown и CODEOWNERS suffix false attribution требуют собственных точных fix SHA и focused recheck. До этого результаты 2af8 по scoped to_thread не переносятся автоматически на b3f/bb.


## Final targeted recheck: 265427f2 — context, admission, matcher and cooldown

Точный совместный SHA `265427f2894d93f75568724e9e7e83b51157daf3`, локальный merge HEAD `9bc52ffd50488f563c9c7621d827c716b6fcc4a9`. Проверены только открытые cases; baseline/auth/TG не повторялись.

* Реальный ASGI/PG прогон: 12 параллельных responsibility запросов двух account workspaces вернули HTTP 200 с правильными tenant paths; cross-workspace run/finding дали `[404, 404]`. Это подтверждает `copy_context().run` вокруг executor и отсутствие смешения ContextVar.
* Same-key cancellation: отмена одного awaiter не отменила второго, результаты сохранили `tenant-0/A.kt` и `tenant-1/A.kt`, после завершения `inflight=0`, `active=0`, `queued=0`. Для 20 реальных запросов при удержанном PG lock наблюдались `active=4`, `queued=8`, `busy=8`; после общего 10 s deadline работающие executor threads ещё были видны как **4 real PG lock waiters**, то есть слот не освобождается до реального окончания sync DB работы. После снятия lock connections вернулись, счётчики очистились.
* Локальный immutable intake повторил suffix boundaries: `A.kt` не совпал с `BA.kt`, `**/src/A.kt` не совпал с `notsrc/A.kt`; fallback сохранился. Ранее подтверждённые ownerless, `**/A.kt` root, inline comment и >400 incomplete остаются зелёными.
* Живой локальный HTTPS transport (Unix socket, self-signed cert только для стенда) вернул PR list/detail `200`, reviews `429`; aggregate PR result сохранился, `reviewsStatus=rate_limited`, provider cooldown был выставлен на ~60 s, следующий другой path получил `rate_limited` до нового HTTP вызова (`extraCalls=0`). Это закрывает nested PR429 propagation.
* Четыре поздних `Future exception was never retrieved`/`ResponsibilityNotFound` warnings появились только в моём диагностическом скрипте при принудительном timeout/PG lock; в нормальном owner proof исключение callback consumption заявлено. Само удержание слотов и pool cleanup подтверждено; warnings передал координатору как low-severity review hygiene.

### Остаток

Gzip exact decoded cap на ровно 262144 bytes остаётся консервативно отвергающим валидный gzip, зафиксировано как minor edge. Внешние provider calls, Telegram и production ports не использовались; frontend build и baseline auth/TG не повторялись. Принимаю 265427f2 по targeted context/admission/matcher/cooldown cases с этой minor edge и diagnostic warning оговорками.
