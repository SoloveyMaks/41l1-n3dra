import pytest

from backend.app.api.repository_hooks import _HookIgnored, _parse_event

SHA = "a" * 40
WATCH_GH = {"url": "https://github.com/acme/demo", "branch": "main"}
WATCH_GL = {"url": "https://gitlab.com/acme/demo", "branch": "main"}


def github_pr(action="opened", base="main", head_ref="feature/x"):
    return {
        "action": action,
        "pull_request": {
            "base": {"ref": base},
            "head": {"sha": SHA, "ref": head_ref},
        },
        "repository": {"html_url": "https://github.com/acme/demo"},
    }


def test_github_pull_request_opens_the_head_commit():
    sha, ref, repo = _parse_event("github", "pull_request", github_pr(), WATCH_GH)
    assert sha == SHA
    assert ref == "refs/heads/feature/x"
    assert repo == "https://github.com/acme/demo"


def test_closed_pull_request_is_ignored():
    with pytest.raises(_HookIgnored):
        _parse_event("github", "pull_request", github_pr(action="closed"), WATCH_GH)


def test_pull_request_to_another_branch_is_ignored():
    with pytest.raises(_HookIgnored):
        _parse_event("github", "pull_request", github_pr(base="release"), WATCH_GH)


def test_gitlab_merge_request_is_supported():
    payload = {
        "object_kind": "merge_request",
        "object_attributes": {
            "action": "open",
            "target_branch": "main",
            "source_branch": "fix/auth",
            "last_commit": {"id": SHA},
        },
        "project": {"web_url": "https://gitlab.com/acme/demo"},
    }
    sha, ref, _ = _parse_event("gitlab", "merge_request", payload, WATCH_GL)
    assert sha == SHA
    assert ref == "refs/heads/fix/auth"


def test_push_event_still_parses():
    payload = {
        "ref": "refs/heads/main",
        "after": SHA,
        "repository": {"html_url": "https://github.com/acme/demo"},
    }
    sha, ref, _ = _parse_event("github", "push", payload, WATCH_GH)
    assert sha == SHA
    assert ref == "refs/heads/main"
