import json

from backend.app.reports.exports import ExportBundle, build_jsonc


def make_bundle() -> ExportBundle:
    return ExportBundle(
        run={
            "id": "run_jsonc_1",
            "workflow_id": "wf_demo",
            "revision_id": "rev_demo_1",
            "artifact_id": "art_1",
            "trigger_type": "manual",
            "execution_status": "succeeded",
            "security_verdict": "fail",
            "created_at": None,
            "started_at": None,
            "finished_at": None,
        },
        report=None,
        findings=[],
        ai_scan={},
        passport={},
        policy={},
        official={},
        workflow_name="Demo scenario",
        counts={"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
        technical={"url": "https://example.test/api"},
    )


def test_jsonc_starts_with_explanatory_comments_and_valid_body():
    text = build_jsonc(make_bundle()).decode("utf-8")
    lines = text.splitlines()
    header = [line for line in lines if line.startswith("//")]
    assert header and "NEDRA" in header[0]
    body = "\n".join(line for line in lines if not line.startswith("//"))
    document = json.loads(body)
    assert document["run"]["id"] == "run_jsonc_1"
    assert document["technicalProvenance"]["url"].startswith("https://")


def test_jsonc_comments_do_not_swallow_slash_slash_values():
    bundle = make_bundle()
    bundle.findings.append({"path": "src//nested/File.kt", "severity": "high"})
    text = build_jsonc(bundle).decode("utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.startswith("//"))
    document = json.loads(body)
    assert document["deterministic"]["findings"][0]["path"] == "src//nested/File.kt"


def test_multiline_workflow_name_does_not_break_comments():
    import dataclasses
    bundle = dataclasses.replace(make_bundle(), workflow_name="Первый\nВторой")
    text = build_jsonc(bundle).decode("utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.startswith("//"))
    document = json.loads(body)
    assert document["run"]["id"] == "run_jsonc_1"
