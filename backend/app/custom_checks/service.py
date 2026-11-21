"""Validation, registration and bounded execution of custom checks."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from backend.app.ai.provider import mask_sensitive_text
from backend.app.artifacts.intake import build_snapshot_from_directory
from backend.app.codefacts.map_builder import ensure_artifact_meta
from backend.app.codefacts.store import CodeFactsStore
from backend.app.contracts.schemas import Severity
from backend.app.custom_checks.catalog import (
    INPUT_CONTRACT,
    LANGUAGE,
    MAX_FINDINGS,
    MAX_MEMORY_MB,
    MAX_NAME_CHARS,
    MAX_OUTPUT_BYTES,
    MAX_SCRIPT_BYTES,
    MAX_TEXT_CHARS,
    MAX_TIMEOUT_SECONDS,
    MAX_VERSION_CHARS,
    MIN_MEMORY_MB,
    MIN_OUTPUT_BYTES,
    MIN_TIMEOUT_SECONDS,
    OUTPUT_CONTRACT,
    RUNNER,
    TEMPLATES,
)
from backend.app.custom_checks.models import (
    CustomCheckDefinition,
    CustomCheckPreview,
    CustomCheckRunResult,
    CustomCheckSpec,
    CustomCheckValidation,
    CustomFinding,
)
from backend.app.custom_checks.sandbox import (
    SandboxCancelled,
    SandboxError,
    SandboxLimits,
    SandboxOutcome,
    SandboxUnavailable,
    run_sandbox,
    sandbox_availability,
)
from backend.app.custom_checks.store import (
    CustomCheckNotFound,
    CustomCheckStore,
)
from backend.app.storage.database import DatabaseRepository

_RULE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*\\)(?!.*//).+$")
_SEVERITIES = {item.value for item in Severity}


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


class CustomCheckValidationError(ValueError):
    pass


def _definition_from_spec(spec: CustomCheckSpec, source_text: str, check_id: Optional[str] = None) -> CustomCheckDefinition:
    name = str(spec.name or "").strip()
    version = str(spec.version or "").strip()
    if not name or len(name) > MAX_NAME_CHARS:
        raise CustomCheckValidationError(f"Название должно быть от 1 до {MAX_NAME_CHARS} символов")
    if not version or len(version) > MAX_VERSION_CHARS:
        raise CustomCheckValidationError(f"Версия должна быть от 1 до {MAX_VERSION_CHARS} символов")
    if spec.language != LANGUAGE or spec.runner != RUNNER:
        raise CustomCheckValidationError("Поддерживается только Python через bwrap-python3")
    if spec.sourceKind not in {"script", "template"}:
        raise CustomCheckValidationError("sourceKind должен быть script или template")
    if spec.sourceKind == "template" and spec.templateId not in TEMPLATES:
        raise CustomCheckValidationError("Неизвестный встроенный шаблон")
    if not (MIN_TIMEOUT_SECONDS <= int(spec.timeoutSeconds) <= MAX_TIMEOUT_SECONDS):
        raise CustomCheckValidationError(f"timeoutSeconds должен быть {MIN_TIMEOUT_SECONDS}..{MAX_TIMEOUT_SECONDS}")
    if not (MIN_MEMORY_MB <= int(spec.memoryMb) <= MAX_MEMORY_MB):
        raise CustomCheckValidationError(f"memoryMb должен быть {MIN_MEMORY_MB}..{MAX_MEMORY_MB}")
    if not (MIN_OUTPUT_BYTES <= int(spec.maxOutputBytes) <= MAX_OUTPUT_BYTES):
        raise CustomCheckValidationError(f"maxOutputBytes должен быть {MIN_OUTPUT_BYTES}..{MAX_OUTPUT_BYTES}")
    if not isinstance(source_text, str) or not source_text.strip():
        raise CustomCheckValidationError("Скрипт проверки пуст")
    if len(source_text.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise CustomCheckValidationError("Скрипт слишком большой")
    if "\x00" in source_text:
        raise CustomCheckValidationError("Скрипт содержит NUL")
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return CustomCheckDefinition(
        id=check_id or str(uuid.uuid4()),
        name=name,
        version=version,
        language=LANGUAGE,
        runner=RUNNER,
        sourceKind=spec.sourceKind,
        templateId=spec.templateId if spec.sourceKind == "template" else None,
        sourceSha256=digest,
        timeoutSeconds=int(spec.timeoutSeconds),
        memoryMb=int(spec.memoryMb),
        maxOutputBytes=int(spec.maxOutputBytes),
        createdBy="preview",
    )


def _source_from_spec(spec: CustomCheckSpec) -> str:
    if spec.sourceKind == "template":
        if spec.templateId not in TEMPLATES:
            raise CustomCheckValidationError("Неизвестный встроенный шаблон")
        return TEMPLATES[spec.templateId]["script"]
    return spec.script


def preview_definition(spec: CustomCheckSpec) -> CustomCheckPreview:
    source = _source_from_spec(spec)
    definition = _definition_from_spec(spec, source)
    return CustomCheckPreview(
        definition=definition,
        inputContract=INPUT_CONTRACT,
        outputContract=OUTPUT_CONTRACT,
        warnings=[
            "Скрипт будет запускаться только в bwrap: без сети, host env и записи в проект.",
            "В палитру проверка попадает только после успешной validate/создания.",
            "Результаты custom-проверки отдельны от canonical findings и verdict.",
        ],
    )


def _fixture_project() -> tempfile.TemporaryDirectory:
    temp = tempfile.TemporaryDirectory(prefix="custom-check-fixture-")
    root = Path(temp.name)
    (root / "src" / "main" / "kotlin").mkdir(parents=True)
    (root / "src" / "main" / "kotlin" / "Sample.kt").write_text(
        "fun sample() {\n    // TODO: custom check fixture\n}\n",
        encoding="utf-8",
    )
    (root / "README.txt").write_text("custom check fixture\n", encoding="utf-8")
    return temp


def _safe_result_path(root: Path, value: Any) -> Optional[Tuple[str, int]]:
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    if _has_control_chars(value) or not _SAFE_PATH_RE.fullmatch(value):
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    try:
        base = root.resolve()
        candidate = (base / relative).resolve()
        candidate.relative_to(base)
        if not candidate.is_file() or candidate.is_symlink():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return str(relative), 0


def _contract_finding(
    raw: Any,
    index: int,
    project: Path,
    workspace: str,
    check_id: str,
    line_counts: Dict[str, int],
) -> Tuple[Optional[CustomFinding], Optional[str], Optional[str]]:
    """One finding -> (finding, None, None) or (None, error_code, message)."""
    if not isinstance(raw, dict):
        return None, "CUSTOM_FINDING_SHAPE", f"finding {index} не объект"
    rule_id = raw.get("ruleId")
    severity = raw.get("severity")
    message = raw.get("message")
    path = raw.get("path")
    start = raw.get("startLine")
    end = raw.get("endLine")
    if not isinstance(rule_id, str) or not _RULE_RE.fullmatch(rule_id):
        return None, "CUSTOM_FINDING_RULE", f"finding {index}: недопустимый ruleId"
    # `severity in set` on a list/dict raises TypeError, so the type check is
    # part of the contract, not a pre-condition.
    if not isinstance(severity, str) or severity not in _SEVERITIES:
        return None, "CUSTOM_FINDING_SEVERITY", f"finding {index}: недопустимый severity"
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_TEXT_CHARS:
        return None, "CUSTOM_FINDING_MESSAGE", f"finding {index}: недопустимый message"
    # bool is an int subclass and must not pass as a line number.
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        return None, "CUSTOM_FINDING_LINES", f"finding {index}: недопустимые строки"
    path_check = _safe_result_path(project, path)
    if path_check is None:
        return None, "CUSTOM_FINDING_PATH", f"finding {index}: path вне проекта или не файл"
    safe_path, _ = path_check
    if safe_path not in line_counts:
        try:
            with (project / safe_path).open(encoding="utf-8", errors="replace") as handle:
                line_counts[safe_path] = sum(1 for _ in handle)
        except (OSError, ValueError):
            return None, "CUSTOM_FINDING_PATH", f"finding {index}: файл недоступен"
    if end > max(1, line_counts[safe_path]):
        return None, "CUSTOM_FINDING_LINES", f"finding {index}: строка за EOF"
    return CustomFinding(
        id="custom_" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"cg-custom:{workspace}:{check_id}:{index}:{safe_path}:{start}:{rule_id}")),
        ruleId=rule_id,
        severity=Severity(severity),
        message=mask_sensitive_text(message)[:MAX_TEXT_CHARS],
        path=safe_path,
        startLine=start,
        endLine=end,
    ), None, None


def _validate_contract(stdout: str, project: Path, workspace: str, check_id: str) -> Tuple[List[CustomFinding], List[Dict[str, Any]], Optional[str]]:
    errors: List[Dict[str, Any]] = []
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return [], [{"code": "CUSTOM_RESULT_JSON", "message": "stdout не является JSON-объектом"}], "contract_error"
    if not isinstance(payload, dict):
        return [], [{"code": "CUSTOM_RESULT_SHAPE", "message": "Результат должен быть JSON-объектом"}], "contract_error"
    if payload.get("schemaVersion") != OUTPUT_CONTRACT:
        errors.append({"code": "CUSTOM_RESULT_VERSION", "message": f"Ожидается {OUTPUT_CONTRACT}"})
    summary = payload.get("summary", "")
    if summary is not None and (not isinstance(summary, str) or len(summary) > 2000):
        errors.append({"code": "CUSTOM_RESULT_SUMMARY", "message": "summary должен быть строкой до 2000 символов"})
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        errors.append({"code": "CUSTOM_RESULT_FINDINGS", "message": "findings должен быть списком"})
        raw_findings = []
    if len(raw_findings) > MAX_FINDINGS:
        errors.append({"code": "CUSTOM_RESULT_TOO_MANY", "message": f"Допустимо не более {MAX_FINDINGS} findings"})
        raw_findings = raw_findings[:MAX_FINDINGS]
    findings: List[CustomFinding] = []
    line_counts: Dict[str, int] = {}
    for index, raw in enumerate(raw_findings):
        try:
            finding, code, detail = _contract_finding(
                raw, index, project, workspace, check_id, line_counts,
            )
        except Exception as exc:  # noqa: BLE001 - malformed JSON must stay recordable
            finding, code, detail = (
                None,
                "CUSTOM_FINDING_INVALID",
                f"finding {index}: некорректный формат ({type(exc).__name__})",
            )
        if finding is None:
            errors.append({"code": code or "CUSTOM_FINDING_INVALID", "message": detail or "некорректный finding"})
        else:
            findings.append(finding)
    return findings, errors, ("contract_error" if errors else None)


def _result_summary(stdout: str) -> str:
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return ""
    summary = payload.get("summary") if isinstance(payload, dict) else ""
    return summary[:2000] if isinstance(summary, str) else ""


def _outcome_result(
    definition: CustomCheckDefinition,
    outcome: SandboxOutcome,
    project: Optional[Path],
    workspace: str,
    artifact_id: Optional[str] = None,
) -> CustomCheckRunResult:
    findings: List[CustomFinding] = []
    diagnostics = list(outcome.diagnostics)
    status = outcome.status
    if status == "ok":
        if project is None:
            diagnostics.append({"code": "CUSTOM_CONTRACT", "message": "Нет проекта для проверки path"})
            status = "contract_error"
        else:
            try:
                findings, contract_errors, contract_status = _validate_contract(
                    outcome.stdout, project, workspace, definition.id,
                )
            except Exception as exc:  # noqa: BLE001 - malformed output is a contract error
                findings = []
                contract_errors = [{
                    "code": "CUSTOM_CONTRACT_INTERNAL",
                    "message": f"Не удалось проверить контракт: {type(exc).__name__}",
                }]
                contract_status = "contract_error"
            diagnostics.extend(contract_errors)
            status = contract_status or "succeeded"
    return CustomCheckRunResult(
        id=str(uuid.uuid4()),
        definitionId=definition.id,
        artifactId=artifact_id,
        status=status,
        exitCode=outcome.exit_code,
        durationMs=outcome.duration_ms,
        stdout=mask_sensitive_text(outcome.stdout)[: definition.maxOutputBytes],
        stderr=mask_sensitive_text(outcome.stderr)[: definition.maxOutputBytes],
        summary=_result_summary(outcome.stdout),
        findings=findings,
        diagnostics=diagnostics[:50],
        limits=outcome.limits,
    )


def ensure_sandbox_available() -> None:
    """Refuse to pretend a check can run when bwrap cannot isolate."""
    available, reason = sandbox_availability()
    if not available:
        raise SandboxUnavailable(reason)


def validate_definition(spec: CustomCheckSpec, workspace: str) -> CustomCheckValidation:
    preview = preview_definition(spec)
    source = _source_from_spec(spec)
    definition = preview.definition
    ensure_sandbox_available()
    fixture = _fixture_project()
    try:
        try:
            outcome = run_sandbox(
                source,
                fixture.name,
                {"id": "fixture", "filename": "fixture", "file_count": 2, "hash_sha256": ""},
                SandboxLimits(definition.timeoutSeconds, definition.memoryMb, definition.maxOutputBytes),
            )
        except SandboxUnavailable:
            raise
        except SandboxError as exc:
            outcome = SandboxOutcome("error", None, 0, diagnostics=[{"code": "CUSTOM_SANDBOX", "message": str(exc)}])
        run = _outcome_result(definition, outcome, Path(fixture.name), workspace)
    finally:
        fixture.cleanup()
    return CustomCheckValidation(valid=run.status == "succeeded", preview=preview, run=run)


def create_definition(
    spec: CustomCheckSpec,
    workspace: str,
    created_by: str,
    store: Optional[CustomCheckStore] = None,
) -> CustomCheckDefinition:
    validation = validate_definition(spec, workspace)
    if not validation.valid:
        raise CustomCheckValidationError(
            "Проверка не прошла validate: " + "; ".join(
                str(item.get("message") or item.get("code")) for item in validation.run.diagnostics[:3]
            )
        )
    source = _source_from_spec(spec)
    definition = validation.preview.definition
    repository = store or CustomCheckStore()
    return repository.create_definition(
        workspace,
        name=definition.name,
        version=definition.version,
        source_kind=definition.sourceKind,
        template_id=definition.templateId,
        source_text=source,
        source_sha256=definition.sourceSha256,
        timeout_seconds=definition.timeoutSeconds,
        memory_mb=definition.memoryMb,
        max_output_bytes=definition.maxOutputBytes,
        created_by=created_by,
        last_validated_at=datetime.now(timezone.utc),
    )


def _verify_snapshot(workspace: str, artifact: Dict[str, Any]) -> Path:
    value = str(artifact.get("extracted_path") or "").strip()
    if not value:
        raise CustomCheckValidationError("У артефакта нет extracted_path")
    raw = Path(value)
    if raw.is_symlink():
        raise CustomCheckValidationError("Каталог артефакта не может быть symlink")
    path = raw.resolve()
    if not path.is_dir():
        raise CustomCheckValidationError("Артефакт недоступен")
    store = CodeFactsStore()
    try:
        meta = ensure_artifact_meta(store, workspace, artifact)
        if meta.get("artifactHash") and artifact.get("hash_sha256") and str(meta["artifactHash"]) != str(artifact["hash_sha256"]):
            raise CustomCheckValidationError("Хэш артефакта не совпадает с зафиксированным snapshot")
        snapshot = store.load_snapshot(workspace, str(meta.get("snapshotId") or ""))
        if not snapshot:
            raise CustomCheckValidationError("Snapshot артефакта не найден")
        current = build_snapshot_from_directory(path, workspace)
    except CustomCheckValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CustomCheckValidationError(f"Snapshot артефакта не прошёл проверку: {exc}") from exc
    if current.get("manifestHash") != snapshot.get("manifestHash"):
        raise CustomCheckValidationError("Snapshot изменился после приёма; запуск запрещён")
    return path


def run_definition(
    definition: CustomCheckDefinition,
    source: str,
    workspace: str,
    artifact_id: str,
    db: DatabaseRepository,
    store: Optional[CustomCheckStore] = None,
    workflow_run_id: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
) -> CustomCheckRunResult:
    ensure_sandbox_available()
    if cancel_event is not None and cancel_event.is_set():
        raise SandboxCancelled("Проверка отменена до запуска")
    artifact = db.get_artifact(artifact_id)
    if not artifact:
        raise CustomCheckNotFound("Артефакт не найден в текущем workspace")
    project = _verify_snapshot(workspace, artifact)
    try:
        outcome = run_sandbox(
            source,
            str(project),
            artifact,
            SandboxLimits(definition.timeoutSeconds, definition.memoryMb, definition.maxOutputBytes),
            cancel_event=cancel_event,
        )
    except SandboxCancelled:
        raise
    except SandboxUnavailable:
        raise
    except SandboxError as exc:
        raise CustomCheckValidationError(f"Sandbox отклонил запуск: {exc}") from None
    if cancel_event is not None and cancel_event.is_set():
        raise SandboxCancelled("Проверка отменена")
    result = _outcome_result(definition, outcome, project, workspace, artifact_id=artifact_id)
    result.workflowRunId = workflow_run_id
    repository = store or CustomCheckStore()
    persisted = repository.save_run(
        workspace,
        result=result,
        artifact_hash=artifact.get("hash_sha256"),
        workflow_run_id=workflow_run_id,
    )
    if not persisted:
        # The workflow run already left `running`: a late thread must not leave
        # a success fact behind an owned, cancelled execution.
        raise SandboxCancelled("Workflow run уже не выполняется; результат не записан")
    return result
