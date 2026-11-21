# Independent review: fullest supported analysis

Review started 2026-11-21 08:50 UTC. Baseline: `db45c68470c3c55847fb4796e98897f40c41fbb4`. Candidate SHA and final verdict pending owner handoff.

Scope: future default and legacy engine selections, union of supported analyzers, trusted advisory input, immutable run/snapshot/workspace identity, report/map evidence, honest degradation. Only this review document is changed by the reviewer. No production services or downloaded Kotlin/Gradle code were executed.

## Independent baseline evidence

A direct `TeamBAdapter._build` run used a private temporary CodeFactsStore and a copy of the exact organizer training checkout `fe1fcbe212669783480666b9b60147c8a8c6eef4`. The organizer catalog was supplied explicitly as trusted server configuration. The ground truth was read only after scanning, for evaluation; it was never analyzer input.

Every ID `A-SEC-01` through `A-SEC-05`, `A-SCA-01` through `A-SCA-05`, and `A-SAST-01` through `A-SAST-05` matched exactly once by category, path and class/overlapping lines (SECRET/SAST), or Maven coordinate, resolved version and advisory ID (SCA). All six safe control IDs matched zero times. Total 15, unexpected findings zero. This establishes the narrow training baseline, not exhaustive security coverage.

The map contained 43 nodes, 32 edges and 67 evidence objects. All primary/related/trace evidence and finding graph-node references resolved; all four bundle parts shared the bundle identity. Advisory version: `osv-maven/2026-11-16`.

One independent synthetic source contained a direct request parameter passed to `Runtime.getRuntime().exec` and a synthetic PEM header. Backend found `KOTLIN-COMMAND-INJECTION` and `SECRET-PRIVATE-KEY`; original Team B found neither. A blind engine switch therefore loses existing supported patterns.

## Concrete baseline defect relayed to owner

`backend/app/integrations/team_b.py`, `_build`: after `ensure_artifact_meta` freezes the source manifest, the adapter constructs `Project(extracted_path)` without checking those bytes against the frozen snapshot. Appending a harmless comment to `PartsImportSettings.kt` after the first build was accepted by a second build with the same old `snapshotManifestHash`. This violates the source-evidence identity guarantee and must be checked on the final candidate.

Reproduction scripts are ephemeral local reviewer artifacts: `/tmp/sol-focused-review.py` and `/tmp/sol-backend-only-review.py`. They print only case IDs, counts, paths, rule IDs and hashes/identity checks; no credential values. Baseline proof directory: `/tmp/sol-focused-pqavf2n7`.

## Pending candidate checks

Owner must supply an exact clean commit and executor/isolated PostgreSQL evidence. Candidate review will check legacy/default engine convergence, training case mapping and deduplication, backend-only findings with map evidence, trusted packaged catalog isolation, missing-component diagnostics/partial state, and rejection of changed frozen source. Historical stored results must remain untouched.

## Candidate worktree checks, 08:58–09:00 UTC

**Verdict: not approved for integration at review cutoff.** No exact candidate commit SHA or owner PostgreSQL/default-workflow proof had arrived by 08:59 UTC. Candidate HEAD was still baseline `db45c68470c3c55847fb4796e98897f40c41fbb4` with uncommitted changes. Results below describe that provisional worktree, not an immutable reviewed implementation.

Real synchronous backend and Team B analyzers followed by the candidate's `merge_full_results` returned SECRET=5, SCA=5, SAST=5. All 15 reference cases were present once (including `SECRET-BEARER-TOKEN` as the equivalent rule for `A-SEC-03`); all six safe controls remained clean. The union retained both backend-only synthetic findings: command injection and PEM private-key header.

With an ephemeral `CG_ANCHOR_HMAC_KEY`, canonical map construction produced 15 findings, 33 nodes, 32 edges and 58 evidence objects, with zero dangling finding evidence references. The difference from Team B's original 43/32/67 map is real: the candidate uses the backend canonical map builder. UI source-link navigation and published PostgreSQL envelope were not established by this direct construction proof.

A fake empty `vulnerability-database/vulnerabilities.json` placed inside the uploaded project did not replace the server catalog: SCA still reported the five expected cases. A missing configured server catalog fell back to the packaged catalog with `team_b.config-fallback` warning. With the Team B HMAC key absent, its failure became an error diagnostic (`team_b.unavailable`), while all three backend SECRET findings survived merging. These checks establish degradation at the analyzer/merge boundary; they do not substitute for an executor-level final partial/report check.

### Findings requiring owner/root action

1. **Analysis provenance is inaccurate for the union.** `backend/app/codefacts/canonical.py:714` still builds its signature from codefacts extractor/source/canonical files, `RULE_DEFINITIONS`, and `backend/app/analyzers/advisories.json`. The new default executes `full_analysis.py`, backend scanner rules, Team B parser/rules and a separately selected trusted catalog, but those inputs are not represented in the published signature. A Team B rule or configured catalog change can therefore leave the relevant canonical rule/advisory signature unchanged. The independently constructed merged map reported `rulesVersion=cf-rules/1`, `advisoryVersion=cg-advisories/1`, and no Team B policy revision. Include actual union/parser/rules/config/catalog provenance before relying on comparison/reproducibility claims.
2. **Frozen input integrity requires final verification.** The baseline mutation proof is concrete; provisional `TeamBAdapter._build` still read the mutable extraction directory directly without a frozen-source check. The unified executor's later `_integrate_codefacts` does check existing frozen text hashes, so this is not proof that a mismatched canonical bundle is published. However, backend and Team B scans can already consume changed input, and added files are outside that check's manifest iteration. Verify/reject changed or added input before either scanner, or scan an immutable validated copy. Root/owner received the reproduction before cutoff.
3. **Required acceptance evidence is missing.** No clean candidate SHA and no default/legacy `backend`/`team_b` executor run on isolated PostgreSQL were available for review. Static routing converges on `scan_category_full` and mixed legacy engines are accepted, but this is not a live execution proof. Do not treat the direct merge checks above as full acceptance.

### Limits

The asynchronous `scan_category_full` direct probe stalled in this tool environment; an eight-second stack showed idle executor workers and an event-loop selector wait. A minimal `asyncio.to_thread` probe also exhibited lifecycle stalling, so this was relayed as environment-specific and is not asserted to be a product defect. The synchronous real-scanner checks completed in under one second. No production services, PostgreSQL clusters, application ports or imported build scripts were touched; no runtime secrets were printed or committed.

Temporary independent proofs: `/tmp/sol-unified-sync-review.py`, `/tmp/sol-backend-only-union.py`, `/tmp/sol-unified-65q28wwf`. These paths are local evidence, not repository dependencies.

## Focused follow-up requested by root, 09:01–09:03 UTC

The owner worktree was still dirty at baseline HEAD, so these remain provisional source findings rather than a reviewed exact commit.

- **Default catalog will be missing from a normal commit unless explicitly included.** `backend/app/analyzers/advisory_catalog.py:18` points to `analyzers/data/trusted-advisories.json`. The file exists locally, but `git ls-files analyzers/data` returned no files and `git check-ignore -v analyzers/data/trusted-advisories.json` returned `.gitignore:17:data/`. The owner must force-add the pinned catalog plus provenance or move it into a tracked path, and verify a clean checkout. This was relayed immediately as a packaging blocker; a later owner commit may resolve it.
- **Line-only SECRET deduplication drops independent secret sites.** `backend/app/integrations/full_analysis.py:250` treats any two SECRET findings with overlapping line ranges in the same file as the same vulnerability. A direct live call to the real `merge_full_results` with two distinct fingerprints/rules at nonoverlapping columns/byte ranges on line 1 (`[0,19]` and `[29,49]`) returned one finding. The removed finding's primary site is not retained by `_merge_pair`. This is a merge-boundary reproduction with synthetic finding DTOs, not a claim that the current lexer emits both sites from every same-line source form. Dedup must compare the actual secret/anchor or precise overlapping site, and preserve distinct credentials.

The follow-up also confirmed the owner added `merge_extra_findings` and mappings for three CF rule families before invoking the canonical builder. Therefore a simple claim that all three codefacts findings necessarily duplicate Team B is not supported by the current diff. Canonical rule remapping is visible in the final map built earlier; published provenance and full executor acceptance still need the exact commit and live evidence.

## Exact signature follow-up verified

Reviewed implementation commit `cb766ca637787dbc402bd6aaabcc84492a016fa4` together with signature fix `22bbc5f964afa30a1f2d1428cb1d8c4d84b6b141` (including its parent `3d0d3aa`), merged normally into the isolated review branch at `8d77573b05a720dff5fcd6e9b539c8779fa3eb70`. The packaged catalog is now tracked under `analyzers/advisory-snapshot`, resolving the earlier ignored-file blocker.

The independent live `/tmp/sol-signature-focused.py` check copied analyzer inputs into a temporary directory and called the real canonical signature builder. Changing each input independently produced these results: Team B `secrets.py` changed `rulesHash`; composition `full_analysis.py` changed `rulesHash`; Team B `kotlin_lexer.py` changed `parserHash`; selected trusted catalog bytes changed `advisoryHash`. Selecting an identical catalog copy under a different path preserved the entire signature. No repository source was mutated for these probes.

Observed versions: `cf-rules/1+cg-full-analysis/1`, `cg-config/2`, and `cg-advisories/1+trusted-advisories/organizer-fe1fcbe21266+backend-catalog-v1`. This resolves the proven omission of Team B/composition/catalog from canonical provenance for the reviewed combination. A final merge verdict still awaits the separately assigned frozen-input and distinct-secret fixes and their exact SHA; this section does not approve the earlier unfixed candidate.

## Final focused review of c3c3adb: one blocker remains

Exact fixes reviewed: `c3c3adb31b8bff0fe5b1c8a7041383ea6d5e9e91` on implementation `cb766ca637787dbc402bd6aaabcc84492a016fa4`, combined with signature fix `22bbc5f964afa30a1f2d1428cb1d8c4d84b6b141`. Their isolated review merge is `7c50d65ad23d22c1a0ed574b4876ebddd9aa7bfb`; root's clean combined worktree was observed at `92f9035d2ca7727aeb3a871efb1c827566f6209c`.

**Verdict: do not release this combination yet; the added-file integrity case fails.** No broad baseline audit or training rerun was performed in this follow-up. Root supplied owner isolated-PG evidence for the default workflow: 15 reference findings plus one unproven owner warning, zero safe-control hits, and zero known secret-prefix leaks. Those executor numbers are owner evidence, not independently repeated here.

The independent real-code `/tmp/sol-final-focused.py` run established:

- Two distinct SECRET fingerprints at disjoint byte ranges on one source line now survive as two findings. The previously reproduced merge defect is fixed.
- An unchanged frozen file is accepted. Changing that existing file raises `SnapshotIntegrityError`; source order in `scan_category_full` calls verification before scheduling either analyzer. The existing-file mutation defect is fixed at that entrypoint.
- **Adding a new file after freezing is still accepted.** Freeze a project containing only `README.md`, then add `injected.properties` with a newly generated synthetic password. `verify_frozen_artifact` returns successfully; the real backend `SecretAnalyzer.analyze` then finds `SECRET-PASSWORD-LITERAL` in that added file. The check at `backend/app/integrations/full_analysis.py:111` only visits manifest members, while both analyzers recursively scan the directory. Thus bytes absent from the immutable manifest still become analyzer input under the old snapshot identity. Compare the actual file set against the frozen manifest (and reject added/escaping paths), or restrict both analyzers to a validated immutable input copy. Root received this proof immediately.

Proof output: `ORIGINAL_ACCEPTED=True`, `MUTATION_REJECTED=SnapshotIntegrityError`, `ADDED_FILE_ACCEPTED=True`, `SCANNER_READS_ADDED_FILE=[('injected.properties','SECRET-PASSWORD-LITERAL')]`, `DISTINCT_SECRET_COUNT=2`. Local proof directory: `/tmp/sol-final-frozen-2qj_q8a3`.

The focused signature mutation checks were repeated on this exact merged combination and passed for Team B rules, composition, parser and selected catalog; an identical catalog at a different path again preserved the signature. Proof directory: `/tmp/sol-signature-jsol2w8v`. The catalog/signature issues are resolved for this combination; the remaining acceptance condition is rejection/exclusion of added frozen-input files, followed by verification of its exact fix SHA.

## a461dca follow-up: root-level extra fixed, excluded-directory extra remains

Read-only verification used root's clean combined HEAD `3b95bdbfdeb15048c375809722e68539dfa2d7ae`, containing exact file-set fix `a461dcaa1a3a1449dc27230aacfda35843dc7d38` and shared signature resolver `e87c0fcebba2eec1c3e693d80b6f6bc00501d4dd`. Only this review document was edited.

The requested `/tmp/sol-final-focused.py` repetition passed: original accepted, existing mutation rejected, root-level `injected.properties` rejected, and distinct SECRET count two. Proof directory `/tmp/sol-final-frozen-499uw5lr`. The file-set commit changes only `backend/app/integrations/full_analysis.py`, preserving security-map construction. Signature input sensitivity and path independence were repeated successfully on root's combined HEAD (`/tmp/sol-signature-yllrdqhp`).

**Verdict remains blocked by the same added-file identity defect in an intake-excluded directory.** The new `_reject_added_scan_files` prunes every `INTAKE_SKIP_DIRS` directory (`full_analysis.py:124`), including `build`, before inspecting extra files. However, backend `SecretAnalyzer` and `SastAnalyzer` recurse with `os.walk(root_path)` without pruning those directories (`secret_analyzer.py:96`, `sast_analyzer.py:52`). Therefore the verifier and actual backend scanner have different input sets.

The identical focused proof, changing only the added file path to `build/injected.properties`, returned `ADDED_FILE_ACCEPTED=True` and `SCANNER_READS_ADDED_FILE=[('build/injected.properties','SECRET-PASSWORD-LITERAL')]`. The frozen project initially contained only README; the password was randomly generated and never printed. Script `/tmp/sol-final-excluded-focused.py`, proof directory `/tmp/sol-final-frozen-7b_c2_g4`. This is a direct continuation of the requested extra-file case, not a new broad audit.

To close it, the guard must reject added files actually read by either backend scanner even under intake-excluded directories, or both backend scanners must enforce the same exclusions before reading. Root received the exact proof immediately. Owner assertions that build-generated additions are intentionally ignored are insufficient while an actual supported scanner demonstrably consumes them under the old frozen identity.
