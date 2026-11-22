from backend.app.engine.notify_policy import validate_webhook_url


def test_loopback_requires_server_allowlist(monkeypatch):
    monkeypatch.delenv("CG_NOTIFY_ALLOW_LOCAL_HOSTS", raising=False)
    decision = validate_webhook_url(
        "http://127.0.0.1:18080/hook", allow_local=True, allow_external=False
    )
    assert decision.allowed is False
    assert "серверной настройкой" in decision.reason


def test_loopback_allowed_only_for_exact_destination(monkeypatch):
    monkeypatch.setenv("CG_NOTIFY_ALLOW_LOCAL_HOSTS", "127.0.0.1:18080")
    allowed = validate_webhook_url(
        "http://127.0.0.1:18080/hook", allow_local=True, allow_external=False
    )
    assert allowed.allowed is True and allowed.local is True
    denied = validate_webhook_url(
        "http://127.0.0.1:18081/hook", allow_local=True, allow_external=False
    )
    assert denied.allowed is False


def test_localhost_alias_matches_server_entry(monkeypatch):
    monkeypatch.setenv("CG_NOTIFY_ALLOW_LOCAL_HOSTS", "127.0.0.1")
    decision = validate_webhook_url(
        "http://localhost:8080/hook", allow_local=True, allow_external=False
    )
    assert decision.allowed is True


def test_user_flag_alone_cannot_enable_loopback(monkeypatch):
    monkeypatch.delenv("CG_NOTIFY_ALLOW_LOCAL_HOSTS", raising=False)
    decision = validate_webhook_url(
        "http://localhost:9000/hook", allow_local=True, allow_external=True
    )
    assert decision.allowed is False


def test_private_network_is_not_loopback(monkeypatch):
    monkeypatch.setenv("CG_NOTIFY_ALLOW_LOCAL_HOSTS", "10.0.0.5")
    decision = validate_webhook_url(
        "http://10.0.0.5/hook", allow_local=True, allow_external=False
    )
    assert decision.allowed is False
    assert "loopback" in decision.reason


def test_global_https_stays_allowed_with_external_flag():
    decision = validate_webhook_url(
        "https://93.184.216.34/hook", allow_local=False, allow_external=True
    )
    assert decision.allowed is True and decision.local is False
