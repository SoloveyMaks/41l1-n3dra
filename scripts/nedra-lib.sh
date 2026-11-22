#!/usr/bin/env bash
# Shared helpers for the local nedra Podman deployment.
set -euo pipefail
umask 077

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

owns_container() {
  [ "$(podman inspect -f '{{index .Config.Labels "io.nedra.owner"}}' "$1" 2>/dev/null || true)" = "nedra" ]
}

require_owned_container() {
  container_exists "$1" || return 0
  owns_container "$1" || die "контейнер $1 существует без label io.nedra.owner=nedra; не трогаю"
}

app_health_ok() {
  curl -fsS --max-time 2 -o /dev/null "http://${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}/api/health" 2>/dev/null
}

# Builds NEDRA_TELEGRAM_APP_ARGS for `podman run` from the optional Telegram
# socket settings. Only the socket's own directory is mounted (never the whole
# runtime dir, never the socket inode); no other host path and no published port
# is added. Mounting the directory keeps working after the relay restarts and
# replaces the socket inode.
telegram_app_mount_args() {
  NEDRA_TELEGRAM_APP_ARGS=()
  [ -n "${TELEGRAM_PROXY_SOCKET:-}" ] || return 0
  case "$TELEGRAM_PROXY_SOCKET" in
    /*) ;;
    *) die "TELEGRAM_PROXY_SOCKET должен быть абсолютным путём внутри контейнера" ;;
  esac
  local socket_host="${TELEGRAM_PROXY_SOCKET_HOST:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/nedra/telegram-proxy/proxy.sock}"
  case "$socket_host" in
    /*) ;;
    *) die "TELEGRAM_PROXY_SOCKET_HOST должен быть абсолютным путём на хосте" ;;
  esac
  local host_dir container_dir
  host_dir=$(dirname -- "$socket_host")
  container_dir=$(dirname -- "$TELEGRAM_PROXY_SOCKET")
  [ "$container_dir" != "/" ] || die "TELEGRAM_PROXY_SOCKET не может указывать в корень контейнера"
  [ "$(basename -- "$socket_host")" = "$(basename -- "$TELEGRAM_PROXY_SOCKET")" ] \
    || die "имена host- и container-сокета должны совпадать"
  # A missing source would make podman create a directory named like the
  # socket, so the relay must already be up.
  local waited=0
  while [ ! -S "$socket_host" ]; do
    if [ "$waited" -ge 20 ]; then
      die "нет UNIX-сокета $socket_host; запустите nedra-telegram-proxy.service"
    fi
    sleep 0.5
    waited=$((waited + 1))
  done
  NEDRA_TELEGRAM_APP_ARGS+=(--volume "${host_dir}:${container_dir}:ro")
  # The socket is 0660 owner:owner; rootless containers keep the host user's
  # groups only with keep-groups.
  if [ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || printf false)" = "true" ]; then
    NEDRA_TELEGRAM_APP_ARGS+=(--group-add keep-groups)
  fi
  say "Telegram proxy socket dir: ${host_dir} -> ${container_dir} (${TELEGRAM_PROXY_SOCKET})"
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
  require_cmd python3
  local key value name pairs_tmp
  # No source/eval: the Python parser emits validated KEY NUL VALUE NUL pairs.
  pairs_tmp=$(mktemp "${TMPDIR:-/tmp}/nedra-pairs.XXXXXX")
  chmod 600 "$pairs_tmp"
  if ! python3 "$NEDRA_REPO_ROOT/scripts/nedra_env.py" --source "$NEDRA_RUNTIME_ENV" --canonical --pairs >"$pairs_tmp"; then
    rm -- "$pairs_tmp" 2>/dev/null || true
    die "runtime.env не разобран"
  fi
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    if ! [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      rm -- "$pairs_tmp" 2>/dev/null || true
      die "runtime.env: недопустимое имя переменной"
    fi
    export "$key=$value"
  done <"$pairs_tmp"
  rm -- "$pairs_tmp"

  for name in DATABASE_URL POSTGRES_USER POSTGRES_DB POSTGRES_PASSWORD \
              CG_SESSION_SECRET CG_ACCESS_CODE; do
    [ -n "${!name:-}" ] || die "runtime.env: отсутствует $name"
  done
  local ai_key_name=""
  for name in OPENCODE_API_KEY AI_API_KEY REFORMBOSS_API_KEY; do
    if [ -n "${!name:-}" ]; then
      ai_key_name=$name
      break
    fi
  done
  [ -n "$ai_key_name" ] || die "runtime.env: отсутствует AI key (OPENCODE_API_KEY, AI_API_KEY или REFORMBOSS_API_KEY)"
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
    if app_health_ok; then
      return 0
    fi
    if ! container_running "$NEDRA_APP_CONTAINER"; then
      podman logs --tail 50 "$NEDRA_APP_CONTAINER" >&2 || true
      die "контейнер backend завершился до готовности"
    fi
    if (( $(date +%s) - start >= timeout )); then
      podman logs --tail 50 "$NEDRA_APP_CONTAINER" >&2 || true
      die "backend не ответил на /api/health за ${timeout}s"
    fi
    sleep 2
  done
}
