# Root snippet: mirror the full retrieval row in the JSON/TS contract

The Python DTO `SimilarFix` in `backend/app/contracts/schemas.py` already carries
`redactedBefore`, `ruleVersion` and a required `status: Literal["draft",
"vetted"]`. This branch does not edit `docs/data-contracts/*`; the JSON schema
and TS type below are the owner-side mirror. Keep the status required.

```json
// docs/data-contracts/contracts.schema.json, $defs/SimilarFix.properties
"redactedBefore": {"type": "string", "minLength": 1},
"ruleVersion": {"type": "string", "minLength": 1},
"status": {"type": "string", "enum": ["draft", "vetted"]}
```

Add all three names to `$defs/SimilarFix.required` and to the
`SimilarFixesResult` item contract. Do not use `"status": {"type": "string"}`
alone: an unknown value must fail validation, not pass through.

```ts
// docs/data-contracts/contracts.ts, type SimilarFix
redactedBefore: string;
ruleVersion: string;
status: 'draft' | 'vetted';
```

No default is safe. A field default of `status: "vetted"` would relabel a legacy
or demo row that never had a status, so the contract must require the value.
Rows from `exact_demo_fixes` always carry `status="draft"` and a
`demo/fixture:` source ref; nothing in the demo path may be published as vetted.
`retrieve_for_findings()` already passes the full row, so no second query is
needed once the contract files match the Python DTO.
