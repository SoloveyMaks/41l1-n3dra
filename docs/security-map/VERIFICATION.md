# Одноразовая проверка источников и fixture

Проверено 21.11.2026 чтением реальных bytes родительского `training-project/`, без Gradle, Kotlin, HTTP-запросов к приложению или scanner run. Это проверка provenance и структуры, не подтверждение эксплуатации и не оценка качества анализатора.

| Что проверено | Наблюдаемый результат |
| --- | --- |
| Полный manifest источника | 29 файлов: относительные пути, размеры, SHA-256 каждого файла совпали при повторном чтении |
| Evidence ranges | 21/21 на 8 файлах: SHA-256 полного файла, line/column, UTF-8 byte `[start,end)` и snippet совпали с исходником |
| Маскировка ranges | Ручной просмотр 21 range: секретных значений/восстанавливаемых literal-частей в них нет; соседний контекст не включён |
| Structural EvidenceRefs | 59 вхождений refs разрешаются в массиве evidence; включая каждую стрелку, policy, ticket/catalog outlines и unknown gap |
| Первый путь | 7 уникальных nodes, 6 уникальных edges; endpoints существуют, trace связан от anonymous до Finding |
| Scope | Evidence, nodes, edges и layout используют одну illustrative identity; каждый sourceNavigation содержит workspace/run/snapshot/evidence IDs |
| Policy/fingerprint | Canonical hashes пересчитаны; Finding ссылается на существующую unapproved policy, signature содержит её hash |
| Unknown/layout | 1 unknown relation с причиной и без выдуманного target; positions покрывают все nodes, layout ссылается на существующий gap |
| Truth labels | `manual_source_review`, `analyzerOutput=false`, `applicationExecuted=false`, `exploitPerformed=false`; after source/run отсутствуют |
| Whitespace | `git diff --check` и проверка новых документов через `git diff --no-index --check` без ошибок |

Source manifest SHA-256:
`2f1266343a8e22406a2b9915f1fad1dc101ef0db0fbe99cf32afac0ab3907bba`.

Алгоритм manifest: sorted relative paths; JSON массива `{path,sizeBytes,sha256}`, UTF-8, `ensure_ascii=false`, sorted keys, compact separators, без конечного newline. Hash полного файла считается по исходным bytes, не по masked snippets и не после нормализации переводов строк. Все hashes и ranges доступны в `pickup-path.illustration.json`.

Одноразовый Python checker перечитал файлы, проверил ranges/hashes/manifest, непрерывность trace, уникальность и разрешение refs, scope, layout, policy hash/fingerprint и признаки illustration. Не добавлялись unit tests, scanner или код приложения. Этот checker не извлекает routes и не является proof of parser support; семантика пути разобрана вручную.

**Не доказано:** настоящий scanner result, runtime authorization, отсутствие всех внешних guards, approved domain policy, чувствительность реальных данных (dataset синтетический), работа endpoint A/React Flow, SQL fix после повторного scan. SQL ingestion полного production bundle этой карты не проверялся; отдельные синтетические SQL probes описаны в `../data-contracts/verification.md`. Точная операция masked-source, семантика graph vocabulary B и утверждение staff-only policy остаются решениями A/B/D.


## Проекция в актуальные DTO 0.1

Прочитаны `docs/data-contracts/contracts.ts`, `docs/data-contracts/contracts.schema.json`, пояснения/validator и ограничения code edges в SQL. Файлы только читались; проверка зафиксировала bytes обоих contract files и убедилась, что они не менялись в течение самой проверки.

SHA-256 проверенных входов:

- `contracts.ts`: `07729e0035191d1eee5fd529023999887fc29bdb8e5f6c52f6bbcd7bcc9cd759`.
- `contracts.schema.json`: `a47a449667547d953a0b92e54e993050b266d07753e44c434184fc230be8098e`.

Одноразовый checker извлёк JSON mapping plan прямо из `MAPPING.md` и сравнил с `$defs` актуальной Schema. Проверены покрытие каждого исходного node/edge явным mapping или omission, разрешение endpoints и refs, allowlist kinds, совпадение enums TypeScript/Schema, обязательные Finding/Diagnostic/link поля. Не создавались UUID, экземпляры production DTO, подписи scanner или approved policy; это **структурная проверка плана**, не Schema-валидация полного payload и не SQL acceptance.

| Результат проекции | Наблюдаемо проверено |
| --- | --- |
| Nodes/edges | 6 code nodes и 5 code edges с допустимыми kinds; finding-node и supports_finding edge исключены |
| Evidence | Все 21 сохранены: 6 dsl, 8 sql, 7 source; ast/manifest output не выдуман |
| Finding links | 6 планируемых FindingNodeLink на те же graphNodeIds; ни один не указывает на удалённый n-finding |
| Trace | 10 Evidence IDs точно равны ordered unique union исходных trace edge evidenceRefs, включая удалённую последнюю стрелку |
| Unknown guards | 2 исходных refs сохраняются через 2 Diagnostic.evidenceId; nullable edge target не возникает |
| Production prerequisites | Зафиксированы несовместимые illustration IDs/signature/fingerprint/manifest hash и отсутствующие поля staff-only policy; подстановок для прохождения схемы нет |
| Повтор источников | 21/21 ranges, 29 file hashes и 59 исходных ref вхождений снова совпали; исходная JSON fixture не изменена |

`git diff --check`, `git diff --cached --check` и whitespace check нового `MAPPING.md` прошли. Отдельный production bundle намеренно не создавался: A должен выдать настоящий scope/identity, B — facts/signature/coverage/anchor, команда — решение по policy и edge semantics. Этот mapping остаётся proposal, а не подтверждением A/B/D.

## Проверка после интеграции policy union

Интегратор 21.11.2026 повторно сверил 21/21 source ranges, координаты UTF-8 bytes и 29/29 hashes исходных файлов. План проекции сохранил 6 code nodes, 5 code edges, 21 evidence и 10 trace refs; kinds сверены с объединённой Schema. К этому моменту hashes контракта: `contracts.ts` — `e7f31182c7cc84dfd93dd88ada730a2ac9c16ff1e754389c91fd8a49da95b4ed`, `contracts.schema.json` — `f16dee13a0ddf61725a0c20aaa63d57992035eb30636499e1825d4794dae4841`. Предыдущие hashes выше относятся к проверке до добавления union.

Прежнее ограничение обязательных owner-полей для любой policy снято: Schema различает role_access и ownership. AJV принял оба корректных resource shape и отклонил role_access с добавленным ownerField. `MAPPING.md` и `INTEGRATION.md` обновлены под эту форму. Это проверка структуры: предложенная роль MECHANIC не стала утверждённым бизнес-требованием; trusted identity для ticket не выдумывалась. Production policy revision, scanner bundle и UI не создавались.

После четырёх исправлений целостности независимое read-only ревью подтвердило category-aware coverage, сохранение полного graph_coverage, AI только после source publish и проверку category diagnostics. Для публичного переноса локальные пути заменены на sourceRepository/sourceCommit/sourceRoot публичного organizer snapshot; manifest, source hashes, locations и snippets не менялись. Повторная проверка статических ссылок и JSON выполнена перед экспортом; это по-прежнему не scanner run.
