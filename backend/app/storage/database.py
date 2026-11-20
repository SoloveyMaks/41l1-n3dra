"""PostgreSQL-хранилище runtime.

Контрактная схема ``data_contract`` приходит из ``db/migrations`` через
ledger-мигратор (``backend.app.storage.migrate``). Таблицы API живут в schema
``runtime``: колонки повторяют прежние SQLite-таблицы, поэтому DTO не меняются.

Единственная обязательная настройка — ``DATABASE_URL``.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from backend.app.storage.migrate import ensure_contract_schema

RUNTIME_SCHEMA_PATH = Path(__file__).with_name("runtime_schema.sql")

_driver: Optional[SimpleNamespace] = None
_pool: Any = None
_pool_lock = threading.Lock()


class DatabaseUnavailable(RuntimeError):
    """Понятный отказ: нет драйвера, DATABASE_URL или доступной БД."""


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise DatabaseUnavailable(
            "DATABASE_URL не задан. Пример: postgresql://user:password@localhost:5432/41l1_n3dra "
            "(см. README, раздел «Запуск backend с PostgreSQL»)."
        )
    return dsn


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
                raise DatabaseUnavailable(
                    "PostgreSQL недоступен по DATABASE_URL: "
                    f"{exc}. Проверьте, что сервер запущен и база существует."
                ) from exc
            _pool = pool
    return _pool


def _jsonb(value: Any) -> Any:
    return _load_driver().Jsonb(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """timestamptz -> ISO-строка: прежний JSON не меняется."""
    if row is None:
        return None
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


def _json_text(value: Any) -> Optional[str]:
    """Совместимость: *_json поля раньше отдавались строкой."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def init_db() -> None:
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS runtime")
        conn.execute(RUNTIME_SCHEMA_PATH.read_text(encoding="utf-8"))
    ensure_contract_schema(_dsn())


class DatabaseRepository:
    def __init__(self):
        init_db()

    # Workflow operations
    def save_workflow(self, workflow_id: str, name: str, description: str, draft: Dict[str, Any]) -> str:
        now = _now()
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.workflows (id, name, description, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                    SET name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        updated_at = EXCLUDED.updated_at
                """,
                (workflow_id, name, description, now, now),
            )
            # Сериализуем выдачу revision_num для одного workflow.
            conn.execute("SELECT 1 FROM runtime.workflows WHERE id = %s FOR UPDATE", (workflow_id,))
            row = conn.execute(
                "SELECT COALESCE(MAX(revision_num), 0) AS max_rev "
                "FROM runtime.workflow_revisions WHERE workflow_id = %s",
                (workflow_id,),
            ).fetchone()
            new_rev_num = int(row["max_rev"]) + 1
            rev_id = f"rev_{workflow_id}_{new_rev_num}"

            conn.execute(
                """
                INSERT INTO runtime.workflow_revisions (id, workflow_id, revision_num, draft_json, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (rev_id, workflow_id, new_rev_num, _jsonb(draft), now),
            )
            conn.execute(
                "UPDATE runtime.workflows SET latest_revision_id = %s WHERE id = %s",
                (rev_id, workflow_id),
            )
            return rev_id

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.workflows WHERE id = %s", (workflow_id,)
            ).fetchone()
            if not row:
                return None
            res = _row(row)
            if res.get("latest_revision_id"):
                rev = conn.execute(
                    "SELECT draft_json FROM runtime.workflow_revisions WHERE id = %s",
                    (res["latest_revision_id"],),
                ).fetchone()
                if rev:
                    res["draft"] = rev["draft_json"]
            return res

    def list_workflows(self) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT w.*, r.draft_json AS draft
                FROM runtime.workflows w
                LEFT JOIN runtime.workflow_revisions r ON r.id = w.latest_revision_id
                ORDER BY w.updated_at DESC
                """
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
                "SELECT * FROM runtime.workflow_revisions WHERE id = %s", (revision_id,)
            ).fetchone()
            if not row:
                return None
            res = _row(row)
            res["draft"] = res.get("draft_json")
            return res

    # Artifact operations
    def save_artifact(self, manifest: Dict[str, Any]) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.artifacts (
                    id, filename, hash_sha256, size_bytes, uncompressed_size_bytes,
                    file_count, kotlin_count, gradle_count, extracted_path, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    hash_sha256 = EXCLUDED.hash_sha256,
                    size_bytes = EXCLUDED.size_bytes,
                    uncompressed_size_bytes = EXCLUDED.uncompressed_size_bytes,
                    file_count = EXCLUDED.file_count,
                    kotlin_count = EXCLUDED.kotlin_count,
                    gradle_count = EXCLUDED.gradle_count,
                    extracted_path = EXCLUDED.extracted_path,
                    created_at = EXCLUDED.created_at
                """,
                (
                    manifest["id"], manifest["filename"], manifest["hash_sha256"],
                    manifest["size_bytes"], manifest["uncompressed_size_bytes"],
                    manifest["file_count"], manifest["kotlin_file_count"],
                    manifest["gradle_file_count"], manifest["extracted_path"],
                    manifest["created_at"],
                ),
            )

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.artifacts WHERE id = %s", (artifact_id,)
            ).fetchone()
            return _row(row)

    def list_artifacts(self) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.artifacts ORDER BY created_at DESC"
            ).fetchall()
            return [_row(r) for r in rows]

    # Run operations
    def create_run(self, run_id: str, workflow_id: str, revision_id: str,
                   artifact_id: Optional[str], trigger_type: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.runs (
                    id, workflow_id, revision_id, artifact_id, trigger_type,
                    execution_status, security_verdict, created_at
                ) VALUES (%s, %s, %s, %s, %s, 'queued', 'pending', %s)
                """,
                (run_id, workflow_id, revision_id, artifact_id, trigger_type, _now()),
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
        params.append(run_id)
        with get_pool().connection() as conn:
            conn.execute(
                f"UPDATE runtime.runs SET {', '.join(sets)} WHERE id = %s", params
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.runs WHERE id = %s", (run_id,)
            ).fetchone()
            if not row:
                return None
            res = _row(row)
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM runtime.run_events WHERE run_id = %s",
                (run_id,),
            ).fetchone()
            res["last_event_seq"] = int(seq_row["max_seq"])
            return res

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.runs ORDER BY created_at DESC LIMIT %s", (limit,)
            ).fetchall()
            return [_row(r) for r in rows]

    # Node Run operations
    def save_node_run(self, node_run: Dict[str, Any]) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.node_runs (
                    id, run_id, node_id, kind, status, started_at, finished_at,
                    duration_ms, error_message, input_data_json, output_data_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
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
                    node_run["id"], node_run["run_id"], node_run["node_id"],
                    node_run["kind"], node_run["status"],
                    node_run.get("started_at"), node_run.get("finished_at"),
                    node_run.get("duration_ms", 0.0), node_run.get("error_message"),
                    _jsonb(node_run.get("input_data_masked", {})),
                    _jsonb(node_run.get("output_data_masked", {})),
                ),
            )

    def get_node_runs(self, run_id: str) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.node_runs WHERE run_id = %s "
                "ORDER BY started_at ASC NULLS FIRST",
                (run_id,),
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
                INSERT INTO runtime.run_events (run_id, timestamp, type, node_id, payload_json)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING seq
                """,
                (run_id, _now(), event_type, node_id, _jsonb(payload)),
            ).fetchone()
            return int(row["seq"])

    def get_run_events(self, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime.run_events WHERE run_id = %s AND seq > %s ORDER BY seq ASC",
                (run_id, after_seq),
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
                        id, run_id, fingerprint, analyzer, rule_id, rule_version,
                        severity, severity_basis, confidence, confidence_basis, status,
                        primary_location_json, related_locations_json, trace_json,
                        redacted_evidence, advisory_ids_json, coverage_warnings_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        run_id = EXCLUDED.run_id,
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
                        f["id"], run_id, f["fingerprint"], f["analyzer"],
                        f["ruleId"], f.get("ruleVersion", "1.0.0"),
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
                "SELECT * FROM runtime.findings WHERE run_id = %s", (run_id,)
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
                    run_id, verdict, passport_json, ai_summary, ai_items_json,
                    ai_status, readable_markdown, official_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
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
                    run_id, verdict, _jsonb(passport), ai_summary,
                    _jsonb(ai_items), ai_status, readable_markdown, official_json, _now(),
                ),
            )

    def get_report(self, run_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime.reports WHERE run_id = %s", (run_id,)
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
                    id, workflow_id, artifact_id, enabled, cron_or_interval,
                    timezone, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    workflow_id = EXCLUDED.workflow_id,
                    artifact_id = EXCLUDED.artifact_id,
                    enabled = EXCLUDED.enabled,
                    cron_or_interval = EXCLUDED.cron_or_interval,
                    timezone = EXCLUDED.timezone
                """,
                (schedule_id, workflow_id, artifact_id, 1 if enabled else 0,
                 cron_or_interval, timezone_str, _now()),
            )

    def list_schedules(self) -> List[Dict[str, Any]]:
        with get_pool().connection() as conn:
            rows = conn.execute("SELECT * FROM runtime.schedules").fetchall()
            return [_row(r) for r in rows]

    def update_schedule_run_time(self, schedule_id: str, last_run: str, next_fire: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                "UPDATE runtime.schedules "
                "SET last_run_at = NULLIF(%s, '')::timestamptz, "
                "    next_fire_at = NULLIF(%s, '')::timestamptz "
                "WHERE id = %s",
                (last_run, next_fire, schedule_id),
            )

    # Deliveries (Webhook notifications)
    def save_delivery(self, delivery_id: str, run_id: str, channel: str, status: str,
                      target: str, status_code: Optional[int], message: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime.deliveries (
                    id, run_id, channel, status, target, status_code, message, timestamp
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (delivery_id, run_id, channel, status, target, status_code, message, _now()),
            )

    def close(self) -> None:
        global _pool
        with _pool_lock:
            if _pool is not None:
                _pool.close()
                _pool = None