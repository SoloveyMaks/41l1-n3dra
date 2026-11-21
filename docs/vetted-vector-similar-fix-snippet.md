# Root snippet: extend `SimilarFix` with the full retrieval row

Owner-only patch for the shared contract C. This branch does not edit
`backend/app/contracts/schemas.py` or `docs/data-contracts/*`; the optional
vector runtime already returns the complete row through
`retrieve_examples_for_findings()` (`backend/app/ai/vector_result.py`).

Legacy `retrieve_for_findings()` passes `redactedBefore`, `ruleVersion` and
`status` as kwargs. Today `SimilarFix` ignores unknown kwargs, so the legacy DTO
silently drops those three fields. Apply this snippet in the contract owner's
branch if the legacy endpoint must expose them.

```python
# backend/app/contracts/schemas.py, class SimilarFix
redactedBefore: Optional[str] = None
ruleVersion: Optional[str] = None
status: str = "vetted"
```

```json
// docs/data-contracts/contracts.schema.json, $defs/SimilarFix.properties
"redactedBefore": {"type": ["string", "null"]},
"ruleVersion": {"type": ["string", "null"]},
"status": {"type": "string", "enum": ["draft", "vetted", "retired"]}
```

```ts
// docs/data-contracts/contracts.ts, type SimilarFix
redactedBefore?: string | null;
ruleVersion?: string | null;
status?: 'draft' | 'vetted' | 'retired';
```

After the change `retrieve_for_findings()` stores all fields without a second
query. If the shared contract must not grow, call
`retrieve_examples_for_findings()` from the new node/endpoint and leave
`SimilarFix` untouched.

`status="vetted"` here is a DTO default only. Demo/fixture rows always carry
`status="draft"` and a `demo/fixture:` source ref; nothing in the demo path may
be published as vetted.
