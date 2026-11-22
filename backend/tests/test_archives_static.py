"""Безопасное чтение архивов и бинарных файлов snapshot (binaries-and-archives)."""

import io
import zipfile
from pathlib import Path

from backend.app.codefacts.archives_static import (
    scan_archive,
    scan_archives_and_binaries,
)


def make_zip(path: Path, entries: dict, *, compression=zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_zip_with_secret_yields_finding_and_checked_scope(tmp_path: Path):
    archive = make_zip(tmp_path / "app.jar", {
        "BOOT-INF/classes/application.properties": "db.password=SuperSecret99\n",
    })
    outcome = scan_archive(archive, "lib/app.jar")

    assert outcome.skipped is None
    assert outcome.scope["ruleId"] == "CF-ARCHIVE-STATIC"
    assert outcome.scope["status"] == "checked"
    assert outcome.scope["reason"].startswith("Архив прочитан без исполнения")
    assert [f.ruleId for f in outcome.findings] == ["CF-ARCHIVE-SECRET"]
    finding = outcome.findings[0]
    # Якорь — файл архива в манифесте; entry и строка — в описании.
    assert finding.primaryLocation.path == "lib/app.jar"
    assert finding.primaryLocation.byteRange == [0, 0]
    assert "application.properties:1" in finding.severityBasis
    assert "SuperSecret99" not in finding.redactedEvidence


def test_zip_dangerous_config_yields_config_finding(tmp_path: Path):
    archive = make_zip(tmp_path / "app.jar", {
        "config/application.yml": "management:\n  endpoints:\n    web:\n      exposure:\n        include: \"*\"\n",
    })
    outcome = scan_archive(archive, "lib/app.jar")
    assert "CF-CONFIG-ACTUATOR-EXPOSED" in {f.ruleId for f in outcome.findings}


def test_clean_zip_is_quiet(tmp_path: Path):
    archive = make_zip(tmp_path / "app.jar", {
        "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\nImplementation-Title: demo\n",
    })
    outcome = scan_archive(archive, "lib/app.jar")
    assert outcome.findings == []
    assert outcome.scope["status"] == "checked"


def test_encrypted_zip_has_explicit_skip_reason(tmp_path: Path):
    # zipfile не пишет zipcrypto-шифр без pyzipper, поэтому имитируем
    # encrypted entry через flag_bits: собираем архив вручную.
    archive = tmp_path / "enc.jar"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("secret.txt", "password=hunter2hunter2")
    data = bytearray(buf.getvalue())
    # Флаги читаются из central directory header (PK\x01\x02), смещение +8.
    offset = data.index(b"PK\x01\x02")
    data[offset + 8] |= 0x1
    # И из local file header (PK\x03\x04), смещение +6.
    local = data.index(b"PK\x03\x04")
    data[local + 6] |= 0x1
    archive.write_bytes(bytes(data))

    outcome = scan_archive(archive, "lib/enc.jar")
    assert outcome.scope is None
    assert outcome.skipped is not None
    assert outcome.skipped.startswith("lib/enc.jar · CF-ARCHIVE-STATIC: skipped —")
    assert outcome.findings == []


def test_oversized_archive_has_explicit_skip_reason(tmp_path: Path, monkeypatch):
    import backend.app.codefacts.archives_static as mod

    archive = make_zip(tmp_path / "big.jar", {"a.txt": "x" * 100})
    monkeypatch.setattr(mod, "MAX_ARCHIVE_BYTES", 10)
    outcome = scan_archive(archive, "lib/big.jar")
    assert outcome.scope is None
    assert "too-large" in outcome.skipped
    assert "lib/big.jar" in outcome.skipped


def test_not_a_zip_has_explicit_skip_reason(tmp_path: Path):
    archive = tmp_path / "broken.jar"
    archive.write_bytes(b"not a zip at all")
    outcome = scan_archive(archive, "lib/broken.jar")
    assert outcome.scope is None
    assert "unsupported: not a valid zip/jar/war/aar" in outcome.skipped


def test_zip_slip_entry_is_not_read(tmp_path: Path):
    archive = make_zip(tmp_path / "app.jar", {"../../etc/passwd": "root:x:0:0\n"})
    outcome = scan_archive(archive, "lib/app.jar")
    assert outcome.findings == []
    assert outcome.scope["status"] == "checked"


def test_nested_archive_is_not_opened(tmp_path: Path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("x.properties", "db.password=InnerSecret99\n")
    archive = make_zip(tmp_path / "app.jar", {"lib/inner.jar": inner.getvalue()})
    outcome = scan_archive(archive, "lib/app.jar")
    # Глубина 1: вложенный архив не раскрывается, значит и находок из него нет.
    assert outcome.findings == []
    assert outcome.scope["status"] == "checked"


def test_binary_and_unread_text_scopes(tmp_path: Path):
    (tmp_path / "src/main/kotlin").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "src/main/kotlin/App.kt").write_text("class App\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    (tmp_path / "docs/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (tmp_path / "lib").mkdir()
    make_zip(tmp_path / "lib" / "app.jar", {"META-INF/MANIFEST.MF": "Manifest-Version: 1.0\n"})
    findings, scopes, skipped = scan_archives_and_binaries(
        tmp_path,
        ["src/main/kotlin/App.kt", "docs/logo.png", "README.md", "lib/app.jar"],
        read_paths=["src/main/kotlin/App.kt"],
    )
    by_path = {s["path"]: s for s in scopes}
    assert by_path["docs/logo.png"]["ruleId"] == "CF-BINARY-STATIC"
    assert by_path["docs/logo.png"]["status"] == "not_applicable"
    assert "Бинарный файл" in by_path["docs/logo.png"]["reason"]
    assert by_path["README.md"]["ruleId"] == "CF-FILE-STATIC"
    assert by_path["README.md"]["status"] == "not_applicable"
    assert by_path["lib/app.jar"]["ruleId"] == "CF-ARCHIVE-STATIC"
    assert by_path["lib/app.jar"]["status"] == "checked"
    # Прочитанный исходник не получает второго scope.
    assert "src/main/kotlin/App.kt" not in by_path
    assert findings == []
    assert skipped == []


def test_missing_archive_is_skipped_with_path(tmp_path: Path):
    _findings, scopes, skipped = scan_archives_and_binaries(
        tmp_path, ["lib/gone.jar"], read_paths=[],
    )
    assert scopes == []
    assert skipped == ["lib/gone.jar · CF-ARCHIVE-STATIC: skipped — файл отсутствует"]


def test_no_root_means_no_claims():
    findings, scopes, skipped = scan_archives_and_binaries(
        None, ["lib/app.jar", "docs/logo.png"], read_paths=[],
    )
    assert (findings, scopes, skipped) == ([], [], [])
