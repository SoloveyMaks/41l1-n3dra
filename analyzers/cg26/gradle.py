"""Gradle Kotlin DSL dependency extraction.

Reads ``dependencies { ... }`` without running Gradle.  Supports direct
literals, local ``val`` constants, string interpolation, Gradle properties and
multi-line ``add(...)`` calls.  Lock file versions take precedence for export.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .kotlin_index import KotlinFile
from .kotlin_lexer import Token, build_matching, lex, split_top_level
from .project import Project

_COORDINATE = re.compile(r"^[A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+(?::.*)?$")


@dataclass
class DependencyDecl:
    config: str
    group: str
    artifact: str
    version: str | None
    file: str
    line: int
    end_line: int
    version_basis: str = "requested_exact"
    version_source: tuple[str, int] | None = None
    raw_coordinate: str = ""
    diagnostics: list[str] = field(default_factory=list)

    @property
    def coordinate(self) -> str:
        return f"{self.group}:{self.artifact}"


@dataclass
class LockEntry:
    group: str
    artifact: str
    version: str
    line: int
    configurations: str


def _clean(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"NL", "COMMENT"}]


def _is_coordinate(text: str) -> bool:
    if not _COORDINATE.match(text):
        return False
    group, artifact = text.split(":")[:2]
    return bool(group and artifact)


def parse_gradle_properties(text: str) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
        elif ":" in line:
            key, _, value = line.partition(":")
        else:
            continue
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = (value, number)
    return result


def parse_lockfile(text: str) -> dict[tuple[str, str], LockEntry]:
    entries: dict[tuple[str, str], LockEntry] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        left, _, configurations = line.partition("=")
        parts = left.split(":")
        if len(parts) < 3:
            continue
        group, artifact, version = parts[0], parts[1], parts[2]
        entries[(group, artifact)] = LockEntry(
            group=group,
            artifact=artifact,
            version=version,
            line=number,
            configurations=configurations,
        )
    return entries


class GradleModel:
    def __init__(self, project: Project):
        self.project = project
        self.declarations: list[DependencyDecl] = []
        self.lock: dict[tuple[str, str], LockEntry] = {}
        self.gradle_properties: dict[str, tuple[str, int]] = {}
        self.diagnostics: list[str] = []
        self._load_properties()
        self._load_lock()
        self._load_declarations()

    def _load_properties(self) -> None:
        for rel_path, text_file in self.project.text_files.items():
            name = rel_path.rsplit("/", 1)[-1]
            if name == "gradle.properties":
                self.gradle_properties.update(parse_gradle_properties(text_file.text))

    def _load_lock(self) -> None:
        for rel_path, text_file in self.project.text_files.items():
            if rel_path.rsplit("/", 1)[-1] == "gradle.lockfile":
                self.lock.update(parse_lockfile(text_file.text))

    def _load_declarations(self) -> None:
        for rel_path, kotlin_file in self.project.files.items():
            name = rel_path.rsplit("/", 1)[-1]
            if not (name == "build.gradle.kts" or name.endswith(".gradle.kts")):
                continue
            self._scan_build_file(rel_path, kotlin_file)

    # ------------------------------------------------------------------ scan

    def _scan_build_file(self, rel_path: str, kotlin_file: KotlinFile) -> None:
        tokens = kotlin_file.tokens
        matching = kotlin_file.matching
        properties = {
            prop.name: prop
            for prop in self.project.properties
            if prop.file == rel_path and prop.scope == "file"
        }

        start, end = self._find_dependencies_block(tokens, matching)
        if start is None:
            return

        index = start
        while index < end:
            token = tokens[index]
            if token.kind != "IDENT":
                index += 1
                continue
            if index + 1 < end and tokens[index + 1].is_op("{"):
                # Nested configuration block (constraints, modules, ...).
                close = matching.get(index + 1, end)
                index = close + 1
                continue
            if index + 1 < end and tokens[index + 1].is_op("("):
                close = matching.get(index + 1, end)
                if close is None:
                    index += 1
                    continue
                self._parse_call(rel_path, tokens, index, close, properties, default_config=token.text)
                index = close + 1
                continue
            index += 1

    @staticmethod
    def _find_dependencies_block(tokens: list[Token], matching: dict[int, int]) -> tuple[int | None, int]:
        for index, token in enumerate(tokens):
            if token.is_ident("dependencies") and index + 1 < len(tokens) and tokens[index + 1].is_op("{"):
                close = matching.get(index + 1)
                if close is not None:
                    return index + 2, close
        return None, 0

    def _parse_call(
        self,
        rel_path: str,
        tokens: list[Token],
        name_index: int,
        close_index: int,
        properties: dict,
        default_config: str,
    ) -> None:
        name = tokens[name_index].text
        args = split_top_level(tokens[name_index + 2 : close_index])
        config = default_config
        coordinate_token: Token | None = None
        if name == "add" and args:
            first = _clean(args[0])
            if first and first[0].kind == "STRING" and first[0].value:
                config = first[0].value
            for argument in args[1:]:
                candidate = self._coordinate_token(argument)
                if candidate is not None:
                    coordinate_token = candidate
                    break
        else:
            for argument in args:
                candidate = self._coordinate_token(argument)
                if candidate is not None:
                    coordinate_token = candidate
                    break
        if coordinate_token is None or not coordinate_token.value:
            return

        parts = coordinate_token.value.split(":")
        if len(parts) < 2:
            return
        group, artifact = parts[0], parts[1]
        version_raw = parts[2] if len(parts) > 2 else ""
        version: str | None = None
        basis = "requested_exact"
        source: tuple[str, int] | None = None
        diagnostics: list[str] = []

        if version_raw:
            resolved, resolved_source, reason = self._resolve_version(
                version_raw, coordinate_token, rel_path, properties
            )
            if resolved is None:
                diagnostics.append(reason)
            else:
                version = resolved
                source = resolved_source
        else:
            diagnostics.append("version-managed")

        self.declarations.append(
            DependencyDecl(
                config=config,
                group=group,
                artifact=artifact,
                version=version,
                file=rel_path,
                line=coordinate_token.line,
                end_line=coordinate_token.line,
                version_basis=basis,
                version_source=source,
                raw_coordinate=coordinate_token.value,
                diagnostics=diagnostics,
            )
        )

    @staticmethod
    def _coordinate_token(argument: list[Token]) -> Token | None:
        for token in argument:
            if token.kind == "STRING" and token.value and _is_coordinate(token.value):
                return token
        return None

    def _resolve_version(
        self,
        version_raw: str,
        token: Token,
        rel_path: str,
        properties: dict,
    ) -> tuple[str | None, tuple[str, int] | None, str]:
        resolved = version_raw
        source: tuple[str, int] | None = None
        for interpolation in token.interpolations:
            marker = ("$" + interpolation.expr) if interpolation.simple else ("${" + interpolation.expr + "}")
            if marker not in resolved:
                continue
            value, value_source = self._eval_interpolation(
                interpolation.expr, interpolation.simple, rel_path, properties
            )
            if value is None:
                return None, None, f"unresolved-version:{marker}"
            resolved = resolved.replace(marker, value, 1)
            source = value_source or source
        if "$" in resolved:
            return None, None, f"unresolved-version:{resolved}"
        return resolved, source, ""

    def _eval_interpolation(
        self,
        expr: str,
        simple: bool,
        rel_path: str,
        properties: dict,
    ) -> tuple[str | None, tuple[str, int] | None]:
        expr = expr.strip()
        if simple:
            return self._eval_property(expr, rel_path, properties)
        lexed = lex(expr)
        clean = _clean(lexed.tokens)
        if len(clean) == 1 and clean[0].kind == "STRING" and clean[0].value is not None:
            return clean[0].value, None
        if len(clean) == 1 and clean[0].kind == "IDENT":
            return self._eval_property(clean[0].text, rel_path, properties)
        # providers.gradleProperty("key").get() / project.property("key")
        for index, token in enumerate(clean):
            if token.kind == "IDENT" and token.text in {"gradleProperty", "property"}:
                if index + 1 < len(clean) and clean[index + 1].is_op("("):
                    for candidate in clean[index + 2 :]:
                        if candidate.kind == "STRING" and candidate.value:
                            if candidate.value in self.gradle_properties:
                                value, line = self.gradle_properties[candidate.value]
                                return value, ("gradle.properties", line)
                            return None, None
        return None, None

    def _eval_property(
        self,
        name: str,
        rel_path: str,
        properties: dict,
    ) -> tuple[str | None, tuple[str, int] | None]:
        prop = properties.get(name)
        if prop is None:
            return None, None
        folded = self.project.fold(prop.expr, prop.file, prop.func, prop.owner)
        if folded.pure and folded.value is not None:
            return folded.value, (prop.file, prop.line)
        # providers.gradleProperty("key").get()
        clean = _clean(prop.expr)
        for index, token in enumerate(clean):
            if token.kind == "IDENT" and token.text in {"gradleProperty", "property"}:
                if index + 1 < len(clean) and clean[index + 1].is_op("("):
                    for candidate in clean[index + 2 :]:
                        if candidate.kind == "STRING" and candidate.value:
                            if candidate.value in self.gradle_properties:
                                value, line = self.gradle_properties[candidate.value]
                                return value, ("gradle.properties", line)
        return None, None
