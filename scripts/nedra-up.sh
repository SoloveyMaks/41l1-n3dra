#!/usr/bin/env bash
# Start local nedra PostgreSQL and backend. Idempotent; never touches other containers.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

TARGET=both
BUILD=0
RECREATE_APP=0
for arg in "$@"; do
  case "$arg" in
    --db-only) TARGET=db ;;
    --app-only) TARGET=app ;;
    --build) BUILD=1 ;;
    --recreate-app) RECREATE_APP=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/nedra-up.sh [--db-only | --app-only] [--build] [--recreate-app]

  --db-only        Ensure network/volume and start PostgreSQL only.
  --app-only       Start backend only; DB must already be healthy.
  --build          Rebuild the application image first.
  --recreate-app   Gracefully stop and recreate only the owned stopped/running
                   nedra-app container when env/config changed. Volumes are kept.
USAGE
      exit 0
      ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

require_cmd podman
require_cmd curl
load_runtime_env

ensure_network_and_volumes() {
  if podman network exists "$NEDRA_NETWORK"; then
    say "сеть $NEDRA_NETWORK уже есть"
  else
    podman network create "$NEDRA_NETWORK" >/dev/null
    say "создана сеть $NEDRA_NETWORK"
  fi
  local volume
  for volume in "$NEDRA_DB_VOLUME" "$NEDRA_APP_VOLUME"; do
    if podman volume exists "$volume" 2>/dev/null; then
      say "volume $volume уже есть"
    else
      podman volume create "$volume" >/dev/null
      say "создан volume $volume"
    fi
  done
}

ensure_db() {
  require_owned_container "$NEDRA_DB_CONTAINER"
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
      --label io.nedra.owner=nedra \
      --label io.nedra.component=db \
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
}

ensure_app() {
  if ! container_exists "$NEDRA_DB_CONTAINER"; then
    die "БД $NEDRA_DB_CONTAINER не создана; запустите scripts/nedra-up.sh --db-only"
  fi
  if ! container_running "$NEDRA_DB_CONTAINER"; then
    die "БД $NEDRA_DB_CONTAINER не запущена; запустите scripts/nedra-up.sh --db-only"
  fi
  wait_for_db_health 120

  if [ "$BUILD" = "1" ] || ! podman image exists "$NEDRA_IMAGE"; then
    "$NEDRA_REPO_ROOT/scripts/nedra-build.sh"
  fi
  local desired_image config_hash current_image current_hash recreate=0
  desired_image=$(podman image inspect -f '{{.Id}}' "$NEDRA_IMAGE")
  config_hash=$(sha256sum "$NEDRA_RUNTIME_ENV" | awk '{print $1}')

  if container_exists "$NEDRA_APP_CONTAINER"; then
    require_owned_container "$NEDRA_APP_CONTAINER"
    current_image=$(podman inspect -f '{{.Image}}' "$NEDRA_APP_CONTAINER" 2>/dev/null || true)
    current_hash=$(podman inspect -f '{{index .Config.Labels "io.nedra.config-sha256"}}' "$NEDRA_APP_CONTAINER" 2>/dev/null || true)
    if [ "$current_image" != "$desired_image" ] || [ "$current_hash" != "$config_hash" ] || [ "$RECREATE_APP" = "1" ]; then
      recreate=1
    fi
  fi

  if [ "$recreate" = "1" ]; then
    if container_running "$NEDRA_APP_CONTAINER"; then
      if [ "$RECREATE_APP" != "1" ]; then
        die "env/config или образ изменились, но backend запущен; выполните scripts/nedra-down.sh или повторите с --recreate-app"
      fi
      podman stop "$NEDRA_APP_CONTAINER" >/dev/null
      say "backend остановлен перед пересозданием"
    fi
    podman rm "$NEDRA_APP_CONTAINER" >/dev/null
    say "удалён только собственный контейнер backend (volumes сохранены)"
  fi

  if container_running "$NEDRA_APP_CONTAINER"; then
    say "backend уже запущен с актуальным env/config"
  elif container_exists "$NEDRA_APP_CONTAINER"; then
    podman start "$NEDRA_APP_CONTAINER" >/dev/null
    say "backend перезапущен"
  else
    telegram_app_mount_args
    podman run -d \
      --name "$NEDRA_APP_CONTAINER" \
      --hostname nedra-app \
      --network "$NEDRA_NETWORK" \
      --network-alias nedra-app \
      --restart=unless-stopped \
      --label io.nedra.owner=nedra \
      --label io.nedra.component=app \
      --label "io.nedra.config-sha256=${config_hash}" \
      --publish "${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}:18080" \
      --env-file "$NEDRA_RUNTIME_ENV" \
      --env NEDRA_ENV=local \
      --volume "${NEDRA_APP_VOLUME}:/app/data:Z" \
      "${NEDRA_TELEGRAM_APP_ARGS[@]}" \
      --read-only \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      --cap-drop=ALL \
      --security-opt no-new-privileges \
      --pids-limit 512 \
      "$NEDRA_IMAGE" >/dev/null
    say "backend запущен на ${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT} (только loopback)"
  fi
  wait_for_app_health 120
}

case "$TARGET" in
  db)
    ensure_network_and_volumes
    ensure_db
    say "готово: БД; backend не запускался"
    ;;
  app)
    ensure_app
    say "готово: backend; статус: scripts/nedra-status.sh"
    ;;
  both)
    ensure_network_and_volumes
    ensure_db
    ensure_app
    say "готово; статус: scripts/nedra-status.sh"
    ;;
esac