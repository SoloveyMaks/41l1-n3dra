#!/usr/bin/env bash
# Start local nedra PostgreSQL and backend. Idempotent; never touches other containers.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

BUILD=0
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    -h|--help) printf 'Usage: scripts/nedra-up.sh [--build]\n'; exit 0 ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

require_cmd podman
require_cmd curl
load_runtime_env

if podman network exists "$NEDRA_NETWORK"; then
  say "сеть $NEDRA_NETWORK уже есть"
else
  podman network create "$NEDRA_NETWORK" >/dev/null
  say "создана сеть $NEDRA_NETWORK"
fi

for volume in "$NEDRA_DB_VOLUME" "$NEDRA_APP_VOLUME"; do
  if podman volume exists "$volume" 2>/dev/null; then
    say "volume $volume уже есть"
  else
    podman volume create "$volume" >/dev/null
    say "создан volume $volume"
  fi
done

if ! podman image exists "$NEDRA_DB_IMAGE"; then
  say "скачивание закреплённого pgvector image"
  podman pull "$NEDRA_DB_IMAGE"
fi

if container_running "$NEDRA_DB_CONTAINER"; then
  say "БД уже запущена"
elif container_exists "$NEDRA_DB_CONTAINER"; then
  podman start "$NEDRA_DB_CONTAINER" >/dev/null
  say "БД перезапущена"
else
  # Values are passed by name only, so the password never appears in argv.
  podman run -d \
    --name "$NEDRA_DB_CONTAINER" \
    --hostname "$NEDRA_DB_ALIAS" \
    --network "$NEDRA_NETWORK" \
    --network-alias "$NEDRA_DB_ALIAS" \
    --restart=unless-stopped \
    --env POSTGRES_USER \
    --env POSTGRES_DB \
    --env POSTGRES_PASSWORD \
    --volume "${NEDRA_DB_VOLUME}:/var/lib/postgresql/data:Z" \
    --volume "${NEDRA_REPO_ROOT}/deploy/db/init:/docker-entrypoint-initdb.d:ro,Z" \
    --shm-size=256m \
    --security-opt no-new-privileges \
    --health-cmd "pg_isready -U ${NEDRA_DB_USER} -d ${NEDRA_DB_NAME}" \
    --health-interval 5s \
    --health-timeout 3s \
    --health-retries 20 \
    "$NEDRA_DB_IMAGE" >/dev/null
  say "БД запущена (порт не публикуется)"
fi
wait_for_db_health 120

if [ "$BUILD" = "1" ] || ! podman image exists "$NEDRA_IMAGE"; then
  "$NEDRA_REPO_ROOT/scripts/nedra-build.sh"
fi

desired_image=$(podman image inspect -f '{{.Id}}' "$NEDRA_IMAGE")
if container_exists "$NEDRA_APP_CONTAINER" \
   && [ "$(podman inspect -f '{{.Image}}' "$NEDRA_APP_CONTAINER" 2>/dev/null || true)" != "$desired_image" ]; then
  container_running "$NEDRA_APP_CONTAINER" && die "образ обновился, контейнер запущен; выполните scripts/nedra-down.sh"
  podman rm "$NEDRA_APP_CONTAINER" >/dev/null
  say "удалён устаревший контейнер backend"
fi

if container_running "$NEDRA_APP_CONTAINER"; then
  say "backend уже запущен"
elif container_exists "$NEDRA_APP_CONTAINER"; then
  podman start "$NEDRA_APP_CONTAINER" >/dev/null
  say "backend перезапущен"
else
  podman run -d \
    --name "$NEDRA_APP_CONTAINER" \
    --hostname nedra-app \
    --network "$NEDRA_NETWORK" \
    --network-alias nedra-app \
    --restart=unless-stopped \
    --publish "${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}:18080" \
    --env-file "$NEDRA_RUNTIME_ENV" \
    --env NEDRA_ENV=local \
    --volume "${NEDRA_APP_VOLUME}:/app/data:Z" \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop=ALL \
    --security-opt no-new-privileges \
    --pids-limit 512 \
    "$NEDRA_IMAGE" >/dev/null
  say "backend запущен на ${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT} (только loopback)"
fi
wait_for_app_health 120

say "готово; статус: scripts/nedra-status.sh"