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
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from backend.app.storage.migrate import ensure_contract_schema, ensure_optional_schema

logger = logging.getLogger("RuntimeSchema")

RUNTIME_SCHEMA_PATH = Path(__file__).with_name("runtime_schema.sql")
RUNTIME_GUARDS_PATH = Path(__file__).with_name("runtime_guards.sql")
RUNTIME_SCHEMA_VERSION = 3
LEGACY_RUNTIME_SCHEMA = "runtime_legacy_v1"
DEFAULT_WORKSPACE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "41l1-n3dra/default-workspace")

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
    raw = os.environ.get("RUNTIME_WORKSPACE_ID", "").strip()
    if not raw:
        return DEFAULT_WORKSPACE_ID
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise DatabaseUnavailable(
            f"RUNTIME_WORKSPACE_ID должен быть UUID, получено: {raw!r}"
        ) from exc


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


def _install_fresh_runtime(conn) -> None:
    _exec_script(conn, RUNTIME_SCHEMA_PATH)
    _exec_script(conn, RUNTIME_GUARDS_PATH)
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
            # v2 = workspace isolation; v3 добавляет immutability-триггеры.
            _exec_script(conn, RUNTIME_GUARDS_PATH)
            conn.execute(
                "UPDATE runtime.schema_meta SET version = %s",
                (RUNTIME_SCHEMA_VERSION,),
            )
        elif version != RUNTIME_SCHEMA_VERSION:
            raise DatabaseUnavailable(
                f"runtime schema version={version}, ожидается {RUNTIME_SCHEMA_VERSION}. "
                "Нужна миграция runtime-слоя, молча работать на чужой версии нельзя."
            )


class DatabaseRepository:
    def __init__(self):
        self.workspace_id = _workspace_id()
        init_db(self.workspace_id)

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

    def update_schedule_run_time(self, schedule_id: str, last_run: str, next_fire: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                "UPDATE runtime.schedules "
                "SET last_run_at = NULLIF(%s, '')::timestamptz, "
                "    next_fire_at = NULLIF(%s, '')::timestamptz "
                "WHERE workspace_id = %s AND id = %s",
                (last_run, next_fire, self.workspace_id, schedule_id),
            )

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