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
  --refresh-key  With --write, re-read the selected AI key from the source env
                 (OPENCODE_API_KEY, AI_API_KEY or REFORMBOSS_API_KEY).

All existing non-managed CG_*/VECTOR_* entries are preserved. Missing
CG_SESSION_SECRET, CG_ANCHOR_HMAC_KEY, CG_ACCESS_CODE and CG_ADMIN_ACCESS_CODE
are generated once. CG_ADMIN_ACCESS_CODE always differs from CG_ACCESS_CODE and
is kept on repeat runs. Secret values are never printed or passed to argv.
USAGE
      exit 0
      ;;
    *) die "неизвестный аргумент: $arg" ;;
  esac
done

managed_key() {
  case "$1" in
    OPENCODE_API_KEY|AI_API_KEY|REFORMBOSS_API_KEY|POSTGRES_USER|POSTGRES_DB|POSTGRES_PASSWORD|DATABASE_URL|\
    CG_SESSION_SECRET|CG_ANCHOR_HMAC_KEY|CG_ACCESS_CODE|CG_ADMIN_ACCESS_CODE|CG_DATA_DIR|\
    CG_PUBLIC_DEMO|CG_ALLOWED_ORIGINS|CG_TRUST_PROXY|AI_PROVIDER|AI_MODEL|LLM_MODEL|AI_BASE_URL|\
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

AI_KEY_NAMES="OPENCODE_API_KEY AI_API_KEY REFORMBOSS_API_KEY"
SOURCE_AI_KEY_NAME=""
SOURCE_AI_KEY_VALUE=""

load_source_ai_key() {
  [ -f "$NEDRA_SECRET_SOURCE" ] || die "нет файла с ключом: $NEDRA_SECRET_SOURCE"
  local name value
  for name in $AI_KEY_NAMES; do
    value=$(python3 "$ENV_PY" --source "$NEDRA_SECRET_SOURCE" --key "$name" --allow-missing)
    case "$value" in
      *[[:space:]]*) die "$name содержит пробельные символы" ;;
    esac
    if [ -n "$value" ]; then
      SOURCE_AI_KEY_NAME=$name
      SOURCE_AI_KEY_VALUE=$value
      return 0
    fi
  done
  die "нужен OPENCODE_API_KEY, AI_API_KEY или REFORMBOSS_API_KEY в $NEDRA_SECRET_SOURCE"
}

existing_ai_key_name() {
  local name
  for name in $AI_KEY_NAMES; do
    if [ -n "$(existing_value "$name")" ]; then
      printf '%s' "$name"
      return 0
    fi
  done
  return 1
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

# Separate admin code must never equal the shared user code. The value is
# returned on stdout only; callers keep it in shell variables, never in argv
# of an external command.
generate_admin_code() {
  local user_code=$1 candidate attempt
  for attempt in 1 2 3 4 5; do
    candidate=$(generate_hex_8)
    if [ "$candidate" != "$user_code" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  die "не удалось сгенерировать CG_ADMIN_ACCESS_CODE, отличный от CG_ACCESS_CODE"
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
  require_cmd python3
  say "repo: $NEDRA_REPO_ROOT"
  say "runtime env: $NEDRA_RUNTIME_ENV (mode $(runtime_env_mode))"
  say "secret source: $NEDRA_SECRET_SOURCE"

  local existing_key_name
  existing_key_name=$(existing_ai_key_name || true)
  if [ "$REFRESH_KEY" = "1" ]; then
    load_source_ai_key
    say "$SOURCE_AI_KEY_NAME будет обновлён из источника (--refresh-key)"
  elif [ -n "$existing_key_name" ]; then
    say "$existing_key_name уже есть; без --refresh-key сохраняется"
  else
    load_source_ai_key
    say "$SOURCE_AI_KEY_NAME будет прочитан из источника (ключа ещё нет)"
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
  local admin_code_value user_code_value
  admin_code_value=$(existing_value CG_ADMIN_ACCESS_CODE)
  user_code_value=$(existing_value CG_ACCESS_CODE)
  if [ -n "$admin_code_value" ]; then
    say "CG_ADMIN_ACCESS_CODE: есть, сохраняется (значение не печатается)"
    if [ "$admin_code_value" = "$user_code_value" ]; then
      warn "CG_ADMIN_ACCESS_CODE совпадает с CG_ACCESS_CODE; --write откажется, значения не печатаются"
    fi
  else
    # --write всегда делает установку публичной (CG_PUBLIC_DEMO=1), поэтому
    # отдельный admin-код генерируется сразу; без него admin login выключен.
    say "CG_ADMIN_ACCESS_CODE: отсутствует, будет сгенерирован отдельно от CG_ACCESS_CODE (значение не печатается)"
  fi
  say "public: CG_PUBLIC_DEMO=1, CG_ALLOWED_ORIGINS=https://$NEDRA_DOMAIN"
  say "AI: OpenCode Go по умолчанию, legacy ReformBoss совместим; ключ только env"
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
  local ai_key_name ai_key_value existing_key_name ai_provider ai_model ai_base existing_model existing_base password session_secret anchor_key access_code admin_code database_url tmp pairs_tmp team_b_key team_b_version team_b_root team_b_db
  existing_key_name=$(existing_ai_key_name || true)
  if [ "$REFRESH_KEY" = "1" ] || [ -z "$existing_key_name" ]; then
    load_source_ai_key
    ai_key_name=$SOURCE_AI_KEY_NAME
    ai_key_value=$SOURCE_AI_KEY_VALUE
  else
    ai_key_name=$existing_key_name
    ai_key_value=$(existing_value "$ai_key_name")
    say "$ai_key_name сохранён; --refresh-key не указан"
  fi
  case "$ai_key_value" in
    *[[:space:]]*) die "$ai_key_name содержит пробельные символы" ;;
  esac

  existing_model=$(existing_value AI_MODEL)
  [ -n "$existing_model" ] || existing_model=$(existing_value LLM_MODEL)
  existing_base=$(existing_value AI_BASE_URL)
  case "$ai_key_name" in
    OPENCODE_API_KEY)
      ai_provider="opencode-go"
      ai_base=""
      if [ -n "$existing_model" ] && [ "$existing_model" != "deepseek-v4.1-flash-1017" ]; then
        ai_model=$existing_model
      else
        ai_model="deepseek-v4.1-flash"
      fi
      ;;
    AI_API_KEY)
      if [ -n "$existing_base" ]; then
        ai_provider="openai-compatible"
        ai_base=$existing_base
        [ -n "$existing_model" ] || die "AI_API_KEY с AI_BASE_URL требует AI_MODEL/LLM_MODEL"
        ai_model=$existing_model
      else
        ai_provider="opencode-go"
        ai_base=""
        if [ -n "$existing_model" ] && [ "$existing_model" != "deepseek-v4.1-flash-1017" ]; then
          ai_model=$existing_model
        else
          ai_model="deepseek-v4.1-flash"
        fi
      fi
      ;;
    REFORMBOSS_API_KEY)
      ai_provider="reformboss"
      ai_base=$existing_base
      if [ -n "$existing_model" ] && [ "$existing_model" != "deepseek-v4.1-flash" ]; then
        ai_model=$existing_model
      else
        ai_model="deepseek-v4.1-flash-1017"
      fi
      ;;
    *) die "неизвестный AI key: $ai_key_name" ;;
  esac
  case "$ai_model" in
    *[[:space:]]*|"") die "AI_MODEL пустой или содержит пробельные символы" ;;
  esac
  if [ -n "$existing_base" ] && [ "$ai_provider" = "opencode-go" ]; then
    warn "AI_BASE_URL не переносится для opencode-go; используется Go endpoint"
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
  admin_code=$(existing_value CG_ADMIN_ACCESS_CODE)
  if [ -z "$admin_code" ]; then
    admin_code=$(generate_admin_code "$access_code")
    say "сгенерирован CG_ADMIN_ACCESS_CODE"
  fi
  if [ "$admin_code" = "$access_code" ]; then
    die "CG_ADMIN_ACCESS_CODE совпадает с CG_ACCESS_CODE; замените один из кодов вручную (значения не печатаются)"
  fi
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
    printf '%s=%s\n' "$ai_key_name" "$ai_key_value"
    printf 'POSTGRES_USER=%s\n' "$NEDRA_DB_USER"
    printf 'POSTGRES_DB=%s\n' "$NEDRA_DB_NAME"
    printf 'POSTGRES_PASSWORD=%s\n' "$password"
    printf 'DATABASE_URL=%s\n' "$database_url"
    printf 'CG_DATA_DIR=/app/data\n'
    printf 'CG_SESSION_SECRET=%s\n' "$session_secret"
    printf 'CG_ANCHOR_HMAC_KEY=%s\n' "$anchor_key"
    printf 'CG_ACCESS_CODE=%s\n' "$access_code"
    printf 'CG_ADMIN_ACCESS_CODE=%s\n' "$admin_code"
    printf 'CG_PUBLIC_DEMO=1\n'
    printf 'CG_ALLOWED_ORIGINS=https://%s\n' "$NEDRA_DOMAIN"
    printf 'CG_TRUST_PROXY=1\n'
    printf 'AI_PROVIDER=%s\n' "$ai_provider"
    printf 'AI_MODEL=%s\n' "$ai_model"
    if [ -n "$ai_base" ]; then
      printf 'AI_BASE_URL=%s\n' "$ai_base"
    fi
    printf 'VECTOR_RETRIEVAL_ENABLED=0\n'
    printf 'CG26_HMAC_KEY=%s\n' "$team_b_key"
    printf 'CG26_HMAC_KEY_VERSION=%s\n' "$team_b_version"
    printf 'CG26_ADVISORY_ROOT=%s\n' "$team_b_root"
    printf 'CG26_ADVISORY_DB=%s\n' "$team_b_db"
  } >>"$tmp"

  python3 "$ENV_PY" --source "$tmp" --canonical --pairs >/dev/null \
    || die "не удалось создать канонический runtime.env"
  chmod 600 "$tmp"
  mv -f "$tmp" "$NEDRA_RUNTIME_ENV"
  trap - EXIT
  [ "$(runtime_env_mode)" = "600" ] || die "не удалось выставить 600 на $NEDRA_RUNTIME_ENV"
  say "записан $NEDRA_RUNTIME_ENV (mode 600); access codes не выводились"
  if [ "$REFRESH_KEY" = "1" ]; then
    say "$ai_key_name обновлён из $NEDRA_SECRET_SOURCE"
  fi
}

if [ "$MODE" = "write" ]; then
  run_check
  run_write
else
  run_check
fi
