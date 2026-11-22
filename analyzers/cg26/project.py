"""Project-wide index: files, declarations, routes, security rules, folding."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .kotlin_index import (
    ClassDecl,
    Function,
    KotlinFile,
    Property,
    Route,
    SecurityRule,
    annotation_methods,
    annotation_paths,
    join_paths,
    load_kotlin_file,
    parse_security_rules,
)
from .kotlin_lexer import Token, split_top_level
from .textfiles import iter_project_files


@dataclass
class FoldResult:
    value: str | None
    parts: list[Property] = field(default_factory=list)
    literal_parts: int = 0
    pure: bool = False
    reason: str = ""

    @property
    def first_line(self) -> int | None:
        return min((part.line for part in self.parts), default=None)


class Project:
    """Parsed view of a Kotlin/Spring project."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.files: dict[str, KotlinFile] = {}
        self.text_files: dict[str, object] = {}
        self.classes: list[ClassDecl] = []
        self.functions: list[Function] = []
        self.properties: list[Property] = []
        self.routes: list[Route] = []
        self.security_rules: list[SecurityRule] = []
        self.parser_errors: list[dict[str, object]] = []
        self.files_skipped: list[dict[str, str]] = []
        self.files_scanned = 0
        self.load()

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        files, skipped = iter_project_files(self.root)
        self.files_skipped.extend(skipped)
        for text_file in files:
            self.files_scanned += 1
            self.text_files[text_file.rel_path] = text_file
            suffix = text_file.abs_path.suffix.lower()
            if suffix not in {".kt", ".kts"}:
                continue
            try:
                parsed = load_kotlin_file(self.root, text_file.rel_path, text_file.text)
            except Exception as error:  # pragma: no cover - defensive
                self.parser_errors.append(
                    {
                        "path": text_file.rel_path,
                        "message": f"{type(error).__name__}: {error}",
                    }
                )
                continue
            self.files[text_file.rel_path] = parsed
            unmatched = [token for index, token in enumerate(parsed.tokens)
                         if token.kind == "OP" and token.text in {"(", ")", "[", "]", "{", "}"}
                         and index not in parsed.matching]
            if unmatched:
                self.parser_errors.append({
                    "path": text_file.rel_path,
                    "message": f"Unbalanced delimiter at line {unmatched[0].line}; structural parsing is incomplete",
                })
            self.classes.extend(parsed.classes)
            self.functions.extend(parsed.functions)
            self.properties.extend(parsed.properties)
        self._build_routes()
        self._build_security()

    def _build_routes(self) -> None:
        for file in self.files.values():
            for cls in file.classes:
                if not cls.has_annotation("RestController", "Controller"):
                    continue
                base_annotations = [
                    item for item in cls.annotations if item.name.endswith("RequestMapping")
                ]
                bases = [""]
                if base_annotations:
                    bases = annotation_paths(base_annotations[0])
                for function in cls.functions:
                    mapping = self._mapping_annotation(function)
                    if mapping is None:
                        continue
                    for base in bases:
                        for path in annotation_paths(mapping):
                            for method in annotation_methods(mapping):
                                self.routes.append(
                                    Route(
                                        method=method,
                                        path=join_paths(base, path),
                                        handler=function,
                                        class_name=cls.name,
                                    )
                                )
            # Top-level handler functions.
            for function in file.functions:
                if function.owner is not None:
                    continue
                mapping = self._mapping_annotation(function)
                if mapping is None:
                    continue
                for path in annotation_paths(mapping):
                    for method in annotation_methods(mapping):
                        self.routes.append(
                            Route(method=method, path=join_paths("", path), handler=function, class_name=None)
                        )
        # Stable ordering by path then method.
        self.routes.sort(key=lambda route: (route.path, route.method or ""))

    @staticmethod
    def _mapping_annotation(function: Function):
        for annotation in function.annotations:
            if annotation.name.endswith("Mapping"):
                return annotation
        return None

    def _build_security(self) -> None:
        for file in self.files.values():
            for function in file.functions:
                if not any(token.is_ident("authorizeHttpRequests") for token in function.body):
                    continue
                rules = parse_security_rules(file, function)
                if rules:
                    self.security_rules.extend(rules)
        # Re-number rules globally while keeping source order per file.
        for index, rule in enumerate(self.security_rules):
            rule.index = index
        for route in self.routes:
            route.applied_rule = self.rule_for(route.method, route.path)
            route.applied_rule_index = route.applied_rule.index if route.applied_rule else None

    def rule_for(self, method: str | None, path: str) -> SecurityRule | None:
        for rule in self.security_rules:
            if rule.matches(method, path):
                return rule
        return None

    # --------------------------------------------------------------- lookup

    def find_class(self, name: str) -> ClassDecl | None:
        for cls in self.classes:
            if cls.name == name:
                return cls
        return None

    def find_function(self, class_name: str | None, name: str) -> Function | None:
        for function in self.functions:
            if function.name == name and function.owner == class_name:
                return function
        return None

    def properties_in(self, file: str) -> list[Property]:
        return [prop for prop in self.properties if prop.file == file]

    def resolve_property(
        self,
        name: str,
        file: str,
        func: str | None = None,
        cls: str | None = None,
    ) -> Property | None:
        candidates = [prop for prop in self.properties if prop.file == file and prop.name == name]
        if not candidates:
            return None
        if func is not None:
            for prop in candidates:
                if prop.scope == "function" and prop.func == func:
                    return prop
        if cls is not None:
            for prop in candidates:
                if prop.scope == "class" and prop.owner == cls:
                    return prop
        for prop in candidates:
            if prop.scope == "file":
                return prop
        # Last resort: the nearest declaration in the same file.
        return candidates[0]

    # --------------------------------------------------------------- folding

    def fold(
        self,
        tokens: list[Token],
        file: str,
        func: str | None = None,
        cls: str | None = None,
        visited: set[str] | None = None,
    ) -> FoldResult:
        """Fold a pure string expression into its literal value.

        Supports literals, local/file properties, string interpolation and
        ``+`` concatenation.  Anything else is reported as not pure.
        """

        visited = visited or set()
        clean = [token for token in tokens if token.kind not in {"NL", "COMMENT"}]
        if not clean:
            return FoldResult(value=None, reason="empty expression")

        operands = split_top_level(clean, "+")
        if len(operands) > 1:
            values: list[str] = []
            parts: list[Property] = []
            literal_parts = 0
            for operand in operands:
                result = self.fold(operand, file, func, cls, visited)
                if not result.pure or result.value is None:
                    return FoldResult(value=None, reason="non-literal operand")
                values.append(result.value)
                parts.extend(result.parts)
                literal_parts += result.literal_parts
            return FoldResult(
                value="".join(values),
                parts=parts,
                literal_parts=literal_parts,
                pure=True,
            )

        if len(clean) == 1 and clean[0].kind == "STRING":
            token = clean[0]
            value = token.value or ""
            if not token.interpolations:
                return FoldResult(value=value, parts=[], literal_parts=1, pure=True)
            # Interpolated string: resolve every interpolation in order and
            # substitute its value into the literal text.
            resolved_parts: list[Property] = []
            rebuilt = token.value or ""
            for interpolation in token.interpolations:
                expr = interpolation.expr.strip()
                if interpolation.simple:
                    prop = self.resolve_property(expr, file, func, cls)
                    if prop is None or expr in visited:
                        return FoldResult(value=None, reason=f"unresolved interpolation ${expr}")
                    sub = self.fold(prop.expr, file, prop.func, prop.owner, visited | {expr})
                    if not sub.pure or sub.value is None:
                        return FoldResult(value=None, reason=f"non-literal interpolation ${expr}")
                    resolved_parts.extend(sub.parts)
                    replacement = sub.value
                    marker = "$" + interpolation.expr
                else:
                    sub = self._fold_expression_text(expr, file, func, cls, visited)
                    if not sub.pure or sub.value is None:
                        return FoldResult(value=None, reason=f"unresolved interpolation ${{{expr}}}")
                    resolved_parts.extend(sub.parts)
                    replacement = sub.value
                    marker = "${" + interpolation.expr + "}"
                rebuilt = rebuilt.replace(marker, replacement, 1)
            return FoldResult(
                value=rebuilt,
                parts=resolved_parts,
                literal_parts=0,
                pure=True,
            )

        if len(clean) == 1 and clean[0].kind == "IDENT":
            name = clean[0].text
            if name in visited:
                return FoldResult(value=None, reason="cyclic property")
            prop = self.resolve_property(name, file, func, cls)
            if prop is None:
                return FoldResult(value=None, reason=f"unresolved identifier {name}")
            sub = self.fold(prop.expr, file, prop.func, prop.owner, visited | {name})
            if not sub.pure:
                return FoldResult(value=None, reason=f"non-literal property {name}")
            return FoldResult(
                value=sub.value,
                parts=[prop, *sub.parts],
                literal_parts=sub.literal_parts,
                pure=True,
            )

        return FoldResult(value=None, reason="unsupported expression")

    def _fold_expression_text(
        self,
        text: str,
        file: str,
        func: str | None,
        cls: str | None,
        visited: set[str],
    ) -> FoldResult:
        from .kotlin_lexer import lex

        lexed = lex(text)
        return self.fold(lexed.tokens, file, func, cls, visited)
