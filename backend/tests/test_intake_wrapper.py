import io
import zipfile
from pathlib import Path

from backend.app.artifacts.intake import SafeArtifactIntake, _detect_wrapper_root


def make_zip(paths):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in paths:
            archive.writestr(path, "class X")
    return buffer.getvalue()


def test_source_only_wrapper_is_the_project_root(tmp_path):
    paths = [
        "training-project/src/main/kotlin/lab/repairdesk/integration/DepotStatusClient.kt",
    ]
    assert _detect_wrapper_root(paths) == "training-project"
    manifest = SafeArtifactIntake(storage_root=tmp_path).ingest_zip_bytes(
        "training-project.zip", make_zip(paths)
    )
    assert manifest["source_root_prefix"] == "training-project"
    root = Path(manifest["extracted_path"])
    assert (root / "src/main/kotlin/lab/repairdesk/integration/DepotStatusClient.kt").is_file()
    assert not (root / "training-project").exists()


def test_flat_src_archive_keeps_src(tmp_path):
    paths = ["src/main/kotlin/lab/repairdesk/integration/DepotStatusClient.kt"]
    assert _detect_wrapper_root(paths) is None
    manifest = SafeArtifactIntake(storage_root=tmp_path).ingest_zip_bytes(
        "flat.zip", make_zip(paths)
    )
    assert manifest["source_root_prefix"] == ""
    assert (Path(manifest["extracted_path"]) / paths[0]).is_file()


def test_multiple_trees_are_not_collapsed(tmp_path):
    paths = ["app/src/A.kt", "lib/src/B.kt"]
    assert _detect_wrapper_root(paths) is None


def test_training_bundle_selects_project_subtree(tmp_path):
    paths = [
        "README.md",
        "ground-truth/training-findings.json",
        "docs/submission-format.md",
        "vulnerability-database/vulnerabilities.json",
        "training-project/settings.gradle.kts",
        "training-project/build.gradle.kts",
        "training-project/src/main/kotlin/X.kt",
    ]
    assert _detect_wrapper_root(paths) == "training-project"
    manifest = SafeArtifactIntake(storage_root=tmp_path).ingest_zip_bytes(
        "bundle.zip", make_zip(paths)
    )
    assert manifest["source_root_prefix"] == "training-project"
    root = Path(manifest["extracted_path"])
    assert (root / "src/main/kotlin/X.kt").is_file()
    assert (root / "build.gradle.kts").is_file()
    assert not (root / "ground-truth").exists()
    assert not (root / "README.md").exists()
