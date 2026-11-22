"""Detect whether an artifact is a supported Kotlin/Spring Boot Gradle project.

The analyzer is intentionally narrow: running it on a random ZIP or a Java-only
project wastes a full run and produces a meaningless empty coverage. Uploads
stay allowed for inspection; the run gate asks this module before dispatching.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

MAX_FILES = 20000
MAX_BUILD_BYTES = 512 * 1024
EXCLUDED_DIRS = {
    ".git", ".gradle", ".idea", "build", "out", "target", "node_modules",
    "dist", "bin", "obj", "__pycache__", ".venv", "venv", "ground-truth",
    "vulnerability-database",
}
BUILD_NAMES = {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
KOTLIN_SUFFIXES = {".kt"}
JAVA_SUFFIXES = {".java"}


def _read_build_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_BUILD_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def detect_project_kind(root: Path) -> Dict[str, Any]:
    kotlin_files = 0
    java_files = 0
    gradle_files = 0
    pom_files = 0
    spring_boot = False
    kotlin_build = False
    evidence: List[str] = []
    inspected = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            inspected += 1
            if inspected > MAX_FILES:
                break
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            suffix = path.suffix.lower()
            if suffix in KOTLIN_SUFFIXES:
                kotlin_files += 1
                if len(evidence) < 12:
                    evidence.append(rel)
                continue
            if suffix in JAVA_SUFFIXES:
                java_files += 1
                continue
            lower = name.lower()
            if lower in BUILD_NAMES or lower == "gradle.properties":
                text = _read_build_text(path)
                if lower.startswith("build.gradle"):
                    gradle_files += 1
                    if "org.jetbrains.kotlin" in text or "kotlin(" in text or "kotlin_version" in text:
                        kotlin_build = True
                    if "org.springframework.boot" in text or "spring-boot" in text:
                        spring_boot = True
                elif lower.startswith("settings.gradle"):
                    gradle_files += 1
            elif lower == "pom.xml":
                pom_files += 1
                text = _read_build_text(path)
                if "kotlin" in text.lower():
                    kotlin_build = True
                if "spring-boot" in text.lower():
                    spring_boot = True
            elif lower == "libs.versions.toml":
                text = _read_build_text(path)
                if "kotlin" in text.lower():
                    kotlin_build = True
                if "spring" in text.lower() and "boot" in text.lower():
                    spring_boot = True
        if inspected > MAX_FILES:
            break

    has_build = gradle_files > 0 or pom_files > 0
    supported = kotlin_files > 0 and has_build
    if supported and spring_boot:
        language = "kotlin"
        framework = "spring-boot"
        confidence = "high"
    elif supported:
        language = "kotlin"
        framework = "none"
        confidence = "medium" if kotlin_build else "low"
    elif kotlin_files > 0:
        language = "kotlin"
        framework = "none"
        confidence = "low"
    elif java_files > 0:
        language = "java"
        framework = "none"
        confidence = "high"
    else:
        language = "other"
        framework = "none"
        confidence = "high"
    reasons: List[str] = []
    if kotlin_files == 0:
        reasons.append("Kotlin-файлов не найдено")
    if not has_build:
        reasons.append("Gradle/Maven сборка не найдена")
    if kotlin_files and not spring_boot:
        reasons.append("Spring Boot не найден — проверка ограничится общими правилами Kotlin")
    return {
        "supported": supported,
        "language": language,
        "framework": framework,
        "confidence": confidence,
        "kotlinFiles": kotlin_files,
        "javaFiles": java_files,
        "gradleFiles": gradle_files,
        "pomFiles": pom_files,
        "springBoot": spring_boot,
        "evidencePaths": evidence,
        "reasons": reasons,
    }


def unsupported_project_message(kind: Dict[str, Any]) -> str:
    return (
        "Это не поддерживаемый Kotlin/Spring Boot Gradle проект: "
        f"Kotlin-файлов — {kind.get('kotlinFiles', 0)}, "
        f"Gradle-файлов — {kind.get('gradleFiles', 0)}, "
        f"Java-файлов — {kind.get('javaFiles', 0)}. "
        + "; ".join(kind.get("reasons") or [])
    )


def project_kind_for_artifact(store: Any, workspace: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Use cached metadata; on legacy artifacts detect once and cache it."""
    artifact_id = str(artifact.get("id") or "")
    meta = store.load_artifact_meta(workspace, artifact_id) or {} if artifact_id else {}
    cached = meta.get("projectKind")
    if isinstance(cached, dict) and "supported" in cached:
        return cached
    extracted = artifact.get("extracted_path")
    if not extracted or not Path(extracted).is_dir():
        return {
            "supported": True,
            "language": "unknown",
            "framework": "none",
            "confidence": "low",
            "kotlinFiles": 0,
            "javaFiles": 0,
            "gradleFiles": 0,
            "pomFiles": 0,
            "springBoot": False,
            "evidencePaths": [],
            "reasons": ["Не удалось прочитать распакованный артефакт — проверка не блокируется"],
        }
    kind = detect_project_kind(Path(extracted))
    meta["projectKind"] = kind
    try:
        store.save_artifact_meta(workspace, artifact_id, meta)
    except Exception:  # noqa: BLE001 - cache write must not break the run gate
        pass
    return kind
