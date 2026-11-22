#!/usr/bin/env bash
# Изолированный e2e-прогон nedra: сборка образа, свои контейнеры/volume/сеть,
# свободный loopback-порт, миграции, регистрация аккаунта, импорт ZIP из
# examples/demo, запуск сценария до terminal-статуса, проверка отчётов
# JSON/JSONC/Markdown/DOCX и карты безопасности.
#
# Гарантии изоляции:
#   * используются только имена с префиксом nedra-rel-e2e-*; прод-контейнеры
#     nedra-app/nedra-db, сеть nedra и volumes nedra-*-data не упоминаются
#     и не трогаются;
#   * каждый ресурс несёт label io.nedra.owner=nedra-rel-e2e, удаление идёт
#     только после проверки этого label (чужие остатки скрипт не трогает);
#   * порт backend выбирается свободный и публикуется только на 127.0.0.1,
#     у БД host-портов нет;
#   * runtime.env и секреты хоста не читаются: пароли и session secret
#     генерируются на прогон во временном каталоге (режим 600);
#   * один активный прогон за раз (flock): параллельный запуск не получает
#     lock и ничего не удаляет; --help завершается до любых обращений к Podman.
#
# Требования: podman, curl, python3, git, flock, сеть для сборки (npm ci /
# pip wheel).
# Запуск: scripts/nedra-e2e.sh [--help]
set -euo pipefail
umask 077

# Разбор аргументов выполняется до trap и до любых обращений к Podman:
# --help и неверный аргумент не трогают ресурсы и не ставят очистку.
case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d;s/^# \{0,1\}//'
    exit 0
    ;;
  "") ;;
  *)
    printf '[nedra-e2e] ERROR: неизвестный аргумент: %s\n' "$1" >&2
    exit 2
    ;;
esac

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=nedra-lib.sh
. "$SCRIPT_DIR/nedra-lib.sh" # say/warn/require_cmd, container_exists/container_running

# --- Изолированные имена (никогда не nedra-app/nedra-db/nedra-*-data) --------
E2E_OWNER=nedra-rel-e2e
E2E_APP=${E2E_OWNER}-app
E2E_DB=${E2E_OWNER}-db
E2E_NET=${E2E_OWNER}-net
E2E_APP_VOL=${E2E_OWNER}-app-data
E2E_DB_VOL=${E2E_OWNER}-db-data
E2E_IMAGE=localhost/${E2E_OWNER}:local
E2E_BIND=127.0.0.1
E2E_DB_NAME=nedra
E2E_DB_USER=nedra
E2E_ZIP=${NEDRA_E2E_ZIP:-$NEDRA_REPO_ROOT/examples/demo/sql-before-after/before.zip}
E2E_RUN_TIMEOUT=${NEDRA_E2E_RUN_TIMEOUT:-600}
E2E_WORKDIR=""
E2E_APP_PORT=""
BASE=""
CJ=""
OUT=""
E2E_STEPS=()
STEP_NAME=""
STEP_T0=0

# --- Диагностика и die ------------------------------------------------------
dump_logs() {
  local name
  for name in "$E2E_APP" "$E2E_DB"; do
    if container_exists "$name"; then
      printf '[nedra-e2e] --- podman logs --tail 40 %s ---\n' "$name" >&2
      podman logs --tail 40 "$name" >&2 || true
    fi
  done
}

die() {
  printf '[nedra-e2e] ERROR: %s\n' "$*" >&2
  dump_logs
  exit 1
}

step() {
  STEP_NAME=$1
  STEP_T0=$(date +%s)
  say "=== ${STEP_NAME}"
}

step_done() {
  local took=$(( $(date +%s) - STEP_T0 ))
  E2E_STEPS+=("${STEP_NAME}: ${took}s")
  say "=== ${STEP_NAME}: ${took}s"
}

# --- Чистка только своих ресурсов ------------------------------------------
e2e_container_owner() {
  podman inspect -f '{{index .Config.Labels "io.nedra.owner"}}' "$1" 2>/dev/null || true
}
e2e_volume_owner() {
  podman volume inspect -f '{{index .Labels "io.nedra.owner"}}' "$1" 2>/dev/null || true
}
e2e_network_owner() {
  podman network inspect -f '{{index .Labels "io.nedra.owner"}}' "$1" 2>/dev/null || true
}

purge_e2e_state() {
  local name
  for name in "$E2E_APP" "$E2E_DB"; do
    if container_exists "$name"; then
      [ "$(e2e_container_owner "$name")" = "$E2E_OWNER" ] \
        || die "контейнер $name существует без label io.nedra.owner=${E2E_OWNER} — не трогаю"
      if container_running "$name"; then
        podman stop --time 20 "$name" >/dev/null
      fi
      podman rm "$name" >/dev/null
      say "удалён остаточный контейнер $name"
    fi
  done
  for name in "$E2E_APP_VOL" "$E2E_DB_VOL"; do
    if podman volume exists "$name" 2>/dev/null; then
      [ "$(e2e_volume_owner "$name")" = "$E2E_OWNER" ] \
        || die "volume $name не наш — не трогаю"
      podman volume rm "$name" >/dev/null
      say "удалён остаточный volume $name"
    fi
  done
  if podman network exists "$E2E_NET" 2>/dev/null; then
    [ "$(e2e_network_owner "$E2E_NET")" = "$E2E_OWNER" ] \
      || die "сеть $E2E_NET не наша — не трогаю"
    podman network rm "$E2E_NET" >/dev/null
    say "удалена остаточная сеть $E2E_NET"
  fi
}

cleanup() {
  local rc=$?
  local -a leftovers=()
  trap - EXIT
  set +e
  local name
  for name in "$E2E_APP" "$E2E_DB"; do
    if container_exists "$name"; then
      if [ "$(e2e_container_owner "$name")" = "$E2E_OWNER" ]; then
        if container_running "$name"; then
          podman stop --time 20 "$name" >/dev/null 2>&1
        fi
        if podman rm "$name" >/dev/null 2>&1; then
          say "контейнер $name удалён"
        else
          warn "не удалось удалить контейнер $name"
          leftovers+=("container $name")
        fi
      else
        warn "контейнер $name без нашего label — пропущен"
        leftovers+=("container $name (чужой label)")
      fi
    fi
  done
  for name in "$E2E_APP_VOL" "$E2E_DB_VOL"; do
    if podman volume exists "$name" 2>/dev/null; then
      if [ "$(e2e_volume_owner "$name")" = "$E2E_OWNER" ]; then
        if podman volume rm "$name" >/dev/null 2>&1; then
          say "volume $name удалён"
        else
          warn "не удалось удалить volume $name"
          leftovers+=("volume $name")
        fi
      else
        warn "volume $name без нашего label — пропущен"
        leftovers+=("volume $name (чужой label)")
      fi
    fi
  done
  if podman network exists "$E2E_NET" 2>/dev/null; then
    if [ "$(e2e_network_owner "$E2E_NET")" = "$E2E_OWNER" ]; then
      if podman network rm "$E2E_NET" >/dev/null 2>&1; then
        say "сеть $E2E_NET удалена"
      else
        warn "не удалось удалить сеть $E2E_NET"
        leftovers+=("network $E2E_NET")
      fi
    else
      warn "сеть $E2E_NET без нашего label — пропущена"
      leftovers+=("network $E2E_NET (чужой label)")
    fi
  fi
  if [ -n "$E2E_WORKDIR" ] && [ -d "$E2E_WORKDIR" ]; then
    if ! rm -rf -- "$E2E_WORKDIR"; then
      warn "не удалось удалить временный каталог $E2E_WORKDIR"
      leftovers+=("workdir $E2E_WORKDIR")
    fi
  fi
  # Остатки — это провал очистки: принудительно ненулевой exit и явный список.
  if [ ${#leftovers[@]} -gt 0 ]; then
    warn "остались ресурсы прогона (очистка неполная):"
    for name in "${leftovers[@]}"; do
      warn "  - $name"
    done
    rc=1
  fi
  if [ "$rc" -eq 0 ]; then
    say "ИТОГ: e2e зелёный; изолированные ресурсы удалены"
    local line
    for line in "${E2E_STEPS[@]:-}"; do
      [ -n "$line" ] && printf '[nedra-e2e]   %s\n' "$line"
    done
  else
    warn "ИТОГ: e2e упал (rc=${rc})"
    if [ ${#leftovers[@]} -eq 0 ]; then
      warn "изолированные ресурсы удалены"
    fi
  fi
  exit "$rc"
}
# Один активный прогон за раз: параллельный запуск падает ДО регистрации trap,
# поэтому не выполняет ни purge, ни cleanup и не трогает чужие ресурсы.
require_cmd flock
exec 9>"${TMPDIR:-/tmp}/nedra-rel-e2e.lock"
if ! flock -n 9; then
  printf '[nedra-e2e] ERROR: другой прогон nedra-rel-e2e уже идёт; ничего не трогаю\n' >&2
  exit 1
fi

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- HTTP -------------------------------------------------------------------
HTTP_CODE=""
http_req() { # <METHOD> <url> <outfile> [extra curl args...]
  local method=$1 url=$2 outfile=$3
  shift 3
  HTTP_CODE=$(curl -sS -X "$method" -b "$CJ" -c "$CJ" \
    -D "${outfile}.headers" -o "$outfile" -w '%{http_code}' \
    "$@" "$url") || die "нет ответа: ${method} ${url}"
}

expect_code() { # <want> <what>
  if [ "$HTTP_CODE" != "$1" ]; then
    printf '[nedra-e2e] тело ответа: %s\n' "$(head -c 600 "$OUT" 2>/dev/null || true)" >&2
    die "$2: ожидали HTTP $1, получили ${HTTP_CODE}"
  fi
}

# --- Ожидания ---------------------------------------------------------------
wait_db_healthy() { # <timeout>
  local timeout=$1 start now status
  start=$(date +%s)
  while :; do
    status=$(podman inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      "$E2E_DB" 2>/dev/null || printf missing)
    [ "$status" = "healthy" ] && return 0
    if ! container_running "$E2E_DB"; then
      podman logs --tail 40 "$E2E_DB" >&2 || true
      die "e2e БД завершился (status=${status})"
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "$timeout" ]; then
      podman logs --tail 40 "$E2E_DB" >&2 || true
      die "e2e БД не стала healthy за ${timeout}s (status=${status})"
    fi
    sleep 2
  done
}

wait_app_health() { # <timeout>
  local timeout=$1 start now
  start=$(date +%s)
  while :; do
    if curl -fsS --max-time 2 -o /dev/null "${BASE}/api/health" 2>/dev/null; then
      return 0
    fi
    if ! container_running "$E2E_APP"; then
      podman logs --tail 60 "$E2E_APP" >&2 || true
      die "e2e backend завершился до готовности"
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "$timeout" ]; then
      podman logs --tail 60 "$E2E_APP" >&2 || true
      die "e2e backend не ответил на /api/health за ${timeout}s"
    fi
    sleep 2
  done
}

wait_migrations() { # <timeout>
  local timeout=$1 start now expected_list ledger_tmp missing
  # Список миграций берётся из каталога: после мержа новых *.up.sql проверка
  # остаётся динамической (все файлы должны быть в ledger по именам), без
  # зашитого числа файлов.
  expected_list="$E2E_WORKDIR/migrations-expected.txt"
  ledger_tmp="$E2E_WORKDIR/migrations-ledger.txt"
  find "$NEDRA_REPO_ROOT/db/migrations" -maxdepth 1 -name '*.up.sql' -printf '%f\n' \
    | LC_ALL=C sort >"$expected_list"
  [ -s "$expected_list" ] || die "нет файлов db/migrations/*.up.sql"
  start=$(date +%s)
  while :; do
    if podman exec "$E2E_DB" psql -U "$E2E_DB_USER" -d "$E2E_DB_NAME" -tAc \
        "select filename from public.schema_migrations order by filename" \
        >"$ledger_tmp" 2>/dev/null; then
      missing=$(LC_ALL=C comm -23 "$expected_list" <(LC_ALL=C sort "$ledger_tmp"))
      if [ -z "$missing" ]; then
        say "миграции: все $(wc -l <"$expected_list") файлов db/migrations есть в ledger по именам"
        return 0
      fi
    else
      missing="(ledger пока недоступен)"
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "$timeout" ]; then
      printf '[nedra-e2e] отсутствуют в ledger:\n%s\n' "$missing" >&2
      die "миграции не сошлись за ${timeout}s (сверка по именам файлов)"
    fi
    sleep 2
  done
}

# --- Preflight --------------------------------------------------------------
require_cmd podman
require_cmd curl
require_cmd python3
require_cmd git
[ -f "$E2E_ZIP" ] || die "нет ZIP для импорта: $E2E_ZIP"

step "preflight"
purge_e2e_state
E2E_WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/nedra-e2e.XXXXXX")
CJ="$E2E_WORKDIR/cookies.txt"
OUT="$E2E_WORKDIR/last-response"
step_done

# --- Сеть, порт, секреты ----------------------------------------------------
step "сеть/порты/секреты"
podman network create --label "io.nedra.owner=${E2E_OWNER}" "$E2E_NET" >/dev/null
podman volume create --label "io.nedra.owner=${E2E_OWNER}" "$E2E_DB_VOL" >/dev/null
podman volume create --label "io.nedra.owner=${E2E_OWNER}" "$E2E_APP_VOL" >/dev/null
E2E_APP_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
BASE="http://${E2E_BIND}:${E2E_APP_PORT}"
DB_PASS=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
E2E_ENV="$E2E_WORKDIR/runtime.env"
cat >"$E2E_ENV" <<EOF
DATABASE_URL=postgresql://${E2E_DB_USER}:${DB_PASS}@${E2E_DB}:5432/${E2E_DB_NAME}
POSTGRES_USER=${E2E_DB_USER}
POSTGRES_DB=${E2E_DB_NAME}
POSTGRES_PASSWORD=${DB_PASS}
CG_SESSION_SECRET=${SESSION_SECRET}
CG_DATA_DIR=/app/data
EOF
chmod 600 "$E2E_ENV"
# podman получает значения только по имени переменной (--env POSTGRES_*),
# поэтому экспортируем их в окружение скрипта; в argv пароль не попадает.
export POSTGRES_USER="$E2E_DB_USER" POSTGRES_DB="$E2E_DB_NAME" POSTGRES_PASSWORD="$DB_PASS"
say "порт backend: ${E2E_BIND}:${E2E_APP_PORT} (только loopback)"
step_done

# --- Сборка образа ----------------------------------------------------------
step "сборка образа ${E2E_IMAGE}"
E2E_REVISION=$(git -C "$NEDRA_REPO_ROOT" rev-parse HEAD)
podman build \
  --format docker \
  --pull=missing \
  --file "$NEDRA_REPO_ROOT/deploy/Dockerfile" \
  --build-arg "NEDRA_REVISION=${E2E_REVISION}" \
  --tag "$E2E_IMAGE" \
  --label "io.nedra.owner=${E2E_OWNER}" \
  --label "org.opencontainers.image.revision=${E2E_REVISION}" \
  "$NEDRA_REPO_ROOT"
step_done

# --- БД ---------------------------------------------------------------------
step "запуск изолированной БД"
podman run -d \
  --name "$E2E_DB" \
  --hostname "$E2E_DB" \
  --network "$E2E_NET" \
  --network-alias "$E2E_DB" \
  --label "io.nedra.owner=${E2E_OWNER}" \
  --label io.nedra.component=e2e-db \
  --env POSTGRES_USER \
  --env POSTGRES_DB \
  --env POSTGRES_PASSWORD \
  --volume "${E2E_DB_VOL}:/var/lib/postgresql/data:Z" \
  --volume "${NEDRA_REPO_ROOT}/deploy/db/init:/docker-entrypoint-initdb.d:ro,Z" \
  --shm-size=256m \
  --security-opt no-new-privileges \
  --health-cmd "pg_isready -U ${E2E_DB_USER} -d ${E2E_DB_NAME}" \
  --health-interval 5s \
  --health-timeout 3s \
  --health-retries 20 \
  "$NEDRA_DB_IMAGE" >/dev/null
wait_db_healthy 120
if [ -n "$(podman port "$E2E_DB" 2>/dev/null || true)" ]; then
  die "e2e БД опубликовала host-порт — нарушение изоляции"
fi
say "БД healthy, host-портов нет"
step_done

# --- Backend ----------------------------------------------------------------
step "запуск изолированного backend"
podman run -d \
  --name "$E2E_APP" \
  --hostname "$E2E_APP" \
  --network "$E2E_NET" \
  --network-alias "$E2E_APP" \
  --label "io.nedra.owner=${E2E_OWNER}" \
  --label io.nedra.component=e2e-app \
  --publish "${E2E_BIND}:${E2E_APP_PORT}:18080" \
  --env-file "$E2E_ENV" \
  --env NEDRA_ENV=local \
  --volume "${E2E_APP_VOL}:/app/data:Z" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop=ALL \
  --security-opt no-new-privileges \
  --pids-limit 512 \
  "$E2E_IMAGE" >/dev/null
wait_app_health 180
health_code=$(curl -sS -o "$OUT" -w '%{http_code}' "${BASE}/api/health")
[ "$health_code" = "200" ] || die "/api/health: HTTP ${health_code}"
say "backend healthy на ${BASE}"
step_done

# --- Миграции ---------------------------------------------------------------
step "проверка миграций"
wait_migrations 90
runs_table=$(podman exec "$E2E_DB" psql -U "$E2E_DB_USER" -d "$E2E_DB_NAME" -tAc \
  "select to_regclass('data_contract.runs') is not null" | tr -d '[:space:]')
[ "$runs_table" = "t" ] || die "схема data_contract.runs не создана"
say "data_contract.runs: есть"
step_done

# --- Регистрация аккаунта ---------------------------------------------------
step "регистрация аккаунта"
E2E_EMAIL="e2e-$(date +%s)-$$@example.test"
E2E_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
http_req POST "${BASE}/api/auth/register" "$OUT" \
  -H 'Content-Type: application/json' \
  --data "{\"email\":\"${E2E_EMAIL}\",\"password\":\"${E2E_PASSWORD}\",\"displayName\":\"nedra e2e\"}"
expect_code 200 "POST /api/auth/register"
grep -q "cg_session" "$CJ" || die "после регистрации нет cookie cg_session"
say "аккаунт создан: ${E2E_EMAIL}"
step_done

# --- Импорт examples/demo ZIP ----------------------------------------------
step "импорт ${E2E_ZIP##*/}"
http_req POST "${BASE}/api/artifacts" "$OUT" \
  -F "file=@${E2E_ZIP};filename=$(basename "$E2E_ZIP")"
expect_code 200 "POST /api/artifacts"
ART_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id") or "")' "$OUT")
[ -n "$ART_ID" ] || die "в ответе импорта нет artifact id"
say "артефакт импортирован: ${ART_ID}"
step_done

# --- Сценарий ---------------------------------------------------------------
step "выбор сценария"
http_req GET "${BASE}/api/workflows" "$OUT"
expect_code 200 "GET /api/workflows"
WF_ID=$(python3 -c '
import json, sys
wfs = json.load(open(sys.argv[1]))
ids = [w.get("id") for w in wfs if isinstance(w, dict)]
print("wf_secure_pr_gate" if "wf_secure_pr_gate" in ids else (ids[0] if ids else ""))
' "$OUT")
[ -n "$WF_ID" ] || die "нет доступных сценариев"
say "сценарий: ${WF_ID}"
step_done

step "запуск сценария"
http_req POST "${BASE}/api/workflows/${WF_ID}/runs" "$OUT" \
  -H 'Content-Type: application/json' \
  --data "{\"artifactId\":\"${ART_ID}\",\"triggerType\":\"manual\"}"
expect_code 202 "POST /api/workflows/${WF_ID}/runs"
RUN_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("runId") or "")' "$OUT")
[ -n "$RUN_ID" ] || die "в ответе запуска нет runId"
say "run: ${RUN_ID}"
step_done

# --- Ожидание terminal-статуса ----------------------------------------------
step "ожидание terminal-статуса (timeout ${E2E_RUN_TIMEOUT}s)"
deadline=$(( $(date +%s) + E2E_RUN_TIMEOUT ))
run_status=""
while :; do
  http_req GET "${BASE}/api/runs/${RUN_ID}" "$OUT"
  expect_code 200 "GET /api/runs/${RUN_ID}"
  run_status=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("execution_status") or "")' "$OUT")
  case "$run_status" in
    queued|running|"") ;;
    *) break ;;
  esac
  if [ "$(date +%s)" -ge "$deadline" ]; then
    die "run ${RUN_ID} не завершился за ${E2E_RUN_TIMEOUT}s (status=${run_status:-пусто})"
  fi
  sleep 3
done
[ "$run_status" = "succeeded" ] || die "run ${RUN_ID}: терминальный статус «${run_status}», ожидали succeeded"
say "run завершён: ${run_status}"
step_done

# --- Отчёты и карта ---------------------------------------------------------
step "отчёт JSON (official.json)"
http_req GET "${BASE}/api/runs/${RUN_ID}/report/official.json" "$OUT"
expect_code 200 "GET /report/official.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d,dict) and d, "official.json пуст"' "$OUT" \
  || die "official.json не разбирается как JSON"
say "official.json: 200, JSON валиден"
step_done

step "отчёт JSONC (export.jsonc)"
http_req GET "${BASE}/api/runs/${RUN_ID}/report/export.jsonc" "$OUT"
expect_code 200 "GET /report/export.jsonc"
python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("schemaVersion") == "nedra-report-export/1", d.get("schemaVersion")
assert d.get("reportAvailable") is True, "reportAvailable != true"
assert d.get("run", {}).get("id") == sys.argv[2], "run.id не совпадает"
' "$OUT" "$RUN_ID" || die "export.jsonc: неверная структура"
say "export.jsonc: 200, schemaVersion nedra-report-export/1"
step_done

step "отчёт Markdown (export.md)"
http_req GET "${BASE}/api/runs/${RUN_ID}/report/export.md" "$OUT"
expect_code 200 "GET /report/export.md"
grep -q "Отчёт о проверке безопасности кода" "$OUT" || die "export.md: нет заголовка отчёта"
[ "$(wc -c <"$OUT")" -gt 500 ] || die "export.md подозрительно мал"
say "export.md: 200, заголовок и объём в порядке"
step_done

step "отчёт DOCX (export.docx)"
DOCX_FILE="$E2E_WORKDIR/report.docx"
http_req GET "${BASE}/api/runs/${RUN_ID}/report/export.docx" "$DOCX_FILE"
expect_code 200 "GET /report/export.docx"
grep -qi "application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
  "${DOCX_FILE}.headers" || die "export.docx: неверный Content-Type"
docx_ver=$(podman exec "$E2E_APP" python -c 'import docx; print(docx.__version__)' 2>/dev/null) \
  || die "python-docx не установлен в образе"
say "python-docx в образе: ${docx_ver}"
python3 - "$DOCX_FILE" <<'PY'
import sys, zipfile
path = sys.argv[1]
with open(path, "rb") as fh:
    magic = fh.read(4)
assert magic == b"PK\x03\x04", f"не ZIP: {magic!r}"
with zipfile.ZipFile(path) as zf:
    bad = zf.testzip()
    assert bad is None, f"битый участник архива: {bad}"
    names = set(zf.namelist())
assert "[Content_Types].xml" in names and "word/document.xml" in names, "нет частей DOCX"
print(f"docx zip ok: {len(names)} участников")
PY
podman exec -i "$E2E_APP" python -c '
import io, sys
from docx import Document
doc = Document(io.BytesIO(sys.stdin.buffer.read()))
assert doc.paragraphs, "пустой документ"
print(f"python-docx открыл документ: {len(doc.paragraphs)} абзацев")
' <"$DOCX_FILE" || die "python-docx в образе не открыл файл"
say "DOCX: 200, открывается python-docx из образа"
step_done

step "карта безопасности (security-map)"
http_req GET "${BASE}/api/runs/${RUN_ID}/security-map" "$OUT"
expect_code 200 "GET /security-map"
python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("identity", {}).get("runId") == sys.argv[2], "identity.runId не совпадает"
assert isinstance(d.get("map"), dict), "нет map"
' "$OUT" "$RUN_ID" || die "security-map: неверная структура"
say "security-map: 200, identity/map в порядке"
step_done

step "проверка, что чужие ресурсы не тронуты"
for foreign in nedra-app nedra-db; do
  if container_exists "$foreign"; then
    say "прод-контейнер ${foreign}: существует, скрипт его не создавал и не останавливал"
  fi
done
step_done
