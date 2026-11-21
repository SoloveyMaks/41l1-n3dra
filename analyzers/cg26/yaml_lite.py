"""Minimal YAML / properties reader with line numbers.

Only the subset used by Spring Boot configuration is supported: nested
mappings, scalar values, simple sequences and comments.  Every entry keeps its
1-based line number so findings can point at the exact configuration line.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class YamlEntry:
    path: tuple[str, ...]
    key: str
    value: str
    line: int
    raw: str

    @property
    def dotted(self) -> str:
        return ".".join(self.path)


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote is not None:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            return value[:index].rstrip()
        index += 1
    return value.rstrip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        inner = value[1:-1]
        if value[0] == '"':
            inner = (
                inner.replace("\\\\", "\\")
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
            )
        else:
            inner = inner.replace("''", "'")
        return inner
    return value


def parse_yaml(text: str) -> list[YamlEntry]:
    entries: list[YamlEntry] = []
    stack: list[tuple[int, str]] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        stripped = raw_line.strip()
        if stripped in {"---", "..."}:
            stack = []
            continue
        if stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line.strip()

        if content.startswith("- "):
            item = _strip_inline_comment(content[2:].strip())
            entries.append(
                YamlEntry(
                    path=tuple(key for _, key in stack),
                    key="[]",
                    value=_unquote(item),
                    line=number,
                    raw=raw_line,
                )
            )
            continue

        if ":" not in content:
            continue
        key_part, _, value_part = content.partition(":")
        key = key_part.strip()
        if not key:
            continue
        value = _strip_inline_comment(value_part.strip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = tuple(item for _, item in stack) + (key,)
        entries.append(
            YamlEntry(
                path=path,
                key=key,
                value=_unquote(value),
                line=number,
                raw=raw_line,
            )
        )
        if value == "" or value in {"|", ">", "|-", ">-"}:
            stack.append((indent, key))
    return entries


def parse_properties(text: str) -> list[YamlEntry]:
    entries: list[YamlEntry] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        separator = "=" if "=" in stripped else ":" if ":" in stripped else None
        if separator is None:
            continue
        key, _, value = stripped.partition(separator)
        key = key.strip()
        value = _strip_inline_comment(value.strip())
        if not key:
            continue
        entries.append(
            YamlEntry(
                path=tuple(key.split(".")),
                key=key,
                value=_unquote(value),
                line=number,
                raw=raw_line,
            )
        )
    return entries
