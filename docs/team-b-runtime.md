# Team B runtime

Team B — пакет `analyzers/` (cg26). Он больше не opt-in: каждый scanner node
выполняет Team B вместе с backend-анализом, а `engine: team_b`/`backend`
сохранённых ревизий разрешается в тот же полный анализ. Актуальное поведение и
ограничения: [full-analysis.md](full-analysis.md). Ниже — серверная часть и
историческое описание adapter-а.

## Как это выглядит в API

`engine` больше не переключает движок. Все допустимые значения выполняют
полный анализ:

```json
{
  "kind": "secret_scan",
  "config": {"engine": "full"}
}
```

- `engine` отсутствует, `full`, `auto`, `backend` или `team_b` → backend +
  Team B для этой категории.
- Смешанные значения в одном workflow допустимы: каждая нода всё равно
  выполняет полный анализ своей категории.
- Набор категорий задаётся наличием нод; скрытого добавления нет.
- `PUT /api/workflows/{id}/draft`, `POST /api/workflows/{id}/validate`, затем
  `POST /api/workflows/{id}/runs` — как раньше. Исторические ревизии не
  переписываются.

## Серверные требования

```text
CG26_HMAC_KEY=...             # обязателен; demo/local-demo-1 запрещён
CG26_HMAC_KEY_VERSION=...     # обязателен, не local-demo-1
CG26_ADVISORY_DB=/srv/advisory/vulnerabilities.json
CG26_ADVISORY_ROOT=/srv/advisory   # optional containment check
```

`CG26_ADVISORY_DB` — только локальный файл, настроенный на сервере. Adapter не
ищет advisory внутри загруженного ZIP, не принимает URL и не исполняет
Kotlin/Gradle. Если advisory не задан, SCA работает без каталога и честно
пишет диагностику, а не ищет файл в snapshot.

## Что делает adapter

`backend/app/integrations/team_b.py`:

1. берёт распакованный intake snapshot и его full manifest;
2. один раз на run вызывает `analyzers.scan.run` + `build_bundle` с реальными
   workspace/run/snapshot/revision UUID;
3. нормализует identity: ZIP `artifactHash`, C-format full snapshot
   `snapshotManifestHash`; все `scanResults`/`codeFacts` получают ту же identity;
4. переводит три ScanResult в backend DTO для policy/AI/report;
5. передаёт их в общий union: совпавшие с backend находки сливаются, остальные
   добавляются, после чего canonical `SecurityMapDto` строится из объединённых
   findings через codefacts и публикуется существующим `CanonicalPublisher`.

Team B evidence остаётся в его `AnalysisBundle`; в объединённой карте evidence
пересобирается из source-локаций каждого сохранённого finding. `expectedScopesVerified`
остаётся false.

## Ограничения

- Общий `compareBundles` остаётся `inconclusive`: policy/scope attestation не
  выдаётся за полную.
- SQL narrow endpoint (`/api/comparisons/{b}/{c}/sql-pattern`) ищет
  `KOTLIN-SQL-INJECTION`; полный анализ приводит `CG26-SAST-001` к этому
  каноническому id, поэтому merged-находка остаётся видимой.
- Owner/auth policy не выдумывается; Team B policy assumption сохраняется.
- В финальный образ нужен `COPY analyzers ./analyzers` — он уже есть в
  `deploy/Dockerfile` и включает `analyzers/advisory-snapshot/`.

## Локальный smoke

```sh
CG26_HMAC_KEY=... CG26_HMAC_KEY_VERSION=server-hmac-v1 \
CG26_ADVISORY_DB=.../training/vulnerability-database/vulnerabilities.json \
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Training snapshot Team B даёт 15 findings (5 SECRET/5 SCA/5 SAST), 43 nodes,
32 edges, 67 evidence. Kotlin/Gradle не запускаются.
