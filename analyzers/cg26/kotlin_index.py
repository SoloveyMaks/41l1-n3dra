"""Structural index for Kotlin sources.

The index is deliberately narrow: declarations, annotations, properties,
function bodies, call sites, Spring routes and the ordered security rules.
Rules consume this index; they never grep raw text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .kotlin_lexer import (
    LexResult,
    Token,
    build_matching,
    find_statement_end,
    lex,
    skip_newlines,
    split_top_level,
)

ANNOTATION_TARGETS = {
    "field",
    "get",
    "set",
    "param",
    "property",
    "receiver",
    "file",
    "all",
    "delegate",
}

MODIFIERS = {
    "private",
    "public",
    "protected",
    "internal",
    "open",
    "final",
    "abstract",
    "sealed",
    "data",
    "enum",
    "inline",
    "inner",
    "lateinit",
    "override",
    "suspend",
    "operator",
    "infix",
    "tailrec",
    "external",
    "const",
    "vararg",
    "crossinline",
    "noinline",
    "reified",
}

MAPPING_METHODS = {
    "GetMapping": ["GET"],
    "PostMapping": ["POST"],
    "PutMapping": ["PUT"],
    "DeleteMapping": ["DELETE"],
    "PatchMapping": ["PATCH"],
}

KEYWORDS = {
    "if",
    "else",
    "for",
    "while",
    "when",
    "try",
    "catch",
    "finally",
    "return",
    "throw",
    "is",
    "as",
    "in",
    "out",
    "object",
    "class",
    "fun",
    "val",
    "var",
    "package",
    "import",
    "super",
    "this",
    "it",
    "by",
    "where",
    "init",
    "constructor",
    "typealias",
}


@dataclass
class Annotation:
    name: str
    args: list[Token]
    line: int
    end_line: int

    def string_args(self) -> list[str]:
        values: list[str] = []
        for token in self.args:
            if token.kind == "STRING" and token.value is not None:
                values.append(token.value)
        return values

    def first_string(self) -> str | None:
        for token in self.args:
            if token.kind == "STRING" and token.value is not None:
                return token.value
        return None

    def named(self, key: str) -> list[Token]:
        for index, token in enumerate(self.args):
            if token.is_ident(key):
                cursor = index + 1
                if cursor < len(self.args) and self.args[cursor].is_op("="):
                    cursor += 1
                    result: list[Token] = []
                    depth = 0
                    while cursor < len(self.args):
                        current = self.args[cursor]
                        if current.kind == "OP" and current.text in "([{":
                            depth += 1
                        elif current.kind == "OP" and current.text in ")]}":
                            if depth == 0:
                                break
                            depth -= 1
                        if depth == 0 and current.is_op(","):
                            break
                        result.append(current)
                        cursor += 1
                    return result
        return []


@dataclass
class Param:
    name: str
    type_text: str
    annotations: list[Annotation]
    line: int
    end_line: int
    has_default: bool = False

    def annotation(self, name: str) -> Annotation | None:
        for item in self.annotations:
            if item.name == name or item.name.endswith("." + name):
                return item
        return None


@dataclass
class Property:
    name: str
    file: str
    line: int
    end_line: int
    expr: list[Token]
    scope: str  # file / class / function
    owner: str | None = None
    func: str | None = None
    type_text: str | None = None
    mutable: bool = False
    annotations: list[Annotation] = field(default_factory=list)


@dataclass
class Call:
    name: str
    receiver: str
    args: list[list[Token]]
    line: int
    end_line: int
    index: int

    def arg_text(self, position: int) -> str:
        if position >= len(self.args):
            return ""
        return " ".join(token.text for token in self.args[position] if token.kind != "NL")

    def string_arg(self, position: int) -> str | None:
        if position >= len(self.args):
            return None
        for token in self.args[position]:
            if token.kind == "STRING" and token.value is not None:
                return token.value
        return None


@dataclass
class Function:
    name: str
    file: str
    line: int
    end_line: int
    annotations: list[Annotation]
    params: list[Param]
    body: list[Token]
    owner: str | None
    body_kind: str  # block / expression / none
    calls: list[Call] = field(default_factory=list)
    return_type: str = ""

    def annotation(self, name: str) -> Annotation | None:
        for item in self.annotations:
            if item.name == name or item.name.endswith("." + name):
                return item
        return None

    def has_annotation(self, *names: str) -> bool:
        return any(self.annotation(name) is not None for name in names)

    def param(self, name: str) -> Param | None:
        for item in self.params:
            if item.name == name:
                return item
        return None

    def calls_named(self, *names: str) -> list[Call]:
        wanted = set(names)
        return [call for call in self.calls if call.name in wanted]

    def tokens_text(self) -> str:
        return " ".join(token.text for token in self.body if token.kind != "NL")


@dataclass
class ClassDecl:
    name: str
    file: str
    line: int
    end_line: int
    annotations: list[Annotation]
    ctor_params: list[Param]
    functions: list[Function] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)

    def annotation(self, name: str) -> Annotation | None:
        for item in self.annotations:
            if item.name == name or item.name.endswith("." + name):
                return item
        return None

    def has_annotation(self, *names: str) -> bool:
        return any(self.annotation(name) is not None for name in names)


@dataclass
class Route:
    method: str | None
    path: str
    handler: Function
    class_name: str | None
    applied_rule_index: int | None = None
    applied_rule: "SecurityRule | None" = None

    @property
    def route_id(self) -> str:
        return f"{self.method or 'ANY'} {self.path}"


@dataclass
class SecurityRule:
    index: int
    line: int
    end_line: int
    matchers: list[tuple[str | None, str]]
    effect: str
    effect_args: list[str]
    any_request: bool = False
    source_file: str = ""

    def matches(self, method: str | None, path: str) -> bool:
        if self.any_request:
            return True
        for rule_method, pattern in self.matchers:
            if rule_method is not None and method is not None and rule_method != method:
                continue
            if rule_method is not None and method is None:
                continue
            if path_matches(pattern, path):
                return True
        return False


@dataclass
class KotlinFile:
    rel_path: str
    lexed: LexResult
    tokens: list[Token]
    matching: dict[int, int]
    classes: list[ClassDecl] = field(default_factory=list)
    functions: list[Function] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)


def parse_annotation(tokens: list[Token], index: int, matching: dict[int, int]) -> tuple[Annotation, int]:
    start = index
    cursor = index + 1
    if (
        cursor + 1 < len(tokens)
        and tokens[cursor].kind == "IDENT"
        and tokens[cursor].text in ANNOTATION_TARGETS
        and tokens[cursor + 1].is_op(":")
    ):
        cursor += 2
    name_parts: list[str] = []
    while cursor < len(tokens) and tokens[cursor].kind == "IDENT":
        name_parts.append(tokens[cursor].text)
        cursor += 1
        if cursor < len(tokens) and tokens[cursor].is_op("."):
            cursor += 1
            continue
        break
    name = ".".join(name_parts) if name_parts else ""
    args: list[Token] = []
    if cursor < len(tokens) and tokens[cursor].is_op("("):
        close = matching.get(cursor)
        if close is not None:
            args = tokens[cursor + 1 : close]
            cursor = close + 1
    end_line = tokens[cursor - 1].line if cursor > start else tokens[start].line
    return Annotation(name=name, args=args, line=tokens[start].line, end_line=end_line), cursor


def _strip_noise(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"NL", "COMMENT"}]


def parse_params(tokens: list[Token], matching: dict[int, int]) -> list[Param]:
    params: list[Param] = []
    for part in split_top_level(tokens):
        part = _strip_noise(part)
        if not part:
            continue
        annotations: list[Annotation] = []
        cursor = 0
        while cursor < len(part) and part[cursor].is_op("@"):
            annotation, cursor = parse_annotation(part, cursor, build_matching(part))
            annotations.append(annotation)
        while cursor < len(part) and part[cursor].kind == "IDENT" and part[cursor].text in MODIFIERS:
            cursor += 1
        colon = None
        depth = 0
        for index in range(cursor, len(part)):
            token = part[index]
            if token.kind == "OP" and token.text in "([{<":
                depth += 1
            elif token.kind == "OP" and token.text in ")]}>":
                depth -= 1
            elif token.is_op(":") and depth == 0:
                colon = index
                break
        if colon is None:
            # Lambda parameter or malformed entry.
            continue
        name = ""
        for index in range(colon - 1, cursor - 1, -1):
            if part[index].kind == "IDENT":
                name = part[index].text
                break
        if not name:
            continue
        type_tokens: list[Token] = []
        depth = 0
        for index in range(colon + 1, len(part)):
            token = part[index]
            if token.kind == "OP" and token.text in "([{<":
                depth += 1
            elif token.kind == "OP" and token.text in ")]}>":
                depth -= 1
            elif token.is_op("=") and depth == 0:
                break
            type_tokens.append(token)
        type_text = "".join(token.text for token in type_tokens)
        has_default = any(token.is_op("=") for token in part[colon + 1 :])
        params.append(
            Param(
                name=name,
                type_text=type_text,
                annotations=annotations,
                line=part[0].line,
                end_line=part[-1].line,
                has_default=has_default,
            )
        )
    return params


def collect_calls(tokens: list[Token], matching: dict[int, int]) -> list[Call]:
    calls: list[Call] = []
    index = 0
    size = len(tokens)
    while index < size:
        token = tokens[index]
        if token.kind != "IDENT" or token.text in KEYWORDS:
            index += 1
            continue
        cursor = index
        chain = [token.text]
        while (
            cursor + 2 < size
            and tokens[cursor + 1].is_op(".")
            and tokens[cursor + 2].kind == "IDENT"
        ):
            cursor += 2
            chain.append(tokens[cursor].text)
        if cursor + 1 < size and tokens[cursor + 1].is_op("("):
            open_index = cursor + 1
            close = matching.get(open_index)
            if close is not None:
                args = split_top_level(tokens[open_index + 1 : close])
                calls.append(
                    Call(
                        name=chain[-1],
                        receiver=".".join(chain[:-1]),
                        args=args,
                        line=token.line,
                        end_line=tokens[close].line,
                        index=index,
                    )
                )
        index = cursor + 1
    return calls


class KotlinFileParser:
    def __init__(self, rel_path: str, source: str):
        self.rel_path = rel_path
        self.lexed = lex(source)
        self.tokens = self.lexed.tokens
        self.matching = build_matching(self.tokens)
        self.file = KotlinFile(
            rel_path=rel_path,
            lexed=self.lexed,
            tokens=self.tokens,
            matching=self.matching,
        )

    def parse(self) -> KotlinFile:
        self._parse_scope(0, len(self.tokens), owner=None, scope="file", func=None)
        return self.file

    def _parse_scope(
        self,
        start: int,
        end: int,
        owner: str | None,
        scope: str,
        func: str | None,
    ) -> None:
        tokens = self.tokens
        matching = self.matching
        index = start
        pending: list[Annotation] = []
        while index < end:
            token = tokens[index]
            if token.kind in {"NL", "COMMENT"}:
                index += 1
                continue
            if token.is_op("@"):
                annotation, index = parse_annotation(tokens, index, matching)
                pending.append(annotation)
                continue
            if token.is_ident("import") or token.is_ident("package"):
                pending = []
                index += 1
                while index < end and tokens[index].kind != "NL" and not tokens[index].is_op(";"):
                    index += 1
                continue
            if token.kind == "IDENT" and token.text in {"class", "interface", "object"}:
                next_index = index + 1
                if next_index < end and tokens[next_index].kind == "IDENT":
                    cls, index = self._parse_class(index, end, owner, pending)
                    pending = []
                    continue
                pending = []
                index += 1
                continue
            if token.is_ident("fun"):
                function, index = self._parse_function(index, end, owner, pending)
                pending = []
                continue
            if token.kind == "IDENT" and token.text in {"val", "var"}:
                prop, index = self._parse_property(index, end, owner, scope, func, pending)
                pending = []
                if prop is not None:
                    self.file.properties.append(prop)
                continue
            if token.is_op("}"):
                pending = []
                index += 1
                continue
            pending = []
            index += 1

    def _parse_class(
        self,
        index: int,
        end: int,
        owner: str | None,
        annotations: list[Annotation],
    ) -> tuple[ClassDecl, int]:
        tokens = self.tokens
        matching = self.matching
        cursor = index + 1
        # Type parameters.
        if cursor < end and tokens[cursor].is_op("<"):
            depth = 1
            cursor += 1
            while cursor < end and depth > 0:
                if tokens[cursor].is_op("<"):
                    depth += 1
                elif tokens[cursor].is_op(">"):
                    depth -= 1
                cursor += 1
        name = tokens[cursor].text if cursor < end and tokens[cursor].kind == "IDENT" else ""
        cursor += 1
        ctor_params: list[Param] = []
        if cursor < end and tokens[cursor].is_op("("):
            close = matching.get(cursor)
            if close is not None:
                ctor_params = parse_params(tokens[cursor + 1 : close], matching)
                cursor = close + 1
        # Skip superclass list / delegation until the body or statement end.
        while cursor < end and not tokens[cursor].is_op("{"):
            if tokens[cursor].kind == "NL" and cursor > index + 1:
                # Class without a body.
                return (
                    ClassDecl(
                        name=name,
                        file=self.rel_path,
                        line=tokens[index].line,
                        end_line=tokens[cursor - 1].line,
                        annotations=annotations,
                        ctor_params=ctor_params,
                    ),
                    cursor,
                )
            cursor += 1
        if cursor >= end:
            return (
                ClassDecl(
                    name=name,
                    file=self.rel_path,
                    line=tokens[index].line,
                    end_line=tokens[end - 1].line if end else tokens[index].line,
                    annotations=annotations,
                    ctor_params=ctor_params,
                ),
                cursor,
            )
        body_close = matching.get(cursor)
        if body_close is None:
            body_close = end
        cls = ClassDecl(
            name=name,
            file=self.rel_path,
            line=tokens[index].line,
            end_line=tokens[body_close].line if body_close < len(tokens) else tokens[end - 1].line,
            annotations=annotations,
            ctor_params=ctor_params,
        )
        self.file.classes.append(cls)
        self._parse_scope(cursor + 1, body_close, owner=name, scope="class", func=None)
        # Attach members parsed in this range.
        for function in self.file.functions:
            if function.owner == name and function not in cls.functions:
                cls.functions.append(function)
        for prop in self.file.properties:
            if prop.scope == "class" and prop.owner == name and prop not in cls.properties:
                cls.properties.append(prop)
        return cls, body_close + 1

    def _parse_function(
        self,
        index: int,
        end: int,
        owner: str | None,
        annotations: list[Annotation],
    ) -> tuple[Function, int]:
        tokens = self.tokens
        matching = self.matching
        cursor = index + 1
        if cursor < end and tokens[cursor].is_op("<"):
            depth = 1
            cursor += 1
            while cursor < end and depth > 0:
                if tokens[cursor].is_op("<"):
                    depth += 1
                elif tokens[cursor].is_op(">"):
                    depth -= 1
                cursor += 1
        name = tokens[cursor].text if cursor < end and tokens[cursor].kind == "IDENT" else ""
        cursor += 1
        params: list[Param] = []
        if cursor < end and tokens[cursor].is_op("("):
            close = matching.get(cursor)
            if close is not None:
                params = parse_params(tokens[cursor + 1 : close], matching)
                cursor = close + 1
        # Return type.
        return_type_tokens: list[Token] = []
        depth = 0
        while cursor < end:
            token = tokens[cursor]
            if token.kind == "OP" and token.text in "([":
                depth += 1
            elif token.kind == "OP" and token.text in ")]":
                depth -= 1
            if depth == 0 and (token.is_op("=") or token.is_op("{") or token.is_op(";") or token.kind == "NL"):
                break
            return_type_tokens.append(token)
            cursor += 1
        return_type = "".join(token.text for token in return_type_tokens if token.kind != "NL")

        if cursor < end and tokens[cursor].is_op("="):
            expr_start = skip_newlines(tokens, cursor + 1, end)
            expr_end = find_statement_end(tokens, expr_start, end)
            body = tokens[expr_start:expr_end]
            body_kind = "expression"
            body_end_line = tokens[expr_end - 1].line if expr_end > expr_start else tokens[cursor].line
            next_index = expr_end
        elif cursor < end and tokens[cursor].is_op("{"):
            body_close = matching.get(cursor, end)
            body = tokens[cursor + 1 : body_close]
            body_kind = "block"
            body_end_line = tokens[body_close].line if body_close < len(tokens) else tokens[end - 1].line
            next_index = body_close + 1
        else:
            body = []
            body_kind = "none"
            body_end_line = tokens[cursor - 1].line if cursor > index else tokens[index].line
            next_index = cursor

        function = Function(
            name=name,
            file=self.rel_path,
            line=tokens[index].line,
            end_line=body_end_line,
            annotations=annotations,
            params=params,
            body=body,
            owner=owner,
            body_kind=body_kind,
            calls=collect_calls(body, build_matching(body)),
            return_type=return_type,
        )
        self.file.functions.append(function)

        if body_kind in {"block", "expression"} and body:
            body_start_index = cursor + 1 if body_kind == "block" else skip_newlines(tokens, cursor + 1, end)
            body_end_index = body_close if body_kind == "block" else next_index
            self._parse_scope(body_start_index, body_end_index, owner=owner, scope="function", func=name)
        return function, next_index

    def _parse_property(
        self,
        index: int,
        end: int,
        owner: str | None,
        scope: str,
        func: str | None,
        annotations: list[Annotation],
    ) -> tuple[Property | None, int]:
        tokens = self.tokens
        cursor = index + 1
        if cursor >= end or tokens[cursor].kind != "IDENT":
            return None, cursor
        name = tokens[cursor].text
        cursor += 1
        # Optional type annotation.
        type_tokens: list[Token] = []
        depth = 0
        while cursor < end:
            token = tokens[cursor]
            if token.kind == "OP" and token.text in "([{<":
                depth += 1
            elif token.kind == "OP" and token.text in ")]}>":
                depth -= 1
            if depth == 0 and (token.is_op("=") or token.kind == "NL" or token.is_op(";") or token.is_op("}")):
                break
            type_tokens.append(token)
            cursor += 1
        type_text = "".join(token.text for token in type_tokens).lstrip(":").strip() or None

        expr: list[Token] = []
        next_index = cursor
        if cursor < end and tokens[cursor].is_op("="):
            expr_start = skip_newlines(tokens, cursor + 1, end)
            expr_end = find_statement_end(tokens, expr_start, end)
            expr = tokens[expr_start:expr_end]
            next_index = expr_end
            end_line = tokens[expr_end - 1].line if expr_end > expr_start else tokens[cursor].line
        else:
            end_line = tokens[cursor - 1].line if cursor > index else tokens[index].line

        return (
            Property(
                name=name,
                file=self.rel_path,
                line=tokens[index].line,
                end_line=end_line,
                expr=expr,
                scope=scope,
                owner=owner,
                func=func,
                type_text=type_text,
                mutable=tokens[index].text == "var",
                annotations=annotations,
            ),
            next_index,
        )


def path_matches(pattern: str, path: str) -> bool:
    """Spring-style Ant pattern matching (subset used by the training project)."""

    pattern = normalize_path(pattern)
    path = normalize_path(path)
    if pattern == path:
        return True
    p_segments = pattern.strip("/").split("/") if pattern.strip("/") else []
    s_segments = path.strip("/").split("/") if path.strip("/") else []
    index = 0
    for segment in p_segments:
        if segment == "**":
            return True
        if index >= len(s_segments):
            return False
        if segment == "*":
            index += 1
            continue
        if segment.startswith("{") and segment.endswith("}"):
            index += 1
            continue
        if "*" in segment:
            prefix, _, suffix = segment.partition("*")
            candidate = s_segments[index]
            if not candidate.startswith(prefix) or not candidate.endswith(suffix):
                return False
            index += 1
            continue
        if segment != s_segments[index]:
            return False
        index += 1
    return index == len(s_segments)


def normalize_path(path: str) -> str:
    if not path:
        return "/"
    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path


def join_paths(base: str, path: str) -> str:
    if not base:
        return normalize_path(path)
    if not path:
        return normalize_path(base)
    return normalize_path(base.rstrip("/") + "/" + path.lstrip("/"))


def annotation_paths(annotation: Annotation) -> list[str]:
    values = annotation.string_args()
    named = annotation.named("value") + annotation.named("path")
    for token in named:
        if token.kind == "STRING" and token.value is not None:
            return [token.value]
    return values or [""]


def annotation_methods(annotation: Annotation) -> list[str | None]:
    if annotation.name in MAPPING_METHODS:
        return list(MAPPING_METHODS[annotation.name])
    method_tokens = annotation.named("method")
    methods: list[str] = []
    for token in method_tokens:
        if token.kind == "IDENT" and token.text.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"}:
            methods.append(token.text.upper())
    return methods or [None]


def parse_security_rules(
    file: KotlinFile,
    function: Function,
) -> list[SecurityRule]:
    tokens = function.body
    matching = build_matching(tokens)
    rules: list[SecurityRule] = []
    index = 0
    size = len(tokens)
    while index < size:
        token = tokens[index]
        if token.kind != "IDENT" or token.text not in {"requestMatchers", "anyRequest"}:
            index += 1
            continue
        # The call may be ``rules.requestMatchers(...)``.
        cursor = index
        args: list[list[Token]] = []
        if cursor + 1 < size and tokens[cursor + 1].is_op("("):
            close = matching.get(cursor + 1)
            if close is None:
                index += 1
                continue
            args = split_top_level(tokens[cursor + 2 : close])
            cursor = close + 1
        any_request = token.text == "anyRequest"
        matchers: list[tuple[str | None, str]] = []
        if not any_request:
            method: str | None = None
            patterns: list[str] = []
            for position, argument in enumerate(args):
                clean = _strip_noise(argument)
                if not clean:
                    continue
                if position == 0:
                    method_candidate = None
                    for item in clean:
                        if item.kind == "IDENT" and item.text.upper() in {
                            "GET",
                            "POST",
                            "PUT",
                            "DELETE",
                            "PATCH",
                            "HEAD",
                            "OPTIONS",
                            "TRACE",
                        }:
                            method_candidate = item.text.upper()
                    if method_candidate is not None:
                        method = method_candidate
                        continue
                for item in clean:
                    if item.kind == "STRING" and item.value is not None:
                        patterns.append(item.value)
            for pattern in patterns:
                matchers.append((method, pattern))
        # Effect after the matcher call: ``.permitAll()``.
        if cursor < size and tokens[cursor].is_op("."):
            cursor += 1
        effect = ""
        effect_args: list[str] = []
        if cursor < size and tokens[cursor].kind == "IDENT":
            effect = tokens[cursor].text
            cursor += 1
            if cursor < size and tokens[cursor].is_op("("):
                close = matching.get(cursor)
                if close is not None:
                    for argument in split_top_level(tokens[cursor + 1 : close]):
                        for item in _strip_noise(argument):
                            if item.kind == "STRING" and item.value is not None:
                                effect_args.append(item.value)
                    cursor = close + 1
        rules.append(
            SecurityRule(
                index=len(rules),
                line=token.line,
                end_line=tokens[cursor - 1].line if cursor > 0 else token.line,
                matchers=matchers,
                effect=effect,
                effect_args=effect_args,
                any_request=any_request,
                source_file=file.rel_path,
            )
        )
        index = cursor
    return rules


def load_kotlin_file(root: Path, rel_path: str, source: str) -> KotlinFile:
    parser = KotlinFileParser(rel_path, source)
    return parser.parse()
