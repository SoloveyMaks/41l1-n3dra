# SQL snake_case ↔ DTO camelCase

Маппинг явный; generic recursive renamer не годится для joined/nested полей. SQL members имеют `(workspace_id,run_id,snapshot_id)`; DTO каждый fact/ref сохраняет `(workspaceId,runId,snapshotId)`. `id` всегда соответствует typed ID таблицы, никогда случайному global UUID lookup.

| DTO | SQL |
|---|---|
| RunIdentity.workspaceId/runId/snapshotId/artifactHash/workflowRevisionId | runs.workspace_id/run_id/snapshot_id/artifact_hash/workflow_revision_id |
| RunIdentity.snapshotManifestHash | snapshots.manifest_hash (sealed snapshot) |
| AnalysisSignature.parserVersion/parserHash | runs.parser_version/parser_hash |
| rulesVersion/rulesHash/advisoryVersion/advisoryHash | runs.rules_version/rules_hash/advisory_version/advisory_hash |
| configVersion/scanConfigHash/hmacKeyVersion | runs.config_version/scan_config_hash/hmac_key_version |
| domainPolicyRevisionId/domainPolicyVersion/domainPolicyHash | runs.domain_policy_revision_id/domain_policy_version/domain_policy_hash; все null или все заполнены |
| Finding.id/fingerprint/fingerprintVersion | findings.finding_id/fingerprint/fingerprint_version |
| category/ruleId/ruleVersion/ruleFamily | findings.category/rule_id/rule_version/rule_family |
| severity/severityBasis/confidence/confidenceBasis/status | findings.severity/severity_basis/confidence/confidence_basis/status |
| primaryEvidenceId/primaryLocation | findings.primary_evidence_id → evidence с тем же workspace/run/snapshot; Location копируется без изменений |
| relatedEvidenceIds/traceEvidenceIds | finding_evidence.evidence_id, role related/trace, сортировка ordinal |
| graphNodeIds | finding_nodes.node_id |
| anchor.normalizedPath | primary evidence.path; дублируется в DTO для fingerprint |
| anchor.symbolKey/normalizedContextHash/role/secretHmac | findings.symbol_key/normalized_context_hash/anchor_role/secret_hmac |
| advisoryIds/coverageWarnings | findings.advisory_ids/coverage_warnings (без AI-added advisory) |
| EvidenceRef.id/location/sourceHash/kind/redactedSnippet/producerVersion | evidence.evidence_id + path/line/column_number/end_line/end_column/byte_start/byte_end + source_hash/kind/redacted_snippet/producer_version |
| CodeNode.id/kind/symbolKey/label/producerVersion/evidenceRefs | code_nodes.node_id/kind/symbol_key/label/producer_version + node_evidence.evidence_id |
| CodeEdge.id/fromId/toId/kind/resolution/resolutionReason/producerVersion | code_edges.edge_id/from_id/to_id/kind/resolution/resolution_reason/producer_version |
| CodeEdge.basis/policyRef/evidenceRefs | code_edges.basis/policy_revision_id + edge_evidence.evidence_id |
| ScanResult.category/status/producerVersion/durationMs | scan_outcomes.category/status/producer_version/duration_ms |
| ScanResult.coverage.scopes[].path/ruleId/category/status/reason | coverage.path/rule_id/category/status/reason; PK включает category, одинаковый path/ruleId разных scanner families не конфликтует |
| Остальные Coverage поля | scan_outcomes.coverage_summary: status/filesTotal/filesVisited/rulesApplied/skippedScopes/parserErrors/unresolvedSymbols/limitsReached (camelCase JSONB, отдельно от scopes); только scanner summary; CodeFacts/Map coverage хранится отдельно и не заменяется scanner aggregation |
| CodeFactsBundle.coverage / SecurityMapDto.coverage | graph_coverage.coverage — полный исходный Coverage JSON, включая graph-only skippedScopes/unresolvedSymbols/scopes; один scoped row на bundle, участвует в hash и freeze; перечитывается без замены scanner summary |
| Diagnostic.id/category/code/message/evidenceId | diagnostics.diagnostic_id/category/code/message/evidence_id |
| DomainPolicyInput.id/version/hash/sourceRef/approvedBy/approvedAt | domain_policy_revisions.policy_revision_id/version/policy_hash/source_ref/approved_by/approved_at |
| DomainPolicyInput.approvalStatus/resources | approvalStatus derived from approved_at; `resources` из `policy.resources` JSONB: `kind=role_access` → `resource,allowedRoles`; `kind=ownership` → `resource,ownerField,trustedIdentity,roleExceptions`; SQL shape check запрещает смешение, массив непустой (явный user input) |
| CodeFactsBundle/SecurityMapDto.identity/signature | runs + sealed snapshots; это не workflow canvas |
| SecurityMapDto.bundleHash | analysis_bundles.bundle_hash, published only |
| SecurityMapDto.findingRefs/findingNodeLinks | findings.finding_id + finding_nodes; тот же published bundle |
| SecurityMapDto.policyInputs/nodes/edges/evidence/coverage/diagnostics | domain_policy_revisions по run policy ID + scoped members; без AI prose |
| ComparisonDto.id/baselineRunId/currentRunId/baselineSnapshotId/currentSnapshotId/comparable/reasons | comparisons.comparison_id/baseline_run_id/current_run_id/baseline_snapshot_id/current_snapshot_id/comparable/reasons |
| ComparisonDto.items[].id/baselineFindingId/currentFindingId/status/matchingBasis/reason | comparison_items.item_id/baseline_finding_id/current_finding_id/status/matching_basis/reason |
| ComparisonDto.deletedFiles | anti-join baseline/current snapshot_files по path; baselineSourceHash из baseline manifest; отдельно от исправлений |
| AiExplanation.id/findingId/modelVersion/promptVersion/validatorVersion/validationStatus/redactedExplanation | ai_explanations.explanation_id/finding_id/model_version/prompt_version/validator_version/validation_status/redacted_explanation |
| EmbeddingSpec.id/modelVersion/queryPreparationVersion/documentPreparationVersion/redactionVersion | embedding_specs.embedding_spec_id/model_version/query_preparation_version/document_preparation_version/redaction_version; прочие имена camelCase→snake_case |
| SimilarFix.exampleId/sourceRef/verificationBasis/limitations/redactedFix | remediation_examples.example_id/source_ref/verification_basis/limitations/redacted_fix |
| SimilarFix.embeddingSpec/distance/metric | exact_similar_fixes result + exact active embedding_specs; similarity score не переименовывает confidence |

`AnalysisBundle` — вход persistence: 3 ScanResult + CodeFactsBundle с одинаковыми identity/signature. Shared evidence refs повторяться между envelopes могут только с идентичными полями; нормализация сохраняет одну row. `ScanResult.findings` категории принадлежат своему scanner outcome. Evidence IDs могут быть shared между scanners, но категория diagnostic/scopes остаётся явной. `schemaVersion` = literal `0.1`, не версия analyzer.

Остальные SQL workflow/delivery/schedule поля — область executor A, DTO этого пакета их не подменяет. Workflow JSON schema и submission export по формату организатора согласуются отдельно; внутренний Finding не является submission.json.
