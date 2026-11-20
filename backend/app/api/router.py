import asyncio
import io
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from backend.app.ai.service import AiService
from backend.app.artifacts.intake import SafeArtifactIntake, SafeIntakeError
from backend.app.contracts.node_registry import get_node_definitions
from backend.app.contracts.schemas import (
    WorkflowDraft, WorkflowValidationResult
)
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

# Store last received test webhooks in memory for UI verification
received_webhooks: List[Dict[str, Any]] = []


# --- Node Registry ---
@api_router.get("/node-types")
def get_node_types():
    return get_node_definitions()


# --- Artifacts ---
@api_router.post("/artifacts")
async def upload_artifact(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Разрешены только файлы с расширением .zip")
    content = await file.read()
    try:
        manifest = intake.process_zip_bytes(file.filename, content)
        db_repo.save_artifact(manifest)
        return manifest
    except SafeIntakeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка обработки архива: {str(e)}")


@api_router.post("/artifacts/sample")
def load_sample_artifact():
    zip_bytes = create_sample_project_zip()
    manifest = intake.process_zip_bytes("sample_bank_app.zip", zip_bytes)
    db_repo.save_artifact(manifest)
    return manifest


@api_router.get("/artifacts")
def list_artifacts():
    return db_repo.list_artifacts()


@api_router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    art = db_repo.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=404, detail="Артефакт не найден")
    return art


# --- Workflows ---
@api_router.get("/workflows")
def list_workflows():
    wfs = db_repo.list_workflows()
    if not wfs:
        # Create default initial workflow
        def_draft = get_default_workflow_draft()
        def_id = "wf_secure_pr_gate"
        db_repo.save_workflow(def_id, def_draft.name, def_draft.description, def_draft.model_dump())
        wfs = db_repo.list_workflows()
    return wfs


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
    return wf


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
class RunTriggerRequest:
    pass


@api_router.post("/workflows/{workflow_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def trigger_run(workflow_id: str, payload: Dict[str, Any] = None):
    payload = payload or {}
    wf = db_repo.get_workflow(workflow_id)
    if not wf or "draft" not in wf:
        raise HTTPException(status_code=404, detail="Сценарий не найден")

    draft = WorkflowDraft(**wf["draft"])
    rev_id = wf.get("latest_revision_id", "rev_1")

    # If artifact is provided or exists in draft
    artifact_id = payload.get("artifactId")
    if not artifact_id:
        for n in draft.nodes:
            if n.kind.value == "project_input" and n.config.get("artifactId"):
                artifact_id = n.config.get("artifactId")
                break

    if not artifact_id:
        # Fallback to latest uploaded artifact if none specified
        arts = db_repo.list_artifacts()
        if arts:
            artifact_id = arts[0]["id"]
        else:
            # Automatically create sample project
            sample_m = load_sample_artifact()
            artifact_id = sample_m["id"]

    run_id = f"run_{uuid.uuid4().hex[:8]}"
    db_repo.create_run(
        run_id=run_id,
        workflow_id=workflow_id,
        revision_id=rev_id,
        artifact_id=artifact_id,
        trigger_type=payload.get("triggerType", "manual")
    )

    # Launch asynchronous executor
    asyncio.create_task(
        executor.execute_run(
            run_id=run_id,
            draft=draft,
            artifact_id=artifact_id,
            trigger_type=payload.get("triggerType", "manual")
        )
    )

    return {
        "runId": run_id,
        "workflowId": workflow_id,
        "revisionId": rev_id,
        "artifactId": artifact_id,
        "status": "queued"
    }


@api_router.get("/runs")
def list_runs():
    return db_repo.list_runs()


@api_router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = db_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return run


@api_router.get("/runs/{run_id}/nodes")
def get_run_nodes(run_id: str):
    return db_repo.get_node_runs(run_id)


@api_router.get("/runs/{run_id}/findings")
def get_run_findings(run_id: str):
    return db_repo.get_findings(run_id)


@api_router.get("/runs/{run_id}/report")
def get_run_report(run_id: str):
    rep = db_repo.get_report(run_id)
    if not rep:
        raise HTTPException(status_code=404, detail="Отчет для данного запуска еще не сформирован")
    return rep


@api_router.get("/runs/{run_id}/report/official.json")
def download_official_json(run_id: str):
    rep = db_repo.get_report(run_id)
    if not rep or not rep.get("official_json"):
        raise HTTPException(status_code=404, detail="Отчет не найден")
    return Response(
        content=rep["official_json"],
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=security-report-{run_id}.json"}
    )


@api_router.get("/runs/{run_id}/report/readable.md")
def download_readable_markdown(run_id: str):
    rep = db_repo.get_report(run_id)
    if not rep or not rep.get("readable_markdown"):
        raise HTTPException(status_code=404, detail="Отчет не найден")
    return PlainTextResponse(
        content=rep["readable_markdown"],
        headers={"Content-Disposition": f"attachment; filename=security-report-{run_id}.md"}
    )


@api_router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str, after: int = Query(default=0)):
    return StreamingResponse(
        sse_broker.subscribe(run_id, after_seq=after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
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
    db_repo.save_schedule(sched_id, wf_id, art_id, cron_interval, enabled=True)
    return {"id": sched_id, "status": "created"}


# --- Webhook Test Receiver ---
@api_router.post("/webhook-test")
async def webhook_test_receiver(request: Request):
    body = await request.json()
    headers = dict(request.headers)
    entry = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "headers": headers,
        "payload": body
    }
    received_webhooks.append(entry)
    logger.info(f"Webhook received! RunID: {body.get('runId')} Verdict: {body.get('verdict')}")
    return {"status": "ok", "message": "Вебхук успешно принят сервером"}


@api_router.get("/webhook-test/logs")
def get_webhook_logs():
    return received_webhooks


# --- AI Synthetic Test for Jury Demo ---
@api_router.post("/ai/synthetic-test")
def run_ai_synthetic_test(payload: Dict[str, Any]):
    run_id = payload.get("runId")
    findings = []
    if run_id:
        f_dicts = db_repo.get_findings(run_id)
        from backend.app.contracts.schemas import Finding
        findings = [Finding(**f) for f in f_dicts]
    return ai_service.generate_synthetic_test(findings)
