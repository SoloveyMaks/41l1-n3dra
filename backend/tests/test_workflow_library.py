import pytest
from fastapi import HTTPException

from backend.app.api.workflow_library import (
    _clear_project_artifact,
    _template_view,
    _validate_draft,
)
from backend.app.storage.workflow_library import sanitize_template_draft

DRAFT = {
    "schemaVersion": 1,
    "name": "demo",
    "description": "",
    "nodes": [
        {
            "id": "n1",
            "kind": "project_input",
            "version": 1,
            "title": "Project",
            "config": {"artifactId": "art_secret_ref", "secretToken": "s3cr3t"},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "n2",
            "kind": "manual_trigger",
            "version": 1,
            "title": "Start",
            "config": {"webhookSecret": "hook-secret"},
            "position": {"x": 0, "y": 120},
        },
    ],
    "edges": [],
}


def test_sanitize_strips_node_secrets_recursively():
    cleaned = sanitize_template_draft(DRAFT)
    assert "secretToken" not in cleaned["nodes"][0]["config"]
    assert "webhookSecret" not in cleaned["nodes"][1]["config"]


def test_copy_clears_project_artifact_reference():
    cleaned = _clear_project_artifact(sanitize_template_draft(DRAFT))
    assert "artifactId" not in cleaned["nodes"][0]["config"]


def test_invalid_draft_is_rejected():
    with pytest.raises(HTTPException) as error:
        _validate_draft({"schemaVersion": 1, "name": "", "description": "", "nodes": [], "edges": []})
    assert error.value.status_code == 422


def test_template_view_hides_draft_by_default():
    view = _template_view({
        "id": "tpl_1",
        "name": "Demo",
        "description": "",
        "publishedBy": "",
        "publishedAt": "2026-11-22T00:00:00+00:00",
        "draft": DRAFT,
    })
    assert "draft" not in view
    assert view["nodeCount"] == 2


def test_copy_disables_notify_delivery():
    from backend.app.storage.workflow_library import disable_delivery
    draft = disable_delivery(sanitize_template_draft({
        "nodes": [{
            "kind": "notify",
            "config": {
                "enabled": True,
                "webhookUrl": "https://hooks.example/secret",
                "secretToken": "s3cr3t",
            },
        }],
    }))
    config = draft["nodes"][0]["config"]
    assert config["enabled"] is False
    assert "webhookUrl" not in config
    assert "secretToken" not in config
