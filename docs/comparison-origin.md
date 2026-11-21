# Comparison origin invariant

`packages/comparison/index.mjs` принимает только published envelopes с
`provenance.origin`:

- `scanner` — настоящий scanner run;
- `contract_scenario` — синтетический DTO для проверки механики contracts;
- `manual_fixture` — ручная fixture.

## Правило

`comparable` вычисляется только после **всех** причин отказа. Любая причина,
включая synthetic origin, делает `comparable=false`; результат с непустыми
`reasons` не может остаться `comparable=true`. Это инвариант движка, а не
предупреждение UI.

`resolved` и `introduced` разрешены только при `comparable=true`. При
`comparable=false` findings остаются `inconclusive` (или `file_deleted`, когда
два полных manifest достоверно подтверждают отсутствие пути); synthetic/manual
DTO не могут выдать scanner-like `resolved`/`introduced`.

## Матрица

| baseline / current | expectedScopesVerified | comparable | Итог |
|---|---|---|---|
| scanner / scanner | true / true | true | scanner comparison |
| scanner / scanner | false где-либо | false | unverified scopes — причина |
| contract_scenario / contract_scenario | true / true | false | synthetic origin — причина |
| contract_scenario / contract_scenario | false где-либо | false | synthetic + scopes |
| manual_fixture / manual_fixture | любое | false | manual fixture |
| разные origins | любое | false | не два scanner run |
| неизвестный origin | — | исключение входа | origin не поддерживается |

`expectedScopesVerified`, normalization/version/signature и coverage guards не
ослаблены. `unknown` domain policy остаётся запрещающей причиной. Статусы DTO не
переименовываются. `origin` — доверенный provenance из published envelope;
self-asserted JSON не доказательство scanner run.

## Почему

`comparable` — не «нет предупреждений в UI», а разрешение на доказательные
`persistent/changed/resolved/introduced`. Synthetic DTO полезны для проверки
механики, но не являются сканированием проекта и не доказывают исправление.
Manual fixture тем более не является A/B scanner run.
