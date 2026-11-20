#!/usr/bin/env bash
# Stop and remove only nedra containers. Named volumes and the database stay intact.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

require_cmd podman
for name in "$NEDRA_APP_CONTAINER" "$NEDRA_DB_CONTAINER"; do
  if container_exists "$name"; then
    podman stop "$name" >/dev/null 2>&1 || true
    podman rm "$name" >/dev/null
    say "удалён контейнер $name"
  else
    say "контейнер $name отсутствует"
  fi
done
say "volumes ${NEDRA_DB_VOLUME} и ${NEDRA_APP_VOLUME} сохранены"
say "чужие контейнеры, сеть $NEDRA_NETWORK и runtime.env не тронуты"