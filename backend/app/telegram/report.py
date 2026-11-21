"""JSONC-compatible report for Telegram delivery.

Deterministic findings use the same saved report bundle and masking as
downloads. AI_SCAN observations are included only under `aiHypotheses`, explicitly
marked not verified; they are never merged into deterministic findings or a
verdict.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.codefacts.identity import data_root
from backend.app.reports.exports import load_export_bundle
from backend.app.storage.database import DatabaseRepository

JSONC_VERSION = "nedra-telegram-jsonc/1"
MAX_JSONC_BYTES = 10 * 1024 * 1024
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


@dataclass(frozen=True)
class JsoncReport:
    filename: str
    content: bytes
    sha256: str
    finding_count: int
    ai_hypothesis_count: int


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _jsonc_path(workspace: str, run_id: str) -> Path:
    try:
        ws = str(uuid.UUID(str(workspace)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("workspace должен быть UUID") from exc
    if not _RUN_ID_RE.fullmatch(str(run_id)):
        raise ValueError("Некорректный run_id для jsonc")
    return data_root() / "telegram" / "reports" / ws / f"{run_id}.jsonc"


def build_jsonc_report(
    db: DatabaseRepository,
    run_id: str,
    workspace: str,
) -> JsoncReport:
    """Build a bounded, redacted JSONC-compatible report for one run."""
    bundle = load_export_bundle(db, run_id)
    run = bundle.run
    findings = bundle.findings
    hypotheses = bundle.ai_scan

    document = {
        "format": JSONC_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "notice": (
            "This is a JSONC-compatible scanner report. Deterministic findings are "
            "separate from AI hypotheses; AI hypotheses are not verified."
        ),
        "run": {
            "id": run.get("id"),
            "workflowId": run.get("workflow_id"),
            "revisionId": run.get("revision_id"),
            "artifactId": run.get("artifact_id"),
            "triggerType": run.get("trigger_type"),
            "executionStatus": run.get("execution_status"),
            "securityVerdict": run.get("security_verdict"),
            "startedAt": _iso(run.get("started_at")),
            "finishedAt": _iso(run.get("finished_at")),
            "counts": {
                key: bundle.counts[key]
                for key in ("total", "critical", "high", "medium", "low", "info")
            },
        },
        "deterministic": {
            "count": len(findings),
            "findings": findings,
        },
        "aiHypotheses": hypotheses,
        "securityPassport": bundle.passport,
        "coverageNote": "Coverage and verdict are deterministic; AI hypotheses do not alter them.",
    }
    content = json.dumps(document, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    if len(content) > MAX_JSONC_BYTES:
        raise ValueError("JSONC report exceeds the delivery limit")
    digest = hashlib.sha256(content).hexdigest()
    return JsoncReport(
        filename=f"nedra-{run_id}.jsonc",
        content=content,
        sha256=digest,
        finding_count=len(findings),
        ai_hypothesis_count=len(hypotheses.get("observations") or []),
    )


def write_jsonc_report(report: JsoncReport, workspace: str, run_id: str) -> Path:
    path = _jsonc_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=".jsonc-", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(report.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return path