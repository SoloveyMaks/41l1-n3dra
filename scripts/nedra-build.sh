#!/usr/bin/env bash
# Build the application image from the repository root.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

require_cmd podman
require_cmd git
require_cmd tar
git_revision=$(git -C "$NEDRA_REPO_ROOT" rev-parse HEAD 2>/dev/null) \
  || die "не удалось определить точный revision"
if ! git -C "$NEDRA_REPO_ROOT" diff --quiet || \
   ! git -C "$NEDRA_REPO_ROOT" diff --cached --quiet; then
  die "рабочее дерево изменено; сборка должна идти из чистого коммита"
fi
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/nedra-build.XXXXXX")
trap 'rm -rf -- "$build_dir"' EXIT
git -C "$NEDRA_REPO_ROOT" archive "$git_revision" | tar -x -C "$build_dir"

say "сборка $NEDRA_IMAGE из коммита $git_revision"
podman build \
  --format docker \
  --pull=missing \
  --file "$build_dir/deploy/Dockerfile" \
  --build-arg "NEDRA_REVISION=$git_revision" \
  --tag "$NEDRA_IMAGE" \
  --label "org.opencontainers.image.revision=$git_revision" \
  "$build_dir"

image_id=$(podman image inspect -f '{{.Id}}' "$NEDRA_IMAGE")
say "готово: $NEDRA_IMAGE ($image_id)"
