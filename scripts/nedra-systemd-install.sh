#!/usr/bin/env bash
# Install rootless systemd user units for nedra. The tunnel unit is prepared but
# is never enabled or started by this script.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

ENABLE=0
START=0
for arg in "$@"; do
  case "$arg" in
    --enable) ENABLE=1 ;;
    --start) START=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/nedra-systemd-install.sh [--enable] [--start]

Installs ~/.config/systemd/user/nedra-{db,app,tunnel}.service and runs daemon-reload.
  --enable  enable nedra-db/app for login/boot (tunnel is never enabled here)
  --start   with --enable, start db and app units now
Rootless boot persistence also needs: loginctl enable-linger $USER
USAGE
      exit 0
      ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

require_cmd systemctl
[ "$START" = "1" ] && [ "$ENABLE" != "1" ] && die "--start требует --enable"
[ -x "$NEDRA_REPO_ROOT/scripts/nedra-up.sh" ] || die "нет scripts/nedra-up.sh"

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"

render() {
  local template=$1 target=$2
  sed -e "s|@REPO@|${NEDRA_REPO_ROOT}|g" "$template" >"$target"
  chmod 644 "$target"
  say "установлен $target"
}

render "$NEDRA_REPO_ROOT/deploy/systemd/nedra-db.service.in" "$UNIT_DIR/nedra-db.service"
render "$NEDRA_REPO_ROOT/deploy/systemd/nedra-app.service.in" "$UNIT_DIR/nedra-app.service"
render "$NEDRA_REPO_ROOT/deploy/systemd/nedra-tunnel.service.in" "$UNIT_DIR/nedra-tunnel.service"

systemctl --user daemon-reload
say "systemd user units перечитаны"

if [ "$ENABLE" = "1" ]; then
  systemctl --user enable nedra-db.service nedra-app.service
  say "db/app units включены; tunnel unit не включён намеренно"
  say "для старта без логина: loginctl enable-linger $USER"
fi
if [ "$START" = "1" ]; then
  systemctl --user start nedra-db.service
  systemctl --user start nedra-app.service
  say "db/app units запущены"
fi

systemctl --user list-unit-files 'nedra-*' --no-pager || true