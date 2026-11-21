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