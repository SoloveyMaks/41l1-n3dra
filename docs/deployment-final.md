# Deployment final c7057ee

Attempted production deploy on loopback `127.0.0.1:18080` and public
`https://nedra.soloveyka.bond`. The exact candidate build passed, but the
production ledger stopped the recreate. The previous app was restored without
touching the database or volumes.

## Candidate

- Source: `c7057ee98283805cb90d831a52789d60aa92abcc` (merged into
  `app-map-integration`; worktree clean).
- Built image: `localhost/nedra-app:local` = `9511e7bda750...`,
  revision label `c7057ee`, asset `index-DA9FQkXU.js`.
- Expected public asset: `index-DA9FQkXU.js`.

## Preflight and rollback material

- Previous production: revision `9862e19`, image
  `9ad49c9d3749bb35b6da316eeba1cbe47c4074a36ff7bb7bfa2c8ea907c87cc2`,
  healthy; public asset was `index-BnBq8EbO.js`.
- Rollback image tag: `localhost/nedra-app:rollback-9862e19`.
- DB backup: `/tmp/nedra-db-pre-c7057ee.sql` (mode 600).
- Runtime env backup: `/tmp/nedra-runtime-pre-c7057ee.env` (mode 600).
- Rollback metadata: `/tmp/nedra-prod-rollback-c7057ee.json` (mode 600).
- Failure log/evidence: `/tmp/nedra-prod-migration-failure.log`,
  `/tmp/nedra-prod-ledger-006.txt` (mode 600).
- Existing `CG_*` keys preserved. Bootstrap `--write` added a persistent
  separate `CG_ADMIN_ACCESS_CODE`; runtime env mode 600, directory 700.
  Values were not printed.

## Result

The production app container was recreated once with the exact image. Startup
failed before serving:

```
MigrationError: Файл миграции 006_snapshot_origins.up.sql изменился после применения.
```

Production ledger already contains `006_snapshot_origins.up.sql` with the
previous checksum. The exact candidate ships the finalized 006 file, so the
migration runner correctly refused to start. No manual SQL, ledger edit,
checksum rewrite, DB reset, or volume removal was performed.

The app container was then restored from
`localhost/nedra-app:rollback-9862e19` with the pre-deploy runtime env:
healthy, `/api/health` 200, public HTTPS 200, public asset
`index-BnBq8EbO.js`. DB stayed at 5 ledger migrations; `nedra-db-data` and
`nedra-app-data` were not touched. The review app on `18082` was not changed.

## Blocker

Production cannot update to `c7057ee` until the 006 checksum/finalization
strategy is resolved without mutating the applied migration history — for
example a new forward migration from the backend owner. This is intentionally
not fixed by editing the production ledger or checksum.

## Limitations

- Exact public asset `index-DA9FQkXU.js` was not served; rollback served the
  previous asset.
- Public live regression was not run on the candidate because the app did not
  start. Production old build remains healthy.
## Successful deploy of 63ca8c1

The historical failed `c7057ee` attempt above is retained. The forward migration
was then accepted and the exact combined source
`63ca8c129181e76a93d0000928db5f98b800f7c1` was deployed.

### Preflight and image

- Production before deploy: `9862e19`, healthy; rollback tag
  `localhost/nedra-app:rollback-9862e19`.
- DB backup: `/tmp/nedra-db-pre-63ca8c1.sql` mode 600.
- Runtime env backup: `/tmp/nedra-runtime-pre-63ca8c1.env` mode 600.
- Predeploy metadata: `/tmp/nedra-prod-predeploy-63ca8c1.json` mode 600,
  including the existing 006 checksum/applied_at and counts.
- Built image: `localhost/nedra-app:local` =
  `d9507494f8c771a5dd1f7e0c811982b34cb8ddb81febf36df79de47891b4ddd1`,
  revision `63ca8c1`; asset `index-DA9FQkXU.js`.

### Migration result

`007_snapshot_origin_guards.up.sql` was applied by normal backend startup; no
manual SQL or ledger edit. Ledger has 6 rows. The historical 006 row is
byte-for-byte unchanged:

- 006 checksum `4956db3112a2a61e85fec353c25236859191f9f64dcb91319b45279b85e87d19`
- 006 applied_at `2026-11-21 02:51:59.450838+00`
- 007 applied_at `2026-11-21 05:02:02.624414+00`

Production counts moved from artifacts/runs/findings/origins
`13/177/2201/5` to `15/183/2279/7`. One sample artifact and one smoke run
(`0f9613c4-a83f-423b-90b2-fc05d9f02778`) account for one artifact/run; the
remaining deltas are scheduled runs/artifacts on the existing workspace. No
deletions or volume changes.

### Public checks

- `https://nedra.soloveyka.bond/api/health` 200; HTML `cache-control:
  no-cache, must-revalidate`; asset `index-DA9FQkXU.js` immutable; missing
  asset 404.
- User login role `user`, admin overview 403; admin login role `admin`,
  overview 200.
- Smoke run: 13 findings, 4 redaction markers; map 10 nodes/9 edges/26
  evidence; SSE saw 11 events with the correct run ID.
- `official.json` 200; `readable.md` 200 with no `100%` and
  “Полнота проверки не установлена” + translated skipped scopes.
- Existing published comparison `c8b41afe`/`52efd83f`: 200,
  `comparable=false`, SQL `pattern_disappeared`; no new comparison fixtures
  fabricated.
- Review `18082` remained `b6abde4` and untouched. Production `18080` now
  serves `63ca8c1` asset `index-DA9FQkXU.js`.

### Limitations

No full UI audit was repeated. Smoke intentionally added one sample/run and
scheduled maintenance added other runs; no destructive recovery was needed.
