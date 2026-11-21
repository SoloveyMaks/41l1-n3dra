"""DTOs for custom check definitions, validation and isolated runs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.app.contracts.schemas import Severity


class CustomCheckSpec(BaseModel):
    name: str = ""
    version: str = "1.0.0"
    language: str = "python"
    runner: str = "bwrap-python3"
    sourceKind: str = "script"  # "script" | "template"
    script: str = ""
    templateId: Optional[str] = None
    timeoutSeconds: int = 3
    memoryMb: int = 256
    maxOutputBytes: int = 16384


class CustomCheckDefinition(BaseModel):
    id: str
    name: str
    version: str
    language: str
    runner: str
    sourceKind: str
    templateId: Optional[str] = None
    sourceSha256: str
    timeoutSeconds: int
    memoryMb: int
    maxOutputBytes: int
    createdBy: str
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    lastValidatedAt: Optional[str] = None


class CustomCheckPreview(BaseModel):
    definition: CustomCheckDefinition
    inputContract: str
    outputContract: str
    warnings: List[str] = Field(default_factory=list)


class CustomFinding(BaseModel):
    id: str
    origin: str = "custom_check"
    ruleId: str
    severity: Severity
    message: str
    path: str
    startLine: int
    endLine: int


class CustomCheckRunResult(BaseModel):
    id: str
    definitionId: str
    artifactId: Optional[str] = None
    workflowRunId: Optional[str] = None
    status: str  # succeeded | failed | timeout | output_limit | contract_error | error
    exitCode: Optional[int] = None
    durationMs: int = 0
    stdout: str = ""
    stderr: str = ""
    summary: str = ""
    findings: List[CustomFinding] = Field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = Field(default_factory=list)
    limits: Dict[str, Any] = Field(default_factory=dict)
    countsAsFindings: bool = False
    origin: str = "custom_check"


class CustomCheckSandboxStatus(BaseModel):
    available: bool
    language: str = "python"
    runner: str = "bwrap-python3"
    reason: str = ""


class CustomCheckRunSummary(BaseModel):
    id: str
    definitionId: str
    definitionName: str = ""
    definitionVersion: str = ""
    status: str
    durationMs: int = 0
    createdAt: Optional[str] = None
    findings: List[CustomFinding] = Field(default_factory=list)
    countsAsFindings: bool = False
    origin: str = "custom_check"


class CustomCheckValidation(BaseModel):
    valid: bool
    preview: CustomCheckPreview
    run: CustomCheckRunResult


class CustomCheckRunRequest(BaseModel):
    artifactId: str
