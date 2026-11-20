# Реально проверено в этой ветке

Проверка выполнена 21.11.2026 в локальном disposable PostgreSQL 16.2 из уже установленного кэша, через Unix-сокет в `/tmp`; TCP, новые порты, скачивания и чужой Kotlin snapshot не использовались. В optional probe расширение `vector` уже присутствовало в локальном PostgreSQL build.

```text
psql -X -v ON_ERROR_STOP=1 -f db/migrations/001_core.up.sql
psql -X -v ON_ERROR_STOP=1 -f db/migrations/002_integrity_publish.up.sql
psql -X -v ON_ERROR_STOP=1 -f db/optional/003_exact_vector.up.sql
```

Одноразовый живой SQL probe на синтетическом workspace показал:

- FK не принимает чужой workspace/run/snapshot для evidence, graph endpoint и join-строк;
- после seal нельзя менять snapshot files; revision, run signature и опубликованные facts immutable;
- evidence с byte range за пределами snapshot file отклоняется;
- draft findings не видны через `published_findings`;
- publish с неверными counts отклоняется, повтор с тем же bundle hash идемпотентен, другой hash конфликтует;
- `pass` без published bundle/complete outcomes/checked coverage отклоняется;
- comparison `resolved` прошёл только с двумя published bundles, совместимой signature и checked file/rule; human metadata обязательна для `vetted`;
- optional exact vector retrieval возвратил distance `0` для probe-вектора, отклонил неверную размерность, второй active spec и мутацию active spec; обязательный language filter дал 0 результатов для неподходящего фильтра.

DTO проверены без добавления зависимостей: AJV 8.20.0 с Draft 2020-12 (shape проверка, date-time format в этом probe отключён) собрал 32 `$defs`; TypeScript 6.0.3 проверил сгенерированный `contracts.ts` (`strict`, `noEmit`). `validate-bundle.mjs` на иллюстративном bundle проверил кириллический UTF-8 + CRLF диапазон `[4,6)`, fingerprint recipe и rejected probes для чужого scope, graph endpoint, неверных bytes и tampered fingerprint. Приложение пока отсутствует; это проверка формата и целостности данных, не реальный scanner run.

Не проверено и оставлено A/B/D: auth/RLS/production roles, artifact store и реальное удаление ZIP, scanner полнота expected scopes, Kotlin semantic normalization, redaction quality, внешний provider D, его embedding dimension/model, workflow definition schema и submission export. Optional vector SQL содержит только generic exact search; synthetic 3D probe не является выбором embedding spec.

## Повтор после review координатора

В чистой локальной PostgreSQL 16.2 повторно применены все три миграции и исходные integrity/vector probes. Дополнительные реальные SQL probes отклонили: (1) pass с published bundle, тремя complete outcomes и **нулём coverage**, (2) resolved, когда тот же symbol/path/rule/role остался finding с **другим fingerprint/context hash**, (3) introduced, когда baseline family complete, но соответствующая пара path/rule не была checked. Корректный переход того же anchor в `changed` прошёл. Ранее added checked-baseline/current guard introduced теперь проверен этим отдельным сценарием.

SQL принял раздельные role_access/ownership ресурсы с null approval metadata, отклонил смешанные owner-поля у role policy, missing trustedIdentity у ownership, пустые resources/allowedRoles и нестроковую роль. AJV и TypeScript повторно проверены с union ресурсами; schema approvalStatus=assumption требует оба approval поля null. Production bundle ручной карты не создавался, embedding model/dimension D по-прежнему pending.

## Четыре integrity gaps после независимого review

На чистой disposable PostgreSQL 16.2 применены все up-миграции и повторены предыдущие integrity/comparison/vector probes. Targeted probes подтвердили: три coverage строки SECRET/SCA/SAST с одинаковыми path/ruleId сохраняются без PK collision; изменение только graph unresolvedSymbols меняет bundle hash, graph-only skippedScopes/unresolvedSymbols возвращаются без потери и после publish не меняются; accepted AI explanation на draft Finding отклоняется, после source publish сохраняется. Одна mandatory graph_coverage row включена в expected_counts/content hash/freeze, даже для unavailable графа.

AJV shape + validateBundle приняли три graph scope с разными category и одинаковым path/rule; дубликат той же category отклонён. Воспроизведён partial SECRET с parserErrors, ссылающимся на SAST Diagnostic: shape валидна, semantic validator теперь отклоняет категорию; после замены на SECRET проходит. Graph diagnostic сохраняет явную SAST family. TypeScript strict/noEmit и git diff --check выполнены; никаких настоящих scanner/AI результатов в этих probes нет.
