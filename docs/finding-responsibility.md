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
  "codeowners": {"status": "available|absent|unavailable", "file": ".github/CODEOWNERS",
                 "matchedPattern": "...", "line": 2, "owners": ["@team"], "basis": "...", "limitations": []},
  "history": {"status": "available|unavailable|rate_limited|failed|unsupported",
              "provider": "github", "reason": "...", "evidenceKind": "last_file_commit",
              "notBlame": true,
              "commit": {"sha": "...", "author": "...", "date": "...", "url": "...", "message": "..."},
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
  путь проверяется на traversal. Лимит 128 KiB.
- Семантика: последнее совпавшее правило, `@team`/email owners, `*`/`**`/`?`,
  каталоги с `/`. Отсутствие файла — `absent`, а не «нет владельцев».

## История: last file commit, не blame

- Используется только `artifact.repository.commit_sha` + `primaryLocation.path`
  finding. URL, branch и path от клиента не принимаются.
- Допустимы только `github.com`/`gitlab.com` из artifact meta; redirects
  отключены, ответ ограничен 256 KiB, timeout 4 s (connect 2 s), кэш 15 минут.
- GitHub: `GET https://api.github.com/repos/{owner}/{repo}/commits?sha={frozenSHA}&path={path}&per_page=1`.
- GitLab: `GET https://gitlab.com/api/v4/projects/{project}/repository/commits?ref_name={frozenSHA}&path={path}&per_page=1`.
- Возвращаются commit SHA/author/date/link/message. `evidenceKind=last_file_commit`,
  `notBlame=true`; в UI явно: автор коммита ≠ автор уязвимости.
- `429`/GitHub rate-limit → `rate_limited`; `404`/`401/403` → `unavailable`;
  timeout/5xx/redirect → `failed`; всё это 200 DTO, анализ не ломается.
- ZIP без repository/commit_sha → `unavailable` с причиной «история не входит в
  snapshot». Роль и CODEOWNERS всё равно считаются, если snapshot доступен.
- Line blame отсутствует: `lineBlame.status=unsupported`; file history не
  выдаётся за blame строк.
- Токены не используются и не читаются; публичные API без credentials.

## UI

`ResponsibilityPanel` получает маленькие props `runId` и `findingId`.
Показывает роль сразу, CODEOWNERS и историю отдельными строками, явно
маркирует `last_file_commit · не blame`, ссылку на коммит только для
https github/gitlab, раскрываемые основания/ограничения. Есть loading/error/
empty/retry. Пока root не смонтировал router, панель показывает понятное
«подсказка недоступна», не ломая Finding/EvidenceCard.

## Живая проверка

Merged backend, test PostgreSQL, account A/B, router mounted только в тестовой
обёртке (root mount остаётся за интегратором).

- Public training repo `https://github.com/TBagirov/training`, artifact
  `art_d3b87fdfe8ef`, commit `fe1fcbe212669783480666b9b60147c8a8c6eef4`:
  finding `SECRET-API-KEY` → history `available`, author `Bagirov Teimur`,
  date `2026-11-18T21:03:23Z`, commit URL, `last_file_commit`, `notBlame=true`.
- Same run/finding from account B → `404`; unknown finding → `404`.
- ZIP sample with `.github/CODEOWNERS`: matching finding
  `KOTLIN-SQL-INJECTION`/UserController.kt → owners
  `@backend-team, @security-owner`, pattern and line; history `unavailable`
  because ZIP has no repository/`.git`.
- Provider invalid commit (`0…0`) → stable `unavailable`, not 500; rate-limit/
  timeout branches are bounded DTO states.
- UI 1440: role/basis, commit author/date/link, warning «не автор уязвимости»,
  CODEOWNERS owners; UI 390: `scrollWidth=390`, panel visible, console errors 0.
- `npm run build`: `index-B13snarW.js` / `index-CzLQxbUp.css`.

## Ограничения

- Роль — эвристическая подсказка по analyzer/rule/path; CODEOWNERS не
  гарантирует актуального человека.
- GitHub/GitLab file history — только публичный API и только последний коммит
  файла на frozen SHA. Line blame не поддержан.
- GitLab code path реализован, но живой provider-прогон выполнен на GitHub;
  rate-limit/timeout стабильны по коду и bounded response.
- ZIP-артефакт никогда не получает историю репозитория.
