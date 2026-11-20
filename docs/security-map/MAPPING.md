# Ручная иллюстрация → schemaVersion 0.1

Это **план проекции**, не production bundle и не формальное согласование A/B/D. Источник общего контракта после интеграции — [contracts.ts](../data-contracts/contracts.ts) и [contracts.schema.json](../data-contracts/contracts.schema.json); проверенные ревизии зафиксированы в `VERIFICATION.md`. Исходная fixture остаётся `manual_source_review / contract illustration` со своими именами типов.

## Узлы, связи и доказательства

Ниже машинно проверяемый **mapping plan**: все ID — ссылки на illustration, **не UUID и не экземпляры DTO**. `N/V/E/F/D(id)` далее обозначают инъективное переназначение node/evidence/edge/finding/diagnostic ID в реальные scoped UUID при ingestion. Scope `workspaceId/runId/snapshotId` и `workflowRevisionId` выдаёт A; UUID сами по себе не дают доступа. Нельзя получить настоящий run простым переименованием fixture IDs.

```json
{
  "targetSchemaVersion": "0.1",
  "status": "projection_plan_only",
  "nodeKinds": {
    "n-anonymous": "actor", "n-route": "route", "n-permit": "matcher",
    "n-handler": "function", "n-sql": "sink", "n-data": "data"
  },
  "omitNodeIds": ["n-finding"],
  "edges": [
    {"sourceEdgeId": "e-anonymous-route", "kind": "flows_to"},
    {"sourceEdgeId": "e-route-permit", "kind": "matches"},
    {"sourceEdgeId": "e-permit-handler", "kind": "permits"},
    {"sourceEdgeId": "e-handler-sql", "kind": "calls"},
    {"sourceEdgeId": "e-sql-data", "kind": "reads"}
  ],
  "omitEdgeIds": ["e-data-finding"],
  "evidenceKinds": {
    "security_dsl": "dsl", "sql_call": "sql", "sql_projection": "sql", "sql_schema": "sql",
    "route_annotation": "source", "route_and_call": "source", "route_and_return": "source",
    "data_model": "source", "identity_usage": "source", "domain_documentation": "source"
  },
  "finding": {
    "sourceFindingId": "illustration-pickup-access",
    "primaryEvidenceId": "ev-pickup-permit",
    "traceEvidenceIds": [
      "ev-pickup-permit", "ev-pickup-route", "ev-pickup-order", "ev-security-scope",
      "ev-pickup-query", "ev-pickup-data", "ev-ticket-schema", "ev-pickup-schema",
      "ev-domain-context", "ev-pickup-role"
    ],
    "graphNodeIds": ["n-anonymous", "n-route", "n-permit", "n-handler", "n-sql", "n-data"]
  },
  "unknownDiagnostics": [{
    "sourceGapId": "gap-additional-guards", "code": "UNKNOWN_RUNTIME_GUARDS", "category": "SAST",
    "evidenceIds": ["ev-security-scope", "ev-pickup-route"]
  }]
}
```

- **CodeNode:** сохранить `symbolKey`, `label`, `evidenceRefs.map(V)` у шести узлов; заменить kind по таблице, ID через N, добавить реальный scope и `producerVersion`. Для ручного preview producer остаётся `manual-source-review/1`; его нельзя заменить версией scanner B, не получив факты B. `n-finding` не сохраняется в `code_nodes`.
- **CodeEdge:** пять рёбер сохраняют исходные endpoints через N, evidence через V, `resolution`, `resolutionReason`, `producerVersion`; kind из plan, ID через E, полный scope; `basis="source"`, `policyRef=null`. `flows_to` здесь — статический кандидат запроса actor→route, не наблюдавшийся трафик; `matches` route→matcher сохраняет first-match оговорку в reason/evidence, `permits` matcher→function — ручную интерпретацию Spring. Перечень kinds сам по себе не подтверждает семантику parser B. `e-data-finding` не превращается в reads/calls/guards: вывод о риске не является code edge.
- **EvidenceRef:** сохранить **все 21** исходных evidence, `location`, `redactedSnippet`, `sourceHash`, `producerVersion` без изменений; ID через V, добавить scope, kind по plan. Это 6 `dsl`, 8 `sql`, 7 `source`; AST не извлекался, поэтому `ast` не используется. `manifest` допустим в allowlist, но sourceManifest fixture не является отдельным source file/range и не получает выдуманный EvidenceRef. `claim`, `redaction`, `sourceNavigation` отсутствуют в DTO с `additionalProperties=false`: оставляем их в ручном документе/контракте source response A, не в EvidenceRef. Навигация A заново строится из scope+V(id), не из локального пути.

## Finding и refs после удаления рисованной находки

`SecurityMapDto` не содержит `findings`: объект Finding принадлежит реальному SAST `ScanResult`, карта содержит `findingRefs=[F(illustration-pickup-access)]`. Для каждой из шести записей `graphNodeIds` plan создаётся `findingNodeLinks={findingId:F(...), nodeId:N(...)}`; тот же набор N используется в `Finding.graphNodeIds`. Последняя иллюстративная стрелка становится выбором Finding/панелью оснований, не шестым code edge.

`primaryEvidenceId=V(ev-pickup-permit)`; `primaryLocation` равен location этого evidence. `relatedEvidenceIds` — исходные Finding.evidenceRefs кроме primary, в прежнем порядке, через V (7 refs). `traceEvidenceIds` — стабильное объединение evidenceRefs **всех шести** исходных trace edges, включая удаляемое `e-data-finding`, по порядку первого появления: точные 10 refs в plan. Это доказательства пути, не новый порядок исполнения и не восстановление Edge IDs из UUID. Поэтому основания чувствительности/domain intent остаются доступны после удаления finding-node/edge; все 21 refs, включая ticket/catalog/SQL, остаются в `evidence`.

Смысл `status=potential`, category SAST, confidence medium и ограничения сохраняется. `severity` требует lowercase (`HIGH→high`), но оценку подтверждает B. `ruleId/ruleVersion` illustration не выдаются за production rule: B даёт настоящие ruleId/version/family и grounds. `anchor.normalizedPath=primaryLocation.path`, `symbolKey` и `role` — по согласованной B семантике, `normalizedContextHash` считает B из нормализованных токенов/структуры, `secretHmac=null` для этого SAST. Старый illustration fingerprint **не переносится и не переименовывается** в `fp/v1`; новый вычисляется по рецепту DTO:

```text
SHA256(UTF8(JSON.stringify(["fp/v1", category, ruleId, normalizedPath,
                           symbolKey, normalizedContextHash, role, null])))
```

`advisoryIds=[]` для этого SAST только при подтверждении B, а не как замена обязательной версии advisory input в signature. Rule outcomes и новые Finding/anchor/fingerprint не создаются в этом пакете.

## Unknown, policy, identity — где прямое преобразование запрещено

**Runtime guards → Diagnostic.** Один unknown gap с двумя refs превращается в два `Diagnostic` с отдельными D IDs, полным scope, category SAST, code `UNKNOWN_RUNTIME_GUARDS`, исходным `resolutionReason` и handler symbol в безопасном `message`; каждый имеет один `evidenceId=V(...)` из plan. Так сохраняются оба основания при singular evidenceId в DTO. Ни `toId=null`, ни придуманного guard-node/SQL edge нет; `fromId`, `toId`, `resolution` не добавляются к Diagnostic. Карточку у handler связывает view state; Diagnostic — пробел анализа, не новая vulnerability. Две исходные fixture diagnostics при ручном preview сохраняют code/message, получают D IDs/scope/category SAST и `evidenceId=null`; это допустимый nullable diagnostic ref, не nullable FK endpoint у code edge.

**Policy не дорисовывается.** Fixture `unapproved_assumption` соответствует по смыслу `approvalStatus="assumption"`, `approvedBy/approvedAt=null`. Общий контракт различает `role_access` и `ownership`. Для pickup предложение ресурса имеет форму `{"kind":"role_access","resource":"GET /api/pickup/export","allowedRoles":["MECHANIC"]}`. Для него не нужны вымышленные ownerField или trustedIdentity. Ticket требует отдельного `ownership` с настоящими ownerField, trustedIdentity и roleExceptions; пока identity mapping неизвестен, такой ресурс не создаём. Форму можно выразить в DTO, но источник, версия, UUID revision и hash нового policy document ещё должны быть зафиксированы A/C после решения пользователя и B. Хеш ручного policy proposal не переносится как hash новой revision; `potential` не повышается и approval не возникает из SQL shape check. Source-only graph без policyInputs использует **все три null** в signature `domainPolicyRevisionId/domainPolicyVersion/domainPolicyHash`; отсутствие политики явно отражается в diagnostic и Finding.coverageWarnings. Ни production revision, ни signature сейчас не создаются.

**Identity/signature/coverage:** fixture IDs, null artifactHash/revision, ручной manifest hash, неполная signature и illustration fingerprint делают fixture невалидной для `0.1`. A должен предоставить настоящий RunIdentity с ZIP hash и sealed `snapshotManifestHash`: production manifest содержит `{path,sourceHash,byteLength,mediaType}`, ручной — `{path,sizeBytes,sha256}` с другим hashing recipe. Число 29 не заменяет production hash. B/A предоставляют все версии/хеши parser/rules/advisory/config и `hmacKeyVersion`; `manual-source-review/1` не заполняет эти поля. C получает `bundleHash` из publish SQL (`bundle_document_hash`) для реального bundle, не из hash файла иллюстрации. Coverage должен прийти от B с `status`, `filesTotal`, per-file/rule `scopes`; 8 просмотренных вручную файлов не считаются scanner coverage. Полный CodeFacts coverage сохраняется отдельно в обязательном `graph_coverage`, входит в expected_counts и hash и перечитывается как SecurityMapDto.coverage; graph-only gaps не заменяются scanner summaries. Scope покрытия включает category/path/ruleId. Обязательные поля не заполняем ради прохождения схемы.

Оригинальные `provenance`, `scenarioOutlines`, `comparisonSourceRefs`, `unresolvedRelations` и layout не добавляются в `SecurityMapDto`; они остаются пояснениями. Ticket/catalog outline не порождает дополнительные code nodes без B facts. Layout отдельно: rekey шесть positions через N, убрать position/selection `n-finding`, unknown expansion перевести на Diagnostic IDs. Никаких changes workflow DTO.

**Итог проверки:** проецируются 6 code nodes / 5 code edges, 21 evidence, 6 FindingNodeLink и 10 traceEvidenceIds; unknown имеет 2 diagnostic refs вместо nullable edge. Это проверенная форма плана, **не** валидный опубликованный bundle: настоящих scope/signature/parser outcomes/policy approval здесь нет, семантика edge vocabulary ещё требует решения A/B/D.
