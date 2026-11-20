import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from backend.app.ai.service import AiService
from backend.app.api.session import (
    SESSION_TTL_SECONDS,
    auth_required,
    issue_session,
    public_demo,
    request_workspace,
    session_from_request,
    set_session_cookie,
    verify_access_code,
)
from backend.app.artifacts.intake import SafeArtifactIntake, SafeIntakeError
from backend.app.artifacts.sample_source import build_zip_from_directory
from backend.app.codefacts.identity import run_uuid
from backend.app.codefacts.map_builder import ensure_artifact_meta
from backend.app.codefacts.publish import get_published_envelope, map_from_analysis_bundle
from backend.app.codefacts.store import CodeFactsStore
from backend.app.contracts.node_registry import get_node_definitions
from backend.app.contracts.schemas import Finding, WorkflowDraft
from backend.app.demo.default_workflow import get_default_workflow_draft
from backend.app.demo.sample_project import create_sample_project_zip
from backend.app.engine.executor import WorkflowExecutor
from backend.app.engine.scheduler import WorkflowScheduler
from backend.app.engine.sse import SSEBroker
from backend.app.engine.validator import WorkflowValidator
from backend.app.storage.database import DatabaseRepository

logger = logging.getLogger("ApiRouter")

api_router = APIRouter(prefix="/api")

# Core services singletons
db_repo = DatabaseRepository()
sse_broker = SSEBroker(db_repo)
executor = WorkflowExecutor(db_repo, sse_broker)
scheduler = WorkflowScheduler(db_repo, executor)
validator = WorkflowValidator()
ai_service = AiService()
intake = SafeArtifactIntake()
codefacts_store = CodeFactsStore()

# Store last received test webhooks in memory for UI verification (bounded, no secrets)
received_webhooks: List[Dict[str, Any]] = []
MAX_WEBHOOK_LOG = 200
MAX_WEBHOOK_BYTES = 64 * 1024

_STORAGE_MANIFEST_KEYS = (
    "id", "filename", "hash_sha256", "size_bytes", "uncompressed_size_bytes",
    "file_count", "kotlin_file_count", "gradle_file_count", "extracted_path",
    "created_at",
)


def _storage_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Ровно тот контракт, который ждёт DatabaseRepository; sidecar живёт отдельно."""
    return {key: manifest[key] for key in _STORAGE_MANIFEST_KEYS if key in manifest}


_PRIVATE_KEYS = {
    "extracted_path", "extractedPath", "extractedpath",
    "secretToken", "secret_token", "webhookSecret", "webhook_secret",
}


def _strip_private_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_private_paths(item) for key, item in value.items() if key not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [_strip_private_paths(item) for item in value]
    return value


def _public_artifact(row: Dict[str, Any]) -> Dict[str, Any]:
    artifact_id = str(row.get("id", ""))
    workspace = str(row.get("workspace_id") or request_workspace_default())
    meta = None
    try:
        meta = codefacts_store.load_artifact_meta(workspace, artifact_id)
    except ValueError:
        meta = None
    public = {
        "id": artifact_id,
        "filename": row.get("filename", ""),
        "hash_sha256": row.get("hash_sha256", ""),
        "size_bytes": row.get("size_bytes", 0),
        "uncompressed_size_bytes": row.get("uncompressed_size_bytes", 0),
        "file_count": row.get("file_count", 0),
        "kotlin_file_count": row.get("kotlin_file_count", row.get("kotlin_count", 0)),
        "gradle_file_count": row.get("gradle_file_count", row.get("gradle_count", 0)),
        "created_at": row.get("created_at", ""),
    }
    if meta:
        public["workspace_id"] = meta.get("workspaceId")
        public["snapshot_id"] = meta.get("snapshotId")
        public["snapshot_manifest_hash"] = meta.get("snapshotManifestHash")
        if meta.get("sourceRef"):
            public["source_ref"] = meta.get("sourceRef")
    return _strip_private_paths(public)


def request_workspace_default() -> str:
    from backend.app.codefacts.identity import workspace_id

    return workspace_id()


# --- Session (same-origin demo) ---
@api_router.get("/health")
def health():
    return {"status": "ok", "authRequired": auth_required()}


@api_router.get("/session")
def get_session(request: Request, response: Response):
    session = session_from_request(request)
    if session:
        return {
            "authenticated": True,
            "authRequired": auth_required(),
            "workspaceId": session["workspaceId"],
            "expiresInSec": SESSION_TTL_SECONDS,
        }
    if public_demo():
        # Публичный режим: без passphrase cookie не выдаётся.
        return {
            "authenticated": False,
            "authRequired": True,
            "workspaceId": request_workspace(request),
            "expiresInSec": 0,
        }
    token = issue_session(request_workspace(request))
    set_session_cookie(response, token, request)
    return {
        "authenticated": True,
        "authRequired": False,
        "workspaceId": request_workspace(request),
        "expiresInSec": SESSION_TTL_SECONDS,
    }


@api_router.post("/session")
def create_session(request: Request, response: Response,
                   payload: Optional[Dict[str, Any]] = None):
    payload = payload or {}
    if public_demo():
        code = payload.get("accessCode", payload.get("access_code"))
        if not verify_access_code(code if isinstance(code, str) else None):
            raise HTTPException(
                status_code=401,
                detail="Неверный код доступа",
                headers={"WWW-Authenticate": "AccessCode"},
            )
    token = issue_session(request_workspace(request))
    set_session_cookie(response, token, request)
    return {
        "authenticated": True,
        "authRequired": auth_required(),
        "workspaceId": request_workspace(request),
        "expiresInSec": SESSION_TTL_SECONDS,
    }


# --- Node Registry ---
@api_router.get("/node-types")
def get_node_types():
    return get_node_definitions()


# --- Artifacts ---
@api_router.post("/artifacts")
async def upload_artifact(file: UploadFile = File(...)):
    filename = file.filename or "project.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Разрешены только файлы с расширением .zip")
    try:
        manifest = await intake.ingest_upload(file, filename)
        db_repo.save_artifact(_storage_manifest(manifest))
        return _public_artifact(manifest)
    except SafeIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Artifact intake failed: %s", exc)
        raise HTTPException(status_code=500, detail="Ошибка обработки архива") from exc


def _load_sample_artifact_internal() -> Dict[str, Any]:
    zip_bytes = create_sample_project_zip()
    manifest = intake.process_zip_bytes("sample_bank_app.zip", zip_bytes)
    db_repo.save_artifact(_storage_manifest(manifest))
    return manifest


@api_router.post("/artifacts/sample")
def load_sample_artifact():
    return _public_artifact(_load_sample_artifact_internal())


@api_router.post("/artifacts/sample/training")
def load_training_artifact():
    source = os.getenv("CG_TRAINING_PROJECT", "").strip()
    if not source:
        raise HTTPException(
            status_code=400,
            detail="CG_TRAINING_PROJECT не задан: укажите путь к локальному training-project на сервере",
        )
    try:
        zip_bytes = build_zip_from_directory(Path(source))
        manifest = intake.ingest_zip_bytes("training-project.zip", zip_bytes)
        db_repo.save_artifact(_storage_manifest(manifest))
        # Публикуем только источник/хэши, без локального пути.
        source_ref = os.getenv("CG_TRAINING_SOURCE_REF", "local:training-project").strip()
        codefacts_store.save_artifact_meta(
            manifest["workspace_id"],
            manifest["id"],
            {
                "artifactId": manifest["id"],
                "workspaceId": manifest["workspace_id"],
                "snapshotId": manifest["snapshot_id"],
                "snapshotManifestHash": manifest["snapshot_manifest_hash"],
                "artifactHash": manifest["hash_sha256"],
                "sizeBytes": manifest["size_bytes"],
                "uncompressedSizeBytes": manifest["uncompressed_size_bytes"],
                "fileCount": manifest["file_count"],
                "kotlinFileCount": manifest["kotlin_file_count"],
                "gradleFileCount": manifest["gradle_file_count"],
                "filename": manifest["filename"],
                "createdAt": manifest["created_at"],
                "expiresAt": manifest.get("expires_at"),
                "sourceRef": source_ref,
            },
        )
        return _public_artifact(manifest)
    except SafeIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Training sample intake failed: %s", exc)
        raise HTTPException(status_code=500, detail="Ошибка обработки training sample") from exc


@api_router.get("/artifacts")
def list_artifacts():
    return [_public_artifact(row) for row in db_repo.list_artifacts()]


@api_router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    art = db_repo.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=404, detail="Артефакт не найден")
    return _public_artifact(art)


# --- Workflows ---
@api_router.get("/workflows")
def list_workflows():
    wfs = db_repo.list_workflows()
    if not wfs:
        def_draft = get_default_workflow_draft()
        def_id = "wf_secure_pr_gate"
        db_repo.save_workflow(def_id, def_draft.name, def_draft.description, def_draft.model_dump())
        wfs = db_repo.list_workflows()
    return _strip_private_paths(wfs)


@api_router.post("/workflows")
def create_workflow(draft: WorkflowDraft):
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    rev_id = db_repo.save_workflow(wf_id, draft.name, draft.description, draft.model_dump())
    return {"id": wf_id, "latest_revision_id": rev_id}


@api_router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    wf = db_repo.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Сценарий не найден")
    return _strip_private_paths(wf)


@api_router.put("/workflows/{workflow_id}/draft")
def update_workflow_draft(workflow_id: str, draft: WorkflowDraft):
    rev_id = db_repo.save_workflow(workflow_id, draft.name, draft.description, draft.model_dump())
    return {"workflow_id": workflow_id, "latest_revision_id": rev_id}


@api_router.post("/workflows/{workflow_id}/validate")
def validate_workflow(workflow_id: str, draft: Optional[WorkflowDraft] = None):
    if not draft:
        wf = db_repo.get_workflow(workflow_id)
        if not wf or "draft" not in wf:
            raise HTTPException(status_code=404, detail="Сценарий не найден")
        draft = WorkflowDraft(**wf["draft"])
    return validator.validate(draft)


# --- Runs ---
@api_router.post("/workflows/{workflow_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def trigger_run(workflow_id: str, payload: Optional[Dict[str, Any]] = None):
    payload = payload or {}
    wf = db_repo.get_workflow(workflow_id)
    if not wf or "draft" not in wf:
        raise HTTPException(status_code=404, detail="Сценарий не найден")

    draft = WorkflowDraft(**wf["draft"])
    rev_id = wf.get("latest_revision_id") or "rev_unknown"

    artifact_id = payload.get("artifactId")
    if not artifact_id:
        for node in draft.nodes:
            if node.kind.value == "project_input" and node.config.get("artifactId"):
                artifact_id = node.config.get("artifactId")
                break

    if not artifact_id:
        arts = db_repo.list_artifacts()
        if arts:
            artifact_id = arts[0]["id"]
        else:
            artifact_id = _load_sample_artifact_internal()["id"]

    if not db_repo.get_artifact(artifact_id):
        raise HTTPException(status_code=404, detail="Артефакт не найден")

    run_id = str(uuid.uuid4())
    db_repo.create_run(
        run_id=run_id,
        workflow_id=workflow_id,
        revision_id=rev_id,
        artifact_id=artifact_id,
        trigger_type=payload.get("triggerType", "manual"),
    )

    asyncio.create_task(
        executor.execute_run(
            run_id=run_id,
            draft=draft,
            artifact_id=artifact_id,
            trigger_type=payload.get("triggerType", "manual"),
            workflow_id=workflow_id,
            revision_id=rev_id,
        )
    )

    return {
        "runId": run_id,
        "workflowId": workflow_id,
        "revisionId": rev_id,
        "artifactId": artifact_id,
        "status": "queued",
    }


@api_router.get("/runs")
def list_runs():
    return db_repo.list_runs()


@api_router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = db_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return _strip_private_paths(run)


@api_router.get("/runs/{run_id}/nodes")
def get_run_nodes(run_id: str):
    return _strip_private_paths(db_repo.get_node_runs(run_id))


@api_router.get("/runs/{run_id}/findings")
def get_run_findings(run_id: str):
    return _strip_private_paths(db_repo.get_findings(run_id))


def _load_run_artifact(run_id: str, workspace: str) -> Optional[Dict[str, Any]]:
    run = db_repo.get_run(run_id)
    if not run:
        return None
    artifact_id = run.get("artifact_id")
    if artifact_id:
        artifact = db_repo.get_artifact(artifact_id)
        if artifact:
            try:
                ensure_artifact_meta(codefacts_store, workspace, artifact)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Artifact meta ensure failed for %s: %s", run_id, exc)
    return codefacts_store.load_run(workspace, run_uuid(run_id))


def _load_published_map(run_id: str, workspace: str,
                        local_payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Финальный источник — storage canonical; локальный файл только fallback."""
    local_payload = local_payload or _load_run_artifact(run_id, workspace)
    snapshot_id = None
    if local_payload:
        snapshot_id = local_payload.get("identity", {}).get("snapshotId")
    run = db_repo.get_run(run_id) or {}
    if not snapshot_id and run.get("artifact_id"):
        meta = codefacts_store.load_artifact_meta(workspace, str(run["artifact_id"]))
        snapshot_id = (meta or {}).get("snapshotId")
    if not snapshot_id:
        return None
    published = get_published_envelope(workspace, run_uuid(run_id), str(snapshot_id))
    if not published:
        return None
    mapped = map_from_analysis_bundle(published["bundle"], published.get("bundleHash"))
    if not mapped:
        return None
    mapped["redactionVersion"] = "cg-redaction/1"
    return mapped


@api_router.get("/runs/{run_id}/security-map")
def get_security_map(run_id: str, request: Request):
    workspace = request_workspace(request)
    run = db_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    payload = _load_published_map(run_id, workspace)
    if not payload:
        payload = _load_run_artifact(run_id, workspace)
    if not payload:
        raise HTTPException(
            status_code=409,
            detail="Карта ещё не построена: запуск не завершил статический анализ или артефакт недоступен",
        )
    identity = payload["identity"]
    return {
        "identity": {
            "workspaceId": identity["workspaceId"],
            "snapshotId": identity["snapshotId"],
            "runId": identity["runId"],
        },
        "map": payload["map"],
        "findings": payload["findings"],
    }


@api_router.get("/runs/{run_id}/evidence/{evidence_id}")
def get_evidence(run_id: str, evidence_id: str, request: Request):
    workspace = request_workspace(request)
    run = db_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    payload = _load_published_map(run_id, workspace)
    if not payload:
        payload = _load_run_artifact(run_id, workspace)
    if not payload:
        raise HTTPException(status_code=409, detail="Карта ещё не построена")
    identity = payload["identity"]
    evidence = next((item for item in payload["map"]["evidence"] if item["id"] == evidence_id), None)
    request_scope = {
        "workspaceId": identity["workspaceId"],
        "runId": identity["runId"],
        "snapshotId": identity["snapshotId"],
        "evidenceId": evidence_id,
    }
    if not evidence:
        return JSONResponse(
            status_code=404,
            content={
                "status": "unavailable",
                "request": request_scope,
                "code": "EVIDENCE_NOT_FOUND",
            },
        )
    return {
        "status": "ready",
        "request": request_scope,
        "sourceHash": evidence["sourceHash"],
        "location": evidence["location"],
        "maskedFragment": evidence["redactedSnippet"],
        "redactionVersion": payload.get("redactionVersion", "cg-redaction/1"),
    }


@api_router.get("/runs/{run_id}/report")
def get_run_report(run_id: str):
    rep = db_repo.get_report(run_id)
    if not rep:
        raise HTTPException(status_code=404, detail="Отчет для данного запуска еще не сформирован")
    return _strip_private_paths(rep)


@api_router.get("/runs/{run_id}/report/official.json")
def download_official_json(run_id: str):
    rep = db_repo.get_report(run_id)
    if not rep or not rep.get("official_json"):
        raise HTTPException(status_code=404, detail="Отчет не найден")
    return Response(
        content=rep["official_json"],
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=security-report-{run_id}.json"},
    )


@api_router.get("/runs/{run_id}/report/readable.md")
def download_readable_markdown(run_id: str):
    rep = db_repo.get_report(run_id)
    if not rep or not rep.get("readable_markdown"):
        raise HTTPException(status_code=404, detail="Отчет не найден")
    return PlainTextResponse(
        content=rep["readable_markdown"],
        headers={"Content-Disposition": f"attachment; filename=security-report-{run_id}.md"},
    )


@api_router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str, after: int = Query(default=0)):
    return StreamingResponse(
        sse_broker.subscribe(run_id, after_seq=after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Schedules ---
@api_router.get("/schedules")
def list_schedules():
    return db_repo.list_schedules()


@api_router.post("/schedules")
def create_schedule(payload: Dict[str, Any]):
    sched_id = f"sch_{uuid.uuid4().hex[:8]}"
    wf_id = payload.get("workflowId")
    art_id = payload.get("artifactId")
    cron_interval = payload.get("cronOrInterval", "interval:60")
    if not wf_id or not art_id:
        raise HTTPException(status_code=400, detail="workflowId и artifactId обязательны")
    if not db_repo.get_artifact(art_id):
        raise HTTPException(status_code=404, detail="Артефакт не найден")
    db_repo.save_schedule(sched_id, wf_id, art_id, cron_interval, enabled=True)
    return {"id": sched_id, "status": "created"}


# --- Webhook Test Receiver ---
@api_router.post("/webhook-test")
async def webhook_test_receiver(request: Request):
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Слишком большой webhook payload")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook payload должен быть JSON") from exc
    allowed_headers = {
        key: request.headers.get(key)
        for key in ("content-type", "user-agent", "x-run-id", "x-signature-sha256")
        if request.headers.get(key)
    }
    entry = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "headers": allowed_headers,
        "payload": body,
    }
    received_webhooks.append(entry)
    if len(received_webhooks) > MAX_WEBHOOK_LOG:
        del received_webhooks[: len(received_webhooks) - MAX_WEBHOOK_LOG]
    logger.info("Webhook received! RunID: %s", body.get("runId") if isinstance(body, dict) else None)
    return {"status": "ok", "message": "Вебхук успешно принят сервером"}


@api_router.get("/webhook-test/logs")
def get_webhook_logs():
    return received_webhooks


# --- AI Synthetic Test for Jury Demo ---
@api_router.post("/ai/synthetic-test")
def run_ai_synthetic_test(payload: Dict[str, Any]):
    run_id = payload.get("runId")
    findings: List[Finding] = []
    if run_id:
        findings = [Finding(**item) for item in db_repo.get_findings(run_id)]
    return ai_service.generate_synthetic_test(findings)
