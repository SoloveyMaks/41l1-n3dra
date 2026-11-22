"""SCA analyzer: declared Gradle dependencies against the provided advisory snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .gradle import GradleModel
from .model import Diagnostic, Finding, RuleInfo
from .project import Project
from .versions import describe_range, in_any_range, parse_version

RULES = [
    RuleInfo(
        "CG26-SCA-001",
        "SCA",
        "1",
        "Объявленная версия зависимости входит в уязвимый диапазон advisory",
        "known-vulnerable-dependency",
    )
]


def locate_database(path: str | Path | None, project: Project) -> Path | None:
    """Return the advisory file this scan would use, without reading it."""

    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    candidates.extend(
        [
            project.root.parent / "vulnerability-database" / "vulnerabilities.json",
            project.root / "vulnerability-database" / "vulnerabilities.json",
            Path.cwd() / "vulnerability-database" / "vulnerabilities.json",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_database(path: str | Path | None, project: Project) -> tuple[dict[str, Any] | None, str | None]:
    candidate = locate_database(path, project)
    if candidate is None:
        return None, None
    try:
        return json.loads(candidate.read_text(encoding="utf-8")), str(candidate)
    except (OSError, json.JSONDecodeError):
        return None, None


def analyze(
    project: Project,
    database_path: str | Path | None = None,
) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []

    database, database_used = _load_database(database_path, project)
    if database is None:
        diagnostics.append(
            Diagnostic(
                code="sca.database-missing",
                level="error",
                message="Справочник уязвимостей не найден; SCA-сопоставление не выполнялось.",
            )
        )
        return findings, diagnostics

    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in database.get("vulnerabilities", []):
        index.setdefault((record["group"], record["artifact"]), []).append(record)

    model = GradleModel(project)

    for declaration in model.declarations:
        if declaration.version is None:
            reason = declaration.diagnostics[0] if declaration.diagnostics else "version-unknown"
            diagnostics.append(
                Diagnostic(
                    code="sca.unknown-version",
                    path=declaration.file,
                    line=declaration.line,
                    message=(
                        f"Версия {declaration.coordinate} не определена ({reason}); "
                        "зависимость не сопоставлялась со справочником."
                    ),
                )
            )
            continue

        version = declaration.version
        basis = declaration.version_basis
        source = declaration.version_source
        lock = model.lock.get((declaration.group, declaration.artifact))
        if lock is not None:
            version = lock.version
            basis = "lock_recorded"
            source = ("gradle.lockfile", lock.line)

        if parse_version(version) is None:
            diagnostics.append(
                Diagnostic(
                    code="sca.unknown-version",
                    path=declaration.file,
                    line=declaration.line,
                    message=(
                        f"Версия {declaration.coordinate}:{version} содержит квалификатор и "
                        "находится вне области учебного снимка; сопоставление не выполнялось."
                    ),
                )
            )
            continue

        records = index.get((declaration.group, declaration.artifact), [])
        for record in records:
            result = in_any_range(version, record.get("ranges", []))
            if result is None:
                diagnostics.append(
                    Diagnostic(
                        code="sca.unresolved-range",
                        path=declaration.file,
                        line=declaration.line,
                        message=(
                            f"Диапазон advisory {record.get('id')} не сравним численно; "
                            "находка не создавалась."
                        ),
                    )
                )
                continue
            if not result:
                continue

            aliases = [alias for alias in record.get("aliases", []) if alias != record.get("id")]
            advisory_ids = [record["id"], *aliases]
            ranges = ", ".join(describe_range(spec) for spec in record.get("ranges", []))
            fixed = ", ".join(record.get("fixedVersions", []))
            description = (
                f"`{declaration.group}:{declaration.artifact}:{version}` попадает в уязвимый "
                f"диапазон {ranges}. Advisory: {', '.join(advisory_ids)}."
            )
            remediation = (
                f"Обновить зависимость до исправленной версии: {fixed}."
                if fixed
                else "Обновить зависимость до версии вне уязвимого диапазона."
            )
            related = []
            if source is not None:
                related.append(
                    {
                        "path": source[0],
                        "line": source[1],
                        "endLine": source[1],
                    }
                )
            findings.append(
                Finding(
                    category="SCA",
                    path=declaration.file,
                    severity=record.get("severity", "MEDIUM"),
                    description=description,
                    remediation=remediation,
                    group=declaration.group,
                    artifact=declaration.artifact,
                    version=version,
                    advisory_id=record["id"],
                    line=declaration.line,
                    end_line=declaration.end_line,
                    rule_id="CG26-SCA-001",
                    confidence="confirmed" if lock is not None else "potential",
                    confidence_basis=(
                        "Версия записана в lock-файле и проверена числовым сравнением границ."
                        if lock is not None else
                        "Запрошенная версия прочитана из Gradle и проверена числовым сравнением; resolved dependency graph не проверен."
                    ),
                    severity_basis=(
                        f"Серьёзность advisory {record['id']}: {record.get('severity', 'MEDIUM')}; "
                        f"CVSS {record.get('cvss')}."
                    ),
                    related=related,
                    evidence={
                        "coordinate": f"{declaration.group}:{declaration.artifact}:{version}",
                        "declaredVersion": declaration.raw_coordinate,
                        "versionBasis": basis,
                        "versionSource": (
                            {"path": source[0], "line": source[1]} if source is not None else None
                        ),
                        "advisoryId": record["id"],
                        "aliases": aliases,
                        "ranges": record.get("ranges", []),
                        "fixedVersions": record.get("fixedVersions", []),
                        "database": database_used,
                    },
                )
            )

    return findings, diagnostics
