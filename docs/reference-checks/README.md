# Reference checks: версионированный эталон против снимка

Фича сравнивает **pinned reference suite** (JSON-манифест из ветки/коммита) с
**published `AnalysisBundle` 0.1** другого снимка того же проекта. Это сравнение
данных, а не запуск чужого кода: runner не делает checkout, не вызывает тесты и
не исполняет репозиторий. Песочница для custom checks остаётся у владельца
`ctx_0504409dada5`.

## Что фиксируется

Манифест `reference-suite/v1`:

- `reference.branch`, `reference.sha` — ветка и коммит эталона;
- `reference.snapshotManifestHash`, `artifactHash`, `bundleSha256` — байты и
  манифест эталонного снимка;
- `reference.runnerVersion` — `producerVersion` из bundle;
- `reference.signature` — полная статическая подпись анализаторов (12 полей,
  включая hashes и domain policy);
- `targetSha` передаётся при запуске и попадает в отчёт.

Отчёт `reference-report/v1` дополнительно пишет `target.runId`, `snapshotId`,
`artifactHash`, `snapshotManifestHash`, `runnerVersions` и версию компаратора
`reference-checks/1`.

## Правила вердикта

| Ситуация | Вердикт |
| --- | --- |
| Все ожидаемые находки совпали по `fp/v1` в `checked` scope, контроли чисты | `pass` |
| Подпись/runner/схема/проект несовместимы | `inconclusive` |
| У ожидаемой пары path/rule нет `checked` scope | `inconclusive` по строке |
| Ожидаемая находка пропала в `checked` scope, fingerprint/severity изменились | `fail` |
| Безопасный контроль дал находку | `fail` |
| Есть находка вне манифеста | `inconclusive` (лишняя находка видна отдельно) |

Пустой список `expectedFindings` или `expectedControls` отклоняется: suite без
ожиданий не может дать `pass`.

## Fixtures и отчёты

- Манифест: `docs/reference-checks/fixtures/reference-suite.json`.
- Эталонные фикстуры и `reference.sha`: коммит
  `ae1fd4805c3ce8cfc533be99a601b0e2d3aa95a1`.
- Эталонный снимок: `fixtures/project-reference/` — SQL-инъекция, fallback-секрет
  в Kotlin и токен в YAML, плюс безопасный контроллер.
- `fixtures/project-compatible/` — тот же проект с безвредным комментарием.
- `fixtures/project-fixed/` — параметризованный SQL, секреты из окружения.

Живые отчёты (`docs/reference-checks/reports/`):

| Отчёт | Статус | Итог |
| --- | --- | --- |
| `compatible.json` | `pass` | 3/3 находки, 2/2 контроля, 0 лишних |
| `failure.json` | `fail` | 3 находки пропали в `checked` scope, контроли чисты |
| `incompatible.json` | `inconclusive` | `rulesVersion` не совпал, сравнение не начато |
| `coverage-loss.json` | `inconclusive` | 2 совпали, 1 без scope покрытия; лишних нет |

## Воспроизвести

```bash
# 1. Реальный bundle из фикстур текущей ветки
python3 -m analyzers.contract_export docs/reference-checks/fixtures/project-reference \
  --db analyzers/fixtures/vulnerabilities.json --out /tmp/reference-bundle.json
sha256sum /tmp/reference-bundle.json   # должен совпасть с reference.bundleSha256

# 2. Совместимый снимок
python3 -m analyzers.contract_export docs/reference-checks/fixtures/project-compatible \
  --db analyzers/fixtures/vulnerabilities.json --out /tmp/compatible-bundle.json
python3 -m backend.app.reference_checks docs/reference-checks/fixtures/reference-suite.json \
  /tmp/compatible-bundle.json \
  --target-sha ae1fd4805c3ce8cfc533be99a601b0e2d3aa95a1 \
  --target-project-id reference-checks/orders-service \
  --target-branch fixtures/target-compatible --out /tmp/compatible.json

# 3. Снимок с исправлениями: ожидаемые находки пропадают
python3 -m analyzers.contract_export docs/reference-checks/fixtures/project-fixed \
  --db analyzers/fixtures/vulnerabilities.json --out /tmp/fixed-bundle.json
python3 -m backend.app.reference_checks docs/reference-checks/fixtures/reference-suite.json \
  /tmp/fixed-bundle.json \
  --target-sha ae1fd4805c3ce8cfc533be99a601b0e2d3aa95a1 \
  --target-project-id reference-checks/orders-service \
  --target-branch fixtures/target-fixed --out /tmp/failure.json
```

Коды выхода CLI: `0` pass, `1` fail, `2` inconclusive, `3` битый вход.
`incompatible.json` и `coverage-loss.json` получены мутацией уже снятого bundle:
первый меняет `signature.rulesVersion` на `cg26-rules/0.2.0`, второй убирает
`checked` scope `OrderLookupController.kt / CG26-SAST-001`. Это проверка
защитных веток, а не второй прогон анализатора.

## Где код

- `backend/app/reference_checks/manifest.py` — разбор и проверка манифеста.
- `backend/app/reference_checks/bundle.py` — чтение `AnalysisBundle` 0.1,
  пересчёт `fp/v1`, покрытие и подпись.
- `backend/app/reference_checks/compare.py` — вердикт и построчный отчёт.
- `backend/app/reference_checks/__main__.py` — CLI для файлов.
- `backend/app/api/reference_checks.py` — router factory, `POST /reference-checks/compare`.
- `frontend/src/components/reference-checks/ReferenceChecksPanel.tsx` — standalone
  выбор pinned эталона/артефакта и показ отчёта.
- `docs/reference-checks/ROOT-INTEGRATION.md` — root-сниппет backend + frontend.

Компонент ничего не выдумывает: если bundle не загружен или API не смонтирован,
он показывает ошибку. `pass` рисуется только из ответа компаратора.