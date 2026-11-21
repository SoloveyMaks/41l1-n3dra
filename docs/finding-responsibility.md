# Ответственная роль по находке: ownership hint

Дата: 21.11.2026. Задача — показать ответственную роль и полезный repository
context по конкретной находке, не меняя analyzer facts, Finding и вердикт.

## Границы

- Новый backend-модуль `backend/app/responsibility/**` и фабрика
  `backend/app/api/responsibility.py`.
- Новый DTO `ResponsibilityResponse` отдельно от `Finding`.
- UI: `frontend/src/components/ResponsibilityPanel.tsx`,
  `FindingsView.tsx`, минимальные `App.tsx`, `types/index.ts`, `api/client.ts`.
- Не изменяются `reports/**`, Telegram, auth/session, scanners, canonical
  reports, Finding fingerprint/severity/confidence/count/verdict.
- Новых миграций и schema нет: enrichment read-only.

## API

```http
GET /api/runs/{run_id}/findings/{finding_id}/responsibility
```

`run_id`/`finding_id` — единственные входные идентификаторы. Клиент не может
передать произвольный URL или path. Run ищется в workspace текущей сессии;
finding обязан принадлежать этому run. Чужой/неизвестный run или finding —
`404 RESPONSIBILITY_NOT_FOUND`. Provider/snapshot сбой не превращается в 500
анализа: возвращается bounded DTO со статусом истории или `503
RESPONSIBILITY_UNAVAILABLE` только для неожиданной внутренней ошибки.

Root mount:

```python
from backend.app.api.responsibility import create_responsibility_router
api_router.include_router(create_responsibility_router(db_repo, codefacts_store))
```

## DTO

```json
{
  "scope": {"runId": "...", "artifactId": "...", "snapshotId": "...",
            "commitSha": "...", "path": "src/..."},
  "findingId": "...", "analyzer": "secret", "ruleId": "SECRET-API-KEY",
  "suggestedRole": {"role": "...", "basis": "analyzer/rule/path...", "confidence": "high"},
  "codeowners": {"status": "available|absent|unavailable|incomplete", "file": ".github/CODEOWNERS",
                 "matchedPattern": "...", "line": 2, "owners": ["@team"], "basis": "...", "limitations": []},
  "history": {"status": "available|unavailable|rate_limited|failed|unsupported",
              "provider": "github", "reason": "...", "evidenceKind": "last_file_commit",
              "notBlame": true,
              "commit": {"sha": "...",
                         "author": {"displayName": "...", "login": "..."},
                         "committer": {"displayName": "...", "login": "..."},
                         "date": "...", "url": "...", "message": "..."},
              "associationCommitSha": "...", "frozenCommitSha": "...",
              "pullRequests": [{"number": 160301, "title": "...", "url": "...",
                                "author": {"login": "..."}, "mergedBy": {"login": "..."},
                                "mergedAt": "...", "headSha": "...",
                                "reviews": [{"reviewer": {"login": "..."},
                                             "state": "APPROVED|CHANGES_REQUESTED|DISMISSED|NONE",
                                             "opinionState": "COMMENTED|APPROVED|...",
                                             "submittedAt": "...", "decisionAt": "...",
                                             "appliesToMerge": false, "limitation": "..."}],
                                "reviewsStatus": "available|incomplete|...",
                                "reviewsTruncated": false, "reviewsIncomplete": false}],
              "pullRequestsStatus": "available|none|unavailable|rate_limited|failed|unsupported",
              "pullRequestsTruncated": false,
              "lineBlame": {"status": "unsupported", "reason": "..."}, "limitations": []},
  "limitations": ["Роль — подсказка, не назначение человека..."]
}
```

## Роль: только подсказка

`suggestedRole` вычисляется по `analyzer + ruleId + path` с явным `basis`:

- `secret` / `SECRET*` / `CG26-SEC*` → владелец конфигурации и секретов.
- `sca` / `CG26-SCA*` → ответственный за зависимости и сборку.
- `CF-ROUTE*`, `CF-OWNER*`, `IDOR` → владелец маршрута/модели доступа.
- SQL-правила → backend-разработчик доступа к данным.
- прочий `sast` → backend-разработчик обработчика.
- иначе → владелец модуля, вычисленного из path (низкая уверенность).

Если CODEOWNERS дал совпавших owner'ов, это добавляется к `basis` и
поднимает уверенность. Это не назначение человека и не доказательство
авторства уязвимости.

## CODEOWNERS

- Ищется только `.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS` в том же
  immutable snapshot.
- Файл перечитывается из artifact root, SHA-256 сверяется с manifest snapshot;
  путь проверяется на traversal. Лимит 128 KiB, 400 правил, pattern 256,
  path 1024.
- Matcher — bounded polynomial wildcard DP (`*`, `**`, `**/`, `?`, каталоги),
  без regex: `*`/`**` backtracking и ReDoS исключены. Unsupported syntax
  (`[]{}()\|!`) даёт `unavailable`, а не угаданного owner.
- Семантика: последнее совпавшее правило; `*` не зависит от глубины,
  `/src/` и `src/` root-relative и не матчат `nested/src/A.kt`; `**/A.kt`
  матчит root `A.kt` и `x/A.kt`, но не `BA.kt`; `**/src/A.kt` матчит
  `src/A.kt`/`x/src/A.kt`, но не `notsrc/A.kt`; ownerless-правило отменяет
  fallback; inline `#comment` не owner.
- Больше 400 правил → `incomplete`, более поздние правила не применяются и
  owner не выдаётся. Отсутствие файла — `absent`, а не «нет владельцев».

## История: last file commit, не blame

- Используется только `artifact.repository.commit_sha` + `primaryLocation.path`
  finding. URL, branch и path от клиента не принимаются.
- Допустимы только `github.com`/`gitlab.com` из artifact meta; redirects
  отключены. Response читается `aiter_raw` с hard ceiling 256 KiB на compressed
  и 256 KiB на decompressed bytes; gzip/deflate декодируются инкрементально
  через `zlib.decompressobj(max_length)`, `Content-Length` не доверяем.
  `Accept-Encoding: identity`; unsupported/повреждённый content-encoding —
  `failed`, без чтения всего body. Request hard deadline 4 s (connect 2 s),
  общий enrichment deadline 9 s, кэш 15 минут. Запросы concurrent bounded
  (не более двух PR detail/review одновременно).
- GitHub: `GET https://api.github.com/repos/{owner}/{repo}/commits?sha={frozenSHA}&path={path}&per_page=1`.
- GitLab: `GET https://gitlab.com/api/v4/projects/{project}/repository/commits?ref_name={frozenSHA}&path={path}&per_page=1`.
- Возвращаются commit SHA, author и committer раздельно (display name/login,
  без email), date/link/message. `evidenceKind=last_file_commit`, `notBlame=true`;
  в UI явно: автор/коммиттер ≠ автор уязвимости.
- Связанные PR берутся для `associationCommitSha` = последний коммит файла,
  `GET /repos/{owner}/{repo}/commits/{associationSHA}/pulls`; frozen snapshot
  SHA хранится отдельно. Список bounded (первые 3), `pullRequestsTruncated`
  честно показывает, что PR больше.
- PR: number/title/url/author/mergedBy/mergedAt/headSha. Reviews/approvals
  запрашиваются только для первых двух PR, `per_page=100`, список reviewers
  bounded.
- Для каждого reviewer берётся **последнее решение до merge**; review после
  merge не может переписать историю. `APPROVED` отдельно от
  `CHANGES_REQUESTED`/`COMMENTED`/`DISMISSED`. `appliesToMerge=true` только когда
  review commit совпал с merged head SHA и submittedAt ≤ mergedAt; иначе
  `appliesToMerge=false` и явное ограничение «историческое/не доказано к merge».
- PR association выполняется для `associationCommitSha` = последний коммит
  файла из file history, а не для текущего head ветки; frozen snapshot SHA
  хранится отдельно в `frozenCommitSha`/`scope.commitSha`. PR, связанный с
  другим head, не приписывается автору изменения файла.
- Reviews pagination: если есть `Link: rel="next"` или страница заполнена до
  `per_page=100`, массив помечается incomplete; такие approvals не считаются
  действующими (`appliesToMerge=false`), даже если в первой странице был
  APPROVED. Полностью загруженный список ограничен 20 reviewers, что честно
  показывается как truncated.
- Review state reconstructs separately: `opinionState` — последний отзыв,
  `state` — решение (APPROVED/CHANGES_REQUESTED/DISMISSED/NONE). COMMENTED после
  APPROVED не отменяет approval; CHANGES_REQUESTED/DISMISSED меняют решение.
- PR association и reviews — факты провайдера, не доказательство внесения
  уязвимости и не определение виновного.
- GitLab PR/MR list поддержан; исторические reviews/approvals без токена
  помечаются `reviewsStatus=unsupported`, а не выдаются за подтверждённые.
- `429`/GitHub rate-limit → `rate_limited` и provider cooldown 60 s; cooldown
  ставится централизованно в `_fetch_json_page` по provider host, включая
  nested detail/reviews 429, а не только aggregate `pullRequestsStatus`.
  Nested 429 оставляет PR `available` с частичными reviews, но следующий
  другой path/repo в cooldown уже не делает сетевых запросов.
  `404`/`401/403` → `unavailable`; timeout/5xx/redirect → `failed`; всё это
  200 DTO, анализ не ломается. PR lookup failure отдельный
  `pullRequestsStatus`, commit history при этом остаётся доступной, если
  provider её отдал.
- DB/store/CODEOWNERS читаются в executor через
  `contextvars.copy_context().run` (не голый `run_in_executor`): tenant
  workspace из account scope сохраняется, env fallback не подменяет его.
  Bounded admission: 4 active + 8 queued; сверх этого — `503
  RESPONSIBILITY_BUSY`, без создания task. Одинаковые workspace/run/finding
  дедуплицируются in-flight, отмена одного caller не отменяет shared build.
- Общий deadline 10 s включает ожидание в очереди. Слот sync worker не
  освобождается при HTTP timeout/cancel, пока `_load_context` реально не
  завершился; shared task exception забирается в done callback без warning.
  Кэш истории — только публичные provider-ответы (provider/project/commit/path),
  без tenant-данных.
- ZIP без repository/commit_sha → `unavailable` с причиной «история не входит в
  snapshot». Роль и CODEOWNERS всё равно считаются, если snapshot доступен.
- Line blame отсутствует: `lineBlame.status=unsupported`; file history не
  выдаётся за blame строк.
- Токены не используются и не читаются; публичные API без credentials.

## UI

`ResponsibilityPanel` получает маленькие props `runId` и `findingId`.
Показывает роль сразу, CODEOWNERS и сводку истории отдельными строками,
явно маркирует `last_file_commit · не blame`. Раскрываемый блок
«История изменения» содержит автора и committer раздельно, связанные PR,
mergedBy/mergedAt и reviews со state/submittedAt/привязкой к merged head;
APPROVED визуально отделён, исторические/недоказанные решения помечены.
Ссылки на коммит/PR только для https github/gitlab. Есть loading/error/
empty/retry. Пока root не смонтировал router, панель показывает понятное
«подсказка недоступна», не ломая Finding/EvidenceCard.

## Живая проверка

Merged backend, test PostgreSQL, account A/B. Router смонтирован в текущем dev;
отдельная тестовая обёртка использовалась только для раннего прогона.

- Root-mounted endpoint on current dev: training repo artifact
  `art_4471bc181f46`, run `9c4979cd…` → 200; commit author/committer
  `Bagirov Teimur / @TBagirov` раздельно, `pullRequestsStatus=none`; account B
  on same run/finding → `404`.
- Public training repo `https://github.com/TBagirov/training`, artifact
  `art_d3b87fdfe8ef`, commit `fe1fcbe212669783480666b9b60147c8a8c6eef4`:
  finding `SECRET-API-KEY` → history `available`, author `Bagirov Teimur`,
  date `2026-11-18T21:03:23Z`, commit URL, `last_file_commit`, `notBlame=true`;
  PR association для этого commit — `none`.
- Public `python/cpython`, frozen commit
  `4b129a74503384f787739793b92dad2120285cec`, path `Lib/http/cookiejar.py`:
  commit author и committer `Gregory P. Smith` / `@gpshead` раздельно;
  associated PR `#160301`, author/mergedBy `@gpshead`, mergedAt
  `2026-10-25T07:01:06Z`; JelleZijlstra APPROVED относится к другому head
  (`f561420…`) → `appliesToMerge=false` с ограничением; review после merge
  (StanFromIreland 09:43) не подменяет решение до merge; все reviewer decisions
  bounded, without email.
- Same run/finding from account B → `404`; unknown finding → `404`.
- ZIP sample with `.github/CODEOWNERS`: matching finding
  `KOTLIN-SQL-INJECTION`/UserController.kt → owners
  `@backend-team, @security-owner`, pattern and line; history `unavailable`
  because ZIP has no repository/`.git`.
- Provider invalid commit (`0…0`) → stable `unavailable`, not 500; rate-limit/
  timeout branches are bounded DTO states.
- UI 1440: role/basis, author/committer, PR #160301, mergedBy, APPROVED as
  historical/not-proven-to-merge, warning «не автор уязвимости»; UI 390:
  `scrollWidth=390`, «История изменения» and PR block readable, console errors 0.
  PR/review UI rendered from the real provider DTO; live endpoint direct provider
  call produced that DTO.
- Local streamed oversized response >256 KiB → `failed` ceiling, не чтение
  всего body; COMMENTED после APPROVED → decision APPROVED, opinion COMMENTED,
  applies true; incomplete/full-page 100 reviews → все approvals
  `appliesToMerge=false`, truncated truthful; bad external href → null; GitLab
  `author_name` → displayName without fake login.
- `npm run build`: `index-CYqb29Vq.js` / `index-D9Z-CuvZ.css`.

## Ограничения

- Роль — эвристическая подсказка по analyzer/rule/path; CODEOWNERS не
  гарантирует актуального человека.
- GitHub/GitLab file history — только публичный API и только последний коммит
  файла на frozen SHA. Line blame не поддержан.
- GitLab commit `author_name`/`committer_name` не выдаются за login:
  `displayName` заполняется, `login` остаётся null, если API не дал username.
- Внешние commit/PR ссылки принимаются только `https` + `github.com`/`gitlab.com`
  (provider-matched); иной href отбрасывается.
- PR association bounded (3 PR, reviews для первых 2); если PR больше,
  `pullRequestsTruncated=true`; отсутствие PR/429/ZIP явно в DTO.
- GitHub reviews реализованы с last-decision-before-merge и head-commit
  ограничением; GitLab reviews/approvals без токена помечаются unsupported.
  PR association/reviews не доказывают внесение уязвимости и не определяют
  виновного.
- ZIP-артефакт никогда не получает историю репозитория.
- Sol-сценарии: malicious CODEOWNERS intake + matcher уложился в микросекунды,
  `/src/` не матчит nested, ownerless `/src/` снял fallback, `**/A.kt` матчит
  root, inline comment не owner, 401-е правило дало `incomplete`.
- Real two-account ASGI endpoint: account A и B с разными workspace
  одновременно получили свои 200 с правильными runId; A→B и B→A одновременно
  получили 404. Это проверяет ContextVar на живом PG, не на fake DB.
- Admission test: 20 distinct keys при slow DB → peak real workers 4, 8 busy
  503, 12 timeout 503; после caller timeout новые ключи не подняли peak выше 4;
  в конце active=0, queue=0, inflight=0. Same-key test: отмена одного caller,
  второй получил результат, БД прочитана 1 раз; exception без task warning.
- Real intake boundary cases: `A.kt` matched root/x, not BA.kt; `**/src/A.kt`
  matched src/x-src, not notsrc; ownerless and >400 incomplete сохранены.
- Cooldown test: file history 200 + PR list 200 + reviews 429 → PR `available`
  с `reviewsStatus=rate_limited`, cooldown выставлен; следующий другой
  path/repo получил `rate_limited` без вызова HTTP-клиента, TTL 60 s. Local
  429 transport также выставил cooldown.
- Stream ceiling test через Unix local HTTP transport: plain exact 262144 → ok;
  plain 2 MiB → failed без полного body; gzip 16 MiB при 8 KiB wire chunks →
  failed, tracemalloc peak ~0.5 MiB (не 22 MiB).
