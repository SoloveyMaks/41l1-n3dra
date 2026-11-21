# Team B runtime (opt-in)

Team B — отдельный пакет `analyzers/` (cg26). Runtime по умолчанию остаётся
`backend/app/analyzers`; Team B включается только явно в draft workflow.

## Как включить через API

У всех трёх scanner nodes должен быть одинаковый engine:

```json
{
  "kind": "secret_scan",
  "config": {"engine": "team_b"}
}
```

То же для `sca_scan` и `kotlin_sast`. `PUT /api/workflows/{id}/draft` сохраняет
draft, `POST /api/workflows/{id}/validate` проверяет его, затем
`POST /api/workflows/{id}/runs`.

- `engine` отсутствует или `backend` → прежний runtime.
- `engine: "team_b"` → adapter Team B.
- Смешанные engines в одном workflow отклоняются validation с перечнем нод.
- Deploy default не переключается этим изменением.

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
5. сохраняет canonical `AnalysisBundle` в run artifact, строит
   `SecurityMapDto` и публикует через существующий `CanonicalPublisher`.

Evidence и policyInputs Team B сохраняются как есть; 67 evidence не
пересоздаются и не теряются. `expectedScopesVerified` остаётся false.

## Ограничения

- Общий `compareBundles` остаётся `inconclusive` для Team B: policy/scope
  attestation не выдаётся за полную.
- SQL narrow endpoint (`/api/comparisons/{b}/{c}/sql-pattern`) ищет
  `KOTLIN-SQL-INJECTION`; Team B rule `CG26-SAST-001` не переименовывается,
  поэтому проверка честно unavailable/inconclusive.
- Owner/auth policy не выдумывается; Team B policy assumption сохраняется.
- В финальный образ нужен `COPY analyzers ./analyzers` (deploy change).

## Локальный smoke

```sh
CG26_HMAC_KEY=... CG26_HMAC_KEY_VERSION=server-hmac-v1 \
CG26_ADVISORY_DB=.../training/vulnerability-database/vulnerabilities.json \
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Training snapshot Team B даёт 15 findings (5 SECRET/5 SCA/5 SAST), 43 nodes,
32 edges, 67 evidence. Kotlin/Gradle не запускаются.
