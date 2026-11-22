from backend.app.ai.provider import mask_sensitive_text


def test_yaml_block_scalar_password_is_masked():
    source = "\n".join([
        "spring:",
        "  datasource:",
        "    password: |-",
        "      hunter2-with-extra-bits",
        "      second-line",
        "    url: jdbc:postgresql://db/app",
    ])
    masked = mask_sensitive_text(source)
    assert "hunter2" not in masked
    assert "second-line" not in masked
    assert "jdbc:postgresql://db/app" in masked
    assert 'password: "***"' in masked


def test_yaml_block_scalar_folded_style_is_masked():
    source = "api_token: >-\n  sk-block-secret-value\nnext: ok"
    masked = mask_sensitive_text(source)
    assert "sk-block-secret-value" not in masked
    assert "next: ok" in masked


def test_non_secret_block_scalar_is_kept():
    source = "description: |\n  ordinary prose\n  second line"
    assert mask_sensitive_text(source) == source


def test_inline_secret_still_masked():
    masked = mask_sensitive_text("password: hunter2\nserver: ok")
    assert "hunter2" not in masked
    assert "server: ok" in masked
