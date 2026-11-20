#!/usr/bin/env bash
# Validate and materialize local runtime secrets for nedra.
# Default is a read-only dry run: --check. Writing requires --write.
set -euo pipefail
umask 077
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

MODE=check
REFRESH_KEY=0
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --write) MODE=write ;;
    --refresh-key) REFRESH_KEY=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/nedra-bootstrap.sh [--check | --write] [--refresh-key]

  --check        Dry run: validate tools, source key, paths and free port. No writes.
  --write        Create/update ~/.config/nedra/runtime.env with mode 600.
  --refresh-key  With --write, re-read REFORMBOSS_API_KEY from the source env.

The WikiVibe GPT key is never read or copied. Secret values are never printed.
USAGE
      exit 0
      ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

read_ref_key() {
  [ -f "$NEDRA_SECRET_SOURCE" ] || die "нет файла с ключом: $NEDRA_SECRET_SOURCE"
  local raw
  raw=$(sed -n 's/^[[:space:]]*REFORMBOSS_API_KEY[[:space:]]*=[[:space:]]*//p' "$NEDRA_SECRET_SOURCE" | tail -n 1)
  raw=${raw%$'\r'}
  case "$raw" in
    \"*\") raw=${raw#\"}; raw=${raw%\"} ;;
    \'*\') raw=${raw#\'}; raw=${raw%\'} ;;
  esac
  [ -n "$raw" ] || die "REFORMBOSS_API_KEY не найден или пуст в $NEDRA_SECRET_SOURCE"
  case "$raw" in
    *[[:space:]]*) die "REFORMBOSS_API_KEY содержит пробельные символы" ;;
  esac
  printf '%s' "$raw"
}

read_existing_password() {
  [ -f "$NEDRA_RUNTIME_ENV" ] || return 0
  sed -n 's/^POSTGRES_PASSWORD=//p' "$NEDRA_RUNTIME_ENV" | tail -n 1
}

generate_password() {
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

check_port() {
  command -v ss >/dev/null 2>&1 || { warn "ss недоступен, порт не проверен"; return 0; }
  if ss -ltnH "sport = :${NEDRA_BACKEND_PORT}" | grep -q .; then
    if container_running "$NEDRA_APP_CONTAINER"; then
      say "порт ${NEDRA_BACKEND_PORT} уже занят nedra-app (переиспользование)"
    else
      die "порт ${NEDRA_BACKEND_PORT} занят чужим процессом"
    fi
  else
    say "порт ${NEDRA_BACKEND_PORT} свободен"
  fi
}

run_check() {
  require_cmd podman
  require_cmd curl
  say "repo: $NEDRA_REPO_ROOT"
  say "runtime env: $NEDRA_RUNTIME_ENV (mode $(runtime_env_mode))"
  say "secret source: $NEDRA_SECRET_SOURCE"

  local key
  key=$(read_ref_key)
  say "REFORMBOSS_API_KEY найден (значение не выводится)"

  if [ -f "$NEDRA_RUNTIME_ENV" ]; then
    local mode
    mode=$(runtime_env_mode)
    if [ "$mode" != "600" ]; then
      if [ "$MODE" = "write" ]; then
        warn "$NEDRA_RUNTIME_ENV имеет режим $mode; --write исправит на 600"
      else
        die "$NEDRA_RUNTIME_ENV существует с режимом $mode, нужен 600"
      fi
    fi
    say "runtime.env уже существует; --write сохранит текущий пароль БД"
  else
    say "runtime.env будет создан при --write"
  fi

  check_port

  if podman image exists "$NEDRA_DB_IMAGE"; then
    say "pinned pgvector image на месте"
  else
    warn "нет локального pgvector image; build/up скачает закреплённый digest"
  fi
  if podman image exists "$NEDRA_IMAGE"; then
    say "образ приложения на месте: $NEDRA_IMAGE"
  else
    say "образ приложения ещё не собран: scripts/nedra-build.sh"
  fi

  local name
  for name in "$NEDRA_NETWORK"; do
    if podman network exists "$name" 2>/dev/null; then say "сеть $name существует"; else say "сеть $name будет создана"; fi
  done
  for name in "$NEDRA_APP_VOLUME" "$NEDRA_DB_VOLUME"; do
    if podman volume exists "$name" 2>/dev/null; then say "volume $name существует"; else say "volume $name будет создан"; fi
  done
  for name in "$NEDRA_APP_CONTAINER" "$NEDRA_DB_CONTAINER"; do
    if container_exists "$name"; then say "контейнер $name существует (будет переиспользован)"; fi
  done

  if command -v cloudflared >/dev/null 2>&1; then
    if [ -r "$NEDRA_CLOUDFLARED_CERT" ]; then
      say "cloudflared и cert.pem доступны; DNS/туннель на этом шаге не публикуются"
    else
      warn "cloudflared есть, но $NEDRA_CLOUDFLARED_CERT не читается"
    fi
  else
    warn "cloudflared не найден; публикация туннеля недоступна"
  fi
  say "проверка OK: контейнеры не запущены, секреты не выводятся"
}

run_write() {
  require_cmd podman
  local key password database_url tmp mode
  key=$(read_ref_key)
  password=$(read_existing_password)
  if [ -z "$password" ]; then
    password=$(generate_password)
    say "сгенерирован новый пароль БД"
  else
    say "сохранён существующий пароль БД"
  fi
  case "$password" in
    *[!0-9a-f]*) die "POSTGRES_PASSWORD в runtime.env не hex; не перезаписываю БД" ;;
  esac
  database_url="postgresql://${NEDRA_DB_USER}:${password}@${NEDRA_DB_ALIAS}:5432/${NEDRA_DB_NAME}"

  mkdir -p "$NEDRA_RUNTIME_DIR"
  chmod 700 "$NEDRA_RUNTIME_DIR"
  tmp=$(mktemp "$NEDRA_RUNTIME_DIR/.runtime.env.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  (
    umask 077
    {
      printf 'REFORMBOSS_API_KEY=%s\n' "$key"
      printf 'POSTGRES_USER=%s\n' "$NEDRA_DB_USER"
      printf 'POSTGRES_DB=%s\n' "$NEDRA_DB_NAME"
      printf 'POSTGRES_PASSWORD=%s\n' "$password"
      printf 'DATABASE_URL=%s\n' "$database_url"
    } >"$tmp"
  )
  chmod 600 "$tmp"
  mv -f "$tmp" "$NEDRA_RUNTIME_ENV"
  trap - EXIT
  mode=$(runtime_env_mode)
  [ "$mode" = "600" ] || die "не удалось выставить 600 на $NEDRA_RUNTIME_ENV (mode=$mode)"
  say "записан $NEDRA_RUNTIME_ENV (mode $mode)"
  if [ "$REFRESH_KEY" = "1" ]; then
    say "ключ обновлён из $NEDRA_SECRET_SOURCE"
  fi
  say "секреты не выводились"
}

if [ "$MODE" = "write" ]; then
  run_check
  run_write
else
  run_check
fi