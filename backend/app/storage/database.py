import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DB_DIR / "workflow.db"

_lock = threading.Lock()


def get_db_connection() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with _lock:
        conn = get_db_connection()
        try:
            with conn:
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    latest_revision_id TEXT
                );

                CREATE TABLE IF NOT EXISTS workflow_revisions (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    revision_num INTEGER NOT NULL,
                    draft_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    hash_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    uncompressed_size_bytes INTEGER NOT NULL,
                    file_count INTEGER NOT NULL,
                    kotlin_count INTEGER NOT NULL,
                    gradle_count INTEGER NOT NULL,
                    extracted_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    artifact_id TEXT,
                    trigger_type TEXT NOT NULL,
                    execution_status TEXT NOT NULL,
                    security_verdict TEXT NOT NULL,
                    total_findings INTEGER DEFAULT 0,
                    critical_count INTEGER DEFAULT 0,
                    high_count INTEGER DEFAULT 0,
                    medium_count INTEGER DEFAULT 0,
                    low_count INTEGER DEFAULT 0,
                    info_count INTEGER DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_runs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    duration_ms REAL DEFAULT 0.0,
                    error_message TEXT,
                    input_data_json TEXT,
                    output_data_json TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    analyzer TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    rule_version TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    severity_basis TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    confidence_basis TEXT NOT NULL,
                    status TEXT NOT NULL,
                    primary_location_json TEXT NOT NULL,
                    related_locations_json TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    redacted_evidence TEXT NOT NULL,
                    advisory_ids_json TEXT NOT NULL,
                    coverage_warnings_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS reports (
                    run_id TEXT PRIMARY KEY,
                    verdict TEXT NOT NULL,
                    passport_json TEXT NOT NULL,
                    ai_summary TEXT NOT NULL,
                    ai_items_json TEXT NOT NULL,
                    ai_status TEXT NOT NULL,
                    readable_markdown TEXT NOT NULL,
                    official_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    type TEXT NOT NULL,
                    node_id TEXT,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    cron_or_interval TEXT NOT NULL,
                    timezone TEXT DEFAULT 'UTC',
                    last_run_at TEXT,
                    next_fire_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target TEXT NOT NULL,
                    status_code INTEGER,
                    message TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);
                CREATE INDEX IF NOT EXISTS idx_findings_run_id ON findings(run_id);
                CREATE INDEX IF NOT EXISTS idx_node_runs_run_id ON node_runs(run_id);
                """)
        finally:
            conn.close()


class DatabaseRepository:
    def __init__(self):
        init_db()

    # Workflow operations
    def save_workflow(self, workflow_id: str, name: str, description: str, draft: Dict[str, Any]) -> str:
        with _lock:
            conn = get_db_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                with conn:
                    cursor = conn.execute("SELECT id FROM workflows WHERE id = ?", (workflow_id,))
                    row = cursor.fetchone()
                    if row:
                        conn.execute(
                            "UPDATE workflows SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                            (name, description, now, workflow_id)
                        )
                    else:
                        conn.execute(
                            "INSERT INTO workflows (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                            (workflow_id, name, description, now, now)
                        )

                    # Get latest revision number
                    cursor = conn.execute(
                        "SELECT MAX(revision_num) FROM workflow_revisions WHERE workflow_id = ?",
                        (workflow_id,)
                    )
                    max_rev = cursor.fetchone()[0] or 0
                    new_rev_num = max_rev + 1
                    rev_id = f"rev_{workflow_id}_{new_rev_num}"

                    conn.execute(
                        "INSERT INTO workflow_revisions (id, workflow_id, revision_num, draft_json, created_at) VALUES (?, ?, ?, ?, ?)",
                        (rev_id, workflow_id, new_rev_num, json.dumps(draft), now)
                    )
                    conn.execute(
                        "UPDATE workflows SET latest_revision_id = ? WHERE id = ?",
                        (rev_id, workflow_id)
                    )
                    return rev_id
            finally:
                conn.close()

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("latest_revision_id"):
                cursor = conn.execute(
                    "SELECT draft_json FROM workflow_revisions WHERE id = ?",
                    (res["latest_revision_id"],)
                )
                rev_row = cursor.fetchone()
                if rev_row:
                    res["draft"] = json.loads(rev_row["draft_json"])
            return res
        finally:
            conn.close()

    def list_workflows(self) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM workflows ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                if item.get("latest_revision_id"):
                    cursor2 = conn.execute(
                        "SELECT draft_json FROM workflow_revisions WHERE id = ?",
                        (item["latest_revision_id"],)
                    )
                    rev_row = cursor2.fetchone()
                    if rev_row:
                        item["draft"] = json.loads(rev_row["draft_json"])
                results.append(item)
            return results
        finally:
            conn.close()

    def get_workflow_revision(self, revision_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM workflow_revisions WHERE id = ?", (revision_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["draft"] = json.loads(res["draft_json"])
            return res
        finally:
            conn.close()

    # Artifact operations
    def save_artifact(self, manifest: Dict[str, Any]) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO artifacts (
                            id, filename, hash_sha256, size_bytes, uncompressed_size_bytes,
                            file_count, kotlin_count, gradle_count, extracted_path, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        manifest["id"], manifest["filename"], manifest["hash_sha256"],
                        manifest["size_bytes"], manifest["uncompressed_size_bytes"],
                        manifest["file_count"], manifest["kotlin_file_count"],
                        manifest["gradle_file_count"], manifest["extracted_path"],
                        manifest["created_at"]
                    ))
            finally:
                conn.close()

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_artifacts(self) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM artifacts ORDER BY created_at DESC")
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    # Run operations
    def create_run(self, run_id: str, workflow_id: str, revision_id: str, artifact_id: Optional[str], trigger_type: str) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                with conn:
                    conn.execute("""
                        INSERT INTO runs (
                            id, workflow_id, revision_id, artifact_id, trigger_type,
                            execution_status, security_verdict, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'queued', 'pending', ?)
                    """, (run_id, workflow_id, revision_id, artifact_id, trigger_type, now))
            finally:
                conn.close()

    def update_run_status(self, run_id: str, execution_status: str, security_verdict: Optional[str] = None,
                          started_at: Optional[str] = None, finished_at: Optional[str] = None,
                          findings_summary: Optional[Dict[str, int]] = None) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                with conn:
                    query = "UPDATE runs SET execution_status = ?"
                    params: List[Any] = [execution_status]
                    if security_verdict is not None:
                        query += ", security_verdict = ?"
                        params.append(security_verdict)
                    if started_at is not None:
                        query += ", started_at = ?"
                        params.append(started_at)
                    if finished_at is not None:
                        query += ", finished_at = ?"
                        params.append(finished_at)
                    if findings_summary is not None:
                        query += """, total_findings = ?, critical_count = ?, high_count = ?,
                                     medium_count = ?, low_count = ?, info_count = ?"""
                        params.extend([
                            findings_summary.get("total", 0),
                            findings_summary.get("critical", 0),
                            findings_summary.get("high", 0),
                            findings_summary.get("medium", 0),
                            findings_summary.get("low", 0),
                            findings_summary.get("info", 0)
                        ])
                    query += " WHERE id = ?"
                    params.append(run_id)
                    conn.execute(query, params)
            finally:
                conn.close()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            # Fetch max event seq
            cursor2 = conn.execute("SELECT MAX(seq) FROM run_events WHERE run_id = ?", (run_id,))
            max_seq = cursor2.fetchone()[0] or 0
            res["last_event_seq"] = max_seq
            return res
        finally:
            conn.close()

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    # Node Run operations
    def save_node_run(self, node_run: Dict[str, Any]) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO node_runs (
                            id, run_id, node_id, kind, status, started_at, finished_at,
                            duration_ms, error_message, input_data_json, output_data_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        node_run["id"], node_run["run_id"], node_run["node_id"],
                        node_run["kind"], node_run["status"], node_run.get("started_at"),
                        node_run.get("finished_at"), node_run.get("duration_ms", 0.0),
                        node_run.get("error_message"),
                        json.dumps(node_run.get("input_data_masked", {})),
                        json.dumps(node_run.get("output_data_masked", {}))
                    ))
            finally:
                conn.close()

    def get_node_runs(self, run_id: str) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM node_runs WHERE run_id = ? ORDER BY started_at ASC", (run_id,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["input_data_masked"] = json.loads(item["input_data_json"]) if item["input_data_json"] else {}
                item["output_data_masked"] = json.loads(item["output_data_json"]) if item["output_data_json"] else {}
                results.append(item)
            return results
        finally:
            conn.close()

    # Events & SSE
    def add_run_event(self, run_id: str, event_type: str, payload: Dict[str, Any], node_id: Optional[str] = None) -> int:
        with _lock:
            conn = get_db_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                with conn:
                    cursor = conn.execute("""
                        INSERT INTO run_events (run_id, timestamp, type, node_id, payload_json)
                        VALUES (?, ?, ?, ?, ?)
                    """, (run_id, now, event_type, node_id, json.dumps(payload)))
                    return cursor.lastrowid
            finally:
                conn.close()

    def get_run_events(self, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq ASC",
                (run_id, after_seq)
            )
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["payload"] = json.loads(item["payload_json"])
                results.append(item)
            return results
        finally:
            conn.close()

    # Findings
    def save_findings(self, run_id: str, findings: List[Dict[str, Any]]) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                with conn:
                    for f in findings:
                        conn.execute("""
                            INSERT OR REPLACE INTO findings (
                                id, run_id, fingerprint, analyzer, rule_id, rule_version,
                                severity, severity_basis, confidence, confidence_basis, status,
                                primary_location_json, related_locations_json, trace_json,
                                redacted_evidence, advisory_ids_json, coverage_warnings_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            f["id"], run_id, f["fingerprint"], f["analyzer"], f["ruleId"],
                            f.get("ruleVersion", "1.0.0"), f["severity"], f["severityBasis"],
                            f["confidence"], f["confidenceBasis"], f.get("status", "confirmed"),
                            json.dumps(f["primaryLocation"]),
                            json.dumps(f.get("relatedLocations", [])),
                            json.dumps(f.get("trace", [])),
                            f.get("redactedEvidence", ""),
                            json.dumps(f.get("advisoryIds", [])),
                            json.dumps(f.get("coverageWarnings", []))
                        ))
            finally:
                conn.close()

    def get_findings(self, run_id: str) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM findings WHERE run_id = ?", (run_id,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["primaryLocation"] = json.loads(item["primary_location_json"])
                item["relatedLocations"] = json.loads(item["related_locations_json"])
                item["trace"] = json.loads(item["trace_json"])
                item["advisoryIds"] = json.loads(item["advisory_ids_json"])
                item["coverageWarnings"] = json.loads(item["coverage_warnings_json"])
                item["ruleId"] = item["rule_id"]
                item["ruleVersion"] = item["rule_version"]
                item["severityBasis"] = item["severity_basis"]
                item["confidenceBasis"] = item["confidence_basis"]
                item["redactedEvidence"] = item["redacted_evidence"]
                results.append(item)
            return results
        finally:
            conn.close()

    # Reports
    def save_report(self, run_id: str, verdict: str, passport: Dict[str, Any],
                    ai_summary: str, ai_items: List[Dict[str, Any]], ai_status: str,
                    readable_markdown: str, official_json: str) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO reports (
                            run_id, verdict, passport_json, ai_summary, ai_items_json,
                            ai_status, readable_markdown, official_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        run_id, verdict, json.dumps(passport), ai_summary,
                        json.dumps(ai_items), ai_status, readable_markdown, official_json, now
                    ))
            finally:
                conn.close()

    def get_report(self, run_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM reports WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["passport"] = json.loads(res["passport_json"])
            res["ai_items"] = json.loads(res["ai_items_json"])
            return res
        finally:
            conn.close()

    # Schedules
    def save_schedule(self, schedule_id: str, workflow_id: str, artifact_id: str,
                      cron_or_interval: str, enabled: bool = True, timezone_str: str = "UTC") -> None:
        with _lock:
            conn = get_db_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO schedules (
                            id, workflow_id, artifact_id, enabled, cron_or_interval,
                            timezone, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (schedule_id, workflow_id, artifact_id, 1 if enabled else 0, cron_or_interval, timezone_str, now))
            finally:
                conn.close()

    def list_schedules(self) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT * FROM schedules")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_schedule_run_time(self, schedule_id: str, last_run: str, next_fire: str) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute("""
                        UPDATE schedules SET last_run_at = ?, next_fire_at = ? WHERE id = ?
                    """, (last_run, next_fire, schedule_id))
            finally:
                conn.close()

    # Deliveries (Webhook notifications)
    def save_delivery(self, delivery_id: str, run_id: str, channel: str, status: str,
                      target: str, status_code: Optional[int], message: str) -> None:
        with _lock:
            conn = get_db_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                with conn:
                    conn.execute("""
                        INSERT INTO deliveries (
                            id, run_id, channel, status, target, status_code, message, timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (delivery_id, run_id, channel, status, target, status_code, message, now))
            finally:
                conn.close()
