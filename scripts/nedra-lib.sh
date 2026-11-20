#!/usr/bin/env bash
# Shared helpers for the local nedra Podman deployment.
set -euo pipefail

NEDRA_REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../deploy/defaults.env
. "$NEDRA_REPO_ROOT/deploy/defaults.env"

say() { printf '[nedra] %s\n' "$*"; }
warn() { printf '[nedra] WARN: %s\n' "$*" >&2; }
die() { printf '[nedra] ERROR: %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "нет команды: $1"
}

container_running() {
  [ "$(podman inspect -f '{{.State.Running}}' "$1" 2>/dev/null || printf false)" = "true" ]
}

container_exists() {
  podman container exists "$1"
}

runtime_env_mode() {
  stat -c '%a' "$NEDRA_RUNTIME_ENV" 2>/dev/null || printf missing
}

require_runtime_env() {
  [ -f "$NEDRA_RUNTIME_ENV" ] || die "нет $NEDRA_RUNTIME_ENV; сначала выполните scripts/nedra-bootstrap.sh --write"
  [ "$(runtime_env_mode)" = "600" ] || die "$NEDRA_RUNTIME_ENV должен иметь режим 600"
}

load_runtime_env() {
  require_runtime_env
  set -a
  # shellcheck disable=SC1090
  . "$NEDRA_RUNTIME_ENV"
  set +a
  [ -n "${DATABASE_URL:-}" ] || die "в runtime.env нет DATABASE_URL"
}

wait_for_db_health() {
  local timeout=${1:-90} start
  start=$(date +%s)
  while :; do
    local status
    status=$(podman inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NEDRA_DB_CONTAINER" 2>/dev/null || printf missing)
    [ "$status" = "healthy" ] && return 0
    if ! container_running "$NEDRA_DB_CONTAINER"; then
      podman logs --tail 30 "$NEDRA_DB_CONTAINER" >&2 || true
      die "контейнер БД завершился (status=${status})"
    fi
    if (( $(date +%s) - start >= timeout )); then
      podman logs --tail 30 "$NEDRA_DB_CONTAINER" >&2 || true
      die "БД не стала healthy за ${timeout}s (status=${status})"
    fi
    sleep 2
  done
}

wait_for_app_health() {
  local timeout=${1:-90} start
  start=$(date +%s)
  while :; do
    if curl -fsS --max-time 2 -o /dev/null "http://${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}/api/runs"; then
      return 0
    fi
    if ! container_running "$NEDRA_APP_CONTAINER"; then
      podman logs --tail 50 "$NEDRA_APP_CONTAINER" >&2 || true
      die "контейнер backend завершился до готовности"
    fi
    if (( $(date +%s) - start >= timeout )); then
      podman logs --tail 50 "$NEDRA_APP_CONTAINER" >&2 || true
      die "backend не ответил на /api/runs за ${timeout}s"
    fi
    sleep 2
  done
}