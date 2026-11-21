#!/usr/bin/env bash
# Prepare/run the separate nedra Cloudflare tunnel.
# Default is read-only status. create/route/run require --yes and are intentionally
# not executed by this task: publication is the coordinator's step.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

MODE=status
CONFIRM=0
for arg in "$@"; do
  case "$arg" in
    status|config|create|route|run) MODE=$arg ;;
    --yes) CONFIRM=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/nedra-tunnel.sh [status|config|create|route|run] [--yes]

  status   Read-only: list tunnels, local config, DNS state. Default.
  config   Write ~/.config/nedra/cloudflared-nedra.yml for an existing tunnel.
  create   Create the nedra tunnel in Cloudflare (mutation, needs --yes).
  route    Publish DNS nedra.soloveyka.bond to the tunnel (needs --yes).
  run      Run the connector with the local config (needs --yes).

Never edits ~/.cloudflared/config.yml or the existing brigada tunnel.
USAGE
      exit 0
      ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

require_cmd cloudflared
require_cmd jq

tunnel_json=$(cloudflared tunnel list --output json 2>/dev/null)
tunnel_id=$(jq -r --arg name "$NEDRA_TUNNEL_NAME" '.[] | select(.name == $name) | .id' <<<"$tunnel_json" | head -n 1)

print_status() {
  say "домен: $NEDRA_DOMAIN"
  say "локальный backend: http://${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}"
  if [ -n "$tunnel_id" ]; then
    say "туннель $NEDRA_TUNNEL_NAME: $tunnel_id"
  else
    say "туннель $NEDRA_TUNNEL_NAME ещё не создан (create --yes)"
  fi
  if [ -f "$NEDRA_CLOUDFLARED_CONFIG" ]; then
    say "локальный конфиг: $NEDRA_CLOUDFLARED_CONFIG (mode $(stat -c '%a' "$NEDRA_CLOUDFLARED_CONFIG"))"
  else
    say "локальный конфиг ещё не создан (config)"
  fi
  if [ -r "$NEDRA_CLOUDFLARED_CERT" ]; then
    say "cert.pem доступен"
  else
    warn "нет $NEDRA_CLOUDFLARED_CERT"
  fi
  local resolved
  resolved=$(getent hosts "$NEDRA_DOMAIN" 2>/dev/null | awk '{print $1}' | head -n 1 || true)
  if [ -n "$resolved" ]; then
    say "DNS $NEDRA_DOMAIN: $resolved"
  else
    say "DNS $NEDRA_DOMAIN ещё не опубликован"
  fi
  say "бригадный туннель e5999ace не изменяется"
}

write_config() {
  [ -n "$tunnel_id" ] || die "туннель $NEDRA_TUNNEL_NAME не существует; сначала create --yes"
  local credentials="$HOME/.cloudflared/${tunnel_id}.json" tmp
  [ -r "$credentials" ] || die "нет credentials файла: $credentials"
  mkdir -p "$NEDRA_RUNTIME_DIR"
  chmod 700 "$NEDRA_RUNTIME_DIR"
  tmp=$(mktemp "$NEDRA_RUNTIME_DIR/.cloudflared-nedra.XXXXXX")
  trap 'rm -- "$tmp" 2>/dev/null || true' EXIT
  sed \
    -e "s|__TUNNEL_ID__|${tunnel_id}|g" \
    -e "s|__CREDENTIALS_FILE__|${credentials}|g" \
    -e "s|__HOSTNAME__|${NEDRA_DOMAIN}|g" \
    "$NEDRA_REPO_ROOT/deploy/cloudflared/nedra.yml.template" >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$NEDRA_CLOUDFLARED_CONFIG"
  trap - EXIT
  say "записан $NEDRA_CLOUDFLARED_CONFIG (mode 600)"
}

case "$MODE" in
  status) print_status ;;
  config) write_config && print_status ;;
  create)
    [ "$CONFIRM" = "1" ] || die "create меняет Cloudflare; повторите с --yes"
    [ -z "$tunnel_id" ] || die "туннель $NEDRA_TUNNEL_NAME уже существует: $tunnel_id"
    cloudflared tunnel create "$NEDRA_TUNNEL_NAME" >/dev/null
    tunnel_json=$(cloudflared tunnel list --output json 2>/dev/null)
    tunnel_id=$(jq -r --arg name "$NEDRA_TUNNEL_NAME" '.[] | select(.name == $name) | .id' <<<"$tunnel_json" | head -n 1)
    [ -n "$tunnel_id" ] || die "туннель создан, но не найден в списке"
    say "создан туннель $NEDRA_TUNNEL_NAME: $tunnel_id"
    write_config
    ;;
  route)
    [ "$CONFIRM" = "1" ] || die "route публикует DNS; повторите с --yes"
    [ -n "$tunnel_id" ] || die "сначала создайте туннель"
    # Use the explicit tunnel ID and never --overwrite-dns: an existing foreign
    # hostname route must fail closed instead of being repointed.
    cloudflared tunnel route dns "$tunnel_id" "$NEDRA_DOMAIN"
    say "DNS $NEDRA_DOMAIN опубликован для туннеля $tunnel_id"
    ;;
  run)
    [ "$CONFIRM" = "1" ] || die "run открывает туннель наружу; повторите с --yes"
    [ -f "$NEDRA_CLOUDFLARED_CONFIG" ] || die "нет $NEDRA_CLOUDFLARED_CONFIG; выполните config"
    exec cloudflared tunnel --config "$NEDRA_CLOUDFLARED_CONFIG" run "$NEDRA_TUNNEL_NAME"
    ;;
esac