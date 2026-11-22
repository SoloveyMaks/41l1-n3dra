"""PostgreSQL-хранилище runtime.

Контрактная схема ``data_contract`` приходит из ``db/migrations`` через
ledger-мигратор (``backend.app.storage.migrate``) и не ослабляется. Таблицы API
живут отдельно в schema ``runtime``: колонки повторяют прежние SQLite-таблицы,
поэтому DTO не меняются, но каждая строка привязана к workspace, а
finding/node_run/report/event — ещё и к run.

Это compatibility layer, а не canonical C facts: ``analysis_bundles``,
``publish_bundle``, ``scan_outcomes`` и ``coverage`` здесь не пишутся и не
читаются, published/coverage не подделываются.

Настройки: ``DATABASE_URL`` (обязательно) и ``RUNTIME_WORKSPACE_ID`` (uuid,
необязательно). API пока не умеет выбирать workspace, поэтому процесс работает
в одном workspace и не отдаёт строки чужих workspace.
"""

import atexit
import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from backend.app.codefacts.identity import (
    WorkspaceConfigError,
    configured_workspace_id,
    workspace_id as identity_workspace_id,
)
from backend.app.storage.migrate import ensure_contract_schema, ensure_optional_schema

logger = logging.getLogger("RuntimeSchema")

RUNTIME_SCHEMA_PATH = Path(__file__).with_name("runtime_schema.sql")
RUNTIME_GUARDS_PATH = Path(__file__).with_name("runtime_guards.sql")
RUNTIME_WATCH_SCHEMA_PATH = Path(__file__).with_name("runtime_repository_watches.sql")
RUNTIME_HOOK_SCHEMA_PATH = Path(__file__).with_name("runtime_repository_hooks.sql")
NOTIFICATION_SCHEMA_PATH = Path(__file__).parents[1] / "notifications" / "schema.sql"
NOTIFICATION_GUARD_PATH = NOTIFICATION_SCHEMA_PATH.with_name("dispatch_guard.sql")
NOTIFICATION_DDL_LOCK = "nedra-completion-notifications-v1"
NOTIFICATION_CONTRACT_MARKER = "legacy_active_consent_purge_v1"
NOTIFICATION_DDL_WAIT_ATTEMPTS = 50
NOTIFICATION_DDL_WAIT_SECONDS = 0.1
NOTIFICATION_DDL_LOCK_TIMEOUT = "5s"
RUNTIME_SCHEMA_VERSION = 6
LEGACY_RUNTIME_SCHEMA = "runtime_legacy_v1"
RUNTIME_DDL_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"41l1-n3dra/runtime-ddl").digest()[:8], "big", signed=True
)

_driver: Optional[SimpleNamespace] = None
_pool: Any = None
_pool_lock = threading.Lock()


def _close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(_close_pool)


class DatabaseUnavailable(RuntimeError):
    """Понятный отказ: нет драйвера, DATABASE_URL, workspace или доступной БД."""


class ArtifactImmutableError(RuntimeError):
    """Попытка переписать уже сохранённый артефакт."""


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise DatabaseUnavailable(
            "DATABASE_URL не задан. Пример: postgresql://user:password@localhost:5432/41l1_n3dra "
            "(см. docs/postgres-runtime.md)."
        )
    return dsn


def _workspace_id() -> uuid.UUID:
    try:
        return uuid.UUID(identity_workspace_id())
    except WorkspaceConfigError as exc:
        raise DatabaseUnavailable(str(exc)) from exc
    except ValueError as exc:  # pragma: no cover - identity returns a valid UUID
        raise DatabaseUnavailable("workspace UUID не разобран") from exc


def _load_driver() -> SimpleNamespace:
    global _driver
    if _driver is None:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
            from psycopg_pool import ConnectionPool
        except ModuleNotFoundError as exc:
            raise DatabaseUnavailable(
                "Нет драйвера PostgreSQL (psycopg). Установите зависимости: "
                "pip install -r backend/requirements.txt"
            ) from exc
        _driver = SimpleNamespace(
            psycopg=psycopg, dict_row=dict_row, Jsonb=Jsonb, ConnectionPool=ConnectionPool
        )
    return _driver


def get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            driver = _load_driver()
            dsn = _dsn()
            pool = None
            try:
                # Отдельная проба, чтобы отказ показывал реальную причину,
                # а не общий таймаут пула.
                probe = driver.psycopg.connect(dsn, connect_timeout=5)
                probe.close()
                pool = driver.ConnectionPool(
                    dsn,
                    min_size=1,
                    max_size=10,
                    timeout=10,
                    kwargs={"row_factory": driver.dict_row},
                )
                pool.wait(timeout=10)
            except DatabaseUnavailable:
                raise
            except Exception as exc:
                if pool is not None:
                    pool.close()
                # Наружу только тип причины: текст драйвера может содержать DSN.
                raise DatabaseUnavailable(
                    "PostgreSQL недоступен по DATABASE_URL "
                    f"(причина: {type(exc).__name__}). Проверьте, что сервер запущен "
                    "и база существует."
                ) from None
            _pool = pool
    return _pool


def _jsonb(value: Any) -> Any:
    return _load_driver().Jsonb(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Строка БД -> прежний JSON: timestamptz в ISO, workspace_id наружу не идёт."""
    if row is None:
        return None
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in row.items()
        if k != "workspace_id"
    }


def _server_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Строка для server-only scheduler: workspace_id остаётся, UUID -> str.

    Эти методы никогда не вызываются из HTTP-хендлеров с клиентским вводом;
    workspace_id нужен только чтобы выставить ContextVar перед обработкой.
    """
    if row is None:
        return None
    result: Dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            result[key] = str(value)
        else:
            result[key] = value
    return result


def _json_text(value: Any) -> Optional[str]:
    """Совместимость: *_json поля раньше отдавались строкой."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


_COPY_COLUMNS = (
    ("workflows", "id, name, description, created_at, updated_at, latest_revision_id"),
    ("workflow_revisions", "id, workflow_id, revision_num, draft_json, created_at"),
    ("artifacts", "id, filename, hash_sha256, size_bytes, uncompressed_size_bytes, "
                  "file_count, kotlin_count, gradle_count, extracted_path, created_at"),
    ("runs", "id, workflow_id, revision_id, artifact_id, trigger_type, execution_status, "
             "security_verdict, total_findings, critical_count, high_count, medium_count, "
             "low_count, info_count, started_at, finished_at, created_at"),
    ("node_runs", "id, run_id, node_id, kind, status, started_at, finished_at, duration_ms, "
                  "error_message, input_data_json, output_data_json"),
    ("findings", "id, run_id, fingerprint, analyzer, rule_id, rule_version, severity, "
                 "severity_basis, confidence, confidence_basis, status, primary_location_json, "
                 "related_locations_json, trace_json, redacted_evidence, advisory_ids_json, "
                 "coverage_warnings_json"),
    ("reports", "run_id, verdict, passport_json, ai_summary, ai_items_json, ai_status, "
                "readable_markdown, official_json, created_at"),
    ("run_events", "seq, run_id, timestamp, type, node_id, payload_json"),
    ("schedules", "id, workflow_id, artifact_id, enabled, cron_or_interval, timezone, "
                  "last_run_at, next_fire_at, created_at"),
    ("deliveries", "id, run_id, channel, status, target, status_code, message, timestamp"),
)


def _exec_script(conn, path: Path) -> None:
    conn.execute(path.read_text(encoding="utf-8"))


def _create_schema_meta(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS runtime.schema_meta (version integer PRIMARY KEY)")


_NOTIFICATION_READY_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM pg_attribute AS a
          JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE a.attrelid = to_regclass('runtime.completion_notifications')
           AND a.attname = 'retry_cancelled' AND NOT a.attisdropped
           AND a.atttypid = 'boolean'::regtype AND a.attnotnull
           AND pg_get_expr(d.adbin, d.adrelid) IN ('false', '''f''::boolean')
    ) AND EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid = to_regclass('runtime.runs')
           AND tgname = 'lock_run_completion_dispatch'
           AND NOT tgisinternal AND tgenabled = 'O'
           AND tgtype = 23
           AND tgfoid = to_regprocedure('runtime.lock_run_completion_dispatch()')
    ) AND EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid = to_regclass('runtime.runs')
           AND tgname = 'capture_run_completion'
           AND NOT tgisinternal AND tgenabled = 'O'
           AND tgtype = 21
           AND tgfoid = to_regprocedure('runtime.capture_run_completion()')
    ) AS ready
"""


def _notification_contract_ready(conn) -> bool:
    """True when the exact installed contract needs no further DDL.

    Checks the column type/nullability/default ``false``, the applied forward
    marker row and the enabled trigger/function pairs. This is a catalog-only
    read: it takes no advisory lock and runs no DDL, so a normal startup cannot
    block run completion or dispatch.
    """
    row = conn.execute(_NOTIFICATION_READY_SQL).fetchone()
    if not row or not row["ready"]:
        return False
    present = conn.execute(
        "SELECT to_regclass('runtime.notification_migrations') IS NOT NULL AS present"
    ).fetchone()
    if not present or not present["present"]:
        return False
    marker = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM runtime.notification_migrations WHERE name = %s) AS applied",
        (NOTIFICATION_CONTRACT_MARKER,),
    ).fetchone()
    return bool(marker and marker["applied"])


def _wait_notification_ddl_lock(conn) -> None:
    """Take the notification DDL lock with a bounded wait, then fail closed."""
    for _ in range(NOTIFICATION_DDL_WAIT_ATTEMPTS):
        locked = conn.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS locked",
            (NOTIFICATION_DDL_LOCK,),
        ).fetchone()["locked"]
        if locked:
            return
        conn.execute("SELECT pg_sleep(%s)", (NOTIFICATION_DDL_WAIT_SECONDS,))
    raise DatabaseUnavailable(
        "Notification schema lock не получен за 5s; startup остановлен"
    )


def _apply_notification_schema(conn, scripts: List[str]) -> None:
    if _notification_contract_ready(conn):
        return
    _wait_notification_ddl_lock(conn)
    # Another startup may have installed the contract while this one waited.
    if _notification_contract_ready(conn):
        return
    previous_timeout = conn.execute(
        "SELECT current_setting('lock_timeout') AS value"
    ).fetchone()["value"]
    conn.execute(
        "SELECT set_config('lock_timeout', %s, true)",
        (NOTIFICATION_DDL_LOCK_TIMEOUT,),
    )
    try:
        for script in scripts:
            conn.execute(script)
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "55P03":
            raise DatabaseUnavailable(
                "Notification DDL не получил lock за 5s; startup остановлен"
            ) from None
        raise
    conn.execute(
        "SELECT set_config('lock_timeout', %s, true)",
        (previous_timeout,),
    )
    if not _notification_contract_ready(conn):
        raise DatabaseUnavailable("Notification schema/dispatch guards не установлены; startup остановлен")


def initialize_notification_schema(conn=None) -> None:
    """Install the notification base schema and its forward dispatch guard.

    ``conn`` is the caller's transaction (``init_db``): it is used as-is, so
    runtime version 6 and both scripts commit or roll back together, and this
    function never commits or closes it.  Without ``conn`` an owned pooled
    connection runs the same statements and commits on its way out.  A caller
    that finds the marker contract already installed gets catalog reads only.
    """
    # Read both files before touching the database: an incomplete notification
    # package must fail the startup before any transaction or lock is opened.
    scripts = [path.read_text(encoding="utf-8") for path in
               (NOTIFICATION_SCHEMA_PATH, NOTIFICATION_GUARD_PATH)]
    if conn is None:
        with get_pool().connection() as owned:
            _apply_notification_schema(owned, scripts)
        return
    _apply_notification_schema(conn, scripts)


def _install_fresh_runtime(conn) -> None:
    _exec_script(conn, RUNTIME_SCHEMA_PATH)
    _exec_script(conn, RUNTIME_GUARDS_PATH)
    _exec_script(conn, RUNTIME_WATCH_SCHEMA_PATH)
    _exec_script(conn, RUNTIME_HOOK_SCHEMA_PATH)
    conn.execute(
        "INSERT INTO runtime.schema_meta (version) VALUES (%s)",
        (RUNTIME_SCHEMA_VERSION,),
    )


def _upgrade_legacy_runtime(conn, workspace_id: uuid.UUID) -> None:
    """v1 (819f4de, без workspace) -> текущая версия без потери строк.

    Старые таблицы не удаляются: schema переименовывается в
    ``runtime_legacy_v1`` и остаётся бэкапом для разбора.
    """
    taken = conn.execute(
        "SELECT count(*) AS n FROM pg_namespace WHERE nspname = %s",
        (LEGACY_RUNTIME_SCHEMA,),
    ).fetchone()["n"]
    if taken:
        raise DatabaseUnavailable(
            f"Схема {LEGACY_RUNTIME_SCHEMA} уже существует: автоматическое обновление "
            "runtime остановлено, разберите dev-базу вручную."
        )
    conn.execute(f"ALTER SCHEMA runtime RENAME TO {LEGACY_RUNTIME_SCHEMA}")
    conn.execute("CREATE SCHEMA runtime")
    _create_schema_meta(conn)
    _exec_script(conn, RUNTIME_SCHEMA_PATH)
    for table, columns in _COPY_COLUMNS:
        conn.execute(
            f"INSERT INTO runtime.{table} (workspace_id, {columns}) "
            f"SELECT %s, {columns} FROM {LEGACY_RUNTIME_SCHEMA}.{table}",
            (workspace_id,),
        )
    conn.execute(
        "SELECT setval(pg_get_serial_sequence('runtime.run_events', 'seq'), "
        "COALESCE((SELECT max(seq) FROM runtime.run_events), 1), "
        "EXISTS (SELECT 1 FROM runtime.run_events))"
    )
    _exec_script(conn, RUNTIME_GUARDS_PATH)
    _exec_script(conn, RUNTIME_WATCH_SCHEMA_PATH)
    _exec_script(conn, RUNTIME_HOOK_SCHEMA_PATH)
    conn.execute(
        "INSERT INTO runtime.schema_meta (version) VALUES (%s)",
        (RUNTIME_SCHEMA_VERSION,),
    )
    logger.warning(
        "runtime schema обновлена до v%s; старые таблицы сохранены в %s",
        RUNTIME_SCHEMA_VERSION,
        LEGACY_RUNTIME_SCHEMA,
    )


def init_db(workspace_id: uuid.UUID) -> None:
    """Применить C-миграции и создать/обновить runtime schema."""
    pool = get_pool()
    ensure_contract_schema(_dsn())
    ensure_optional_schema(_dsn())
    with pool.connection() as conn:
        # Serialize runtime workspace row + DDL for parallel startups on an empty DB.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (RUNTIME_DDL_LOCK_KEY,))
        # Идентичность workspace живёт в C-схеме; runtime пишет только эту строку.
        conn.execute(
            "INSERT INTO data_contract.workspaces (workspace_id, name) VALUES (%s, %s) "
            "ON CONFLICT (workspace_id) DO NOTHING",
            (workspace_id, f"runtime:{workspace_id}"),
        )
        conn.execute("CREATE SCHEMA IF NOT EXISTS runtime")
        _create_schema_meta(conn)
        meta = conn.execute("SELECT max(version) AS version FROM runtime.schema_meta").fetchone()
        version = meta["version"] if meta else None
        if version is None:
            legacy = conn.execute(
                "SELECT count(*) AS tables FROM pg_tables "
                "WHERE schemaname = 'runtime' AND tablename <> 'schema_meta'"
            ).fetchone()["tables"]
            if legacy:
                _upgrade_legacy_runtime(conn, workspace_id)
            else:
                _install_fresh_runtime(conn)
        elif version == 2:
            # v2 = workspace isolation; v3 добавляет immutability-триггеры,
            # v4 — durable repository watches, v5 — push-доставки.
            _exec_script(conn, RUNTIME_GUARDS_PATH)
            _exec_script(conn, RUNTIME_WATCH_SCHEMA_PATH)
            _exec_script(conn, RUNTIME_HOOK_SCHEMA_PATH)
            conn.execute(
                "UPDATE runtime.schema_meta SET version = %s",
                (RUNTIME_SCHEMA_VERSION,),
            )
        elif version == 3:
            _exec_script(conn, RUNTIME_WATCH_SCHEMA_PATH)
            _exec_script(conn, RUNTIME_HOOK_SCHEMA_PATH)
            conn.execute(
                "UPDATE runtime.schema_meta SET version = %s",
                (RUNTIME_SCHEMA_VERSION,),
            )
        elif version == 4:
            _exec_script(conn, RUNTIME_HOOK_SCHEMA_PATH)
            conn.execute(
                "UPDATE runtime.schema_meta SET version = %s",
                (RUNTIME_SCHEMA_VERSION,),
            )
        elif version == 5:
            conn.execute(
                "UPDATE runtime.schema_meta SET version = %s",
                (RUNTIME_SCHEMA_VERSION,),
            )
        elif version != RUNTIME_SCHEMA_VERSION:
            raise DatabaseUnavailable(
                f"runtime schema version={version}, ожидается {RUNTIME_SCHEMA_VERSION}. "
                "Нужна миграция runtime-слоя, молча работать на чужой версии нельзя."
            )
        # Version and both notification scripts commit together. The later
        # NotificationRuntime initializer calls the same helper without conn.
        initialize_notification_schema(conn)


class DatabaseRepository:
    def __init__(self, initialize: bool = True):
        # init_db всегда на явном env workspace: аккаунты создают свои
        # workspace-строки при регистрации, а не через runtime-инициализацию.
        # Read-only tools pass initialize=False so a dry-run cannot run DDL or
        # insert a workspace row.
        self._default_workspace_id = uuid.UUID(configured_workspace_id())
        if initialize:
            init_db(self._default_workspace_id)

    @property
    def workspace_id(self) -> uuid.UUID:
        """Текущий workspace: request/background scope или env default.

        Свойство, а не поле: один общий db_repo обслуживает все аккаунты, но
        каждый запрос/задача видит только свой workspace. Scheduler/demo без
        scope остаются на env workspace — это явный demo-режим.
        """
        return uuid.UUID(identity_workspace_id())

    # Workflow operations
    def save_workflow(self, workflow_id: str, name: str, description: str, draft: Dict[str, Any]) -> str:
        ws = self.workspace_id
        now = _now()
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.workflows (workspace_id, id, name, description, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, id) DO UPDATE
                    SET name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        updated_at = EXCLUDED.updated_at
                """,
                (ws, workflow_id, name, description, now, now),
            )
            # Сериализуем выдачу revision_num для одного workflow.
            conn.execute(
                "SELECT 1 FROM runtime.workflows WHERE workspace_id = %s AND id = %s FOR UPDATE",
                (ws, workflow_id),
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(revision_num), 0) AS max_rev "
                "FROM runtime.workflow_revisions WHERE workspace_id = %s AND workflow_id = %s",
                (ws, workflow_id),
            ).fetchone()
            new_rev_num = int(row["max_rev"]) + 1
            rev_id = f"rev_{workflow_id}_{new_rev_num}"

            conn.execute(
                """
                INSERT INTO runtime.workflow_revisions (
                    workspace_id, id, workflow_id, revision_num, draft_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (ws, rev_id, workflow_id, new_rev_num, _jsonb(draft), now),
            )
            conn.execute(
                "UPDATE runtime.workflows SET latest_revision_id = %s "
                "WHERE workspace_id = %s AND id = %s",
                (rev_id, ws, workflow_id),
            )
            return rev_id

    def save_workflow_if_latest(
        self,
        workflow_id: str,
        expected_revision_id: Optional[str],
        name: str,
        description: str,
        draft: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create the next revision only if the active revision is unchanged.

        `expected_revision_id` is read and compared under a row lock in the same
        transaction that inserts the revision. On a conflict the current latest
        revision and its draft are returned from the same transaction, so the
        caller can re-evaluate without racing a second read.

        Result: `revision_id` is the new revision on success, otherwise None.
        `latest_revision_id` and `draft` describe the current winner on conflict.
        `expected_revision_id=None` means "workflow must not exist yet" (or must
        still have no active revision), which lets the explicit create path race
        safely too.
        """
        ws = self.workspace_id
        now = _now()
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT latest_revision_id FROM runtime.workflows "
                "WHERE workspace_id = %s AND id = %s FOR UPDATE",
                (ws, workflow_id),
            ).fetchone()
            if row is None:
                if expected_revision_id is not None:
                    return {"revision_id": None, "latest_revision_id": None, "draft": None}
                conn.execute(
                    """
                    INSERT INTO runtime.workflows (
                        workspace_id, id, name, description, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (ws, workflow_id, name, description, now, now),
                )
            elif row["latest_revision_id"] != expected_revision_id:
                latest = row["latest_revision_id"]
                current_draft = None
                if latest:
                    revision = conn.execute(
                        "SELECT draft_json FROM runtime.workflow_revisions "
                        "WHERE workspace_id = %s AND id = %s",
                        (ws, latest),
                    ).fetchone()
                    if revision:
                        current_draft = revision["draft_json"]
                return {
                    "revision_id": None,
                    "latest_revision_id": latest,
                    "draft": current_draft,
                }

            max_row = conn.execute(
                "SELECT COALESCE(MAX(revision_num), 0) AS max_rev "
                "FROM runtime.workflow_revisions WHERE workspace_id = %s AND workflow_id = %s",
                (ws, workflow_id),
            ).fetchone()
            new_rev_num = int(max_row["max_rev"]) + 1
            rev_id = f"rev_{workflow_id}_{new_rev_num}"
            conn.execute(
                """
                INSERT INTO runtime.workflow_revisions (
                    workspace_id, id, workflow_id, revision_num, draft_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (ws, rev_id, workflow_id, new_rev_num, _jsonb(draft), now),
            )
            conn.execute(
                """
                UPDATE runtime.workflows
                SET name = %s, description = %s, updated_at = %s, latest_revision_id = %s
                WHERE workspace_id = %s AND id = %s
                """,
                (name, description, now, rev_id, ws, workflow_id),
            )
            return {
                "revision_id": rev_id,
                "latest_revision_id": rev_id,
                "draft": draft,
            }

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        ws = self.workspace_id
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.workflows WHERE workspace_id = %s AND id = %s",
                (ws, workflow_id),
            ).fetchone()
            if not row:
                return None
            res = _row(row)
            if res.get("latest_revision_id"):
                rev = conn.execute(
                    "SELECT draft_json FROM runtime.workflow_revisions "
                    "WHERE workspace_id = %s AND id = %s",
                    (ws, res["latest_revision_id"]),
                ).fetchone()
                if rev:
                    res["draft"] = rev["draft_json"]
            return res

    def list_workflows(self) -> List[Dict[str, Any]]:
        ws = self.workspace_id
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT w.*, r.draft_json AS draft
                FROM runtime.workflows w
                LEFT JOIN runtime.workflow_revisions r
                    ON r.workspace_id = w.workspace_id AND r.id = w.latest_revision_id
                WHERE w.workspace_id = %s
                ORDER BY w.updated_at DESC
                """,
                (ws,),
            ).fetchall()
            results = []
            for row in rows:
                item = _row(dict(row))
                if item.get("draft") is None:
                    item.pop("draft", None)
                results.append(item)
            return results

    def get_workflow_revision(self, revision_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.workflow_revisions "
                "WHERE workspace_id = %s AND id = %s",
                (self.workspace_id, revision_id),
            ).fetchone()
            if not row:
                return None
            res = _row(row)
            res["draft"] = res.get("draft_json")
            res["draft_json"] = _json_text(res.get("draft_json"))
            return res

    # Artifact operations
    def save_artifact(self, manifest: Dict[str, Any]) -> None:
        wanted = (
            manifest["hash_sha256"], manifest["size_bytes"],
            manifest["uncompressed_size_bytes"], manifest["extracted_path"],
        )
        with get_pool().connection() as conn:
            inserted = conn.execute(
                """
                INSERT INTO runtime.artifacts (
                    workspace_id, id, filename, hash_sha256, size_bytes, uncompressed_size_bytes,
                    file_count, kotlin_count, gradle_count, extracted_path, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, id) DO NOTHING
                RETURNING id
                """,
                (
                    self.workspace_id, manifest["id"], manifest["filename"],
                    manifest["hash_sha256"], manifest["size_bytes"],
                    manifest["uncompressed_size_bytes"], manifest["file_count"],
                    manifest["kotlin_file_count"], manifest["gradle_file_count"],
                    manifest["extracted_path"], manifest["created_at"],
                ),
            ).fetchone()
            if inserted is not None:
                return
            existing = conn.execute(
                "SELECT hash_sha256, size_bytes, uncompressed_size_bytes, extracted_path "
                "FROM runtime.artifacts WHERE workspace_id = %s AND id = %s",
                (self.workspace_id, manifest["id"]),
            ).fetchone()
            current = None if existing is None else (
                existing["hash_sha256"], existing["size_bytes"],
                existing["uncompressed_size_bytes"], existing["extracted_path"],
            )
            if current != wanted:
                raise ArtifactImmutableError(
                    f"Артефакт {manifest['id']} уже сохранён с другими данными; "
                    "перезапись запрещена."
                )

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.artifacts WHERE workspace_id = %s AND id = %s",
                (self.workspace_id, artifact_id),
            ).fetchone()
            return _row(row)

    def list_artifacts(self) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.artifacts WHERE workspace_id = %s ORDER BY created_at DESC",
                (self.workspace_id,),
            ).fetchall()
            return [_row(r) for r in rows]

    # Run operations
    def create_run(self, run_id: str, workflow_id: str, revision_id: str,
                   artifact_id: Optional[str], trigger_type: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.runs (
                    workspace_id, id, workflow_id, revision_id, artifact_id, trigger_type,
                    execution_status, security_verdict, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', 'pending', %s)
                """,
                (self.workspace_id, run_id, workflow_id, revision_id, artifact_id,
                 trigger_type, _now()),
            )

    def update_run_status(self, run_id: str, execution_status: str, security_verdict: Optional[str] = None,
                          started_at: Optional[str] = None, finished_at: Optional[str] = None,
                          findings_summary: Optional[Dict[str, int]] = None) -> None:
        sets = ["execution_status = %s"]
        params: List[Any] = [execution_status]
        if security_verdict is not None:
            sets.append("security_verdict = %s")
            params.append(security_verdict)
        if started_at is not None:
            sets.append("started_at = NULLIF(%s, '')::timestamptz")
            params.append(started_at)
        if finished_at is not None:
            sets.append("finished_at = NULLIF(%s, '')::timestamptz")
            params.append(finished_at)
        if findings_summary is not None:
            sets.extend([
                "total_findings = %s", "critical_count = %s", "high_count = %s",
                "medium_count = %s", "low_count = %s", "info_count = %s",
            ])
            params.extend([
                findings_summary.get("total", 0),
                findings_summary.get("critical", 0),
                findings_summary.get("high", 0),
                findings_summary.get("medium", 0),
                findings_summary.get("low", 0),
                findings_summary.get("info", 0),
            ])
        params.extend([self.workspace_id, run_id])
        with get_pool().connection() as conn:
            conn.execute(
                f"UPDATE runtime.runs SET {', '.join(sets)} WHERE workspace_id = %s AND id = %s",
                params,
            )

    def claim_run_start(self, run_id: str, started_at: str) -> bool:
        """Atomically promote a queued run to running.

        Only the caller that wins the transition gets True. A second dispatcher
        that acquired the same run through a stale watch lease gets False and
        must not execute the run again.
        """
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.runs
                   SET execution_status = 'running',
                       started_at = NULLIF(%s, '')::timestamptz
                 WHERE workspace_id = %s AND id = %s
                   AND execution_status = 'queued'
                 RETURNING id
                """,
                (started_at, self.workspace_id, run_id),
            ).fetchone()
            return row is not None

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        ws = self.workspace_id
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.runs WHERE workspace_id = %s AND id = %s",
                (ws, run_id),
            ).fetchone()
            if not row:
                return None
            res = _row(row)
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM runtime.run_events "
                "WHERE workspace_id = %s AND run_id = %s",
                (ws, run_id),
            ).fetchone()
            res["last_event_seq"] = int(seq_row["max_seq"])
            return res

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.runs WHERE workspace_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (self.workspace_id, limit),
            ).fetchall()
            return [_row(r) for r in rows]

    # Node Run operations
    def save_node_run(self, node_run: Dict[str, Any]) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.node_runs (
                    workspace_id, run_id, id, node_id, kind, status, started_at, finished_at,
                    duration_ms, error_message, input_data_json, output_data_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, run_id, id) DO UPDATE SET
                    node_id = EXCLUDED.node_id,
                    kind = EXCLUDED.kind,
                    status = EXCLUDED.status,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    duration_ms = EXCLUDED.duration_ms,
                    error_message = EXCLUDED.error_message,
                    input_data_json = EXCLUDED.input_data_json,
                    output_data_json = EXCLUDED.output_data_json
                """,
                (
                    self.workspace_id, node_run["run_id"], node_run["id"],
                    node_run["node_id"], node_run["kind"], node_run["status"],
                    node_run.get("started_at"), node_run.get("finished_at"),
                    node_run.get("duration_ms", 0.0), node_run.get("error_message"),
                    _jsonb(node_run.get("input_data_masked", {})),
                    _jsonb(node_run.get("output_data_masked", {})),
                ),
            )

    def get_node_runs(self, run_id: str) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.node_runs WHERE workspace_id = %s AND run_id = %s "
                "ORDER BY started_at ASC NULLS FIRST",
                (self.workspace_id, run_id),
            ).fetchall()
            results = []
            for row in rows:
                item = _row(row)
                item["input_data_masked"] = item.get("input_data_json") or {}
                item["output_data_masked"] = item.get("output_data_json") or {}
                item["input_data_json"] = _json_text(item.get("input_data_json"))
                item["output_data_json"] = _json_text(item.get("output_data_json"))
                results.append(item)
            return results

    # Events & SSE
    def add_run_event(self, run_id: str, event_type: str, payload: Dict[str, Any],
                      node_id: Optional[str] = None) -> int:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO runtime.run_events (
                    workspace_id, run_id, timestamp, type, node_id, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING seq
                """,
                (self.workspace_id, run_id, _now(), event_type, node_id, _jsonb(payload)),
            ).fetchone()
            return int(row["seq"])

    def get_run_events(self, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.run_events "
                "WHERE workspace_id = %s AND run_id = %s AND seq > %s ORDER BY seq ASC",
                (self.workspace_id, run_id, after_seq),
            ).fetchall()
            results = []
            for row in rows:
                item = _row(row)
                item["payload"] = item.get("payload_json")
                item["payload_json"] = _json_text(item.get("payload_json"))
                results.append(item)
            return results

    # Findings
    def save_findings(self, run_id: str, findings: List[Dict[str, Any]]) -> None:
        with get_pool().connection() as conn:
            for f in findings:
                conn.execute(
                    """
                    INSERT INTO runtime.findings (
                        workspace_id, run_id, id, fingerprint, analyzer, rule_id, rule_version,
                        severity, severity_basis, confidence, confidence_basis, status,
                        primary_location_json, related_locations_json, trace_json,
                        redacted_evidence, advisory_ids_json, coverage_warnings_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, run_id, id) DO UPDATE SET
                        fingerprint = EXCLUDED.fingerprint,
                        analyzer = EXCLUDED.analyzer,
                        rule_id = EXCLUDED.rule_id,
                        rule_version = EXCLUDED.rule_version,
                        severity = EXCLUDED.severity,
                        severity_basis = EXCLUDED.severity_basis,
                        confidence = EXCLUDED.confidence,
                        confidence_basis = EXCLUDED.confidence_basis,
                        status = EXCLUDED.status,
                        primary_location_json = EXCLUDED.primary_location_json,
                        related_locations_json = EXCLUDED.related_locations_json,
                        trace_json = EXCLUDED.trace_json,
                        redacted_evidence = EXCLUDED.redacted_evidence,
                        advisory_ids_json = EXCLUDED.advisory_ids_json,
                        coverage_warnings_json = EXCLUDED.coverage_warnings_json
                    """,
                    (
                        self.workspace_id, run_id, f["id"], f["fingerprint"],
                        f["analyzer"], f["ruleId"], f.get("ruleVersion", "1.0.0"),
                        f["severity"], f["severityBasis"],
                        f["confidence"], f["confidenceBasis"], f.get("status", "confirmed"),
                        _jsonb(f["primaryLocation"]),
                        _jsonb(f.get("relatedLocations", [])),
                        _jsonb(f.get("trace", [])),
                        f.get("redactedEvidence", ""),
                        _jsonb(f.get("advisoryIds", [])),
                        _jsonb(f.get("coverageWarnings", [])),
                    ),
                )

    def get_findings(self, run_id: str) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.findings WHERE workspace_id = %s AND run_id = %s",
                (self.workspace_id, run_id),
            ).fetchall()
            results = []
            for row in rows:
                item = _row(row)
                item["primaryLocation"] = item.get("primary_location_json")
                item["relatedLocations"] = item.get("related_locations_json") or []
                item["trace"] = item.get("trace_json") or []
                item["advisoryIds"] = item.get("advisory_ids_json") or []
                item["coverageWarnings"] = item.get("coverage_warnings_json") or []
                item["ruleId"] = item["rule_id"]
                item["ruleVersion"] = item["rule_version"]
                item["severityBasis"] = item["severity_basis"]
                item["confidenceBasis"] = item["confidence_basis"]
                item["redactedEvidence"] = item["redacted_evidence"]
                for key in (
                    "primary_location_json", "related_locations_json", "trace_json",
                    "advisory_ids_json", "coverage_warnings_json",
                ):
                    item[key] = _json_text(item.get(key))
                results.append(item)
            return results

    # Reports
    def save_report(self, run_id: str, verdict: str, passport: Dict[str, Any],
                    ai_summary: str, ai_items: List[Dict[str, Any]], ai_status: str,
                    readable_markdown: str, official_json: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.reports (
                    workspace_id, run_id, verdict, passport_json, ai_summary, ai_items_json,
                    ai_status, readable_markdown, official_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, run_id) DO UPDATE SET
                    verdict = EXCLUDED.verdict,
                    passport_json = EXCLUDED.passport_json,
                    ai_summary = EXCLUDED.ai_summary,
                    ai_items_json = EXCLUDED.ai_items_json,
                    ai_status = EXCLUDED.ai_status,
                    readable_markdown = EXCLUDED.readable_markdown,
                    official_json = EXCLUDED.official_json,
                    created_at = EXCLUDED.created_at
                """,
                (
                    self.workspace_id, run_id, verdict, _jsonb(passport), ai_summary,
                    _jsonb(ai_items), ai_status, readable_markdown, official_json, _now(),
                ),
            )

    def get_report(self, run_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.reports WHERE workspace_id = %s AND run_id = %s",
                (self.workspace_id, run_id),
            ).fetchone()
            if not row:
                return None
            res = _row(row)
            res["passport"] = res.get("passport_json")
            res["ai_items"] = res.get("ai_items_json") or []
            res["passport_json"] = _json_text(res.get("passport_json"))
            res["ai_items_json"] = _json_text(res.get("ai_items_json"))
            return res

    # Schedules
    def save_schedule(self, schedule_id: str, workflow_id: str, artifact_id: str,
                      cron_or_interval: str, enabled: bool = True, timezone_str: str = "UTC") -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.schedules (
                    workspace_id, id, workflow_id, artifact_id, enabled,
                    cron_or_interval, timezone, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, id) DO UPDATE SET
                    workflow_id = EXCLUDED.workflow_id,
                    artifact_id = EXCLUDED.artifact_id,
                    enabled = EXCLUDED.enabled,
                    cron_or_interval = EXCLUDED.cron_or_interval,
                    timezone = EXCLUDED.timezone
                """,
                (self.workspace_id, schedule_id, workflow_id, artifact_id,
                 1 if enabled else 0, cron_or_interval, timezone_str, _now()),
            )

    def list_schedules(self) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.schedules WHERE workspace_id = %s",
                (self.workspace_id,),
            ).fetchall()
            return [_row(r) for r in rows]

    def claim_due_schedules_server(self, limit: int,
                                   lease_seconds: int) -> List[Dict[str, Any]]:
        """Lease due schedules across all workspaces; returns workspace_id.

        ``next_fire_at`` temporarily becomes a lease deadline. If the process
        dies before settling, the row becomes due again after the lease and the
        run is recovered by ``list_queued_schedule_runs_server``.
        """
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                WITH due AS (
                    SELECT workspace_id, id
                      FROM runtime.schedules
                     WHERE enabled = 1
                       AND (next_fire_at IS NULL OR next_fire_at <= now())
                     ORDER BY COALESCE(next_fire_at, created_at)
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE runtime.schedules s
                   SET next_fire_at = now() + make_interval(secs => %s)
                  FROM due
                 WHERE s.workspace_id = due.workspace_id AND s.id = due.id
                 RETURNING s.*
                """,
                (limit, lease_seconds),
            ).fetchall()
            return [_server_row(r) for r in rows]

    def create_scheduled_run(self, run_id: str, schedule_id: str,
                             workflow_id: str, revision_id: str,
                             artifact_id: str, last_run_at: str,
                             next_fire_at: str) -> bool:
        """Insert queued run and settle the claimed schedule in one transaction."""
        with get_pool().connection() as conn:
            settled = conn.execute(
                """
                UPDATE runtime.schedules
                   SET last_run_at = NULLIF(%s, '')::timestamptz,
                       next_fire_at = NULLIF(%s, '')::timestamptz
                 WHERE workspace_id = %s AND id = %s AND enabled = 1
                 RETURNING id
                """,
                (last_run_at, next_fire_at, self.workspace_id, schedule_id),
            ).fetchone()
            if settled is None:
                return False
            conn.execute(
                """
                INSERT INTO runtime.runs (
                    workspace_id, id, workflow_id, revision_id, artifact_id, trigger_type,
                    execution_status, security_verdict, created_at
                ) VALUES (%s, %s, %s, %s, %s, 'schedule', 'queued', 'pending', %s)
                """,
                (self.workspace_id, run_id, workflow_id, revision_id,
                 artifact_id, _now()),
            )
            return True

    def list_queued_schedule_runs_server(self, limit: int,
                                         older_than_seconds: int = 5) -> List[Dict[str, Any]]:
        """Queued schedule runs across all workspaces for crash recovery."""
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT workspace_id, id, workflow_id, revision_id, artifact_id,
                       trigger_type, created_at
                  FROM runtime.runs
                 WHERE execution_status = 'queued'
                   AND trigger_type = 'schedule'
                   AND created_at <= now() - make_interval(secs => %s)
                 ORDER BY created_at
                 LIMIT %s
                """,
                (older_than_seconds, limit),
            ).fetchall()
            return [_server_row(r) for r in rows]

    def update_schedule_run_time(self, schedule_id: str, last_run: str, next_fire: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                "UPDATE runtime.schedules "
                "SET last_run_at = NULLIF(%s, '')::timestamptz, "
                "    next_fire_at = NULLIF(%s, '')::timestamptz "
                "WHERE workspace_id = %s AND id = %s",
                (last_run, next_fire, self.workspace_id, schedule_id),
            )

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        """Pause/resume one schedule in this workspace; resume recalculates from now."""
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.schedules
                   SET enabled = %s,
                       next_fire_at = CASE WHEN %s THEN NULL ELSE next_fire_at END
                 WHERE workspace_id = %s AND id = %s
                 RETURNING *
                """,
                (1 if enabled else 0, bool(enabled), self.workspace_id, schedule_id),
            ).fetchone()
            return _row(row)

    # Repository watches
    def create_repository_watch(self, watch_id: str, workflow_id: str, url: str,
                                provider: str, branch: str, cron_or_interval: str,
                                next_check_at: Optional[str], baseline_commit: str,
                                checked_at: str, trigger_mode: str = "poll") -> None:
        now = _now()
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.repository_watches (
                    workspace_id, id, workflow_id, url, provider, branch,
                    cron_or_interval, trigger_mode, enabled, next_check_at,
                    last_checked_at, last_seen_commit, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, %s)
                """,
                (self.workspace_id, watch_id, workflow_id, url, provider, branch,
                 cron_or_interval, trigger_mode, next_check_at, checked_at,
                 baseline_commit, now, now),
            )

    def get_repository_watch(self, watch_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.repository_watches WHERE workspace_id = %s AND id = %s",
                (self.workspace_id, watch_id),
            ).fetchone()
            return _row(row)

    def find_repository_watches_by_id_server(self, watch_id: str) -> List[Dict[str, Any]]:
        """Resolve a public hook id without a cookie; server-only.

        The id is not globally unique in the schema (PK is workspace+id), so the
        caller must reject ambiguous results instead of guessing a tenant.
        """
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.repository_watches WHERE id = %s LIMIT 2",
                (watch_id,),
            ).fetchall()
            return [_server_row(r) for r in rows]

    def list_repository_watches(self) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.repository_watches WHERE workspace_id = %s "
                "ORDER BY created_at DESC",
                (self.workspace_id,),
            ).fetchall()
            return [_row(r) for r in rows]

    def set_repository_watch_enabled(self, watch_id: str, enabled: bool,
                                     next_check_at: Optional[str]) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET enabled = %s,
                       next_check_at = CASE WHEN %s THEN %s::timestamptz ELSE next_check_at END,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                 RETURNING *
                """,
                (bool(enabled), bool(enabled), next_check_at, self.workspace_id, watch_id),
            ).fetchone()
            return _row(row)

    def claim_repository_watch(self, watch_id: str, token: str,
                               timeout_seconds: int) -> Optional[Dict[str, Any]]:
        """Atomic per-watch claim; expired claims are recoverable by time."""
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET claim_token = %s,
                       claimed_until = now() + make_interval(secs => %s),
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                   AND (claimed_until IS NULL OR claimed_until < now())
                 RETURNING *
                """,
                (token, timeout_seconds, self.workspace_id, watch_id),
            ).fetchone()
            return _row(row)

    def claim_due_repository_watches_server(self, limit: int, token: str,
                                            timeout_seconds: int) -> List[Dict[str, Any]]:
        """Claim due watches across all workspaces; server-only scheduler entry.

        Returns ``workspace_id`` so the caller can set ContextVar per row before
        any tenant-scoped read/write.
        """
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                WITH due AS (
                    SELECT workspace_id, id
                      FROM runtime.repository_watches
                     WHERE enabled
                       AND trigger_mode = 'poll'
                       AND (next_check_at IS NULL OR next_check_at <= now())
                       AND (claimed_until IS NULL OR claimed_until < now())
                     ORDER BY COALESCE(next_check_at, created_at)
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE runtime.repository_watches w
                   SET claim_token = %s,
                       claimed_until = now() + make_interval(secs => %s),
                       updated_at = now()
                  FROM due
                 WHERE w.workspace_id = due.workspace_id AND w.id = due.id
                 RETURNING w.*
                """,
                (limit, token, timeout_seconds),
            ).fetchall()
            return [_server_row(r) for r in rows]

    def finish_repository_watch_unchanged(self, watch_id: str, token: str,
                                          checked_at: str, next_check_at: str,
                                          last_seen_commit: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET last_checked_at = %s,
                       next_check_at = %s,
                       last_seen_commit = COALESCE(%s, last_seen_commit),
                       last_error = NULL,
                       claim_token = NULL,
                       claimed_until = NULL,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s AND claim_token = %s
                 RETURNING *
                """,
                (checked_at, next_check_at, last_seen_commit,
                 self.workspace_id, watch_id, token),
            ).fetchone()
            return _row(row)

    def fail_repository_watch(self, watch_id: str, token: str, error: str,
                              checked_at: str, next_check_at: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET last_checked_at = %s,
                       next_check_at = %s,
                       last_error = %s,
                       claim_token = NULL,
                       claimed_until = NULL,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s AND claim_token = %s
                 RETURNING *
                """,
                (checked_at, next_check_at, error[:500],
                 self.workspace_id, watch_id, token),
            ).fetchone()
            return _row(row)

    def repository_watch_claim_commit(self, watch_id: str, commit_sha: str,
                                      token: str, timeout_seconds: int) -> Optional[Dict[str, Any]]:
        """Claim one watch+sha. failed/stale processing rows can be retried."""
        now = _now()
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO runtime.repository_watch_commits AS c (
                    workspace_id, watch_id, commit_sha, state, claim_token,
                    claimed_until, created_at, updated_at
                ) VALUES (%s, %s, %s, 'processing', %s,
                          now() + make_interval(secs => %s), %s, %s)
                ON CONFLICT (workspace_id, watch_id, commit_sha) DO UPDATE
                   SET state = 'processing',
                       claim_token = EXCLUDED.claim_token,
                       claimed_until = EXCLUDED.claimed_until,
                       error = NULL,
                       updated_at = now()
                 WHERE c.state = 'failed'
                    OR (c.state = 'processing'
                        AND (c.claimed_until IS NULL OR c.claimed_until < now()))
                RETURNING c.*
                """,
                (self.workspace_id, watch_id, commit_sha, token,
                 timeout_seconds, now, now),
            ).fetchone()
            return _row(row)

    def get_repository_watch_commit(self, watch_id: str,
                                    commit_sha: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.repository_watch_commits "
                "WHERE workspace_id = %s AND watch_id = %s AND commit_sha = %s",
                (self.workspace_id, watch_id, commit_sha),
            ).fetchone()
            return _row(row)

    def commit_repository_watch_run(self, watch_id: str, commit_sha: str, token: str,
                                    run_id: str, workflow_id: str, revision_id: str,
                                    artifact_id: str, checked_at: str,
                                    next_check_at: str) -> bool:
        """Finalize ledger + insert queued run + advance cursor in one transaction.

        Only the worker holding claim_token can create the run, so a timed-out
        duplicate checker cannot enqueue the same commit twice.
        """
        with get_pool().connection() as conn:
            claimed = conn.execute(
                """
                UPDATE runtime.repository_watch_commits
                   SET state = 'queued',
                       run_id = %s,
                       artifact_id = %s,
                       claim_token = NULL,
                       claimed_until = NULL,
                       error = NULL,
                       updated_at = now()
                 WHERE workspace_id = %s AND watch_id = %s AND commit_sha = %s
                   AND state = 'processing' AND claim_token = %s
                 RETURNING watch_id
                """,
                (run_id, artifact_id, self.workspace_id, watch_id, commit_sha, token),
            ).fetchone()
            if claimed is None:
                return False
            conn.execute(
                """
                INSERT INTO runtime.runs (
                    workspace_id, id, workflow_id, revision_id, artifact_id, trigger_type,
                    execution_status, security_verdict, created_at
                ) VALUES (%s, %s, %s, %s, %s, 'repository_update', 'queued', 'pending', %s)
                """,
                (self.workspace_id, run_id, workflow_id, revision_id, artifact_id, _now()),
            )
            conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET last_seen_commit = %s,
                       last_run_id = %s,
                       last_checked_at = %s,
                       next_check_at = %s,
                       last_error = NULL,
                       claim_token = NULL,
                       claimed_until = NULL,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                   AND (claim_token = %s OR claim_token IS NULL)
                """,
                (commit_sha, run_id, checked_at, next_check_at,
                 self.workspace_id, watch_id, token),
            )
            return True

    def fail_repository_watch_commit(self, watch_id: str, commit_sha: str, token: str,
                                     error: str, checked_at: str,
                                     next_check_at: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_watch_commits
                   SET state = 'failed',
                       claim_token = NULL,
                       claimed_until = NULL,
                       error = %s,
                       updated_at = now()
                 WHERE workspace_id = %s AND watch_id = %s AND commit_sha = %s
                   AND state = 'processing' AND claim_token = %s
                """,
                (error[:500], self.workspace_id, watch_id, commit_sha, token),
            )
            conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET last_checked_at = %s,
                       next_check_at = %s,
                       last_error = %s,
                       claim_token = NULL,
                       claimed_until = NULL,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                   AND (claim_token = %s OR claim_token IS NULL)
                """,
                (checked_at, next_check_at, error[:500],
                 self.workspace_id, watch_id, token),
            )

    def claim_repository_watch_dispatch(self, run_id: str, token: str,
                                        timeout_seconds: int) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.repository_watch_commits
                   SET state = 'running',
                       claim_token = %s,
                       claimed_until = now() + make_interval(secs => %s),
                       updated_at = now()
                 WHERE workspace_id = %s AND run_id = %s
                   AND (state = 'queued'
                        OR (state = 'running'
                            AND (claimed_until IS NULL OR claimed_until < now())))
                 RETURNING watch_id, commit_sha, run_id
                """,
                (token, timeout_seconds, self.workspace_id, run_id),
            ).fetchone()
            return _row(row)

    def finish_repository_watch_dispatch(self, run_id: str, token: str,
                                         error: Optional[str] = None) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_watch_commits
                   SET state = 'done',
                       claim_token = NULL,
                       claimed_until = NULL,
                       error = COALESCE(%s, error),
                       updated_at = now()
                 WHERE workspace_id = %s AND run_id = %s
                   AND state = 'running' AND claim_token = %s
                """,
                (error[:500] if error else None, self.workspace_id, run_id, token),
            )

    def release_repository_watch_dispatch(self, run_id: str, token: str,
                                          error: Optional[str] = None) -> None:
        """Return an unstarted run to the recoverable queued state."""
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_watch_commits
                   SET state = 'queued',
                       claim_token = NULL,
                       claimed_until = NULL,
                       error = COALESCE(%s, error),
                       updated_at = now()
                 WHERE workspace_id = %s AND run_id = %s
                   AND state = 'running' AND claim_token = %s
                """,
                (error[:500] if error else None, self.workspace_id, run_id, token),
            )

    def interrupt_repository_watch_dispatch(self, run_id: str, token: str,
                                            error: Optional[str] = None) -> None:
        """Run already started but has no terminal status: never claim done."""
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_watch_commits
                   SET claim_token = NULL,
                       claimed_until = NULL,
                       error = COALESCE(%s, error),
                       updated_at = now()
                 WHERE workspace_id = %s AND run_id = %s
                   AND state = 'running' AND claim_token = %s
                """,
                (error[:500] if error else None, self.workspace_id, run_id, token),
            )

    def recover_repository_watch_dispatches_server(self, limit: int) -> List[Dict[str, Any]]:
        """Queued/stale watch runs across all workspaces, with workspace_id."""
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT c.workspace_id, c.run_id, c.watch_id, c.commit_sha, c.state
                  FROM runtime.repository_watch_commits c
                  JOIN runtime.runs r
                    ON r.workspace_id = c.workspace_id AND r.id = c.run_id
                 WHERE (c.state = 'queued'
                        OR (c.state = 'running'
                            AND (c.claimed_until IS NULL OR c.claimed_until < now())))
                   AND r.execution_status = 'queued'
                 ORDER BY c.created_at
                 LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [_server_row(r) for r in rows]

    def release_stale_repository_claims_server(self) -> List[Dict[str, Any]]:
        """Bounded timeout recovery for crashed checks/dispatches, all tenants."""
        with get_pool().connection() as conn:
            stale = conn.execute(
                """
                WITH stale AS (
                    UPDATE runtime.repository_watch_commits
                       SET state = 'failed',
                           claim_token = NULL,
                           claimed_until = NULL,
                           error = COALESCE(error, 'Проверка прервана'),
                           updated_at = now()
                     WHERE state = 'processing'
                       AND (claimed_until IS NULL OR claimed_until < now())
                     RETURNING workspace_id, watch_id
                ), marked AS (
                    UPDATE runtime.repository_watches w
                       SET last_error = COALESCE(w.last_error, 'Проверка прервана'),
                           claim_token = NULL,
                           claimed_until = NULL,
                           updated_at = now()
                      FROM stale
                     WHERE w.workspace_id = stale.workspace_id
                       AND w.id = stale.watch_id
                )
                SELECT workspace_id, watch_id FROM stale
                """,
            ).fetchall()
            conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET claim_token = NULL,
                       claimed_until = NULL,
                       last_error = COALESCE(last_error, 'Проверка прервана'),
                       updated_at = now()
                 WHERE claimed_until IS NOT NULL AND claimed_until < now()
                """,
            )
            return [_server_row(r) for r in stale]

    # Push deliveries (runtime v5)
    def set_repository_watch_webhook_nonce(self, watch_id: str,
                                           nonce: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET webhook_nonce = %s, updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                 RETURNING *
                """,
                (nonce, self.workspace_id, watch_id),
            ).fetchone()
            return _row(row)

    def mark_repository_watch_delivery(self, watch_id: str, status: str,
                                       error: Optional[str] = None,
                                       at: Optional[str] = None) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_watches
                   SET last_delivery_at = COALESCE(%s::timestamptz, now()),
                       last_delivery_status = %s,
                       last_delivery_error = %s,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                """,
                (at, status[:32], error[:500] if error else None,
                 self.workspace_id, watch_id),
            )

    def enqueue_repository_hook_delivery(
        self, delivery_id: str, watch_id: str, provider: str, external_id: str,
        commit_sha: str, ref: str, workflow_revision_id: str,
        received_at: str,
    ) -> bool:
        """Persist the event before 202. False means the delivery is a replay."""
        now = _now()
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO runtime.repository_hook_deliveries (
                    workspace_id, id, watch_id, provider, delivery_id,
                    commit_sha, ref, workflow_revision_id, state,
                    attempt, next_attempt_at, received_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0, %s, %s, %s)
                ON CONFLICT (workspace_id, watch_id, delivery_id) DO NOTHING
                RETURNING id
                """,
                (self.workspace_id, delivery_id, watch_id, provider, external_id,
                 commit_sha, ref, workflow_revision_id, received_at,
                 received_at, now),
            ).fetchone()
            return row is not None

    def claim_repository_hook_deliveries_server(self, limit: int, token: str,
                                                lease_seconds: int) -> List[Dict[str, Any]]:
        """Lease due deliveries across all workspaces; returns workspace_id."""
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                WITH due AS (
                    SELECT workspace_id, id
                      FROM runtime.repository_hook_deliveries
                     WHERE (
                           (state = 'pending'
                            AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
                           OR (state = 'processing'
                               AND (claimed_until IS NULL OR claimed_until < now()))
                       )
                     ORDER BY received_at
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE runtime.repository_hook_deliveries d
                   SET state = 'processing',
                       claim_token = %s,
                       claimed_until = now() + make_interval(secs => %s),
                       updated_at = now()
                  FROM due
                 WHERE d.workspace_id = due.workspace_id AND d.id = due.id
                 RETURNING d.*
                """,
                (limit, token, lease_seconds),
            ).fetchall()
            return [_server_row(r) for r in rows]

    def finish_repository_hook_delivery(self, delivery_id: str, token: str,
                                        state: str, run_id: Optional[str],
                                        detail: Optional[str],
                                        error: Optional[str] = None,
                                        attempt: Optional[int] = None) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_hook_deliveries
                   SET state = %s,
                       claim_token = NULL,
                       claimed_until = NULL,
                       next_attempt_at = NULL,
                       attempt = COALESCE(%s, attempt),
                       run_id = COALESCE(%s, run_id),
                       detail = %s,
                       error = %s,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                   AND state = 'processing' AND claim_token = %s
                """,
                (state, attempt, run_id, detail[:500] if detail else None,
                 error[:500] if error else None,
                 self.workspace_id, delivery_id, token),
            )

    def defer_repository_hook_delivery(self, delivery_id: str, token: str,
                                       detail: str, next_attempt_at: str) -> None:
        """Return a busy delivery to pending without spending a failure attempt."""
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_hook_deliveries
                   SET state = 'pending',
                       claim_token = NULL,
                       claimed_until = NULL,
                       next_attempt_at = %s,
                       detail = %s,
                       error = NULL,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                   AND state = 'processing' AND claim_token = %s
                """,
                (next_attempt_at, detail[:500], self.workspace_id,
                 delivery_id, token),
            )

    def retry_repository_hook_delivery(self, delivery_id: str, token: str,
                                       error: str, next_attempt_at: str,
                                       attempt: int) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE runtime.repository_hook_deliveries
                   SET state = 'pending',
                       claim_token = NULL,
                       claimed_until = NULL,
                       next_attempt_at = %s,
                       attempt = %s,
                       error = %s,
                       updated_at = now()
                 WHERE workspace_id = %s AND id = %s
                   AND state = 'processing' AND claim_token = %s
                """,
                (next_attempt_at, attempt, error[:500], self.workspace_id,
                 delivery_id, token),
            )

    def list_repository_hook_deliveries(self, watch_id: str,
                                        limit: int = 20) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT id, watch_id, provider, delivery_id, commit_sha, ref,
                       state, attempt, run_id, detail, error, received_at
                  FROM runtime.repository_hook_deliveries
                 WHERE workspace_id = %s AND watch_id = %s
                 ORDER BY received_at DESC
                 LIMIT %s
                """,
                (self.workspace_id, watch_id, limit),
            ).fetchall()
            return [_row(r) for r in rows]

    # Deliveries (Webhook notifications)
    def save_delivery(self, delivery_id: str, run_id: str, channel: str, status: str,
                      target: str, status_code: Optional[int], message: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.deliveries (
                    workspace_id, id, run_id, channel, status, target, status_code, message, timestamp
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (self.workspace_id, delivery_id, run_id, channel, status, target,
                 status_code, message, _now()),
            )

    def close(self) -> None:
        _close_pool()
