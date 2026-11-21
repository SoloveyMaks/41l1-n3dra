"""API DTOs for the scenario builder."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from backend.app.contracts.schemas import WorkflowDraft, WorkflowValidationResult


class ScenarioPromptRequest(BaseModel):
    prompt: str = Field(default="")


class ScenarioPreview(BaseModel):
    draft: WorkflowDraft
    validation: WorkflowValidationResult
    summary: str
    warnings: List[str] = Field(default_factory=list)
    source: str = "ai"
    intents: List[str] = Field(default_factory=list)
