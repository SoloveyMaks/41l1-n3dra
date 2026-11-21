#!/usr/bin/env bash
# Validate and materialize local runtime secrets for nedra.
# Default is a read-only dry run: --check. Writing requires --write.
set -euo pipefail
umask 077
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

ENV_PY="$NEDRA_REPO_ROOT/scripts/nedra_env.py"
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

  --check        Dry run: validate tools, source key, runtime.env shape and port.
  --write        Create/update ~/.config/nedra/runtime.env with mode 600.
  --refresh-key  With --write, re-read REFORMBOSS_API_KEY from the source env.

All existing non-managed CG_*/VECTOR_* entries are preserved. Missing
CG_SESSION_SECRET, CG_ANCHOR_HMAC_KEY and CG_ACCESS_CODE are generated once.
Secret values (especially the access code) are never printed.
USAGE
      exit 0
      ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

managed_key() {
  case "$1" in
    REFORMBOSS_API_KEY|POSTGRES_USER|POSTGRES_DB|POSTGRES_PASSWORD|DATABASE_URL|\
    CG_SESSION_SECRET|CG_ANCHOR_HMAC_KEY|CG_ACCESS_CODE|CG_DATA_DIR|\
    CG_PUBLIC_DEMO|CG_ALLOWED_ORIGINS|CG_TRUST_PROXY|AI_PROVIDER|AI_MODEL|\
    VECTOR_RETRIEVAL_ENABLED|CG26_HMAC_KEY|CG26_HMAC_KEY_VERSION|CG26_ADVISORY_ROOT|\
    CG26_ADVISORY_DB)
      return 0
      ;;
    *) return 1 ;;
  esac
}

read_key() {
  python3 "$ENV_PY" --source "$1" --key "$2" --allow-missing
}

check_key() {
  python3 "$ENV_PY" --source "$1" --key "$2" --check
}

require_source_key() {
  [ -f "$NEDRA_SECRET_SOURCE" ] || die "нет файла с ключом: $NEDRA_SECRET_SOURCE"
  local key
  key=$(python3 "$ENV_PY" --source "$NEDRA_SECRET_SOURCE" --key REFORMBOSS_API_KEY) \
    || die "REFORMBOSS_API_KEY не найден или пуст в $NEDRA_SECRET_SOURCE"
  case "$key" in
    *[[:space:]]*) die "REFORMBOSS_API_KEY содержит пробельные символы" ;;
  esac
}

existing_value() {
  if [ -f "$NEDRA_RUNTIME_ENV" ]; then
    read_key "$NEDRA_RUNTIME_ENV" "$1"
  fi
}

existing_or_generate() {
  local name=$1 generator=$2 value
  value=$(existing_value "$name")
  if [ -z "$value" ]; then
    value=$($generator)
    say "сгенерирован $name" >&2
  fi
  printf '%s' "$value"
}

generate_hex_32() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
generate_hex_8() { od -An -N8 -tx1 /dev/urandom | tr -d ' \n'; }

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
  require_cmd python3
  say "repo: $NEDRA_REPO_ROOT"
  say "runtime env: $NEDRA_RUNTIME_ENV (mode $(runtime_env_mode))"
  say "secret source: $NEDRA_SECRET_SOURCE"

  local existing_ref
  existing_ref=$(existing_value REFORMBOSS_API_KEY)
  if [ "$REFRESH_KEY" = "1" ]; then
    require_source_key
    say "REFORMBOSS_API_KEY будет обновлён из источника (--refresh-key)"
  elif [ -n "$existing_ref" ]; then
    say "REFORMBOSS_API_KEY уже есть; без --refresh-key сохраняется"
  else
    require_source_key
    say "REFORMBOSS_API_KEY будет прочитан из источника (ключа ещё нет)"
  fi

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
    say "runtime.env существует; custom CG_*/VECTOR_* сохраняются"
  else
    say "runtime.env будет создан при --write"
  fi

  local name
  for name in CG_SESSION_SECRET CG_ANCHOR_HMAC_KEY CG_ACCESS_CODE; do
    if [ -n "$(existing_value "$name")" ]; then
      say "$name: есть, сохраняется"
    else
      say "$name: отсутствует, будет сгенерирован (значение не печатается)"
    fi
  done
  say "public: CG_PUBLIC_DEMO=1, CG_ALLOWED_ORIGINS=https://$NEDRA_DOMAIN"
  say "AI: AI_PROVIDER=reformboss, AI_MODEL=deepseek-v4.1-flash-1017, ключ только env"
  say "vectors: VECTOR_RETRIEVAL_ENABLED=0"

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

  for name in "$NEDRA_APP_VOLUME" "$NEDRA_DB_VOLUME"; do
    if podman volume exists "$name" 2>/dev/null; then say "volume $name существует"; else say "volume $name будет создан"; fi
  done
  for name in "$NEDRA_APP_CONTAINER" "$NEDRA_DB_CONTAINER"; do
    if container_exists "$name"; then
      if owns_container "$name"; then
        say "контейнер $name принадлежит nedra (owner label)"
      else
        warn "контейнер $name существует без owner label; скрипты не будут его трогать"
      fi
    fi
  done

  if command -v cloudflared >/dev/null 2>&1; then
    if [ -r "$NEDRA_CLOUDFLARED_CERT" ]; then
      say "cloudflared и cert.pem доступны; DNS/туннель не публикуются"
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
  require_cmd python3
  local ref_key existing_ref password session_secret anchor_key access_code database_url tmp pairs_tmp team_b_key team_b_version team_b_root team_b_db
  existing_ref=$(existing_value REFORMBOSS_API_KEY)
  if [ "$REFRESH_KEY" = "1" ] || [ -z "$existing_ref" ]; then
    require_source_key
    ref_key=$(python3 "$ENV_PY" --source "$NEDRA_SECRET_SOURCE" --key REFORMBOSS_API_KEY)
  else
    ref_key=$existing_ref
    say "REFORMBOSS_API_KEY сохранён; --refresh-key не указан"
  fi
  password=$(existing_value POSTGRES_PASSWORD)
  if [ -z "$password" ]; then
    password=$(generate_hex_32)
    say "сгенерирован POSTGRES_PASSWORD"
  fi
  case "$password" in
    *[!0-9a-f]*) die "POSTGRES_PASSWORD в runtime.env не hex; не перезаписываю БД" ;;
  esac
  database_url="postgresql://${NEDRA_DB_USER}:${password}@${NEDRA_DB_ALIAS}:5432/${NEDRA_DB_NAME}"

  session_secret=$(existing_or_generate CG_SESSION_SECRET generate_hex_32)
  anchor_key=$(existing_or_generate CG_ANCHOR_HMAC_KEY generate_hex_32)
  access_code=$(existing_or_generate CG_ACCESS_CODE generate_hex_8)
  team_b_key=$(existing_or_generate CG26_HMAC_KEY generate_hex_32)
  team_b_version=$(existing_value CG26_HMAC_KEY_VERSION)
  [ -n "$team_b_version" ] || team_b_version="nedra-team-b-1"
  team_b_root=$(existing_value CG26_ADVISORY_ROOT)
  [ -n "$team_b_root" ] || team_b_root="/app/data/team-b-advisories"
  team_b_db=$(existing_value CG26_ADVISORY_DB)
  [ -n "$team_b_db" ] || team_b_db="${team_b_root}/vulnerabilities.json"

  mkdir -p "$NEDRA_RUNTIME_DIR"
  chmod 700 "$NEDRA_RUNTIME_DIR"
  tmp=$(mktemp "$NEDRA_RUNTIME_DIR/.runtime.env.XXXXXX")
  trap 'rm -- "$tmp" 2>/dev/null || true' EXIT

  if [ -f "$NEDRA_RUNTIME_ENV" ]; then
    local key value
    pairs_tmp=$(mktemp "$NEDRA_RUNTIME_DIR/.pairs.XXXXXX")
    chmod 600 "$pairs_tmp"
    if ! python3 "$ENV_PY" --source "$NEDRA_RUNTIME_ENV" --pairs >"$pairs_tmp"; then
      rm -- "$pairs_tmp" 2>/dev/null || true
      die "не удалось разобрать $NEDRA_RUNTIME_ENV"
    fi
    while IFS= read -r -d '' key && IFS= read -r -d '' value; do
      if managed_key "$key"; then
        continue
      fi
      case "$value" in
        \"*|\'*) die "значение $key начинается с кавычки; канонический env-file не поддерживает quoting" ;;
      esac
      # Canonical env-file format: no export, no quotes; podman reads raw bytes.
      printf '%s=%s\n' "$key" "$value" >>"$tmp"
    done <"$pairs_tmp"
    rm -- "$pairs_tmp"
  fi

  {
    printf 'REFORMBOSS_API_KEY=%s\n' "$ref_key"
    printf 'POSTGRES_USER=%s\n' "$NEDRA_DB_USER"
    printf 'POSTGRES_DB=%s\n' "$NEDRA_DB_NAME"
    printf 'POSTGRES_PASSWORD=%s\n' "$password"
    printf 'DATABASE_URL=%s\n' "$database_url"
    printf 'CG_DATA_DIR=/app/data\n'
    printf 'CG_SESSION_SECRET=%s\n' "$session_secret"
    printf 'CG_ANCHOR_HMAC_KEY=%s\n' "$anchor_key"
    printf 'CG_ACCESS_CODE=%s\n' "$access_code"
    printf 'CG_PUBLIC_DEMO=1\n'
    printf 'CG_ALLOWED_ORIGINS=https://%s\n' "$NEDRA_DOMAIN"
    printf 'CG_TRUST_PROXY=1\n'
    printf 'AI_PROVIDER=reformboss\n'
    printf 'AI_MODEL=deepseek-v4.1-flash-1017\n'
    printf 'VECTOR_RETRIEVAL_ENABLED=0\n'
    printf 'CG26_HMAC_KEY=%s\n' "$team_b_key"
    printf 'CG26_HMAC_KEY_VERSION=%s\n' "$team_b_version"
    printf 'CG26_ADVISORY_ROOT=%s\n' "$team_b_root"
    printf 'CG26_ADVISORY_DB=%s\n' "$team_b_db"
  } >>"$tmp"

  chmod 600 "$tmp"
  mv -f "$tmp" "$NEDRA_RUNTIME_ENV"
  trap - EXIT
  [ "$(runtime_env_mode)" = "600" ] || die "не удалось выставить 600 на $NEDRA_RUNTIME_ENV"
  say "записан $NEDRA_RUNTIME_ENV (mode 600); access code не выводился"
  if [ "$REFRESH_KEY" = "1" ]; then
    say "REFORMBOSS_API_KEY обновлён из $NEDRA_SECRET_SOURCE"
  fi
}

if [ "$MODE" = "write" ]; then
  run_check
  run_write
else
  run_check
fi