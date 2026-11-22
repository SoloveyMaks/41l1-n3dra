from backend.app.codefacts.coverage import expected_scope_categories, expected_scopes_verified


def workflow(*kinds):
    return {"draft": {"nodes": [{"kind": kind} for kind in kinds]}}


def envelope(*statuses_by_category):
    results = []
    for category, statuses in statuses_by_category:
        results.append({
            "codeFacts": {
                "coverage": {
                    "scopes": [
                        {"category": category, "status": status, "path": f"src/F{i}.kt"}
                        for i, status in enumerate(statuses)
                    ]
                }
            }
        })
    return {"scanResults": results}


def test_all_requested_categories_checked():
    result = expected_scopes_verified(
        envelope(("SECRET", ["checked"]), ("SCA", ["checked"]), ("SAST", ["checked", "checked"])),
        workflow("secret_scan", "sca_scan", "kotlin_sast"),
    )
    assert result is True


def test_missing_requested_category_is_not_verified():
    result = expected_scopes_verified(
        envelope(("SECRET", ["checked"]), ("SAST", ["checked"])),
        workflow("secret_scan", "sca_scan", "kotlin_sast"),
    )
    assert result is False


def test_skipped_scope_blocks_verification():
    result = expected_scopes_verified(
        envelope(("SECRET", ["checked"]), ("SCA", ["checked", "skipped"]), ("SAST", ["checked"])),
        workflow("secret_scan", "sca_scan", "kotlin_sast"),
    )
    assert result is False


def test_workflow_without_scanners_is_not_verified():
    assert expected_scope_categories(workflow("project_input")) == set()
    assert expected_scopes_verified(envelope(("SAST", ["checked"])), workflow("project_input")) is False


def test_unavailable_status_is_not_verified():
    result = expected_scopes_verified(
        envelope(("SECRET", ["unavailable"])),
        workflow("secret_scan"),
    )
    assert result is False


def test_builder_coverage_shape_is_supported():
    result = {
        "scanResults": [
            {"coverage": {"scopes": [{"category": "SAST", "status": "checked"}]}},
            {"coverage": {"scopes": [{"category": "SECRET", "status": "checked"}]}},
            {"coverage": {"scopes": [{"category": "SCA", "status": "checked"}]}},
        ]
    }
    assert expected_scopes_verified(result, workflow("secret_scan", "sca_scan", "kotlin_sast")) is True
