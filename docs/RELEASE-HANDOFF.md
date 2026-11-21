# Release handoff — 41l1-n3dra / nedra

Короткий пакет для backend-разработчика и жюри: что реально готово, как это
запустить и что пока нельзя заявлять. Полные контракты — в
[docs/data-contracts](data-contracts/README.md), решения — в
[PROJECT-DECISIONS.md](../PROJECT-DECISIONS.md).

## Состояние

- `dev` — собранная интеграция backend/frontend/map/comparison/AI/PostgreSQL.
- `main` — после приёмки. Merge/push в `main` делает координатор, не worker.
- `https://nedra.soloveyka.bond` опубликован: отдельный Cloudflare Tunnel,
  shared demo access gate. Хост — локальный ПК; `Linger=no`, после reboot нужен
  пользовательский login. Работающий образ `c01c96d`; интегрированный Team B
  adapter ожидает финальную пересборку. Production readiness не заявляется.

## Живая проверка (факты)

Последний loopback smoke на текущем `dev`:

- PostgreSQL publish: `run` succeeded, verdict `fail`, **13 findings**,
  AI `succeeded`.
- Security map: **10 nodes, 9 edges, 26 evidence**, coverage `partial` (runtime
  gaps видимы); `SecurityMapDto.bundleHash` совпал с независимым recompute
  (map hash self-check).
- Session/API: `/api/health` 200; `POST /api/session` выдаёт same-origin cookie;
  sample artifact и workflow-run проходят; после session — без ложных auth
  ошибок.
- SQL proof через настоящий upload/executor/PostgreSQL: два immutable scanner
  runs, before — 1 `KOTLIN-SQL-INJECTION`, after — 0; отдельный SQL API возвращает
  `pattern_disappeared/source_pattern`, общий comparison — `inconclusive`.
  Comparison сохранён в БД; повторный запрос сохраняет тот же ID.
- Training на default engine: 33 nodes, 32 edges, 39 evidence, 6 findings.
  Pickup export прослеживается до SQL/данных; evidence открывает masked source.
- Optional Team B adapter: PostgreSQL publication, 15 findings (5/5/5), 43 nodes,
  32 edges, 67 evidence. Mixed engine validation отклоняется; default не сменён.
  Отсутствующий advisory даёт error и `inconclusive` при zero findings;
  известный demo HMAC key отклоняется, raw secrets в AI payload не передаются.
- Внешний HTTPS: health 200, anonymous runs 401, GET session без Set-Cookie,
  wrong login 401, correct login 200 с Secure/HttpOnly/SameSite=Lax; auth map,
  evidence и SQL API 200. Default Python-urllib UA блокируется Cloudflare 1010;
  curl и браузер проходят. Brigada config не менялся.
- Отказ AI provider даёт fallback и не меняет finding/evidence/вердикт.
  Потеря checked scope, несовместимый rulesHash и неоднозначный anchor дают
  `inconclusive`; удалённый path — `file_deleted`. Эти negative cases проверены
  на копиях реальных envelopes в /tmp; отдельные scanner runs для них не создавались.
  Demo и его ограничения — [sql-before-after.md](sql-before-after.md).
- Loopback-образ и health проверялись deploy-скриптами; актуальные image ID,
  revision и container health смотри через `scripts/nedra-status.sh`, а не в
  этом файле — они меняются.

## Запуск

### Локально (PostgreSQL)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
export DATABASE_URL=postgresql://user:password@127.0.0.1:5432/41l1_n3dra
python -m backend.app.storage.migrate     # 001/002/004/005; +003 при DB_ENABLE_VECTOR=1
uvicorn backend.app.main:app --env-file .env --port 8000
cd frontend && npm ci && npm run build    # статику отдаёт backend
# dev-сервер: npm run dev (proxy /api → 127.0.0.1:8000)
```

Локальная сессия: `POST /api/session`. Проверка:

```bash
curl -s -c /tmp/nedra.jar http://127.0.0.1:8000/api/health
curl -s -b /tmp/nedra.jar -c /tmp/nedra.jar -X POST http://127.0.0.1:8000/api/session
curl -s -b /tmp/nedra.jar -X POST http://127.0.0.1:8000/api/artifacts/sample
```

### Локальный контейнер

```bash
scripts/nedra-bootstrap.sh --check     # read-only
scripts/nedra-bootstrap.sh --write     # ~/.config/nedra/runtime.env (600)
scripts/nedra-build.sh
scripts/nedra-up.sh                    # --db-only/--app-only/--recreate-app
scripts/nedra-status.sh
scripts/nedra-stop.sh                  # или nedra-down.sh
scripts/nedra-systemd-install.sh       # tunnel unit не включается
scripts/nedra-tunnel.sh status         # опубликованный отдельный tunnel nedra
```

Секреты и shared demo access code (`CG_ACCESS_CODE`) — только в
`~/.config/nedra/runtime.env`; скрипты не печатают значения, в argv/логи/репозиторий
они не попадают. Детали образов, owner labels и Cloudflare — в
[DEPLOYMENT.md](DEPLOYMENT.md).

### SQL proof / comparison

```bash
python examples/demo/sql-before-after/prepare_snapshots.py
python examples/demo/sql-before-after/run_real_scan.py
node examples/demo/sql-before-after/build_envelopes.mjs
node examples/demo/sql-before-after/negative_probes.mjs
node packages/comparison/compare-cli.mjs \
  examples/demo/sql-before-after/baseline.published.json \
  examples/demo/sql-before-after/current.published.json
```

Это demo-доказательство узкого `source_pattern`; оно не публикует production
bundle и не заменяет global comparison.

## Инварианты, которые нельзя ломать

- `workspaceId`, `runId`, `snapshotId` — UUID и не переписываются; snapshot
  immutable после seal; canonical envelope сохраняет identity.
- Runtime — PostgreSQL без SQLite; факты — `data_contract`, API-состояние —
  `runtime`; миграции идут ledger-ом.
- Факты/evidence, AI-объяснения и domain policy разделены; AI не меняет
  findings/coverage/вердикт.
- Карта фактов (code facts/evidence) ≠ workflow-граф исполнения.
- Три hash не смешиваются: SQL `provenance.bundleHash`, jsonb
  `canonicalHash`/`envelopeHash`, internal `SecurityMapDto.bundleHash`.
  Internal map hash пересчитывается по своему recipe после замены coverage.
- Comparison не выдаёт fix при `changed`, потере покрытия или неоднозначном
  anchor; `deletedFiles` — отдельный список, не «исправление».
- SQL proof `KOTLIN-SQL-INJECTION` — узкий per-file source pattern; per-rule
  observation и comparison persistence включены, global attestation остаётся false.
- AI — только ReformBoss, default `deepseek-v4.1-flash-1017`; ключ не в логах.
- Embeddings `unavailable`: реальный `/v1/embeddings` вернул 403
  `token_model_not_allowed`; spec/dimension не выдумываются, fake vectors нет.
- `vetted` remediation требует реального scanner provenance, published fixed
  bundle, checked coverage и published comparison `resolved` для exact finding;
  synthetic `contract_scenario` — только draft.
- 3–5 подготовленных примеров — механика хранения/сравнения, не доказательство
  преимущества retrieval.

## Перед слиянием в main

1. Закончить отрицательные API/БД сценарии: scope, immutability, ошибочные ZIP.
2. Пересобрать итоговый dev с optional Team B package и проверить публичный путь.
3. Обновить PR и слить `dev → main` с сохранением истории.

## Оставшиеся ограничения

- Нет проверенного embedding provider/model/dimension и реального vetted
  remediation example. Это optional часть: основной анализ работает без неё.
- Parser/anchor/runtime graph ограничения остаются видимыми. Полное покрытие и
  отсутствие уязвимостей не заявляются; общий comparison может быть `inconclusive`.
- Shared demo workspace не является системой приватных многопользовательских
  кабинетов. Хост должен работать, user manager — быть запущен.
- 3–5 примеров не доказывают преимущество retrieval. Независимый VPS и
  production readiness не заявляются.
