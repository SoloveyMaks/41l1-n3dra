import pytest

from backend.app.api.comparison import ComparisonError, ensure_same_repository


def test_two_known_different_repositories_are_rejected():
    with pytest.raises(ComparisonError) as error:
        ensure_same_repository("https://github.com/acme/one", "https://github.com/acme/two")
    assert error.value.status_code == 409
    assert error.value.code == "DIFFERENT_REPOSITORIES"


def test_same_repository_is_allowed():
    ensure_same_repository("https://github.com/acme/one", "https://github.com/acme/one")


def test_missing_repository_identity_keeps_zip_comparison():
    ensure_same_repository(None, "https://github.com/acme/two")
    ensure_same_repository("https://github.com/acme/one", None)
    ensure_same_repository(None, None)
