# Trusted advisory snapshot

`trusted-advisories.json` is the server-side advisory input for SCA. It is never
read from an uploaded project.

- Source: `https://github.com/TBagirov/training`,
  `vulnerability-database/vulnerabilities.json`
- Pinned commit: `fe1fcbe212669783480666b9b60147c8a8c6eef4`
- Git blob: `c34253459d1ae3f621d997534a513ab59e37f683`
- Organizer snapshot date: `2026-11-16`
- Upstream: GitHub Advisory Database (CC BY 4.0) via OSV
- Scope: bounded Maven/Gradle subset plus the preserved internal backend
  catalog. It is not a complete advisory database and does not cover every
  ecosystem or every published advisory.

Each record keeps its provenance. The file is copied into the runtime image by
`COPY analyzers ./analyzers`; `backend/app/analyzers/advisories.json` is the
legacy projection used by the backend SCA analyzer and the AI fixed-version
validator. Both files must stay byte-equivalent in meaning when the snapshot is
refreshed.

`analyzers/fixtures/vulnerabilities.json` remains a minimal self-test fixture.
It is not a production catalog.