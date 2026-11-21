#!/usr/bin/env bash
# Build the application image from the repository root.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

require_cmd podman
git_revision=$(git -C "$NEDRA_REPO_ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)

say "сборка $NEDRA_IMAGE из $NEDRA_REPO_ROOT"
podman build \
  --format docker \
  --pull=missing \
  --file "$NEDRA_REPO_ROOT/deploy/Dockerfile" \
  --tag "$NEDRA_IMAGE" \
  --label "org.opencontainers.image.revision=$git_revision" \
  "$NEDRA_REPO_ROOT"

image_id=$(podman image inspect -f '{{.Id}}' "$NEDRA_IMAGE")
say "готово: $NEDRA_IMAGE ($image_id)"