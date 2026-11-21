#!/usr/bin/env bash
# Stop (not remove) owned nedra containers. Safe for systemd ExecStop.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

TARGET=both
for arg in "$@"; do
  case "$arg" in
    --db) TARGET=db ;;
    --app) TARGET=app ;;
    -h|--help) printf 'Usage: scripts/nedra-stop.sh [--db | --app]\n'; exit 0 ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

require_cmd podman
stop_one() {
  local name=$1
  if ! container_exists "$name"; then
    say "контейнер $name отсутствует"
    return 0
  fi
  require_owned_container "$name"
  if container_running "$name"; then
    podman stop "$name" >/dev/null
    say "остановлен $name"
  else
    say "$name уже остановлен"
  fi
}
case "$TARGET" in
  db) stop_one "$NEDRA_DB_CONTAINER" ;;
  app) stop_one "$NEDRA_APP_CONTAINER" ;;
  both) stop_one "$NEDRA_APP_CONTAINER"; stop_one "$NEDRA_DB_CONTAINER" ;;
esac