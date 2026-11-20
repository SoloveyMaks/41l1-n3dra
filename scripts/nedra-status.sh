#!/usr/bin/env bash
# Local health/status for nedra. Read-only: never starts, stops or publishes anything.
set -euo pipefail
# shellcheck source=nedra-lib.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nedra-lib.sh"

require_cmd podman
fail=0

say "контейнеры nedra:"
podman ps -a --filter 'name=^nedra-' --format '  {{.Names}}  {{.Image}}  {{.Status}}  {{.Ports}}' || true

if container_running "$NEDRA_APP_CONTAINER"; then
  code=$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' "http://${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}/api/health" 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    say "backend /api/health: HTTP 200"
  elif [ "$code" = "404" ]; then
    legacy=$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' "http://${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}/api/runs" 2>/dev/null || true)
    if [ "$legacy" = "200" ]; then
      warn "backend /api/health: 404; временный fallback /api/runs: HTTP 200"
    else
      warn "backend /api/health: 404; fallback /api/runs: HTTP ${legacy:-нет ответа}"
      fail=1
    fi
  else
    warn "backend /api/health: HTTP ${code:-нет ответа}"
    fail=1
  fi
  for env_name in CG_DATA_DIR CG_PUBLIC_DEMO VECTOR_RETRIEVAL_ENABLED AI_MODEL; do
    value=$(podman inspect -f "{{range .Config.Env}}{{println .}}{{end}}" "$NEDRA_APP_CONTAINER" 2>/dev/null | sed -n "s/^${env_name}=//p" | tail -n 1)
    if [ -n "$value" ]; then say "$env_name=$value"; fi
  done
  health=$(podman inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NEDRA_APP_CONTAINER" 2>/dev/null || printf missing)
  say "backend healthcheck: $health"
else
  warn "backend $NEDRA_APP_CONTAINER не запущен"
  fail=1
fi

if container_running "$NEDRA_DB_CONTAINER"; then
  health=$(podman inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NEDRA_DB_CONTAINER" 2>/dev/null || printf missing)
  say "БД healthcheck: $health"
  if podman exec "$NEDRA_DB_CONTAINER" pg_isready -U "$NEDRA_DB_USER" -d "$NEDRA_DB_NAME" >/dev/null 2>&1; then
    say "pg_isready: OK"
  else
    warn "pg_isready: FAIL"
    fail=1
  fi
  vector=$(podman exec "$NEDRA_DB_CONTAINER" psql -U "$NEDRA_DB_USER" -d "$NEDRA_DB_NAME" -tAc "select extversion from pg_extension where extname='vector'" 2>/dev/null | tr -d '[:space:]' || true)
  if [ -n "$vector" ]; then say "pgvector: $vector"; else warn "pgvector extension не найдена"; fail=1; fi
  optional=$(podman exec "$NEDRA_DB_CONTAINER" psql -U "$NEDRA_DB_USER" -d "$NEDRA_DB_NAME" -tAc "select to_regclass('data_contract.example_embeddings')" 2>/dev/null | tr -d '[:space:]' || true)
  if [ -n "$optional" ]; then
    say "optional 003: example_embeddings есть"
  else
    say "optional 003: ещё не в ledger (backend storage pending)"
  fi
else
  warn "БД $NEDRA_DB_CONTAINER не запущена"
  fail=1
fi

host_ports=$(podman port "$NEDRA_DB_CONTAINER" 2>/dev/null || true)
if container_exists "$NEDRA_APP_CONTAINER" && ! owns_container "$NEDRA_APP_CONTAINER"; then
  warn "backend без owner label; скрипты не будут его трогать"
  fail=1
fi
if [ -n "$host_ports" ]; then
  warn "БД опубликована наружу: $host_ports"
  fail=1
else
  say "БД: нет host-портов, доступна только в сети $NEDRA_NETWORK"
fi
if command -v ss >/dev/null 2>&1; then
  listeners=$(ss -ltnH "sport = :${NEDRA_BACKEND_PORT}" || true)
  if [ -z "$listeners" ]; then
    if container_running "$NEDRA_APP_CONTAINER"; then
      warn "backend-контейнер запущен, но порт ${NEDRA_BACKEND_PORT} не слушается"
      fail=1
    else
      say "порт ${NEDRA_BACKEND_PORT} не слушается (backend остановлен)"
    fi
  else
    bad=$(printf '%s\n' "$listeners" | awk '{print $4}' | grep -v "^127\.0\.0\.1:${NEDRA_BACKEND_PORT}$" || true)
    if [ -n "$bad" ]; then
      warn "порт ${NEDRA_BACKEND_PORT} слушается не только на 127.0.0.1: $bad"
      fail=1
    else
      say "backend слушает ${NEDRA_BACKEND_BIND}:${NEDRA_BACKEND_PORT}"
    fi
  fi
fi

if [ "$fail" -ne 0 ]; then
  say "status: DEGRADED"
  exit 1
fi
say "status: OK"