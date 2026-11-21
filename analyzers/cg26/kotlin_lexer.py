"""Small, deterministic Kotlin / Gradle Kotlin DSL lexer.

It is not a full Kotlin parser.  It produces tokens with exact source offsets,
line/column data, string values, interpolations and comments.  The structural
index and the analyzers are built on top of these tokens, so rules never depend
on file names, line numbers or formatting.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
IDENT_PART = IDENT_START | set("0123456789")
DIGITS = set("0123456789")

# Longest first.
OPERATORS = [
    "!==",
    "===",
    "..<",
    "?:",
    "?.",
    "::",
    "->",
    "==",
    "!=",
    "<=",
    ">=",
    "&&",
    "||",
    "++",
    "--",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "..",
    "=>",
    "!!",
    "<",
    ">",
    "!",
    "=",
    "+",
    "-",
    "*",
    "/",
    "%",
    "?",
    "&",
    "|",
    "^",
    "~",
    ":",
    ";",
    ",",
    ".",
    "(",
    ")",
    "[",
    "]",
    "{",
    "}",
    "@",
    "#",
]

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "0": "\0",
    '"': '"',
    "'": "'",
    "\\": "\\",
    "$": "$",
}


@dataclass
class Interpolation:
    expr: str
    start: int
    end: int
    line: int
    col: int
    simple: bool  # True for $name, False for ${...}


@dataclass
class Token:
    kind: str  # IDENT, STRING, CHAR, NUMBER, OP, NL, COMMENT
    text: str
    start: int
    end: int
    line: int
    col: int
    value: str | None = None
    interpolations: list[Interpolation] = field(default_factory=list)

    def is_op(self, text: str) -> bool:
        return self.kind == "OP" and self.text == text

    def is_ident(self, text: str | None = None) -> bool:
        return self.kind == "IDENT" and (text is None or self.text == text)


@dataclass
class Comment:
    text: str
    start: int
    end: int
    line: int
    end_line: int
    block: bool


@dataclass
class LexResult:
    source: str
    tokens: list[Token]
    comments: list[Comment]
    line_starts: list[int]

    def line_col(self, offset: int) -> tuple[int, int]:
        offset = max(0, min(offset, len(self.source)))
        line = bisect.bisect_right(self.line_starts, offset)
        line = max(1, min(line, len(self.line_starts)))
        return line, offset - self.line_starts[line - 1] + 1

    def line_text(self, line: int) -> str:
        if line < 1 or line > len(self.line_starts):
            return ""
        start = self.line_starts[line - 1]
        end = self.line_starts[line] if line < len(self.line_starts) else len(self.source)
        return self.source[start:end].rstrip("\r\n")


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(source):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _skip_nested_string(source: str, index: int) -> int:
    """Skip a quoted string starting at ``index`` (used inside ${...})."""

    size = len(source)
    index += 1
    while index < size:
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    return index


def _read_string(source: str, index: int, raw: bool) -> tuple[int, str, list[tuple[str, int, int, bool]]]:
    size = len(source)
    start = index
    if raw:
        index += 3
    else:
        index += 1
    pieces: list[str] = []
    interpolations: list[tuple[str, int, int, bool]] = []

    while index < size:
        if raw:
            if source.startswith('"""', index):
                index += 3
                break
        else:
            if source[index] == '"':
                index += 1
                break
        char = source[index]
        if not raw and char == "\\":
            if index + 1 >= size:
                index += 1
                break
            escaped = source[index + 1]
            if escaped == "u" and index + 5 < size:
                hex_text = source[index + 2 : index + 6]
                try:
                    pieces.append(chr(int(hex_text, 16)))
                    index += 6
                    continue
                except ValueError:
                    pass
            pieces.append(_ESCAPES.get(escaped, escaped))
            index += 2
            continue
        if char == "$" and index + 1 < size:
            following = source[index + 1]
            if following == "{":
                depth = 1
                cursor = index + 2
                while cursor < size and depth > 0:
                    current = source[cursor]
                    if current == "{":
                        depth += 1
                    elif current == "}":
                        depth -= 1
                    elif current == '"':
                        cursor = _skip_nested_string(source, cursor)
                        continue
                    cursor += 1
                expr = source[index + 2 : max(index + 2, cursor - 1)]
                interpolations.append((expr, index, cursor, False))
                pieces.append("${" + expr + "}")
                index = cursor
                continue
            if following in IDENT_START:
                cursor = index + 2
                while cursor < size and source[cursor] in IDENT_PART:
                    cursor += 1
                expr = source[index + 1 : cursor]
                interpolations.append((expr, index, cursor, True))
                pieces.append("$" + expr)
                index = cursor
                continue
        pieces.append(char)
        index += 1

    return index, "".join(pieces), interpolations


def lex(source: str) -> LexResult:
    size = len(source)
    tokens: list[Token] = []
    comments: list[Comment] = []
    starts = _line_starts(source)

    def add(kind: str, text: str, start: int, end: int, value: str | None = None, interps=None) -> None:
        line = bisect.bisect_right(starts, start)
        col = start - starts[line - 1] + 1
        tokens.append(
            Token(
                kind=kind,
                text=text,
                start=start,
                end=end,
                line=line,
                col=col,
                value=value,
                interpolations=[
                    Interpolation(
                        expr=expr,
                        start=istart,
                        end=iend,
                        line=bisect.bisect_right(starts, istart),
                        col=istart - starts[bisect.bisect_right(starts, istart) - 1] + 1,
                        simple=simple,
                    )
                    for expr, istart, iend, simple in (interps or [])
                ],
            )
        )

    index = 0
    while index < size:
        char = source[index]

        if char in " \t\f\v":
            index += 1
            continue

        if char == "\r":
            if index + 1 < size and source[index + 1] == "\n":
                add("NL", "\r\n", index, index + 2)
                index += 2
            else:
                add("NL", "\r", index, index + 1)
                index += 1
            continue

        if char == "\n":
            add("NL", "\n", index, index + 1)
            index += 1
            continue

        if source.startswith("//", index):
            cursor = index + 2
            while cursor < size and source[cursor] not in "\r\n":
                cursor += 1
            text = source[index:cursor]
            line = bisect.bisect_right(starts, index)
            comments.append(Comment(text, index, cursor, line, line, False))
            add("COMMENT", text, index, cursor)
            index = cursor
            continue

        if source.startswith("/*", index):
            depth = 1
            cursor = index + 2
            while cursor < size and depth > 0:
                if source.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                elif source.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            text = source[index:cursor]
            start_line = bisect.bisect_right(starts, index)
            end_line = bisect.bisect_right(starts, max(index, cursor - 1))
            comments.append(Comment(text, index, cursor, start_line, end_line, True))
            add("COMMENT", text, index, cursor)
            index = cursor
            continue

        if source.startswith('"""', index):
            end, value, interps = _read_string(source, index, raw=True)
            add("STRING", source[index:end], index, end, value=value, interps=interps)
            index = end
            continue

        if char == '"':
            end, value, interps = _read_string(source, index, raw=False)
            add("STRING", source[index:end], index, end, value=value, interps=interps)
            index = end
            continue

        if char == "'":
            cursor = index + 1
            value_parts: list[str] = []
            while cursor < size:
                current = source[cursor]
                if current == "\\" and cursor + 1 < size:
                    value_parts.append(_ESCAPES.get(source[cursor + 1], source[cursor + 1]))
                    cursor += 2
                    continue
                if current == "'":
                    cursor += 1
                    break
                value_parts.append(current)
                cursor += 1
            add("CHAR", source[index:cursor], index, cursor, value="".join(value_parts))
            index = cursor
            continue

        if char in IDENT_START:
            cursor = index + 1
            while cursor < size and source[cursor] in IDENT_PART:
                cursor += 1
            text = source[index:cursor]
            add("IDENT", text, index, cursor, value=text)
            index = cursor
            continue

        if char in DIGITS:
            cursor = index + 1
            while cursor < size and (source[cursor] in DIGITS or source[cursor] in "._xXaAbBcCdDeEfF"):
                cursor += 1
            text = source[index:cursor]
            add("NUMBER", text, index, cursor, value=text)
            index = cursor
            continue

        matched = None
        for operator in OPERATORS:
            if source.startswith(operator, index):
                matched = operator
                break
        if matched is None:
            matched = char
        add("OP", matched, index, index + len(matched))
        index += len(matched)

    return LexResult(source=source, tokens=tokens, comments=comments, line_starts=starts)


# ---------------------------------------------------------------------------
# Token helpers


def build_matching(tokens: list[Token]) -> dict[int, int]:
    """Map opening bracket index to closing bracket index and back."""

    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in pairs.items()}
    stack: list[tuple[str, int]] = []
    matching: dict[int, int] = {}
    for index, token in enumerate(tokens):
        if token.kind != "OP":
            continue
        if token.text in pairs:
            stack.append((token.text, index))
        elif token.text in closing:
            expected = closing[token.text]
            # Tolerate broken input: pop until a matching opener is found.
            while stack and stack[-1][0] != expected:
                stack.pop()
            if stack:
                _, opener = stack.pop()
                matching[opener] = index
                matching[index] = opener
    return matching


def significant(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"NL", "COMMENT"}]


def split_top_level(tokens: list[Token], separator: str = ",") -> list[list[Token]]:
    parts: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if token.kind == "OP" and token.text in "([{":
            depth += 1
        elif token.kind == "OP" and token.text in ")]}":
            depth -= 1
        if depth == 0 and token.kind == "OP" and token.text == separator:
            parts.append(current)
            current = []
        else:
            current.append(token)
    if current:
        parts.append(current)
    return parts


def token_text(tokens: list[Token]) -> str:
    return " ".join(token.text for token in tokens if token.kind != "NL")


CONTINUATION_OPERATORS = {
    ".",
    "?.",
    "?:",
    "+",
    "-",
    "*",
    "/",
    "%",
    "&&",
    "||",
    "==",
    "!=",
    "<",
    ">",
    "<=",
    ">=",
    "=",
    "->",
    "..",
    "..<",
}


def _continues_on_next_line(tokens: list[Token], index: int, limit: int) -> bool:
    """Kotlin allows a line break before ``.``, ``?.`` and infix operators."""

    cursor = index + 1
    while cursor < limit and tokens[cursor].kind in {"NL", "COMMENT"}:
        cursor += 1
    if cursor >= limit:
        return False
    token = tokens[cursor]
    if token.kind == "OP" and token.text in CONTINUATION_OPERATORS:
        return True
    if token.kind == "IDENT" and token.text in {"else", "catch", "finally"}:
        return True
    # A trailing operator / comma also continues the statement.
    previous = index - 1
    while previous >= 0 and tokens[previous].kind in {"NL", "COMMENT"}:
        previous -= 1
    if previous >= 0:
        before = tokens[previous]
        if before.kind == "OP" and before.text in CONTINUATION_OPERATORS | {","}:
            return True
    return False


def find_statement_end(tokens: list[Token], start: int, limit: int | None = None) -> int:
    """Find the end of a Kotlin statement starting at ``start``.

    Stops at a top-level newline, semicolon or closing brace.  Newlines inside
    parentheses/brackets/braces and line breaks before a chained call do not
    terminate the statement.
    """

    limit = len(tokens) if limit is None else limit
    depth = 0
    index = start
    while index < limit:
        token = tokens[index]
        if token.kind == "OP":
            if token.text in "([{":
                depth += 1
            elif token.text in ")]}":
                if depth == 0:
                    return index
                depth -= 1
            elif token.text == ";" and depth == 0:
                return index
        if token.kind == "NL" and depth == 0:
            if _continues_on_next_line(tokens, index, limit):
                index += 1
                continue
            return index
        index += 1
    return limit


def skip_newlines(tokens: list[Token], index: int, limit: int | None = None) -> int:
    limit = len(tokens) if limit is None else limit
    while index < limit and tokens[index].kind in {"NL", "COMMENT"}:
        index += 1
    return index
