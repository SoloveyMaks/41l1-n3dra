from backend.app.api.comparison import CROSS_REPOSITORY_REASON, apply_cross_repository


def sample_result():
    return {
        "comparable": True,
        "reasons": [],
        "deletedFiles": [{"path": "src/A.kt"}],
        "items": [
            {"status": "resolved", "reason": "anchor missing"},
            {"status": "introduced", "reason": ""},
            {"status": "persistent", "reason": "same"},
            {"status": "inconclusive", "reason": "unknown"},
        ],
    }


def test_different_artifacts_are_not_a_fix():
    result = apply_cross_repository(sample_result(), "https://github.com/acme/one", "https://github.com/acme/two")
    assert result["comparable"] is False
    assert result["reasons"][0] == CROSS_REPOSITORY_REASON
    assert result["deletedFiles"] == []
    statuses = [item["status"] for item in result["items"]]
    assert statuses == ["inconclusive", "inconclusive", "persistent", "inconclusive"]
    assert result["items"][0]["reason"].startswith(CROSS_REPOSITORY_REASON)


def test_same_artifact_keeps_normal_comparison():
    result = sample_result()
    unchanged = apply_cross_repository(result, "https://github.com/acme/one", "https://github.com/acme/one")
    assert unchanged["comparable"] is True
    assert unchanged["reasons"] == []
    assert unchanged["deletedFiles"] == [{"path": "src/A.kt"}]
    assert [item["status"] for item in unchanged["items"]] == [
        "resolved", "introduced", "persistent", "inconclusive",
    ]


def test_missing_hashes_do_not_trigger_cross_repository_mode():
    result = sample_result()
    assert apply_cross_repository(result, None, "https://github.com/acme/two")["comparable"] is True
